# -*- coding: utf-8 -*-
"""Shared pytest harness for the admin-panel backend.

Design goals:
  * Never touch the real admin.db / pipeline.db. We point ADMIN_DB_URL at a throwaway
    temp SQLite file BEFORE any `app.*` module is imported, so the module-level engine in
    app/db.py binds to the temp DB (config.get_settings() is lru_cached, so the env var must
    win at import time — which it does, because this conftest runs first).
  * Never hit real Barsy / Shopify / couriers. Tests that exercise the ERP push path
    monkeypatch `app.routers.intake.barsy.call`.
  * A clean database per test (drop_all + create_all) so the first-run setup guard sees
    zero users and role tests start from a known state.

Fixtures exported:
  client        - unauthenticated httpx.AsyncClient bound to the ASGI app
  owner_client  - client with the first-run owner created and logged in (cookie auth)
  staff_client  - client logged in as a non-owner staff account (owner creates it)
  make_draft    - async factory that inserts an IntakeDraft row and returns its id
  mock_barsy    - monkeypatches barsy.call to a recording stub (no real ERP call)
"""
import os
import shutil
import tempfile
from pathlib import Path

# --- point the app at a disposable DB BEFORE importing anything from app.* ----------
_TMPDIR = tempfile.mkdtemp(prefix="admin_panel_tests_")
_TEST_DB = Path(_TMPDIR, "test_admin.db")
os.environ["ADMIN_DB_URL"] = "sqlite+aiosqlite:///" + _TEST_DB.as_posix()
# the ASGI test client talks plain http://test, so a Secure-flagged cookie would be dropped by
# httpx and every authenticated fixture would fail. Force non-secure cookies for the suite only.
os.environ["COOKIE_SECURE"] = "0"
# known webhook creds so the receiver-signature tests can forge valid HMAC / token.
os.environ["SHOPIFY_WEBHOOK_SECRET"] = "test-shopify-secret"
os.environ["BARSY_WEBHOOK_TOKEN"] = "test-barsy-token"
# The catalog/pricing/purchasing write-gate defaults OFF (pre-launch safety); the suite exercises
# the LIVE write bodies, so turn it on here. test_barsy_write_gate.py flips it off per-test to prove
# the gate blocks writes.
os.environ["BARSY_WRITE_LIVE"] = "1"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport  # noqa: E402

import app.routers.intake as intake_module  # noqa: E402
from app import ratelimit  # noqa: E402
from app.db import Base, async_session_maker, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import IntakeDraft  # noqa: E402

# safety net: the temp URL must actually be in force, or we'd risk the real admin.db
assert "admin_panel_tests_" in str(engine.url), (
    f"test engine is not bound to the temp DB (got {engine.url!r}); "
    "ADMIN_DB_URL was not applied before app import")

_OWNER = {"email": "owner@example.com", "password": "owner-pw-123456", "full_name": "Owner"}
_STAFF = {"email": "staff@example.com", "password": "staff-pw-123456", "role": "staff"}


