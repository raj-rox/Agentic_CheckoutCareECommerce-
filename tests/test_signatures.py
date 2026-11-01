import time

import pytest

from gateway import register_agents, signatures
from gateway.signatures import InvalidSignature, sign, verify

ARGS = {"items": [{"product_id": "prod_1", "qty": 1}], "ship_to": "1 Main St"}


def signed_envelope(agent="shop-agent", key_of=None, **overrides):
    """Sign an envelope by hand, so tests can set any field."""
    envelope = {"agent": agent, "action": "purchase", "args": ARGS, "ts": int(time.time()), "nonce": "n" * 32, **overrides}
    key = register_agents.load_key(key_of or agent)
    return envelope, signatures._b64(key.sign(signatures.canonical(envelope)))


def test_valid_signature():
    meta = sign(register_agents.load_key("shop-agent"), "shop-agent", "purchase", ARGS)
    record = verify(meta["envelope"], meta["sig"])
    assert record["agent"] == "shop-agent" and record["allowed_actions"] == ["purchase"]


def test_key_order_does_not_matter():
    meta = sign(register_agents.load_key("shop-agent"), "shop-agent", "purchase", ARGS)
    reordered = dict(reversed(list(meta["envelope"].items())))
    verify(reordered, meta["sig"])


def test_tampered_args():
    meta = sign(register_agents.load_key("shop-agent"), "shop-agent", "purchase", ARGS)
    meta["envelope"]["args"] = {**ARGS, "items": [{"product_id": "prod_1", "qty": 50}]}
    with pytest.raises(InvalidSignature, match="does not match"):
        verify(meta["envelope"], meta["sig"])


def test_replay():
    meta = sign(register_agents.load_key("shop-agent"), "shop-agent", "purchase", ARGS)
    verify(meta["envelope"], meta["sig"])
    with pytest.raises(InvalidSignature, match="Replayed"):
        verify(meta["envelope"], meta["sig"])


@pytest.mark.parametrize("make, match", [
    pytest.param(lambda: signed_envelope("shop-agent", key_of="care-agent"), "does not match", id="other-agents-key"),
    pytest.param(lambda: signed_envelope("rogue-agent", key_of="shop-agent"), "Unknown agent", id="unknown-agent"),
    pytest.param(lambda: signed_envelope(ts=int(time.time()) - 120, nonce="a" * 32), "Stale", id="stale"),
    pytest.param(lambda: signed_envelope(ts=int(time.time()) + 120, nonce="b" * 32), "Stale", id="future"),
    pytest.param(lambda: ({"agent": "shop-agent"}, "sig"), "malformed", id="missing-fields"),
    pytest.param(lambda: (None, None), "malformed", id="no-signature"),
    pytest.param(lambda: (signed_envelope(nonce="c" * 32)[0], "AAAA"), "does not match", id="garbage-sig"),
])
def test_rejected(make, match):
    envelope, sig = make()
    with pytest.raises(InvalidSignature, match=match):
        verify(envelope, sig)
