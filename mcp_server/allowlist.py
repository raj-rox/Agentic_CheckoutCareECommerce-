"""Per-agent tool allowlist. Stage 1.

The agent is the token's client_id, which the IdP signs, so an agent cannot
claim another agent's tools. A tool missing from an agent's list is hidden
from list_tools and refused on call_tool.
"""

from fastmcp.utilities.authorization import AuthContext

ALLOWLIST: dict[str, set[str]] = {
    "shop-agent": {"search_catalog", "checkout"},
    "care-agent": {"get_order", "refund"},
}


def agent_may_use(ctx: AuthContext) -> bool:
    if ctx.token is None:
        return False
    return ctx.component.name in ALLOWLIST.get(ctx.token.client_id, set())
