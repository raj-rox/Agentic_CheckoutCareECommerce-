"""User token verification for the MCP server. Stage 1.

verify_token() is the whole check: RS256 signature against the IdP's JWKS,
then exp, aud and iss, then the claims later checks depend on. Any failure
raises InvalidToken; FastMCP turns that into a 401 before a tool runs.
"""

import json
import os
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastmcp.server.auth import AccessToken, TokenVerifier

load_dotenv()

JWKS_PATH = Path(os.getenv("IDP_JWKS_PATH", Path(__file__).parent.parent / "idp" / "jwks.json"))
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


def verify_token(token: str) -> dict:
    """Return the token's claims if it is valid, else raise InvalidToken."""
    try:
        claims = jwt.decode(
            token,
            _signing_key(token).key,
            algorithms=["RS256"],  # pinned: no "none", no HS256 with the public key
            audience=IDP_AUD,
            issuer=IDP_ISS,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise InvalidToken(str(e)) from e

    if claims.get("role") not in ROLES:
        raise InvalidToken(f"Unknown role: {claims.get('role')}")
    if not isinstance(claims.get("client_id"), str):
        raise InvalidToken("Token has no client_id")
    scopes = claims.get("scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise InvalidToken("scopes must be a list of strings")
    return claims


class StoreTokenVerifier(TokenVerifier):
    """Adapts verify_token() to FastMCP's auth hook."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = verify_token(token)
        except InvalidToken:
            return None
        return AccessToken(
            token=token,
            client_id=claims["client_id"],
            scopes=claims.get("scopes", []),
            expires_at=claims["exp"],
            subject=claims["sub"],
            claims=claims,
        )