def pytest_sessionfinish(session, exitstatus):
    """Best-effort cleanup of the throwaway DB directory."""
    try:
        engine.sync_engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TMPDIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The auth rate limiter is process-global in-memory state. Every test shares the same
    `testclient` IP, so without this the per-ip request cap would leak across tests and start
    429-ing later logins. Clear it around each test."""
    ratelimit._fails._hits.clear()
    ratelimit._reqs._hits.clear()
    yield
    ratelimit._fails._hits.clear()
    ratelimit._reqs._hits.clear()


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    """A clean schema for every test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    """Unauthenticated ASGI client. Cookies persist within the client instance, so logging
    in on it upgrades it to an authenticated session."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(c: httpx.AsyncClient, email: str, password: str) -> None:
    # FastAPI-Users login is OAuth2 form-encoded: username/password. The cookie backend
    # returns 204 and sets the httponly darling_admin cookie, which httpx stores.
    r = await c.post("/api/auth/login", data={"username": email, "password": password})
    assert r.status_code == 204, f"login failed: {r.status_code} {r.text}"
    assert "darling_admin" in c.cookies


@pytest_asyncio.fixture
async def owner_client(client):
    """First-run owner, created via the one-time /setup endpoint, then logged in."""
    r = await client.post("/api/auth/setup", json=_OWNER)
    assert r.status_code == 201, r.text
    await _login(client, _OWNER["email"], _OWNER["password"])
    return client


@pytest_asyncio.fixture
async def staff_client(owner_client):
    """A logged-in staff (non-owner) account. The owner mints it via /api/auth/staff
    (that is the only way staff accounts are created), then we log in a separate client."""
    r = await owner_client.post("/api/auth/staff", json=_STAFF)
    assert r.status_code == 201, r.text
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    await _login(c, _STAFF["email"], _STAFF["password"])
    try:
        yield c
    finally:
        await c.aclose()


@pytest_asyncio.fixture
async def manager_client(monkeypatch):
    """A logged-in Barsy MANAGER (role_id 1 → panel manager/superuser). Managers come from
    Barsy-login (not local /setup), so we mock barsy_auth.verify to mint one."""
    import app.routers.auth_extra as _ax
    monkeypatch.setattr(_ax.barsy_auth, "verify", lambda u, p: {
        "user_id": 100, "user_name": "mgr", "role_id": 1, "role_name": "Мениджър",
        "fname": "Мария", "lname": "Мениджър", "email": "mgr@x.bg"})
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    r = await c.post("/api/auth/barsy-login", json={"username": "mgr", "password": "x"})
    assert r.status_code in (200, 204), r.text
    try:
        yield c
    finally:
        await c.aclose()


@pytest_asyncio.fixture
async def make_draft():
    """Factory: insert an IntakeDraft directly (bypassing the API) and return its id."""
    async def _make(**kw) -> int:
        async with async_session_maker() as s:
            d = IntakeDraft(**kw)
            s.add(d)
            await s.commit()
            await s.refresh(d)
            return d.id
    return _make


@pytest.fixture
def mock_barsy(monkeypatch):
    """Replace the real Barsy client call with a recording stub. Returns the list of
    (operation, body) calls so tests can assert what would have been sent to the ERP."""
    calls: list[tuple] = []

    def _fake_call(operation, body, article_id=12345):
        calls.append((operation, body))
        return article_id

    monkeypatch.setattr(intake_module.barsy, "call", _fake_call)
    return calls


class _BarsyRecorder:
    """Records every barsy.call(op, body) and lets a test dictate per-operation return
    values. `barsy` is a single shared module object imported by every router, so patching
    it once covers catalog / purchasing / admin alike."""

    def __init__(self):
        self.calls: list[tuple] = []
        self._returns: dict = {}
        self._default = None

    def __call__(self, operation, body=None, *args, **kwargs):
        self.calls.append((operation, body))
        if operation in self._returns:
            val = self._returns[operation]
            return val(body) if callable(val) else val
        return self._default

    # -- configuration -------------------------------------------------------
    def set_return(self, operation, value):
        """Value (or a callable body->value) returned for a given operation."""
        self._returns[operation] = value
        return self

    def set_default(self, value):
        self._default = value
        return self

    # -- assertions helpers --------------------------------------------------
    @property
    def operations(self) -> list:
        return [op for op, _ in self.calls]

    def body_for(self, operation) -> dict:
        """The body of the first recorded call to `operation` (KeyError if none)."""
        for op, body in self.calls:
            if op == operation:
                return body
        raise KeyError(f"no recorded barsy.call for {operation!r}; saw {self.operations}")


@pytest.fixture
def barsy_recorder(monkeypatch):
    """A reusable, configurable stand-in for the shared barsy.call. Patches the one
    darling_core.barsy module object that catalog/purchasing/admin all import, so no test
    can reach the real ERP. Returns the recorder for return-value config + body assertions."""
    rec = _BarsyRecorder()
    # intake_module.barsy is `from ..core import barsy` — the same shared module object the
    # other routers reference, so a single setattr covers them all.
    monkeypatch.setattr(intake_module.barsy, "call", rec)
    return rec


@pytest_asyncio.fixture
async def packer_client(owner_client):
    """A logged-in packer account — the lowest role, below 'staff'. Used to prove that
    staff-gated writes refuse a non-staff/non-superuser. Owner mints it, then we log in a
    separate client (mirrors the staff_client fixture)."""
    creds = {"email": "packer@example.com", "password": "packer-pw-123456", "role": "packer"}
    r = await owner_client.post("/api/auth/staff", json=creds)
    assert r.status_code == 201, r.text
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    await _login(c, creds["email"], creds["password"])
    try:
        yield c
    finally:
        await c.aclose()
