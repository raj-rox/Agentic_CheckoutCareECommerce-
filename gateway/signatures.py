"""Agent request signatures. Stage 2.

The agent runtime (not the model, and not the MCP server) holds its Ed25519
key and signs an envelope for every action it asks for:

    {"agent": "shop-agent", "action": "purchase", "args": {...}, "ts": 1730000000, "nonce": "..."}

The envelope travels to the gateway in the MCP call's _meta, untouched. The
gateway verifies it against the agent's registered public key and then acts
on the signed args only, so nothing in between can change what was asked.
"""

import base64
import json
import os
import secrets
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature as _BadSig
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from dotenv import load_dotenv

load_dotenv()

REGISTRY_PATH = Path(os.getenv("AGENT_REGISTRY_PATH", Path(__file__).parent / "registry" / "agents.json"))
MAX_SKEW_SECONDS = 60
ENVELOPE_FIELDS = {"agent", "action", "args", "ts", "nonce"}

_seen_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()


class InvalidSignature(Exception):
    pass


def canonical(envelope: dict) -> bytes:
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: Ed25519PrivateKey, agent: str, action: str, args: dict) -> dict:
    """Build and sign an envelope. Returns {"envelope": ..., "sig": ...} for the call's _meta."""
    envelope = {"agent": agent, "action": action, "args": args, "ts": int(time.time()), "nonce": secrets.token_hex(16)}
    return {"envelope": envelope, "sig": _b64(key.sign(canonical(envelope)))}


def load_registry() -> dict[str, dict]:
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except FileNotFoundError as e:
        raise InvalidSignature(f"No agent registry at {REGISTRY_PATH}; run python -m gateway.register_agents") from e


def _public_key(record: dict) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(record["public_key"].encode())


def verify(envelope: object, sig: object) -> dict:
    """Return the signing agent's registry record if the envelope is genuine and fresh."""
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS or not isinstance(sig, str):
        raise InvalidSignature("Missing or malformed agent signature")
    record = load_registry().get(envelope["agent"])
    if record is None:
        raise InvalidSignature(f"Unknown agent: {envelope['agent']}")
    try:
        _public_key(record).verify(_unb64(sig), canonical(envelope))
    except (_BadSig, ValueError) as e:
        raise InvalidSignature(f"Signature does not match {envelope['agent']}'s key") from e

    now = time.time()
    if not isinstance(envelope["ts"], int) or abs(now - envelope["ts"]) > MAX_SKEW_SECONDS:
        raise InvalidSignature("Stale or future timestamp")
    with _nonce_lock:
        for nonce, expires in list(_seen_nonces.items()):
            if expires < now:
                del _seen_nonces[nonce]
        if envelope["nonce"] in _seen_nonces:
            raise InvalidSignature("Replayed request: nonce already used")
        _seen_nonces[envelope["nonce"]] = now + 2 * MAX_SKEW_SECONDS
    return {"agent": envelope["agent"], **record}
