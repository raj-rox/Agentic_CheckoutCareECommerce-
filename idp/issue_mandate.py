"""Fake IdP: issues a user's spending mandate for one agent. Stage 2.

A mandate is the user's signed say-so for what an agent may buy: a total cap
across every order placed under it, one merchant, one shipping address, and
an expiry. It is signed with the same key as user tokens, but typ=mandate
means neither can stand in for the other.

    python -m idp.issue_mandate --sub user_1 --agent shop-agent --max 300 --ship-to "1 Main St"
"""

import argparse
import time
import uuid

import jwt

from idp.issue_token import IDP_AUD, IDP_ISS, _kid, load_or_create_key

MERCHANT_ID = "merchant_1"


def issue_mandate(sub: str, agent: str, max_amount: float, ship_to: str,
                  merchant_id: str = MERCHANT_ID, ttl: int = 3600) -> str:
    """Issue a mandate letting `agent` spend up to `max_amount` dollars in total for `sub`."""
    key = load_or_create_key()
    now = int(time.time())
    claims = {
        "iss": IDP_ISS,
        "aud": IDP_AUD,
        "typ": "mandate",
        "jti": f"mnd_{uuid.uuid4().hex[:12]}",
        "sub": sub,
        "agent": agent,
        "max_amount": max_amount,
        "merchant_id": merchant_id,
        "ship_to": ship_to,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": _kid(key.public_key())})


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a spending mandate for an agent")
    parser.add_argument("--sub", required=True, help="user ID, e.g. user_1")
    parser.add_argument("--agent", required=True, help="agent the mandate is for, e.g. shop-agent")
    parser.add_argument("--max", type=float, required=True, help="total spend cap in dollars")
    parser.add_argument("--ship-to", required=True, help="the only address orders may ship to")
    parser.add_argument("--merchant", default=MERCHANT_ID)
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    args = parser.parse_args()
    print(issue_mandate(args.sub, args.agent, args.max, args.ship_to, args.merchant, args.ttl))


if __name__ == "__main__":
    main()
