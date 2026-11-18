# Dashboard Session Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require the management key to open `/dashboard`, exchange it for an HttpOnly session cookie, never expose the key to the browser, and fail closed when no key is configured.

**Architecture:** A new `/v1/dashboard/session` router verifies the management key and sets a signed HttpOnly cookie. `verify_management_key` accepts that cookie or the existing header/query key, and a `kv` claim ties every session to the current key. `/dashboard` serves a login page unless the session is valid; the console uses cookies only. The management settings API redacts the key and restricts rotation.

**Tech Stack:** FastAPI, Starlette cookies, python-jose JWT, Pydantic, vanilla JS dashboard, pytest/TestClient.

**Spec:** `VoiceClonerAppServer/docs/superpowers/specs/2026-11-18-dashboard-auth-design.md`

## Global Constraints

- Session lifetime: 8 hours (`28800` seconds), no refresh.
- Cookie name `vc_dashboard_session`; `HttpOnly`, `SameSite=Strict`, `Path=/`; `Secure` only when `DASHBOARD_COOKIE_SECURE=true` (default false).
- Login rate limit: 10 failed attempts per IP per 300 seconds; `429` with `Retry-After: 300`.
- `MANAGEMENT_KEY` environment variable wins over `config/settings.json` and is never written to disk.
- Empty management key fails closed: management endpoints `503`, login `503`, dashboard locked.
- The management key value never appears in an API response, a static file, or browser storage.
- Dashboard HTML and session responses send `Cache-Control: no-store`.
- Run tests from the monorepo root: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/ -v`.
- Work on a feature branch in `VoiceClonerAppServer/`; update `README.md` and `CONTEXT.md` in the same commit as behavior changes.
- Every commit ends with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## Review Focus

- An unauthenticated `GET /v1/management/*` must return `401`, never `200`, after the refactor.
- A settings save of any kind must never write the env-provided key into `config/settings.json`.
- A missing, expired, garbage, or rotated session cookie must redirect `/dashboard` to the login page, never raise `500`.
- A whitespace-only or wrong login key must return a generic `401` without revealing whether a key exists.
- Cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` without `X-Dashboard-Request: 1` must return `403`.

---

### Task 1: Environment-aware management key and fail-closed default

**Files:**
- Modify: `VoiceClonerAppServer/core/settings_manager.py:22-27,106-113,188-193`
- Modify: `VoiceClonerAppServer/config/settings.json:7`
- Modify: `VoiceClonerAppServer/conftest.py`
- Test: `VoiceClonerAppServer/tests/test_management_key_config.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `SecurityConfig.management_key: str` defaults to `""`.
  - `SettingsManager.management_key_source -> str` returning `"environment" | "config" | "unset"`.
  - `SettingsManager.get_effective_management_key() -> str`.
  - `SettingsManager.get_settings() -> AppSettings` returns a deep copy with the env key applied when the variable is set; `_settings` keeps the file value.

- [ ] **Step 1: Create the feature branch**

```bash
cd VoiceClonerAppServer
git checkout -b security/dashboard-auth
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_management_key_config.py`:

```python
import json

from VoiceClonerAppServer.core.settings_manager import SettingsManager


def _write_config(tmp_path, key):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"security": {"management_key": key}}), encoding="utf-8")
    return cfg


def test_management_key_default_is_empty_and_source_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGEMENT_KEY", raising=False)
    cfg = _write_config(tmp_path, "")
    sm = SettingsManager(settings_path=cfg)
    assert sm.get_settings().security.management_key == ""
    assert sm.get_effective_management_key() == ""
    assert sm.management_key_source == "unset"


def test_env_key_overrides_file_and_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGEMENT_KEY", "env_secret_key")
    cfg = _write_config(tmp_path, "file_secret_key")
    sm = SettingsManager(settings_path=cfg)

    assert sm.get_effective_management_key() == "env_secret_key"
    assert sm.get_settings().security.management_key == "env_secret_key"
    assert sm.management_key_source == "environment"

    sm.update_settings({"server": {"port": 8010}})
    on_disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert on_disk["security"]["management_key"] == "file_secret_key"


def test_config_key_reports_config_source(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGEMENT_KEY", raising=False)
    cfg = _write_config(tmp_path, "file_secret_key")
    sm = SettingsManager(settings_path=cfg)
    assert sm.get_effective_management_key() == "file_secret_key"
    assert sm.management_key_source == "config"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_management_key_config.py -v`
Expected: FAIL (`management_key_source` / `get_effective_management_key` missing; default is not empty).

- [ ] **Step 4: Implement the settings changes**

In `core/settings_manager.py`:
- `SecurityConfig.management_key = Field("", ...)`; remove the historical default literal.
- In `SettingsManager.__init__`, set `self._env_management_key = (os.getenv("MANAGEMENT_KEY") or "").strip()` before `self._load()`.
- Add:

```python
def get_effective_management_key(self) -> str:
    self._check_and_reload()
    return self._env_management_key or self._settings.security.management_key.strip()

@property
def management_key_source(self) -> str:
    self._check_and_reload()
    if self._env_management_key:
        return "environment"
    return "config" if self._settings.security.management_key.strip() else "unset"
```

`_check_and_reload()` is required so an on-disk key change invalidates sessions through the `kv` claim.

- `get_settings()` returns a deep copy with `settings.security.management_key = self._env_management_key` when the env value is non-empty; otherwise return `self._settings` unchanged. `update_settings`, `generate_client_key`, `revoke_client_key`, `toggle_client_key`, and `_save_to_disk` continue to use `self._settings`, so the env value never reaches disk.

In `conftest.py`, before any test imports `main`:

```python
import os
os.environ["MANAGEMENT_KEY"] = "test_management_key"
```

The assignment (not `setdefault`) keeps the suite deterministic on a machine that already exports `MANAGEMENT_KEY`.

In `config/settings.json`, change `"management_key": "vc_admin_secret_key_123"` to `"management_key": ""`.

- [ ] **Step 5: Run the new tests and the existing settings tests**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_management_key_config.py VoiceClonerAppServer/tests/test_settings_and_3tier_auth.py -v`
Expected: new tests PASS; existing tests PASS (they read the env key from `get_settings`).

- [ ] **Step 6: Commit**

```bash
git add core/settings_manager.py config/settings.json conftest.py tests/test_management_key_config.py
git commit -m "feat: make management key env-aware and fail closed

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: Session token primitives and fail-closed management auth

**Files:**
- Modify: `VoiceClonerAppServer/core/security.py:1-20,168-184`
- Modify: `VoiceClonerAppServer/api/auth.py:55-66`
- Test: `VoiceClonerAppServer/tests/test_dashboard_auth.py` (create)

**Interfaces:**
- Consumes: `settings_manager.get_effective_management_key()`, `management_key_source` from Task 1.
- Produces:
  - `DASHBOARD_SESSION_COOKIE: str = "vc_dashboard_session"`
  - `DASHBOARD_SESSION_TTL_SECONDS: int = 28800`
  - `create_dashboard_session_token(management_key: str) -> str`
  - `verify_dashboard_session_token(token: str, management_key: str) -> bool`
  - `has_valid_dashboard_session(request: Request) -> bool`
  - `resolve_client_ip(request: Request) -> str`
  - `verify_management_key(...) -> str`, returning `"dashboard-session"`, `"management-key"`, or `"management-key-query"`, raising `503` when no key is configured.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_auth.py`:

```python
import asyncio

from fastapi.testclient import TestClient

from VoiceClonerAppServer.core import security
from VoiceClonerAppServer.core.redis_client import redis_client
from VoiceClonerAppServer.core.settings_manager import settings_manager
from VoiceClonerAppServer.main import app

KEY = "test_management_key"
client = TestClient(app)


def _reset_login_rate_limit():
    asyncio.run(redis_client.delete("dashboard_login:testclient"))


def test_session_token_roundtrip_and_rotation():
    token = security.create_dashboard_session_token(KEY)
    assert security.verify_dashboard_session_token(token, KEY) is True
    assert security.verify_dashboard_session_token(token, "different_key") is False
    assert security.verify_dashboard_session_token("garbage", KEY) is False


def test_management_api_fails_closed_when_key_unset(monkeypatch):
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    settings_manager._settings.security.management_key = ""
    res = client.get("/v1/management/settings")
    assert res.status_code == 503


def test_header_key_still_authenticates():
    res = client.get("/v1/management/settings", headers={"X-Management-Key": KEY})
    assert res.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py -v`
Expected: FAIL (`create_dashboard_session_token` missing; unset key returns `401`/`200` instead of `503`).

- [ ] **Step 3: Implement the primitives**

In `core/security.py`, add imports `hmac`, `hashlib`, `Request`, `Cookie` from FastAPI, then:

```python
DASHBOARD_SESSION_COOKIE = "vc_dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = 8 * 60 * 60


def _dashboard_signing_key(management_key: str) -> bytes:
    return hmac.new(
        JWT_SECRET_KEY.encode("utf-8"), management_key.encode("utf-8"), hashlib.sha256
    ).digest()


def create_dashboard_session_token(management_key: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "dashboard",
        "type": "dashboard",
        "kv": hashlib.sha256(management_key.encode("utf-8")).hexdigest()[:16],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=DASHBOARD_SESSION_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, _dashboard_signing_key(management_key), algorithm=JWT_ALGORITHM)


def verify_dashboard_session_token(token: str, management_key: str) -> bool:
    if not token or not management_key:
        return False
    try:
        payload = jwt.decode(
            token, _dashboard_signing_key(management_key), algorithms=[JWT_ALGORITHM]
        )
    except JWTError:
        return False
    expected = hashlib.sha256(management_key.encode("utf-8")).hexdigest()[:16]
    return payload.get("type") == "dashboard" and payload.get("kv") == expected


def has_valid_dashboard_session(request: Request) -> bool:
    key = settings_manager.get_effective_management_key()
    token = request.cookies.get(DASHBOARD_SESSION_COOKIE, "")
    return verify_dashboard_session_token(token, key)
```

Move `_resolve_client_ip` from `api/auth.py` to `core/security.py` as public `resolve_client_ip(request: Request) -> str` (same body). In `api/auth.py`, delete the local definition, import it from `core.security`, and update every `_resolve_client_ip(...)` call site (grep the file) to `resolve_client_ip(...)`.

Rewrite `verify_management_key`:

```python
async def verify_management_key(
    request: Request,
    x_management_key: Optional[str] = Header(None, alias="X-Management-Key"),
    management_key: Optional[str] = Query(None),
    vc_dashboard_session: Optional[str] = Cookie(None, alias=DASHBOARD_SESSION_COOKIE),
) -> str:
    configured_key = settings_manager.get_effective_management_key()
    if not configured_key:
        raise HTTPException(status_code=503, detail="Management key is not configured")

    if verify_dashboard_session_token(vc_dashboard_session or "", configured_key):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("X-Dashboard-Request") != "1":
            raise HTTPException(status_code=403, detail="Missing X-Dashboard-Request header")
        return "dashboard-session"

    if x_management_key is not None:
        if secrets.compare_digest(x_management_key.strip(), configured_key):
            return "management-key"
        raise HTTPException(status_code=401, detail="Invalid or missing management key",
                            headers={"WWW-Authenticate": "ManagementKey"})

    if management_key is not None:
        if secrets.compare_digest(management_key.strip(), configured_key):
            return "management-key-query"
        raise HTTPException(status_code=401, detail="Invalid or missing management key",
                            headers={"WWW-Authenticate": "ManagementKey"})

    raise HTTPException(status_code=401, detail="Invalid or missing management key",
                        headers={"WWW-Authenticate": "ManagementKey"})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py VoiceClonerAppServer/tests/test_settings_and_3tier_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/security.py api/auth.py tests/test_dashboard_auth.py
git commit -m "feat: add dashboard session token and fail-closed key check

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: Dashboard session API with rate limiting

**Files:**
- Create: `VoiceClonerAppServer/api/dashboard.py`
- Modify: `VoiceClonerAppServer/main.py:19-32,176-182`
- Test: `VoiceClonerAppServer/tests/test_dashboard_auth.py` (append)

**Interfaces:**
- Consumes: `create_dashboard_session_token`, `verify_dashboard_session_token`, `resolve_client_ip`, `DASHBOARD_SESSION_COOKIE`, `DASHBOARD_SESSION_TTL_SECONDS` from Task 2; `redis_client.incrby/expire/delete`.
- Produces:
  - `POST /v1/dashboard/session` body `{"key": str}` → `{"authenticated": true, "expires_in": 28800}` + `Set-Cookie`; `401`, `429`, `503`.
  - `GET /v1/dashboard/session` → `{"authenticated": true}` or `401`.
  - `DELETE /v1/dashboard/session` → `204` and clears the cookie.
  - Login counter key: `dashboard_login:{ip}` with TTL `300`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_auth.py`:

```python
import pytest

@pytest.fixture(autouse=True)
def _clear_cookies():
    client.cookies.clear()
    _reset_login_rate_limit()
    yield
    client.cookies.clear()
    _reset_login_rate_limit()


def test_login_with_wrong_key_returns_401_without_cookie():
    res = client.post("/v1/dashboard/session", json={"key": "wrong"})
    assert res.status_code == 401
    assert "set-cookie" not in {k.lower() for k in res.headers}


def test_login_sets_strict_httponly_cookie_and_status_works():
    res = client.post("/v1/dashboard/session", json={"key": KEY})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/v1/dashboard/session").status_code == 200


def test_logout_clears_session():
    client.post("/v1/dashboard/session", json={"key": KEY})
    assert client.delete("/v1/dashboard/session").status_code == 204
    assert client.get("/v1/dashboard/session").status_code == 401


def test_login_rate_limit_returns_429():
    for _ in range(10):
        assert client.post("/v1/dashboard/session", json={"key": "wrong"}).status_code == 401
    res = client.post("/v1/dashboard/session", json={"key": "wrong"})
    assert res.status_code == 429
    assert res.headers["retry-after"] == "300"


def test_login_fails_closed_when_key_unset(monkeypatch):
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    settings_manager._settings.security.management_key = ""
    res = client.post("/v1/dashboard/session", json={"key": "anything"})
    assert res.status_code == 503
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py -v`
Expected: FAIL with `404` for `/v1/dashboard/session`.

- [ ] **Step 3: Implement the router and include it**

Create `api/dashboard.py` with `router = APIRouter(prefix="/v1/dashboard", tags=["Dashboard"])`:

```python
class DashboardLoginRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=512)

LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300


def _cookie_secure() -> bool:
    return os.getenv("DASHBOARD_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


async def _attempts(ip: str) -> int:
    return int(await redis_client.get(f"dashboard_login:{ip}") or 0)


async def _record_failure(ip: str) -> int:
    key = f"dashboard_login:{ip}"
    count = await redis_client.incrby(key, 1)
    if count == 1:
        await redis_client.expire(key, LOGIN_WINDOW_SECONDS)
    return count
```

`POST /session` takes `request: Request, response: Response`. If effective key is empty → `503`; `ip = resolve_client_ip(request)`; if `await _attempts(ip) >= LOGIN_MAX_ATTEMPTS` → `429` with `headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)}`; compare `secrets.compare_digest(payload.key.strip(), configured)`; on mismatch call `_record_failure` and return `401`; on success `await redis_client.delete(f"dashboard_login:{ip}")`, `response.set_cookie(DASHBOARD_SESSION_COOKIE, token, max_age=DASHBOARD_SESSION_TTL_SECONDS, httponly=True, samesite="strict", secure=_cookie_secure(), path="/")`, set `Cache-Control: no-store`, return the body.

`GET /session`: verify cookie against the effective key; `401` or `{"authenticated": True}` with `no-store`.

`DELETE /session`: build `out = Response(status_code=204)`, call `out.delete_cookie(DASHBOARD_SESSION_COOKIE, path="/")`, and return `out`.

In `main.py`, import `dashboard_router` and include it with the other routers.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/dashboard.py main.py tests/test_dashboard_auth.py
git commit -m "feat: add dashboard session login, status, and logout

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: Redacted settings responses and rotation rules

**Files:**
- Modify: `VoiceClonerAppServer/api/management.py:31-35,78-91`
- Test: `VoiceClonerAppServer/tests/test_dashboard_auth.py` (append)

**Interfaces:**
- Consumes: `verify_management_key` return value from Task 2; `settings_manager.management_key_source` and `get_effective_management_key` from Task 1.
- Produces:
  - `serialize_settings(settings: AppSettings) -> dict` with `security.management_key == ""`, plus top-level `management_key_configured: bool` and `management_key_source: str`.
  - `PUT /v1/management/settings` accepts `principal: str = Depends(verify_management_key)` and applies the rotation rules below.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _login():
    assert client.post("/v1/dashboard/session", json={"key": KEY}).status_code == 200


def test_settings_response_redacts_management_key():
    res = client.get("/v1/management/settings", headers={"X-Management-Key": KEY})
    data = res.json()
    assert data["security"]["management_key"] == ""
    assert data["management_key_configured"] is True
    assert data["management_key_source"] == "environment"
    assert data["client_api_keys"] is not None


def test_cookie_cannot_rotate_management_key():
    _login()
    res = client.put(
        "/v1/management/settings",
        headers={"X-Dashboard-Request": "1"},
        json={"security": {"management_key": "new_key_from_cookie"}},
    )
    assert res.status_code == 403


def test_header_rotation_invalidates_sessions(monkeypatch):
    _login()
    # Simulate a config-managed key without touching the real config file.
    monkeypatch.setattr(settings_manager, "_settings", settings_manager._settings.model_copy(deep=True))
    monkeypatch.setattr(settings_manager, "_env_management_key", "")
    settings_manager._settings.security.management_key = KEY
    monkeypatch.setattr(settings_manager, "_save_to_disk", lambda: None)

    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": "rotated_key_123"}},
    )
    assert res.status_code == 200
    assert res.json()["security"]["management_key"] == ""
    assert client.get("/v1/dashboard/session").status_code == 401
    assert client.get("/v1/management/settings", headers={"X-Management-Key": "rotated_key_123"}).status_code == 200


def test_env_managed_key_rotation_is_rejected():
    _login()
    res = client.put(
        "/v1/management/settings",
        headers={"X-Management-Key": KEY},
        json={"security": {"management_key": "ignored_key_123"}},
    )
    assert res.status_code == 409


def test_cookie_authenticates_management_get():
    _login()
    assert client.get("/v1/management/settings").status_code == 200


def test_cookie_mutation_requires_csrf_header():
    _login()
    res = client.put("/v1/management/settings", json={"server": {"port": 8000}})
    assert res.status_code == 403
```

The rotation test stubs `_save_to_disk` so the real `config/settings.json` is not modified; monkeypatch restores the in-memory settings afterwards.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py -k "settings_response or rotate or rotation or env_managed" -v`
Expected: FAIL (key returned in full; cookie rotation currently allowed; no flags).

- [ ] **Step 3: Implement redaction and rotation logic**

In `api/management.py` add:

```python
def serialize_settings(settings: AppSettings) -> Dict[str, Any]:
    data = settings.model_dump()
    data["security"]["management_key"] = ""
    data["management_key_configured"] = bool(settings.security.management_key)
    data["management_key_source"] = settings_manager.management_key_source
    return data
```

`GET /settings` returns `serialize_settings(settings_manager.get_settings())`.

`PUT /settings` becomes `async def update_settings(payload: UpdateSettingsRequest, principal: str = Depends(verify_management_key))` and before calling `update_settings`:

```python
updates = payload.model_dump(exclude_unset=True)
security_update = updates.get("security") or {}
requested_key = security_update.get("management_key")
current_key = settings_manager.get_effective_management_key()
if requested_key is not None and requested_key != current_key:
    if not requested_key.strip():
        security_update.pop("management_key", None)
    elif settings_manager.management_key_source == "environment":
        raise HTTPException(status_code=409, detail="Management key is managed by the environment")
    elif principal == "dashboard-session":
        raise HTTPException(status_code=403, detail="Rotate the management key with X-Management-Key or the environment")
return serialize_settings(settings_manager.update_settings(updates))
```

The router-level `Depends(verify_management_key)` stays; FastAPI caches the dependency result within the request.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/management.py tests/test_dashboard_auth.py
git commit -m "feat: redact management key and restrict rotation

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: Gate dashboard pages and add the login screen

**Files:**
- Create: `VoiceClonerAppServer/static/dashboard/login.html`
- Modify: `VoiceClonerAppServer/main.py:167-195`
- Modify: `VoiceClonerAppServer/tests/test_dashboard_ui.py:6-11`
- Modify: `VoiceClonerAppServer/tests/test_settings_and_3tier_auth.py:310-318`
- Test: `VoiceClonerAppServer/tests/test_dashboard_auth.py` (append)

**Interfaces:**
- Consumes: `has_valid_dashboard_session` from Task 2; session endpoints from Task 3.
- Produces:
  - `GET /dashboard` → `303 Location: /dashboard/login` without a session, console HTML with a session; `Cache-Control: no-store`.
  - `GET /dashboard/login` → login HTML without a session, `303 Location: /dashboard` with one.
  - `static/dashboard/login.html` posting to `/v1/dashboard/session`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_auth.py`:

```python
def test_dashboard_redirects_to_login_without_session():
    res = client.get("/dashboard", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/dashboard/login"


def test_login_page_is_public_and_has_no_key():
    res = client.get("/dashboard/login")
    assert res.status_code == 200
    assert 'id="loginKeyInput"' in res.text
    assert "vc_admin_secret_key" not in res.text
    assert res.headers["cache-control"] == "no-store"


def test_dashboard_serves_console_with_session():
    _login()
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "VOICE CLONER" in res.text
    assert res.headers["cache-control"] == "no-store"


def test_invalid_session_cookie_redirects_to_login():
    client.cookies.set(security.DASHBOARD_SESSION_COOKIE, "garbage")
    res = client.get("/dashboard", follow_redirects=False)
    assert res.status_code == 303
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py -k dashboard -v`
Expected: FAIL (unauthenticated `/dashboard` returns `200`; `/dashboard/login` is `404`).

- [ ] **Step 3: Implement the routes and login page**

In `main.py` replace the `/dashboard` handler with two routes using `has_valid_dashboard_session` and `RedirectResponse(status_code=303)`; serve `login.html` from `DASHBOARD_DIR`; include `headers={"Cache-Control": "no-store"}` on both HTML responses. Keep `/` redirecting to `/dashboard`.

In the `lifespan` function, after `settings = settings_manager.get_settings()`, add one startup warning when the effective key is empty, without printing the key:

```python
if not settings.security.management_key:
    logger.warning(
        "Management key is not configured; the dashboard is locked. "
        "Set MANAGEMENT_KEY or security.management_key."
    )
```

Create `static/dashboard/login.html` with a dark card consistent with the console: a form (`id="loginForm"`), password input (`id="loginKeyInput"`), error paragraph (`id="loginError"`), and inline script:

```javascript
const res = await fetch('/v1/dashboard/session', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  credentials: 'same-origin',
  body: JSON.stringify({ key: document.getElementById('loginKeyInput').value }),
});
if (res.ok) window.location.assign('/dashboard');
else if (res.status === 429) error.textContent = 'Quá nhiều lần thử. Vui lòng đợi 5 phút.';
else if (res.status === 503) error.textContent = 'Máy chủ chưa cấu hình khóa quản trị.';
else error.textContent = 'Khóa quản trị không đúng.';
```

No key is written to `localStorage`, `sessionStorage`, or the URL.

Update `tests/test_dashboard_ui.py::test_dashboard_endpoint_serves_html` to log in first with a fresh `TestClient(app)`, then assert the existing console markers. Update `test_settings_and_3tier_auth.py::test_exempt_endpoints_accessible_without_client_key` step 2 to use `client.get("/dashboard", follow_redirects=False)` and assert `303` and `Location: /dashboard/login` (keep `/health` as `200`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_auth.py VoiceClonerAppServer/tests/test_dashboard_ui.py VoiceClonerAppServer/tests/test_settings_and_3tier_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py static/dashboard/login.html tests/test_dashboard_auth.py tests/test_dashboard_ui.py tests/test_settings_and_3tier_auth.py
git commit -m "feat: gate dashboard behind session login

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: Migrate the console to cookie auth

**Files:**
- Modify: `VoiceClonerAppServer/static/dashboard/app.js:38-82,2559-2600,2668-2702,3185-3208,3210-3218`
- Modify: `VoiceClonerAppServer/static/dashboard/index.html:50-58,887-914,1266-1285`
- Test: `VoiceClonerAppServer/tests/test_dashboard_ui.py` (append)

**Interfaces:**
- Consumes: `/v1/dashboard/session` endpoints from Task 3; redacted settings from Task 4.
- Produces: dashboard JS that sends no management key; `logoutDashboard() -> Promise<void>`; static test asserting no hardcoded key or `localStorage` key usage.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_ui.py`:

```python
def test_dashboard_js_uses_cookie_session_only():
    from pathlib import Path
    js_path = Path("VoiceClonerAppServer/static/dashboard/app.js")
    if not js_path.exists():
        js_path = Path("static/dashboard/app.js")
    js = js_path.read_text(encoding="utf-8")

    assert "vc_admin_secret_key" not in js
    assert "X-Management-Key" not in js
    assert "credentials: 'same-origin'" in js
    assert "X-Dashboard-Request" in js
    assert "logoutDashboard" in js
    assert "/dashboard/login" in js
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py::test_dashboard_js_uses_cookie_session_only -v`
Expected: FAIL (`vc_admin_secret_key` present).

- [ ] **Step 3: Update `app.js`**

- Delete `getManagementKey` and `setManagementKey`.
- In `apiRequest`, drop the key header, add `'X-Dashboard-Request': '1'`, and add `credentials: 'same-origin'` to the fetch options. On `401`, call `window.location.assign('/dashboard/login')` and throw; remove the modal call.
- In `fetchSettings`, remove the `mgmtKeySettingsInput` population; instead set `#mgmtKeyStatusText` to `Đã cấu hình` / `Chưa cấu hình` using `data.management_key_configured`, and `#mgmtKeySourceText` from `data.management_key_source`.
- Rename `saveMgmtKeySettings` to `saveSecuritySettings`; send only `{security: {require_client_api_key: requireKey}}`; on `409`, toast the environment-managed message.
- Delete `openAuthModal`, `closeAuthModal`, `saveAuthModalKey`.
- Add:

```javascript
async function logoutDashboard() {
  try {
    await fetch('/v1/dashboard/session', { method: 'DELETE', credentials: 'same-origin' });
  } finally {
    window.location.assign('/dashboard/login');
  }
}
```

- [ ] **Step 4: Update `index.html`**

- Header button: `onclick="logoutDashboard()"`, label `Đăng xuất`, shield icon retained.
- Delete the `authModal` block.
- In the Settings security card: delete the key input and eye button; add

```html
<div>
  <label class="text-slate-400 block mb-1 font-medium">Khóa Quản Trị (X-Management-Key):</label>
  <div class="flex items-center space-x-2 text-xs">
    <span id="mgmtKeyStatusText" class="font-mono text-slate-200">Chưa cấu hình</span>
    <span id="mgmtKeySourceText" class="text-slate-500"></span>
  </div>
  <span class="text-[11px] text-slate-500 mt-1 block">Đổi khóa qua biến môi trường MANAGEMENT_KEY hoặc config/settings.json.</span>
</div>
```

- Change the save button to `onclick="saveSecuritySettings()"`.

- [ ] **Step 5: Run the tests and the JS syntax check**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -v`
Expected: PASS (syntax test passes or skips when Node is absent).

- [ ] **Step 6: Commit**

```bash
git add static/dashboard/app.js static/dashboard/index.html tests/test_dashboard_ui.py
git commit -m "feat: move dashboard console to cookie session auth

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: Compose, docs, and full verification

**Files:**
- Modify: `VoiceClonerAppServer/docker-compose.yml`
- Modify: `VoiceClonerAppServer/README.md`
- Modify: `VoiceClonerAppServer/CONTEXT.md`
- Modify: `VoiceClonerAppServer/CLAUDE.md`
- Modify: `../docker-compose.yml`
- Test: `VoiceClonerAppServer/tests/test_dashboard_ui.py` (append static secret scan)

**Interfaces:**
- Consumes: everything above.
- Produces: documented `MANAGEMENT_KEY` and dashboard login behavior; no shipped static file contains the historical key.

- [ ] **Step 1: Add the static secret scan test**

Append to `tests/test_dashboard_ui.py`:

```python
def test_no_hardcoded_management_key_in_static_files():
    from pathlib import Path
    root = Path("VoiceClonerAppServer/static")
    if not root.exists():
        root = Path("static")
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "vc_admin_secret_key_123" not in text, str(path)
```

Run it; expected PASS after Task 6.

- [ ] **Step 2: Add the environment variable to both compose files**

Under the `api`/`appserver` service `environment`, add `MANAGEMENT_KEY: ${MANAGEMENT_KEY:-}`. Keep the existing `CONFIG_PATH`.

- [ ] **Step 3: Update documentation**

- `README.md`: dashboard section says login is required; document `MANAGEMENT_KEY`, the generation command `python -c "import secrets; print(secrets.token_urlsafe(32))"`, `DASHBOARD_COOKIE_SECURE=true` behind HTTPS, session lifetime, and that the console cannot rotate the key. Add a migration note: deployments still using the historical default are locked out until a key is set, and operators may clear the old `vc_management_key` localStorage entry.
- `CONTEXT.md` dashboard bullet: session-cookie auth, login page, key never returned by settings API, fail-closed empty key.
- `CLAUDE.md` invariants: add "`/dashboard` requires a session cookie minted from the management key; the key is never sent to clients" and "empty management key fails closed (`503`)".
- Update the auth table row for `/dashboard` (it no longer serves the shell publicly).

- [ ] **Step 4: Run the full suite**

Run: `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/ -v`
Expected: PASS (no failures; skips allowed for Node-dependent tests).

- [ ] **Step 5: Manual smoke check**

```bash
docker compose up -d --build api
curl -i http://localhost:8000/dashboard          # 303 -> /dashboard/login
curl -s http://localhost:8000/v1/management/settings  # 401
```
Then log in through the browser with `MANAGEMENT_KEY`, confirm the console loads, and confirm `document.cookie` does not contain the key.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml ../docker-compose.yml README.md CONTEXT.md CLAUDE.md tests/test_dashboard_ui.py
git commit -m "docs: document dashboard session auth and management key setup

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```
