"""User token verification for the MCP server.

The check itself lives in idp/verify.py, shared with the gateway. Any failure
there means FastMCP returns a 401 before a tool runs.
"""

from fastmcp.server.auth import AccessToken, TokenVerifier

from idp.verify import InvalidToken, verify_token

__all__ = ["InvalidToken", "StoreTokenVerifier", "verify_token"]


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
