"""Offline JP007 acceptance: real RSA signatures, synthetic claims, no JWKS HTTP."""
from __future__ import annotations

import json
import os
import socket
import time
import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from jobagent.api.founder_auth import (
    CORS_HEADERS, CORS_METHODS, FounderAuthMiddleware, FounderAuthSettings, FounderTokenVerifier,
)


CONFIG = FounderAuthSettings(issuer="https://synthetic-team.cloudflareaccess.com",
                             audience="synthetic-founder-audience", owner_email="owner@example.test",
                             allowed_origins=("https://founder.example.test",))


class FounderAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key())),
                   "kid": "trusted-key", "use": "sig", "alg": "RS256"}

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for method in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))
        self.fetch = self.stack.enter_context(patch.object(jwt.PyJWKClient, "fetch_data", return_value={"keys": [self.jwk]}))
        self.handler = Mock(return_value={"ok": True})

    def client(self, config=CONFIG, peer="testclient", host="https://founder.example.test"):
        app = FastAPI()
        app.add_middleware(CORSMiddleware, allow_origins=list(config.allowed_origins),
                           allow_methods=list(CORS_METHODS), allow_headers=list(CORS_HEADERS))
        app.add_middleware(FounderAuthMiddleware, settings=config)

        @app.api_route("/api/private", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
        def private():
            return self.handler()

        @app.get("/beta")
        def public():
            return {"public": True}

        # Override the peer in ASGI, never through a trusted proxy header.
        async def with_peer(scope, receive, send):
            if scope["type"] == "http":
                scope = {**scope, "client": (peer, 1000)}
            await app(scope, receive, send)

        return self.stack.enter_context(TestClient(with_peer, base_url=host))

    def token(self, changes=None, remove=(), key=None, headers=None):
        now = int(time.time())
        claims = {"iss": CONFIG.issuer, "aud": [CONFIG.audience], "sub": "synthetic-owner-id",
                  "email": CONFIG.owner_email, "type": "app", "exp": now + 300, "iat": now - 1}
        claims.update(changes or {})
        for name in remove:
            claims.pop(name)
        return jwt.encode(claims, key or self.key, algorithm="RS256",
                          headers=headers or {"kid": "trusted-key"})

    def test_default_and_incomplete_configuration_deny(self):
        for config in (FounderAuthSettings(), replace(CONFIG, owner_email=""),
                       replace(CONFIG, audience=""), replace(CONFIG, mode="disabled")):
            with self.subTest(config=config):
                response = self.client(config).get("/api/private")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["cache-control"], "no-store")
        self.handler.assert_not_called()
        self.fetch.assert_not_called()

    def test_email_headers_cookies_loopback_and_beta_bearer_are_not_identity(self):
        client = self.client(peer="127.0.0.1")
        for headers in ({}, {"Cf-Access-Authenticated-User-Email": CONFIG.owner_email},
                        {"Cookie": "CF_Authorization=fake"},
                        {"Authorization": "Bearer beta-session"},
                        {"X-Forwarded-For": "127.0.0.1", "Host": "localhost"}):
            self.assertEqual(client.get("/api/private", headers=headers).status_code, 401)
        self.handler.assert_not_called()

    def test_valid_owner_signature_access_and_no_store(self):
        client = self.client()
        for header in ("Cf-Access-Jwt-Assertion", "Authorization"):
            token = self.token({"email": "OWNER@example.test"})
            value = token if header.startswith("Cf-") else "Bearer " + token
            response = client.get("/api/private", headers={header: value, "Origin": CONFIG.allowed_origins[0]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["access-control-allow-origin"], CONFIG.allowed_origins[0])
        self.assertEqual(self.handler.call_count, 2)

    def test_signed_other_user_is_forbidden_even_with_spoofed_owner_header(self):
        response = self.client().get("/api/private", headers={
            "Cf-Access-Jwt-Assertion": self.token({"email": "beta@example.test"}),
            "Cf-Access-Authenticated-User-Email": CONFIG.owner_email})
        self.assertEqual(response.status_code, 403)
        self.handler.assert_not_called()

    def test_signature_issuer_audience_expiry_and_required_claims(self):
        now = int(time.time())
        cases = [self.token(key=self.foreign_key), self.token({"iss": "https://attacker.example.test"}),
                 self.token({"iss": CONFIG.issuer + "/wrong"}), self.token({"aud": "beta-audience"}),
                 self.token({"exp": now - 10}), self.token({"iat": now + 60}),
                 self.token({"nbf": now + 60}), self.token({"type": "org"}),
                 self.token({"exp": str(now + 300)}), self.token({"sub": ""}),
                 self.token(headers={"kid": "unknown"})]
        cases += [self.token(remove=(name,)) for name in ("iss", "aud", "sub", "email", "exp", "iat", "type")]
        client = self.client()
        for token in cases:
            with self.subTest(case=cases.index(token)):
                self.assertEqual(client.get("/api/private", headers={"Cf-Access-Jwt-Assertion": token}).status_code, 401)
        self.handler.assert_not_called()

    def test_none_and_symmetric_algorithms_rejected_before_key_lookup(self):
        client = self.client()
        for token in (jwt.encode({"email": CONFIG.owner_email}, "", algorithm="none"),
                      jwt.encode({"email": CONFIG.owner_email}, "synthetic-test-secret-that-is-long-enough", algorithm="HS256")):
            self.assertEqual(client.get("/api/private", headers={"Cf-Access-Jwt-Assertion": token}).status_code, 401)
        self.fetch.assert_not_called()

    def test_jwks_failure_fails_closed_without_diagnostics(self):
        self.fetch.side_effect = RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")
        response = self.client().post("/api/private", headers={"Cf-Access-Jwt-Assertion": self.token()})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("SYNTHETIC_PRIVATE", response.text)
        self.handler.assert_not_called()

    def test_key_url_is_operator_pinned_and_header_jku_is_ignored(self):
        verifier = FounderTokenVerifier(CONFIG)
        self.assertEqual(verifier.jwks.uri, CONFIG.issuer + "/cdn-cgi/access/certs")
        verifier.verify(self.token(headers={"kid": "trusted-key", "jku": "https://attacker.example.test/keys"}))

    def test_all_sensitive_paths_and_methods_protected_without_prefix_exceptions(self):
        client = self.client()
        for path in ("/api", "/api/private", "/api/mobile", "/api/mobileish/jobs", "/api/mobile/jobs",
                     "/api%2Fprivate", "/admin", "/administrator", "/docs", "/redoc", "/openapi.json"):
            for method in ("GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(client.request(method, path).status_code, 401)
        self.assertEqual(client.get("/beta").status_code, 200)
        self.handler.assert_not_called()

    def test_cors_and_csrf_origin_not_host_or_proxy_derived(self):
        client = self.client()
        auth = {"Cf-Access-Jwt-Assertion": self.token()}
        for headers in ({"Origin": "https://evil.example.test"}, {"Origin": "null"},
                        {"Origin": "https://evil.example.test", "Host": "evil.example.test"},
                        {"Sec-Fetch-Site": "cross-site"}):
            response = client.post("/api/private", headers={**auth, **headers})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("access-control-allow-origin", response.headers)
        self.handler.assert_not_called()

    def test_allowlisted_preflight_has_no_identity_or_route_side_effects(self):
        client = self.client()
        response = client.options("/api/private", headers={"Origin": CONFIG.allowed_origins[0],
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], CONFIG.allowed_origins[0])
        self.assertNotIn("*", response.headers["access-control-allow-headers"])
        response = client.options("/api/private", headers={"Origin": "https://evil.example.test",
                                                          "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.status_code, 403)
        self.handler.assert_not_called()
        self.fetch.assert_not_called()

    def test_ambiguous_duplicate_and_oversized_credentials_deny(self):
        token = self.token()
        for headers in ([('Cf-Access-Jwt-Assertion', token), ('Cf-Access-Jwt-Assertion', token)],
                        {"Cf-Access-Jwt-Assertion": token, "Authorization": "Bearer unrelated"},
                        {"Authorization": "Bearer " + "x" * 16385}):
            self.assertEqual(self.client().post("/api/private", headers=headers).status_code, 401)
        self.handler.assert_not_called()

    def test_local_dev_needs_explicit_mode_strong_token_peer_host_and_no_proxy(self):
        secret = "offline-synthetic-dev-token-32-bytes-long"
        config = FounderAuthSettings(mode="loopback-dev", dev_token=secret,
                                     allowed_origins=("http://localhost:5173",))
        auth = {"Authorization": "Bearer " + secret}
        self.assertEqual(self.client(config, "127.0.0.1", "http://localhost:8842").get("/api/private", headers=auth).status_code, 200)
        # The installed Starlette TestClient cannot parse IPv6 base URLs. Pass
        # the actual IPv6 Host header and peer separately to exercise ASGI logic.
        self.assertEqual(self.client(config, "::1", "http://localhost:8842").get(
            "/api/private", headers={**auth, "Host": "[::1]:8842"}).status_code, 200)
        cases = [("192.0.2.1", "http://localhost", auth), ("127.0.0.1", "http://public.example.test", auth),
                 ("127.0.0.1", "http://localhost", {}),
                 ("127.0.0.1", "http://localhost", {**auth, "Cf-Connecting-Ip": "127.0.0.1"}),
                 ("127.0.0.1", "http://localhost", {**auth, "X-Forwarded-For": "127.0.0.1"})]
        for peer, host, headers in cases:
            self.assertEqual(self.client(config, peer, host).get("/api/private", headers=headers).status_code, 401)
        self.assertFalse(replace(config, dev_token="short").configured())
        self.assertFalse(replace(config, allowed_origins=("https://public.example.test",)).configured())

    def test_invalid_configuration_never_trusts_arbitrary_jwks_or_wildcard_origin(self):
        for config in (replace(CONFIG, issuer="http://synthetic-team.cloudflareaccess.com"),
                       replace(CONFIG, issuer="https://synthetic-team.cloudflareaccess.com.evil.test"),
                       replace(CONFIG, issuer="https://synthetic-team.cloudflareaccess.com/keys"),
                       replace(CONFIG, allowed_origins=("*",)), replace(CONFIG, owner_email="*@example.test")):
            self.assertFalse(config.configured())

    def test_environment_defaults_are_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(FounderAuthSettings.from_environment(), FounderAuthSettings())

    def test_missing_jwt_dependency_denies_instead_of_bypassing(self):
        with patch("jobagent.api.founder_auth.jwt", None):
            response = self.client().get("/api/private", headers={"Authorization": "Bearer synthetic"})
        self.assertEqual(response.status_code, 503)
        self.handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
