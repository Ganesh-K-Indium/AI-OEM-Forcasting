"""OIDC bearer-token path against a mock IdP (locally generated RSA key + served JWKS). Not a real IdP."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import Base, SessionLocal, engine
from app.models.ops import User


@pytest.fixture()
def idp():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", alg="RS256", use="sig")
    body = json.dumps({"keys": [jwk]}).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = get_settings()
    old = (s.auth_mode, s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience)
    s.auth_mode, s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience = "oidc", f"http://127.0.0.1:{srv.server_port}/jwks", "https://idp.test", "oem-app"
    import app.core.security as sec
    sec._jwks_client = None
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    yield lambda **c: jwt.encode({"iss": "https://idp.test", "aud": "oem-app", "exp": int(time.time()) + 300, **c}, key, algorithm="RS256", headers={"kid": "k1"})
    srv.shutdown()
    s.auth_mode, s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience = old
    sec._jwks_client = None
    Base.metadata.drop_all(engine)


def test_oidc_accepts_valid_token_and_jit_provisions_role(idp):
    from app.main import create_app
    with TestClient(create_app()) as c:
        r = c.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + idp(sub="abc-123", email="ann@corp.com", name="Ann", roles=["planner"])})
        assert r.status_code == 200 and r.json()["email"] == "ann@corp.com" and r.json()["role"] == "planner"
        assert c.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + idp(email="bob@corp.com", roles=["superuser"])}).json()["role"] == "viewer"  # unknown role -> least privilege
    with SessionLocal() as s:
        assert s.execute(select(User).where(User.email == "ann@corp.com")).scalar_one().role == "planner"


def test_oidc_rejects_bad_audience_expired_and_garbage(idp):
    from app.main import create_app
    with TestClient(create_app()) as c:
        for tok in (idp(email="x@corp.com", aud="other-app"), idp(email="x@corp.com", exp=int(time.time()) - 10), idp(email="x@corp.com", iss="https://evil"), "not.a.jwt"):
            assert c.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + tok}).status_code == 401
        assert c.get("/api/v1/auth/me").status_code == 401
