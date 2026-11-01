import pytest

from gateway import audit, register_agents, signatures
from gateway import main as gateway
from idp import issue_token as idp
from idp import verify

SERVICE_KEY = "test-service-key"


@pytest.fixture(scope="session", autouse=True)
def idp_keys(tmp_path_factory):
    """Point the IdP and the verifier at a throwaway keypair for the whole run."""
    keys = tmp_path_factory.mktemp("idp")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(idp, "KEY_PATH", keys / "private.pem")
        mp.setattr(idp, "JWKS_PATH", keys / "jwks.json")
        mp.setattr(verify, "JWKS_PATH", keys / "jwks.json")
        idp.load_or_create_key()
        yield


@pytest.fixture(scope="session", autouse=True)
def agent_keys(tmp_path_factory):
    """Register every agent with a throwaway key and registry for the whole run."""
    agents = tmp_path_factory.mktemp("agents")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(register_agents, "KEYS_DIR", agents / "keys")
        mp.setattr(signatures, "REGISTRY_PATH", agents / "agents.json")
        for agent, actions in register_agents.AGENTS.items():
            register_agents.register(agent, actions)
        yield


@pytest.fixture(autouse=True)
def gateway_state(tmp_path, monkeypatch):
    """A fresh audit log, spend ledger and approval queue for every test."""
    monkeypatch.setenv("STORE_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    gateway._spent.clear()
    gateway._pending.clear()
