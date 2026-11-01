"""The user's spending mandate, as the gateway sees it. Stage 2.

check() verifies the mandate JWT (idp/verify.py) and binds it to this
request: same user as the token, same agent as the signature. Whether the
purchase fits inside it is up to rules.decide_purchase().
"""

from dataclasses import dataclass

from idp.verify import InvalidToken, verify_mandate


class MandateError(Exception):
    pass


@dataclass(frozen=True)
class Mandate:
    id: str
    sub: str
    agent: str
    max_cents: int
    merchant_id: str
    ship_to: str


def check(token: object, sub: str, agent: str) -> Mandate:
    if not isinstance(token, str) or not token:
        raise MandateError("No mandate: the user has not authorized this agent to spend")
    try:
        claims = verify_mandate(token)
    except InvalidToken as e:
        raise MandateError(f"Invalid mandate: {e}") from e
    if claims["sub"] != sub:
        raise MandateError("Mandate belongs to a different user")
    if claims["agent"] != agent:
        raise MandateError(f"Mandate was issued to {claims['agent']}, not {agent}")
    return Mandate(
        id=claims["jti"],
        sub=claims["sub"],
        agent=claims["agent"],
        max_cents=round(claims["max_amount"] * 100),
        merchant_id=claims["merchant_id"],
        ship_to=claims["ship_to"],
    )
