import pytest

from idp import issue_token as idp
from mcp_server import auth


@pytest.fixture(scope="session", autouse=True)
def idp_keys(tmp_path_factory):
    """Point the IdP and the verifier at a throwaway keypair for the whole run."""
    keys = tmp_path_factory.mktemp("idp")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(idp, "KEY_PATH", keys / "private.pem")
        mp.setattr(idp, "JWKS_PATH", keys / "jwks.json")
        mp.setattr(auth, "JWKS_PATH", keys / "jwks.json")
        idp.load_or_create_key()
        yield
