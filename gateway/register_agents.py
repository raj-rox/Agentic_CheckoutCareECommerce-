"""Generates agent keys and the gateway's agent registry. Stage 2.

Each agent gets an Ed25519 keypair. The private key goes to agents/keys/ for
that agent's runtime; the public key and the agent's allowed_actions go to
gateway/registry/agents.json. Adding an agent is a new entry here, with no
gateway code changes.

    python -m gateway.register_agents                    # register every agent below
    python -m gateway.register_agents --agent shop-agent # rotate one agent's key
"""

import argparse
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gateway import signatures

KEYS_DIR = Path(__file__).parent.parent / "agents" / "keys"

AGENTS: dict[str, list[str]] = {
    "shop-agent": ["purchase"],
    "care-agent": ["refund"],
}


def key_path(agent: str) -> Path:
    return KEYS_DIR / f"{agent}.pem"


def register(agent: str, allowed_actions: list[str]) -> None:
    """Generate a fresh key for `agent` and record its public half in the registry."""
    key = Ed25519PrivateKey.generate()
    path = key_path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    path.chmod(0o600)

    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    registry_path = signatures.REGISTRY_PATH
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    registry[agent] = {"public_key": public.decode(), "allowed_actions": allowed_actions}
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2))


def load_key(agent: str) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(key_path(agent).read_bytes(), password=None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate agent keys and the gateway registry")
    parser.add_argument("--agent", choices=sorted(AGENTS), help="only (re)register this agent")
    args = parser.parse_args()
    for agent, actions in AGENTS.items():
        if args.agent in (None, agent):
            register(agent, actions)
            print(f"{agent}: key at {key_path(agent)}, allowed_actions={actions}")


if __name__ == "__main__":
    main()
