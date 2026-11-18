# Design Document: Dashboard Session Authentication

**Date:** 2026-11-18  
**Status:** Awaiting Approval  
**Component:** `VoiceClonerAppServer` (Gateway & Management Console)

---

## 1. Intent & The Why

The management dashboard is currently reachable by anyone. `GET /dashboard` and
`/static/dashboard/*` are unauthenticated, and `static/dashboard/app.js` falls back to a
hardcoded management key that matches the committed default in `config/settings.json`
and `core/settings_manager.py`. A visitor in a private window therefore gets the full console
without entering a key. The same secret is public in the repository and in each browser's
`localStorage`. `verify_management_key` also fails open when the configured key is empty.

The dashboard must require the management key to enter, the key must never be shipped to or
stored in the browser, and an unconfigured gateway must deny management access instead of
allowing it.

## 2. Goals

1. Unauthenticated visitors to `/dashboard` see only a login screen; no console HTML, JS data,
   or management API response is available to them.
2. Successful login exchanges the management key for an `HttpOnly`, `SameSite=Strict` session
   cookie. The key itself is never returned to or persisted by the browser.
3. `/v1/management/*` accepts either a valid session cookie (dashboard) or the existing
   `X-Management-Key` header / `api_key` query (automation, scripts, tests).
4. Empty or default management keys fail closed: management endpoints return `503`, the login
   screen reports the server is not configured, and startup logs a warning.
5. Rotating the management key invalidates all existing dashboard sessions.
6. Login attempts are rate limited per client IP.
7. Dashboard pages use `Cache-Control: no-store`.
8. Management APIs never return the management key; the console cannot read it back or rotate it
   from the UI.

## 3. Non-Goals

- Multiple admin accounts, RBAC, or per-admin audit identities.
- 2FA, SSO, or OAuth for the dashboard.
- HTTPS termination or TLS certificate management (deploy-time concern).
- Locking down `/v1/system/status`; the Android client depends on it.
- Changing the client API key model or user JWT flows.

## 4. Anti-Goals

- Hiding the console client-side while leaving server responses readable.
- Keeping the management key in `localStorage`, a JS constant, or a non-HttpOnly cookie.
- Treating "no key configured" as "no authentication required".

## 5. Current Behavior (verified)

| Location | Current behavior |
|---|---|
| `main.py:185` | `/dashboard` returns `index.html` unconditionally |
| `main.py:173` | `/static` mounted publicly, including `dashboard/app.js` |
| `app.js:38-44` | `getManagementKey()` returns `localStorage` value or `vc_admin_secret_key_123` |
| `app.js:61-74` | Every management call sends `X-Management-Key` |
| `core/security.py:168` | `verify_management_key` returns `""` when no key configured (fail-open) |
| `core/settings_manager.py:23` | Model default is `vc_admin_secret_key_123` |
| `config/settings.json:7` | Committed plaintext key equals that default |
| `api/management.py:31` | Router-level dependency on `verify_management_key` |
| `api/system.py:34` | `/v1/system/status` public by design |

## 6. Approaches Considered

### A. Server-side session with HttpOnly cookie (chosen)
A login screen posts the key once; the server verifies it and sets a signed, `HttpOnly` cookie.
Dashboard pages and cookie-based management calls require that session. The key never reaches
the browser after login. Fits the existing FastAPI app, needs no new dependency, supports
key rotation, and lets automation keep using the header.

### B. Client-side prompt only
Remove the hardcoded key and always show the auth modal. Simpler, but the console shell and
all JS remain public, the key persists in `localStorage`, and it is sent on every request.
Does not satisfy "must have the key to enter".

### C. Network allowlist
Restrict `/dashboard` to localhost or allowlisted IPs. Useful as defence in depth but not a
replacement for authentication behind a reverse proxy, and it breaks LAN/phone access.
Not selected for this change; can be added at the proxy layer later without code changes.

**Decision:** A, preserving the existing header/query key path for automation and tests.

## 7. Detailed Design

### 7.1 Session token

