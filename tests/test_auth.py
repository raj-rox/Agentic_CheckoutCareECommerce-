import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from idp import issue_token as idp
from idp.issue_mandate import issue_mandate
from idp.verify import verify_mandate
from mcp_server.auth import InvalidToken, StoreTokenVerifier, verify_token

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def claims(**overrides):
    now = int(time.time())
    base = {
        "iss": idp.IDP_ISS, "aud": idp.IDP_AUD, "sub": "user_1", "role": "customer",
        "scopes": ["orders:read"], "client_id": "shop-agent", "iat": now, "exp": now + 60,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def kid():
    return idp._kid(idp.load_or_create_key().public_key())


def sign(payload, key=None):
    """Sign with the IdP's key (or another one), always under the IdP's kid."""
    return jwt.encode(payload, key or idp.load_or_create_key(), algorithm="RS256", headers={"kid": kid()})


def test_valid_token():
    token = idp.issue_token("user_1", "customer", "shop-agent")
    got = verify_token(token)
    assert got["sub"] == "user_1" and got["client_id"] == "shop-agent"
    assert got["scopes"] == idp.ROLE_SCOPES["customer"]


def test_scopes_override():
    token = idp.issue_token("user_1", "customer", "shop-agent", scopes=["orders:read"])
    assert verify_token(token)["scopes"] == ["orders:read"]


@pytest.mark.parametrize("token", [
    pytest.param(lambda: sign(claims(exp=int(time.time()) - 10)), id="expired"),
    pytest.param(lambda: sign(claims(aud="someone-else")), id="wrong-aud"),
    pytest.param(lambda: sign(claims(iss="https://evil.example")), id="wrong-iss"),
    pytest.param(lambda: sign(claims(exp=None)), id="no-exp"),
    pytest.param(lambda: sign(claims(client_id=None)), id="no-client-id"),
    pytest.param(lambda: sign(claims(role="admin")), id="unknown-role"),
    pytest.param(lambda: sign(claims(scopes="orders:read")), id="scopes-not-list"),
    pytest.param(lambda: sign(claims(), key=rsa.generate_private_key(public_exponent=65537, key_size=2048)), id="other-key"),
    pytest.param(lambda: jwt.encode(claims(), None, algorithm="none", headers={"kid": kid()}), id="alg-none"),
    pytest.param(lambda: "not-a-jwt", id="garbage"),
])
def test_rejected(token):
    with pytest.raises(InvalidToken):
        verify_token(token())


async def test_verifier_maps_claims():
    token = idp.issue_token("staff_1", "staff", "care-agent")
    access = await StoreTokenVerifier().verify_token(token)
    assert access.client_id == "care-agent" and access.subject == "staff_1"
    assert access.scopes == idp.ROLE_SCOPES["staff"]
    assert access.claims["role"] == "staff"


async def test_verifier_returns_none_for_bad_token():
    assert await StoreTokenVerifier().verify_token("not-a-jwt") is None


def test_valid_mandate():
    got = verify_mandate(issue_mandate("user_1", "shop-agent", 300, "1 Main St"))
    assert (got["sub"], got["agent"], got["max_amount"], got["ship_to"]) == ("user_1", "shop-agent", 300, "1 Main St")


def test_tokens_and_mandates_are_not_interchangeable():
    with pytest.raises(InvalidToken):
        verify_mandate(idp.issue_token("user_1", "customer", "shop-agent"))
    with pytest.raises(InvalidToken):
        verify_token(issue_mandate("user_1", "shop-agent", 300, "1 Main St"))


@pytest.mark.parametrize("overrides", [
    pytest.param({"typ": None}, id="no-typ"),
    pytest.param({"max_amount": -5}, id="negative-cap"),
    pytest.param({"max_amount": "300"}, id="cap-not-number"),
    pytest.param({"ship_to": None}, id="no-ship-to"),
    pytest.param({"jti": None}, id="no-jti"),
])
def test_rejected_mandate(overrides):
    now = int(time.time())
    base = {
        "iss": idp.IDP_ISS, "aud": idp.IDP_AUD, "typ": "mandate", "jti": "mnd_1", "sub": "user_1",
        "agent": "shop-agent", "max_amount": 300, "merchant_id": "merchant_1", "ship_to": "1 Main St",
        "iat": now, "exp": now + 60,
    }
    base.update(overrides)
    with pytest.raises(InvalidToken):
        verify_mandate(sign({k: v for k, v in base.items() if v is not None}))
