"""Trust gateway. Stage 2: one deterministic checkpoint for every action that moves money.

It sits in front of the store API, which now only accepts calls carrying the
gateway's service key. Every purchase and refund goes through these checks in
order, and the first one that fails decides:

1. User token valid, with the scope for this action?
2. Agent signature matches a registered key, and the token was issued to that agent?
3. Action in this agent's allowed_actions?
4. Inside the user's mandate? (purchases)
5. Risk rules (gateway/rules.py): approve, step-up or deny

Every outcome is written to the audit log. A step-up returns 202 with an
approval_id; the user approves it from their own app (never an agent) with
POST /approve/{id}, and the gateway then runs the action it was holding.

    uvicorn gateway.main:app --port 8002
"""

import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, NoReturn, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError

from gateway import audit, rules, signatures
from gateway import mandate as mandates
from gateway.rules import Decision
from idp.verify import InvalidToken, verify_token

load_dotenv()

STORE_API_URL = os.getenv("STORE_API_URL", "http://localhost:8001")
MERCHANT_ID = "merchant_1"
APPROVER_CLIENTS = {"customer-app"}  # the user's own app; agents can never approve their own step-ups
APPROVAL_TTL_SECONDS = 600
ACTION_SCOPES = {"purchase": "payment:execute", "refund": "refund:request"}

app = FastAPI(title="Trust gateway")
store = httpx.Client(base_url=STORE_API_URL, timeout=10.0)

# One money action at a time, so a spend check and the order it allows can't interleave with another.
_lock = threading.Lock()
_spent: dict[str, int] = {}     # mandate ID -> cents already spent under it
_pending: dict[str, dict] = {}  # approval ID -> action waiting on the user


class ActionRequest(BaseModel):
    """What the MCP server forwards: the agent's signed envelope, plus the user's mandate."""
    envelope: Optional[dict] = None
    sig: Optional[str] = None
    mandate: Optional[str] = None


class LineItem(BaseModel):
    product_id: str
    qty: int = Field(default=1, ge=1)


class PurchaseArgs(BaseModel):
    items: list[LineItem] = Field(min_length=1)
    ship_to: Optional[str] = None


class RefundArgs(BaseModel):
    order_id: str
    amount: Optional[float] = Field(default=None, gt=0)


def _cents(dollars: float) -> int:
    return round(dollars * 100)


def _deny(status: int, rule: str, reason: str, ctx: dict) -> NoReturn:
    decision_id = audit.record(**ctx, outcome="deny", rule=rule, reason=reason)
    raise HTTPException(status, {"decision": "deny", "rule": rule, "reason": reason, "decision_id": decision_id})


def _store(method: str, path: str, **kwargs) -> dict:
    headers = {"X-Service-Key": os.getenv("STORE_SERVICE_KEY", "")}
    try:
        resp = store.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Store API unreachable: {e}") from e
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    return resp.json()


