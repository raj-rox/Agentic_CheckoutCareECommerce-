"""Audit log. Stage 2: one JSON line per gateway decision.

Each line carries the hash of the line before it, so editing or deleting an
earlier line breaks the chain; verify_chain() finds the first broken line.
Tokens and signatures are never written, only who asked for what and why the
gateway answered as it did.

    python -m gateway.audit    # check the chain
"""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

AUDIT_PATH = Path(os.getenv("GATEWAY_AUDIT_PATH", Path(__file__).parent / "audit.jsonl"))
GENESIS = "0" * 64

_lock = threading.Lock()


def _hash(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _last_hash() -> str:
    if not AUDIT_PATH.exists():
        return GENESIS
    last = None
    with AUDIT_PATH.open() as f:
        for line in f:
            if line.strip():
                last = line
    return json.loads(last)["hash"] if last else GENESIS


def record(**fields) -> str:
    """Append one decision and return its ID."""
    with _lock:
        entry = {
            "id": f"dec_{uuid.uuid4().hex[:12]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            **fields,
            "prev": _last_hash(),
        }
        entry["hash"] = _hash(entry)
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry["id"]


def entries() -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    return [json.loads(line) for line in AUDIT_PATH.read_text().splitlines() if line.strip()]


def verify_chain() -> int | None:
    """Return the 1-based line number of the first broken entry, or None if the chain holds."""
    prev = GENESIS
    for n, entry in enumerate(entries(), start=1):
        body = {k: v for k, v in entry.items() if k != "hash"}
        if entry.get("prev") != prev or _hash(body) != entry.get("hash"):
            return n
        prev = entry["hash"]
    return None


if __name__ == "__main__":
    broken = verify_chain()
    print(f"{AUDIT_PATH}: " + ("chain intact" if broken is None else f"chain broken at line {broken}"))
    raise SystemExit(0 if broken is None else 1)
