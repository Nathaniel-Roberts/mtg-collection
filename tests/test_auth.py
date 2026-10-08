import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt import PyJWK
from jwt.algorithms import RSAAlgorithm

from app import db
from app.auth import AccessError, AccessVerifier
from app.main import create_app
from tests.conftest import make_settings, seed_catalogue

TEAM = "example.cloudflareaccess.com"
AUD = "a" * 64


class FakeKeys:
    def __init__(self, public_key, kid="kid-1"):
        jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
        jwk["kid"] = kid
        self.jwk = PyJWK.from_dict(jwk)

    def get_signing_key_from_jwt(self, token):
        return self.jwk


@pytest.fixture
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem, private.public_key()


def make_token(pem, *, aud=AUD, iss=f"https://{TEAM}", exp_delta=3600, kid="kid-1", **claims):
    now = int(time.time())
    payload = {"aud": aud, "iss": iss, "iat": now, "exp": now + exp_delta, **claims}
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


def test_verifier_accepts_user_token(tmp_path, keypair):
    pem, public = keypair
    settings = make_settings(
        tmp_path, dev_mode=False, cf_access_team_domain=TEAM, cf_access_aud=AUD
    )
    verifier = AccessVerifier(settings, key_source=FakeKeys(public))
    identity = verifier.verify(make_token(pem, email="Someone@Example.com"))
    assert identity.email == "someone@example.com" and identity.via == "access"


def test_verifier_service_token(tmp_path, keypair):
    pem, public = keypair
    settings = make_settings(
        tmp_path, dev_mode=False, cf_access_team_domain=TEAM, cf_access_aud=AUD
    )
    verifier = AccessVerifier(settings, key_source=FakeKeys(public))
    identity = verifier.verify(make_token(pem, common_name="claude-code.access"))
    assert (
        identity.email is None
        and identity.common_name == "claude-code.access"
        and identity.via == "service_token"
    )


@pytest.mark.parametrize(
    "bad", [{"aud": "b" * 64}, {"iss": "https://other.cloudflareaccess.com"}, {"exp_delta": -10}]
)
def test_verifier_rejects_bad_tokens(tmp_path, keypair, bad):
    pem, public = keypair
    settings = make_settings(
        tmp_path, dev_mode=False, cf_access_team_domain=TEAM, cf_access_aud=AUD
    )
    verifier = AccessVerifier(settings, key_source=FakeKeys(public))
    with pytest.raises(AccessError):
        verifier.verify(make_token(pem, email="x@example.com", **bad))


def test_api_requires_access_when_not_dev(tmp_path, keypair):
    pem, public = keypair
    settings = make_settings(
        tmp_path, dev_mode=False, cf_access_team_domain=TEAM, cf_access_aud=AUD
    )
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    seed_catalogue(conn)
    conn.close()
    app = create_app(settings, run_scheduler=False)
    app.state.access_verifier = AccessVerifier(settings, key_source=FakeKeys(public))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/status").status_code == 401
        headers = {"Cf-Access-Jwt-Assertion": make_token(pem, email="me@example.com")}
        assert client.get("/api/v1/status", headers=headers).status_code == 200
        bad = {"Cf-Access-Jwt-Assertion": make_token(pem, email="me@example.com", aud="c" * 64)}
        assert client.get("/api/v1/status", headers=bad).status_code == 401
        # A service token is not a person; the web API refuses it.
        svc = {"Cf-Access-Jwt-Assertion": make_token(pem, common_name="svc")}
        assert client.get("/api/v1/status", headers=svc).status_code == 401
