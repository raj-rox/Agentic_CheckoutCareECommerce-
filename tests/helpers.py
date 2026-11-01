from gateway import register_agents, signatures
from idp.issue_mandate import issue_mandate
from idp.issue_token import issue_token


def bearer(sub="user_1", role="customer", client="shop-agent", scopes=None) -> dict:
    return {"Authorization": f"Bearer {issue_token(sub, role, client, scopes=scopes)}"}


def mandate(sub="user_1", agent="shop-agent", max_amount=300, ship_to="1 Main St", **kwargs) -> str:
    return issue_mandate(sub, agent, max_amount, ship_to, **kwargs)


def signed(agent: str, action: str, args: dict, mandate: str | None = None) -> dict:
    """What an agent runtime puts in a call's _meta."""
    meta = signatures.sign(register_agents.load_key(agent), agent, action, args)
    if mandate is not None:
        meta["mandate"] = mandate
    return meta
