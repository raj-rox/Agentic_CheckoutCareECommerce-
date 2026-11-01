"""Verifies tokens the fake IdP signs. Shared by the MCP server and the gateway.

verify_token() checks a user token: RS256 signature against the IdP's JWKS,
then exp, aud and iss, then the claims later checks depend on.
verify_mandate() does the same for a user's spending mandate. Any failure
raises InvalidToken.
"""

import json
import os
from pathlib import Path

import jwt
from dotenv import load_dotenv

load_dotenv()

JWKS_PATH = Path(os.getenv("IDP_JWKS_PATH", Path(__file__).parent / "jwks.json"))
IDP_ISS = os.getenv("IDP_ISS", "https://fake-idp.local")
IDP_AUD = os.getenv("IDP_AUD", "agentic-checkout-care")
ROLES = {"customer", "staff"}


class InvalidToken(Exception):
    pass


def _signing_key(token: str) -> jwt.PyJWK:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        jwks = jwt.PyJWKSet.from_dict(json.loads(JWKS_PATH.read_text()))
    except FileNotFoundError as e:
        raise InvalidToken(f"No JWKS at {JWKS_PATH}; run python -m idp.issue_token first") from e
    except (jwt.PyJWTError, ValueError) as e:
        raise InvalidToken(f"Malformed token or JWKS: {e}") from e
    for key in jwks.keys:
        if key.key_id == kid:
            return key
    raise InvalidToken(f"Unknown signing key: {kid}")


def _decode(token: str, require: list[str]) -> dict:
    try:
        return jwt.decode(
            token,
            _signing_key(token).key,
            algorithms=["RS256"],  # pinned: no "none", no HS256 with the public key
            audience=IDP_AUD,
            issuer=IDP_ISS,
            options={"require": ["exp", "iss", "aud", "sub", *require]},
        )
    except jwt.PyJWTError as e:
        raise InvalidToken(str(e)) from e


def verify_token(token: str) -> dict:
    """Return a user token's claims if it is valid, else raise InvalidToken."""
    claims = _decode(token, [])
    if claims.get("typ", "user") != "user":
        raise InvalidToken(f"Not a user token: {claims.get('typ')}")
    if claims.get("role") not in ROLES:
        raise InvalidToken(f"Unknown role: {claims.get('role')}")
    if not isinstance(claims.get("client_id"), str):
        raise InvalidToken("Token has no client_id")
    scopes = claims.get("scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise InvalidToken("scopes must be a list of strings")
    return claims


def verify_mandate(token: str) -> dict:
    """Return a mandate's claims if it is valid, else raise InvalidToken."""
    claims = _decode(token, ["jti", "agent", "max_amount", "merchant_id", "ship_to"])
    if claims.get("typ") != "mandate":
        raise InvalidToken(f"Not a mandate: {claims.get('typ')}")
    if not isinstance(claims["max_amount"], (int, float)) or claims["max_amount"] <= 0:
        raise InvalidToken("max_amount must be a positive number")
    for field in ("agent", "merchant_id", "ship_to"):
        if not isinstance(claims[field], str):
            raise InvalidToken(f"{field} must be a string")
    return claims
