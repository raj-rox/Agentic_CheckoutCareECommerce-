"""Fake identity provider. Stage 1: issues RS256 user tokens for an agent.

The first run generates a keypair: the private key stays in idp/keys/ and the
public key is published as idp/jwks.json for the MCP server to verify against.

    python -m idp.issue_token --sub user_1 --role customer --client shop-agent
    python -m idp.issue_token --sub staff_1 --role staff --client care-agent --ttl 600
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv

load_dotenv()

IDP_DIR = Path(__file__).parent
KEY_PATH = IDP_DIR / "keys" / "private.pem"
JWKS_PATH = IDP_DIR / "jwks.json"
IDP_ISS = os.getenv("IDP_ISS", "https://fake-idp.local")
IDP_AUD = os.getenv("IDP_AUD", "agentic-checkout-care")

ROLE_SCOPES = {
    "customer": ["orders:read", "payment:execute", "refund:request"],
    "staff": ["orders:read", "refund:request"],
}


def _kid(public_key: rsa.RSAPublicKey) -> str:
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def load_or_create_key() -> rsa.RSAPrivateKey:
    """Load the signing key, generating it and publishing jwks.json on first use."""
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    KEY_PATH.chmod(0o600)

    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid=_kid(key.public_key()), alg="RS256", use="sig")
    JWKS_PATH.write_text(json.dumps({"keys": [jwk]}, indent=2))
    return key


def issue_token(sub: str, role: str, client_id: str, scopes: Optional[list[str]] = None, ttl: int = 3600) -> str:
    """Issue a user token for one agent. Scopes default to the role's scopes."""
    if role not in ROLE_SCOPES:
        raise ValueError(f"Unknown role: {role}")
    key = load_or_create_key()
    now = int(time.time())
    claims = {
        "iss": IDP_ISS,
        "aud": IDP_AUD,
        "sub": sub,
        "role": role,
        "scopes": ROLE_SCOPES[role] if scopes is None else scopes,
        "client_id": client_id,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": _kid(key.public_key())})


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a user token for an agent")
    parser.add_argument("--sub", required=True, help="user ID, e.g. user_1")
    parser.add_argument("--role", required=True, choices=sorted(ROLE_SCOPES))
    parser.add_argument("--client", required=True, help="agent the token is issued to, e.g. shop-agent")
    parser.add_argument("--scopes", help="space-separated scopes; defaults to the role's scopes")
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    args = parser.parse_args()

    scopes = args.scopes.split() if args.scopes is not None else None
    print(issue_token(args.sub, args.role, args.client, scopes=scopes, ttl=args.ttl))


if __name__ == "__main__":
    main()
