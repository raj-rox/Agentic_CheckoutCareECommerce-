"""The user's side of a step-up: see what an agent is asking for, then approve it.

Runs as the user's own app (client customer-app), never as an agent, so an
agent holding the user's token still can't approve its own step-ups.

    export APPROVER_TOKEN=$(python -m idp.issue_token --sub user_1 --role customer --client customer-app)
    python -m gateway.approve appr_1234abcd
"""

import argparse
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8002")
TOKEN_HINT = "python -m idp.issue_token --sub user_1 --role customer --client customer-app"


def _detail(resp: httpx.Response) -> str:
    detail = resp.json().get("detail", resp.text)
    return detail.get("reason", detail) if isinstance(detail, dict) else detail


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve an agent's step-up request")
    parser.add_argument("approval_id")
    parser.add_argument("--token", default=os.getenv("APPROVER_TOKEN"), help="user token for customer-app (default: $APPROVER_TOKEN)")
    parser.add_argument("--yes", action="store_true", help="approve without asking")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit(f"No approver token. Set APPROVER_TOKEN or pass --token. Issue one with:\n  {TOKEN_HINT}")

    with httpx.Client(base_url=GATEWAY_URL, headers={"Authorization": f"Bearer {args.token}"}, timeout=10.0) as gw:
        resp = gw.get(f"/approvals/{args.approval_id}")
        if resp.status_code != 200:
            raise SystemExit(f"Can't load {args.approval_id}: {_detail(resp)}")
        print(json.dumps(resp.json(), indent=2))

        if not args.yes and input("\nApprove? [y/N] ").strip().lower() != "y":
            raise SystemExit("Not approved. It expires on its own.")
        resp = gw.post(f"/approve/{args.approval_id}")
        if resp.status_code != 200:
            raise SystemExit(f"Denied: {_detail(resp)}")
        order = resp.json()["order"]
        print(f"Approved. Order {order['id']} is {order['status']}, total ${order['total']:.2f}")


if __name__ == "__main__":
    main()