- Signed JWT, HS256, using a key derived as
  `HMAC-SHA256(JWT_SECRET_KEY, management_key)` so a session cannot be forged from the
  (possibly default) JWT secret alone.
- Claims: `{sub: "dashboard", type: "dashboard", kv: sha256(management_key)[:16], iat, exp}`.
- Lifetime: 8 hours, no refresh or sliding renewal. Re-login is required after expiry.
- `verify_management_key` compares `kv` with the hash of the current configured key; changing
  the key invalidates every outstanding session. A mismatch is treated as unauthenticated.

### 7.2 Cookie

- Name: `vc_dashboard_session`; `Path=/`; `HttpOnly`; `SameSite=Strict`.
- `Max-Age=28800`.
- `Secure` only when `DASHBOARD_COOKIE_SECURE=true` (default `false`), because the documented
  LAN/HTTP workflow (`http://<lan-ip>:8000`) would otherwise never receive the cookie. README
  notes to enable it behind HTTPS.
- Session responses and dashboard HTML send `Cache-Control: no-store`.

### 7.3 Endpoints

New router `api/dashboard.py`, prefix `/v1/dashboard`, tag `Dashboard`:

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `POST` | `/v1/dashboard/session` | management key in JSON body `{"key": "..."}` | `secrets.compare_digest` against configured key; 401 on mismatch; 503 if no key configured; 429 when rate limited; sets cookie on success; body `{"authenticated": true, "expires_in": 28800}` |
| `GET` | `/v1/dashboard/session` | cookie | `{"authenticated": true}` or 401 |
| `DELETE` | `/v1/dashboard/session` | none | Clears cookie, returns `204` |

All three send `Cache-Control: no-store`. The key is never echoed in responses or logs.

### 7.4 Rate limiting login

- 10 failed attempts per IP per 5 minutes; successful login clears the counter.
- Use the existing Redis abstraction (`redis_client.incrby` / `expire`) with its in-memory
  fallback; key `dashboard_login:{ip}`.
- Client IP helper mirrors `api/auth.py:66` (`request.client.host`), respecting
  `security.trust_forwarded_for` only if an existing trusted-proxy helper is used.
- On limit: `429` with `Retry-After: 300`; generic message, no hint about the key.

### 7.5 Management authentication

`verify_management_key` gains an optional `request: Request` (or `Cookie` header) and evaluates:

1. If configured key is empty → `HTTPException(503, "Management key is not configured")`.
2. If a valid session cookie exists → return a sentinel principal (e.g. `"dashboard-session"`).
   For state-changing methods (`POST`, `PUT`, `PATCH`, `DELETE`) also require header
   `X-Dashboard-Request: 1`; otherwise `403`. This is the CSRF guard on top of `SameSite=Strict`.
3. Else use `X-Management-Key` or `api_key` query with `secrets.compare_digest` as today.
4. Else `401`.

Because the dependency is on the management router, all existing routes inherit the behavior.

### 7.6 Page serving

- `GET /dashboard`:
  - valid session → `index.html`, `Cache-Control: no-store`;
  - otherwise → `303` redirect to `/dashboard/login`.
- `GET /dashboard/login`:
  - valid session → `303` redirect to `/dashboard`;
  - otherwise → `login.html`.
- `/` keeps redirecting to `/dashboard`.
- `/static/dashboard/*` stays publicly readable. After the hardcoded key is removed these files
  contain no secrets; gating them would require duplicating login assets. A test asserts no
  management key literal exists in any shipped static file.

### 7.7 Login screen

New `static/dashboard/login.html` plus inline (or `login.js`) lightweight logic:

- Single password input and submit button, styled consistently with the console (dark card).
- `fetch('/v1/dashboard/session', {method:'POST', headers:{'Content-Type':'application/json'},
  credentials:'same-origin', body: JSON.stringify({key})})`.
- On success: `window.location.assign('/dashboard')`.
- On 401: inline error "Khóa quản trị không đúng."
- On 429: inline message with wait time from `Retry-After`.
- On 503: "Máy chủ chưa cấu hình khóa quản trị."
- No key value is written to `localStorage`, `sessionStorage`, or the URL.