def _user(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise InvalidToken("Missing bearer token")
    return verify_token(authorization.removeprefix("Bearer "))


def _authorize(action: str, authorization: Optional[str], req: ActionRequest, ctx: dict) -> tuple[dict, str]:
    """Checks 1 to 3. Returns the user's claims and the signing agent."""
    try:
        claims = _user(authorization)
    except InvalidToken as e:
        _deny(401, "token", f"Invalid user token: {e}", ctx)
    ctx.update(sub=claims["sub"], role=claims["role"], client_id=claims["client_id"])
    if ACTION_SCOPES[action] not in claims["scopes"]:
        _deny(403, "token.scope", f"Token lacks the {ACTION_SCOPES[action]} scope", ctx)

    try:
        agent = signatures.verify(req.envelope, req.sig)
    except signatures.InvalidSignature as e:
        _deny(401, "signature", str(e), ctx)
    ctx["agent"] = agent["agent"]
    if agent["agent"] != claims["client_id"]:
        _deny(403, "signature.client_id", f"Token was issued to {claims['client_id']}, but {agent['agent']} signed the request", ctx)
    if req.envelope["action"] != action:
        _deny(403, "signature.action", f"Signed for {req.envelope['action']}, not {action}", ctx)

    if action not in agent["allowed_actions"]:
        _deny(403, "allowed_actions", f"{agent['agent']} may not {action}", ctx)
    return claims, agent["agent"]


def _parse(model: type[BaseModel], raw: object, ctx: dict):
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        _deny(422, "args", f"Invalid {ctx['action']} args: {e.errors()[0]['msg']}", ctx)


def _visible_order(order_id: str, claims: dict) -> dict:
    """The order if this user may see it. Someone else's order looks like one that doesn't exist."""
    try:
        order = _store("GET", f"/orders/{order_id}")
    except HTTPException as e:
        if e.status_code != 404:
            raise
        order = None
    if order is None or (claims["role"] != "staff" and order.get("user_id") != claims["sub"]):
        raise HTTPException(404, f"Unknown order: {order_id}")
    return order


def _execute(execute: Callable[[], dict], decision: Decision, ctx: dict) -> dict:
    try:
        result = execute()
    except HTTPException as e:
        audit.record(**ctx, outcome="error", rule=decision.rule, reason=f"Store refused: {e.detail}")
        raise
    decision_id = audit.record(**{**ctx, "order_id": result["id"]}, outcome="approve", rule=decision.rule, reason=decision.reason)
    return {"decision": "approve", "reason": decision.reason, "decision_id": decision_id, "order": result}


def _act(decision: Decision, ctx: dict, sub: str, response: Response,
         execute: Callable[[], dict], redecide: Callable[[], Decision]) -> dict:
    """Check 5: carry out the rules' decision. `redecide` reruns the rules when a step-up is approved."""
    if decision.outcome == "deny":
        _deny(403, decision.rule, decision.reason, ctx)
    if decision.outcome == "approve":
        return _execute(execute, decision, ctx)

    approval_id = f"appr_{uuid.uuid4().hex[:12]}"
    decision_id = audit.record(**ctx, outcome="step_up", rule=decision.rule, reason=decision.reason, approval_id=approval_id)
    _pending[approval_id] = {
        "sub": sub, "ctx": ctx, "reason": decision.reason, "decision_id": decision_id,
        "execute": execute, "redecide": redecide, "expires": time.time() + APPROVAL_TTL_SECONDS,
    }
    response.status_code = 202
    return {
        "decision": "step_up", "reason": decision.reason, "decision_id": decision_id,
        "approval_id": approval_id, "expires_in": APPROVAL_TTL_SECONDS,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/purchase")
def purchase(req: ActionRequest, response: Response, authorization: Optional[str] = Header(None)):
    ctx = {"action": "purchase"}
    with _lock:
        claims, agent = _authorize("purchase", authorization, req, ctx)
        args = _parse(PurchaseArgs, req.envelope["args"], ctx)
        ctx.update(items=[i.model_dump() for i in args.items], ship_to=args.ship_to)

        try:
            mandate = mandates.check(req.mandate, claims["sub"], agent)
        except mandates.MandateError as e:
            _deny(403, "mandate", str(e), ctx)
        ctx["mandate_id"] = mandate.id

        # The gateway prices the order itself; nothing the agent says about cost is trusted.
        total = 0
        for item in args.items:
            try:
                total += _cents(_store("GET", f"/catalog/{item.product_id}")["price"]) * item.qty
            except HTTPException as e:
                if e.status_code != 404:
                    raise
                _deny(404, "args", f"Unknown product: {item.product_id}", ctx)
        ctx["amount"] = total / 100

        def redecide() -> Decision:
            return rules.decide_purchase(total, args.ship_to, MERCHANT_ID, mandate, _spent.get(mandate.id, 0))

        def execute() -> dict:
            body = {"items": [i.model_dump() for i in args.items], "ship_to": args.ship_to, "user_id": claims["sub"]}
            order = _store("POST", "/checkout", json=body)
            _spent[mandate.id] = _spent.get(mandate.id, 0) + _cents(order["total"])
            return order

        return _act(redecide(), ctx, claims["sub"], response, execute, redecide)


@app.post("/refund")
def refund(req: ActionRequest, response: Response, authorization: Optional[str] = Header(None)):
    ctx = {"action": "refund"}
    with _lock:
        claims, _ = _authorize("refund", authorization, req, ctx)
        args = _parse(RefundArgs, req.envelope["args"], ctx)
        ctx["order_id"] = args.order_id

        try:
            order = _visible_order(args.order_id, claims)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            _deny(404, "ownership", e.detail, ctx)
        amount = _cents(order["total"]) if args.amount is None else _cents(args.amount)
        ctx["amount"] = amount / 100

        def redecide() -> Decision:
            # Re-read the order: it may have been refunded since the step-up was raised.
            current = _visible_order(args.order_id, claims)
            created_at = datetime.fromisoformat(current["created_at"])
            return rules.decide_refund(amount, _cents(current["total"]), current["status"], created_at, datetime.now(timezone.utc))

        def execute() -> dict:
            return _store("POST", "/refund", json={"order_id": args.order_id, "amount": amount / 100})

        return _act(redecide(), ctx, claims["sub"], response, execute, redecide)


@app.get("/orders/{order_id}")
def get_order(order_id: str, authorization: Optional[str] = Header(None)):
    """Read-only, so no agent signature and no audit line; the user token and ownership still apply."""
    try:
        claims = _user(authorization)
    except InvalidToken as e:
        raise HTTPException(401, f"Invalid user token: {e}") from e
    if "orders:read" not in claims["scopes"]:
        raise HTTPException(403, "Token lacks the orders:read scope")
    return _visible_order(order_id, claims)


def _approver(approval_id: str, authorization: Optional[str], ctx: dict) -> tuple[dict, dict]:
    """The user's claims and their pending action, if this caller may act on it."""
    try:
        claims = _user(authorization)
    except InvalidToken as e:
        _deny(401, "token", f"Invalid user token: {e}", ctx)
    ctx.update(sub=claims["sub"], client_id=claims["client_id"])
    if claims["client_id"] not in APPROVER_CLIENTS:
        _deny(403, "approve.client", f"{claims['client_id']} cannot approve; only the user's own app can", ctx)
    pending = _pending.get(approval_id)
    if pending is None or pending["sub"] != claims["sub"]:
        _deny(404, "approve.unknown", f"Unknown approval: {approval_id}", ctx)
    if pending["expires"] < time.time():
        del _pending[approval_id]
        _deny(410, "approve.expired", f"Approval {approval_id} expired", ctx)
    return claims, pending


@app.get("/approvals/{approval_id}")
def show_approval(approval_id: str, authorization: Optional[str] = Header(None)):
    """What the user is being asked to approve."""
    with _lock:
        _, pending = _approver(approval_id, authorization, {"action": "approve", "approval_id": approval_id})
    fields = ("action", "agent", "amount", "items", "ship_to", "order_id")
    return {
        "approval_id": approval_id, "reason": pending["reason"],
        "expires_in": int(pending["expires"] - time.time()),
        **{k: pending["ctx"][k] for k in fields if k in pending["ctx"]},
    }


@app.post("/approve/{approval_id}")
def approve(approval_id: str, authorization: Optional[str] = Header(None)):
    ctx = {"action": "approve", "approval_id": approval_id}
    with _lock:
        claims, pending = _approver(approval_id, authorization, ctx)
        del _pending[approval_id]  # single use, even if what follows fails
        ctx = {**pending["ctx"], "approval_id": approval_id, "approved_by": claims["client_id"], "step_up_id": pending["decision_id"]}

        decision = pending["redecide"]()
        if decision.outcome == "deny":
            _deny(403, decision.rule, f"No longer allowed: {decision.reason}", ctx)
        return _execute(pending["execute"], Decision("approve", "Approved by the user", "step_up.approved"), ctx)
