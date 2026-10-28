"""Token checks and the role map. Keys are generated for each run; nothing here talks to Entra."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from energyops.config import ConfigurationError, Environment, Settings
from energyops.security.permissions import ROLE_PERMISSIONS, Permission
from energyops.security.tokens import EntraTokens, LocalTokens, TokenError
from energyops.security.verifier import verifier_from

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "99999999-8888-7777-6666-555555555555"
SECRET = SecretStr("x" * 40)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FixedKeys:
    def __init__(self, key: Any) -> None:
        self._key = key

    def signing_key(self, token: str) -> Any:
        return self._key.public_key()


def entra() -> EntraTokens:
    return EntraTokens(tenant_id=TENANT, client_id=CLIENT, keys=FixedKeys(KEY))


def entra_token(key: Any = KEY, algorithm: str = "RS256", **changes: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "tid": TENANT,
        "oid": "object-1",
        "ver": "2.0",
        "scp": "access_as_user",
        "preferred_username": "Someone@Example.com",
        "name": "Some One",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(changes)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=algorithm)


# ------------------------------------------------------------------ local tokens


async def test_a_local_token_round_trips() -> None:
    tokens = LocalTokens(SECRET)
    claims = await tokens.verify(tokens.issue("Hana@Harbour.example"))
    assert claims.email == "Hana@Harbour.example"
    assert claims.external_id is None


async def test_a_local_token_signed_with_another_secret_is_refused() -> None:
    forged = LocalTokens(SecretStr("y" * 40)).issue("hana@harbour.example")
    with pytest.raises(TokenError):
        await LocalTokens(SECRET).verify(forged)


async def test_an_expired_local_token_is_refused() -> None:
    tokens = LocalTokens(SECRET)
    with pytest.raises(TokenError, match="expired"):
        await tokens.verify(tokens.issue("a@b.example", minutes=-5))


async def test_a_local_token_with_no_signature_is_refused() -> None:
    now = int(time.time())
    unsigned = jwt.encode(
        {"iss": "energyops-local", "aud": "energyops-local", "sub": "a", "exp": now + 60},
        key=None,
        algorithm="none",
    )
    with pytest.raises(TokenError):
        await LocalTokens(SECRET).verify(unsigned)


async def test_an_entra_token_is_not_accepted_as_a_local_one() -> None:
    with pytest.raises(TokenError):
        await LocalTokens(SECRET).verify(entra_token())


def test_a_short_local_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        LocalTokens(SecretStr("short"))


# ------------------------------------------------------------------ Entra tokens


async def test_a_good_entra_token_is_accepted() -> None:
    claims = await entra().verify(entra_token())
    assert claims.external_id == "object-1"
    assert claims.email == "Someone@Example.com"


async def test_the_api_uri_form_of_the_audience_is_accepted() -> None:
    assert (await entra().verify(entra_token(aud=f"api://{CLIENT}"))).subject == "object-1"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"exp": int(time.time()) - 600}, "expired"),
        ({"aud": "someone-else"}, "not for this application"),
        ({"iss": "https://login.microsoftonline.com/other-tenant/v2.0"}, "issuer"),
        ({"iss": f"https://sts.windows.net/{TENANT}/"}, "issuer"),
        ({"tid": "other-tenant"}, "different tenant"),
        ({"ver": "1.0"}, "2.0"),
        ({"scp": "something_else"}, "scope"),
        ({"scp": None}, "scope"),
        ({"oid": None}, "oid"),
    ],
)
async def test_an_entra_token_that_breaks_a_rule_is_refused(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(TokenError, match=message):
        await entra().verify(entra_token(**changes))


async def test_a_token_signed_by_a_different_key_is_refused() -> None:
    with pytest.raises(TokenError):
        await entra().verify(entra_token(key=OTHER_KEY))


async def test_a_token_with_no_signature_is_refused() -> None:
    with pytest.raises(TokenError):
        await entra().verify(entra_token(key=None, algorithm="none"))


async def test_a_shared_secret_token_cannot_pass_for_an_rsa_one() -> None:
    """The classic confusion attack: sign with HS256 using the public key as the secret. The
    token is built by hand because the library refuses to create it."""
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    good = entra_token()
    payload = good.split(".")[1]
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    signing_input = f"{header}.{payload}".encode()
    mac = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    forged = f"{header}.{payload}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"
    with pytest.raises(TokenError):
        await entra().verify(forged)


def test_entra_needs_both_ids() -> None:
    with pytest.raises(ValueError):
        EntraTokens(tenant_id="", client_id=CLIENT, keys=FixedKeys(KEY))


# ------------------------------------------------------------------ choosing a verifier


def settings(**values: Any) -> Settings:
    return Settings(database_url=SecretStr("postgresql://u:p@localhost/x"), **values)


def test_local_uses_the_local_secret() -> None:
    assert isinstance(verifier_from(settings(local_jwt_secret=SECRET)), LocalTokens)


def test_local_without_a_secret_refuses_to_start() -> None:
    with pytest.raises(ConfigurationError, match="LOCAL_JWT_SECRET"):
        verifier_from(settings())


def test_outside_local_the_ids_are_required_and_the_local_secret_is_impossible() -> None:
    with pytest.raises(ConfigurationError, match="ENTRA"):
        verifier_from(settings(environment=Environment.DEV, data_mode="live"))
    with pytest.raises(ValueError, match="only allowed in the local"):
        settings(environment=Environment.DEV, data_mode="live", local_jwt_secret=SECRET)
    ok = settings(
        environment=Environment.DEV,
        data_mode="live",
        entra_tenant_id=TENANT,
        entra_client_id=CLIENT,
    )
    assert isinstance(verifier_from(ok), EntraTokens)


# ------------------------------------------------------------------ roles


def test_each_role_has_a_subset_of_the_one_above() -> None:
    order = ["viewer", "technician", "manager", "org_admin"]
    for lower, higher in zip(order, order[1:], strict=False):
        assert ROLE_PERMISSIONS[lower] < ROLE_PERMISSIONS[higher]


def test_a_platform_admin_holds_no_customer_permission() -> None:
    assert ROLE_PERMISSIONS["platform_admin"] == {Permission.ADMIN_PLATFORM}
    for role in ("viewer", "technician", "manager", "org_admin"):
        assert Permission.ADMIN_PLATFORM not in ROLE_PERMISSIONS[role]


def test_only_managers_and_up_approve_tickets_and_change_tariffs() -> None:
    for role in ("viewer", "technician"):
        assert Permission.APPROVE_TICKETS not in ROLE_PERMISSIONS[role]
        assert Permission.MANAGE_TARIFFS not in ROLE_PERMISSIONS[role]
    for role in ("manager", "org_admin"):
        assert Permission.APPROVE_TICKETS in ROLE_PERMISSIONS[role]
        assert Permission.MANAGE_TARIFFS in ROLE_PERMISSIONS[role]


def test_a_viewer_cannot_run_agents() -> None:
    assert Permission.RUN_AGENTS not in ROLE_PERMISSIONS["viewer"]


# ------------------------------------------------------------------ fetching the signing keys


def _jwks_for(*pairs: tuple[str, Any]) -> bytes:
    import json

    keys = []
    for kid, private in pairs:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        keys.append(jwk)
    return json.dumps({"keys": keys}).encode()


@pytest.fixture
def jwks_server() -> Any:
    """A tiny local web server that serves a key set, standing in for Microsoft's."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    body = _jwks_for(("key-a", KEY), ("key-b", OTHER_KEY))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # keep test output quiet
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/keys"
    server.shutdown()
    thread.join(timeout=5)


async def test_the_key_provider_picks_the_key_the_token_names(jwks_server: str) -> None:
    from energyops.security.tokens import JwksKeyProvider

    verifier = EntraTokens(tenant_id=TENANT, client_id=CLIENT, keys=JwksKeyProvider(jwks_server))
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "tid": TENANT,
        "oid": "object-1",
        "ver": "2.0",
        "scp": "access_as_user",
        "exp": now + 600,
    }
    signed_by_b = jwt.encode(claims, OTHER_KEY, algorithm="RS256", headers={"kid": "key-b"})
    assert (await verifier.verify(signed_by_b)).subject == "object-1"
    # Signed by one key but naming the other key's id: the signature check must fail.
    lying = jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "key-b"})
    with pytest.raises(TokenError):
        await verifier.verify(lying)
    # A key id the provider has never published is refused, not guessed at.
    unknown = jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "key-zzz"})
    with pytest.raises(TokenError):
        await verifier.verify(unknown)