### 7.8 Console changes (`app.js`, `index.html`)

- Delete `getManagementKey`, `setManagementKey`, the `X-Management-Key` header, and the
  `localStorage` fallback.
- `apiRequest` uses `credentials: 'same-origin'` and adds `X-Dashboard-Request: 1` to every
  request; on `401` it redirects to `/dashboard/login` instead of opening the old modal.
- Replace the key/auth modal with a "Đăng xuất" action that calls
  `DELETE /v1/dashboard/session` then navigates to `/dashboard/login`.
- Remove the now-unused `authModal` markup.
- In the Settings tab, replace the management-key input and `saveMgmtKeySettings` with a
  read-only status row: "Đã cấu hình" / "Chưa cấu hình", the source (`environment`, `config`,
  `unset`), and helper text telling the operator to rotate via `MANAGEMENT_KEY` or
  `config/settings.json`. Key rotation is not available from the console.

### 7.9 Key configuration and fail-closed startup

- Remove the `vc_admin_secret_key_123` default from `SecurityConfig` (default becomes `""`).
- Remove the key from `config/settings.json` (set `""`).
- Add `MANAGEMENT_KEY` environment override. `SettingsManager` stores it separately
  (`_env_management_key`) and `get_settings()` returns a copy whose
  `security.management_key` is the env value when set; `_settings` itself keeps the value read
  from the file. `_save_to_disk` therefore never writes the env secret to
  `config/settings.json`, including on client-key creation, limit updates, or hot reload.
- Add a read-only `management_key_source` (`"environment" | "config" | "unset"`) to the
  settings response so the console can show where the key comes from. Do not expose the value.
- While `MANAGEMENT_KEY` is set, `PUT /v1/management/settings` rejects any attempt to change
  `security.management_key` with `409` ("management key is managed by the environment"), for
  both cookie and header authentication.
- Startup logs one warning when the key is empty or still the historical default:
  `WARNING: management key is not configured; the dashboard is locked. Set MANAGEMENT_KEY or
  security.management_key.` It must not print the key.
- `docker-compose.yml` (root and gateway-only): pass `MANAGEMENT_KEY: ${MANAGEMENT_KEY:-}` to
  the api service. README quickstart adds a generation command:
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 7.10 Session invalidation

- Key rotation: `kv` claim mismatch → 401 → redirect to login.
- Logout: cookie cleared; stateless token remains technically valid until expiry if replayed,
  but it is never exposed to JS. This is accepted; Redis blacklisting is not in scope.
- Server restart with the same key: sessions remain valid (derived signing key is stable).
- Changing `JWT_SECRET_KEY`: sessions invalidate, which is acceptable.

### 7.11 Secret redaction and rotation

`GET /v1/management/settings` and the `PUT` response currently serialize the whole settings
model, including `security.management_key`. Under cookie authentication that would hand the
admin credential back to the browser and defeat goal 2. Therefore:

- Both responses replace `security.management_key` with `""` and add
  `management_key_configured: bool` and `management_key_source: "environment" | "config" |
  "unset"`. The value is never serialized.
- `client_api_keys` continue to be returned in full: the API Keys tab must display and manage
  them, and they are client credentials, not the admin credential. This matches current
  behavior.
- `PUT /v1/management/settings` handling of `security.management_key`:
  - omitted, empty, or the masked/redacted placeholder → treated as unchanged;
  - non-empty and different, authenticated by `X-Management-Key`/`api_key`, no env override →
    rotate the key. The request succeeds; the caller's old key stops working, all sessions are
    invalidated by the `kv` mismatch, and no cookie is issued;
  - non-empty and different, authenticated by session cookie → `403`
    ("rotate the key with the management header or the environment");
  - non-empty and different while `MANAGEMENT_KEY` is set → `409`, regardless of auth method.
- If `PUT` changes the key, the response must not echo it (same redaction as above).
- Rotation through config edit plus hot reload is supported: sessions invalidate on the next
  request because `kv` no longer matches.

## 8. Files Touched

| File | Change |
|---|---|
| `api/dashboard.py` | New session router (login/logout/status, rate limit) |
| `api/management.py` | Redact management key in settings responses; rotation rules (§7.11) |
| `core/security.py` | Fail-closed key check; cookie session verification; CSRF header check; session token create/verify |
| `core/settings_manager.py` | Empty default; non-persisted `MANAGEMENT_KEY` override; key source |
| `main.py` | Include router; gate `/dashboard`; add `/dashboard/login`; `no-store` headers |
| `static/dashboard/login.html` | New login screen |
| `static/dashboard/app.js` | Cookie auth, logout, remove hardcoded key |
| `static/dashboard/index.html` | Remove key modal/settings key display; logout button |
| `config/settings.json` | Blank management key |
| `docker-compose.yml` (both) | `MANAGEMENT_KEY` passthrough |
| `README.md`, `CONTEXT.md`, `CLAUDE.md` | Auth model, env var, quickstart, invariants |
| `tests/test_dashboard_auth.py` | New coverage |
| `tests/test_dashboard_ui.py`, `tests/test_settings_and_3tier_auth.py` | Adapt to gated pages and fail-closed behavior |

## 9. Test Plan

1. `GET /dashboard` unauthenticated → `303` to `/dashboard/login`; body does not contain console markup.
2. `GET /dashboard/login` → 200; contains key input; contains no secrets; `no-store`.
3. `POST /v1/dashboard/session` wrong key → 401, no `Set-Cookie`.
4. Correct key → 200, `Set-Cookie` includes `HttpOnly` and `SameSite=Strict`.
5. `GET /dashboard` with session cookie → 200 console; without → redirect.
6. `GET /v1/management/settings` with cookie → 200; with `X-Management-Key` → 200.
7. Cookie-authenticated `POST /v1/management/settings` without `X-Dashboard-Request` → 403;
   with it → 200.
8. Key rotation → old cookie rejected (`kv` mismatch).
9. Empty key → management endpoints 503, login 503, startup warning captured.
10. Eleven rapid failed logins → 429 with `Retry-After`.
11. Static scan: no shipped `static/**` file contains the historical default key.
12. `DELETE /v1/dashboard/session` clears the cookie and subsequent console access redirects.
13. `GET /v1/management/settings` via cookie returns no `management_key` value, reports
    `management_key_configured` and `management_key_source`, and still returns client keys.
14. `PUT /v1/management/settings` with a new key via cookie → 403; via `X-Management-Key` →
    success, old key 401, existing sessions rejected; with `MANAGEMENT_KEY` set → 409.
15. Setting `MANAGEMENT_KEY`, then saving unrelated settings (create a client key or change a
    limit), leaves the on-disk `config/settings.json` key unchanged (no env secret written).

Existing tests that read `settings.security.management_key` continue to work after
`conftest.py` sets `MANAGEMENT_KEY` before app import. Tests asserting `/dashboard` returns 200
without auth are updated to authenticate via the login endpoint first.

## 10. Deployment & Rollout

- Operator generates a key, sets `MANAGEMENT_KEY` (preferred) or `security.management_key`.
- Existing deployments with the committed default will keep working only after setting a key;
  until then the dashboard is locked and management APIs return 503. This is intentional.
- Existing browser sessions and stored `localStorage` keys become irrelevant; the new console
  never reads them. README notes operators may clear the old `vc_management_key` entry.
- Behind HTTPS, set `DASHBOARD_COOKIE_SECURE=true`.

## 11. Left to the Builder

- Exact refactor shape of `verify_management_key` (single dependency vs. helper + thin wrappers)
  as long as the four-step order in §7.5 holds and existing route signatures are unchanged.
- Whether the login page ships inline JS or a separate `login.js`.
- Precise visual treatment of the login card, consistent with the existing console styling.
- Whether the in-memory rate-limit fallback lives in `api/dashboard.py` or `core/redis_client.py`.
- Exact response schema mechanism for the redacted settings object (a dedicated response model
  vs. a copy with the field cleared), as long as the key value is never serialized.
