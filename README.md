# VoiceClonerAppServer

API gateway, reverse proxy, quota controller, and management console for the Voice Cloner platform. It fronts any number of stateless TTS workers that implement the Voice API contract.

- Port: `8000`
- Stack: FastAPI, SQLAlchemy async (PostgreSQL with SQLite fallback), Redis with in-memory fallback
- Dashboard: `http://localhost:8000/dashboard` (requires a management-key login)
- Swagger: `http://localhost:8000/docs`

## Architecture

```
Client (Android / Web)
        │  X-API-Key + Bearer JWT
        ▼
VoiceClonerAppServer ── dynamic voice discovery ──► workers
        │  utilization + capability routing
        ├── standard cache / unbilled cache
        ├── singleflight request coalescing
        └── PostgreSQL usage logs + Redis quotas
```

| Module | Responsibility |
| :--- | :--- |
| `api/auth.py` | Registration, login, Google sign-in, refresh, logout, profile |
| `api/voices.py` | Dynamic catalog, preview, cloning, update, delete |
| `api/speech.py` | Speech pipeline: quota, rate limit, cache, singleflight, routing |
| `api/management.py` | Keys, workers, settings, users, metrics, cache purge |
| `api/system.py` | Health and system status |
| `services/model_dispatcher.py` | Health probes, circuit breaker, capability and utilization routing |
| `services/voice_service.py` | Dynamic catalog aggregation with cold-boot fallback |
| `services/cache_service.py` | SHA-256 standard and unbilled audio caches |
| `services/inflight_manager.py` | Singleflight coalescing |
| `services/concurrency_limiter.py` | Per-user stream and queue limits |
| `services/clone_reference.py` | Resolves cloned voices to base64 `.npz` |
| `services/usage_tracker.py` | PostgreSQL usage analytics |

## Authentication

| Tier | Header | Used by |
| :--- | :--- | :--- |
| Management key | `X-Management-Key` | `/v1/management/*` |
| Dashboard session | `vc_dashboard_session` cookie | `/dashboard` and `/v1/management/*` calls made by the console |
| Client API key | `X-API-Key: vc_live_...` | All `/v1/*` client routes |
| User token | `Authorization: Bearer <JWT>` | Speech, preview, clone, profile |
| Worker key | `X-API-Key: vtts_worker_...` | Gateway → worker calls |

When `security.require_client_api_key` is true, every `/v1/*` request except management requires a valid active client key, even if a user token is present.

### Dashboard authentication

The console is not public: `/dashboard` redirects to `/dashboard/login` unless the request carries a valid session cookie. The login form exchanges the management key at `POST /v1/dashboard/session` for an `HttpOnly`, `SameSite=Strict` session cookie (`vc_dashboard_session`, 8-hour lifetime, `Path=/`). The key is never returned to the browser and is not written to `localStorage`; the console cannot read or rotate it.

- Set the key through the `MANAGEMENT_KEY` environment variable (recommended, takes precedence over config and is never persisted to `config/settings.json`), or leave `security.management_key` in `config/settings.json` as a file-based fallback. Generate a key with:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- Set `DASHBOARD_COOKIE_SECURE=true` when the gateway is served over HTTPS so the cookie carries the `Secure` flag.
- If the effective key is empty the dashboard fails closed: login and session checks return `503`, and `/dashboard` keeps redirecting to the login page.
- Login attempts are rate limited per client IP (10 attempts per 5 minutes, then `429`).
- The console cannot change the key. Rotating it is an operator action: update `MANAGEMENT_KEY` and restart the gateway (existing sessions become invalid because the cookie is signed with the key).

Migration note: deployments that still rely on the historical default key have no effective key configured and are locked out of the dashboard until an operator sets `MANAGEMENT_KEY` (or `security.management_key`). Browsers may still hold the obsolete `vc_management_key` `localStorage` entry; clearing it is safe — the key is never handled by the console anymore.

## Configuration

Dynamic configuration lives in `config/settings.json` and is hot-reloaded:

| Section | Fields |
| :--- | :--- |
| `server` | `host`, `port` |
| `security` | `management_key` (file fallback for `MANAGEMENT_KEY`), `require_client_api_key` |
| `client_api_keys[]` | `id`, `name`, `key`, `is_active`, `daily_char_limit` |
| `model_workers[]` | `id`, `url`, `api_key`, `max_concurrent` (1–64), `is_active` |
| `default_user_limits` | `max_cloned_slots`, `daily_tts_char_limit`, `rate_limit_requests_per_minute`, `max_speech_char_limit`, `max_preview_char_limit`, `max_concurrent_tts`, `max_queue_waiters` |
| `default_anonymous_limits` | Same fields for anonymous install accounts; `max_cloned_slots` defaults to `0` |
| `ad_rewards` | `enabled`, `chars_per_ad`, `max_ads_per_user_per_day`, `rewarded_ad_unit_id`, `allowed_ad_unit_ids`, `ssv_verifier_url` |
| `concurrency` | Fallback defaults for the same user-limit fields |
| `audio_cache` | `max_size_mb`, `ttl_days`, `clean_interval_sec` |

Environment variables cover infrastructure and deployment only:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://voice_user:voice_pass@db:5432/voicecloner` | Primary database |
| `REDIS_URL` | `redis://redis:6379/0` | Quota, rate limit, token blacklist |
| `CONFIG_PATH` | `/app/VoiceClonerAppServer/config/settings.json` | Settings file |
| `MANAGEMENT_KEY` | empty | Dashboard/management key; overrides `security.management_key` and is never written to disk |
| `DASHBOARD_COOKIE_SECURE` | `false` | Set to `true` behind HTTPS so the session cookie is `Secure` |
| `JWT_SECRET_KEY` | dev value | Token signing key — replace in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `GOOGLE_CLIENT_ID` | empty | Google sign-in audience |
| `BUSINESS_TIMEZONE` | `Asia/Ho_Chi_Minh` | Daily quota reset timezone |
| `WORKER_HEALTH_INTERVAL_SEC` | `15` | Background health probe interval |
| `WORKER_HEALTH_TIMEOUT_SEC` | `3` | Health probe timeout |

## Endpoints

### Authentication — `/v1/auth`

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `POST` | `/register` | Create account and return tokens plus limits |
| `POST` | `/login` | Email/password login |
| `POST` | `/google` | Google ID-token login |
| `POST` | `/refresh` | Rotate access token |
| `POST` | `/logout` | Revoke tokens |
| `GET` | `/me` | Profile, daily usage, cloned slots, all 7 limits |

### Tokens — `/v1/tokens`

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/balance` | Bonus characters, daily characters used, limit, and remaining (JWT) |

### Voices — `/v1/voices`

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `` | Aggregated preset catalog plus the user's cloned voices |
| `GET` | `/{voice_id}` | Voice profile |
| `GET` | `/{voice_id}/audio` | Clone reference WAV (IDOR-protected) |
| `POST` | `/clone` | Upload a 3–15 s sample; embedding extraction is mandatory (502 on failure) |
| `PUT` | `/{voice_id}` | Update a cloned voice |
| `DELETE` | `/{voice_id}` | Delete a cloned voice and free its slot |

### Synthesis

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `POST` | `/v1/audio/speech` | Text → 48 kHz WAV through the full quota/cache/routing pipeline |
| `POST` | `/v1/voices/preview` | Short preview with the same quota and cache rules |

### Rewarded Ads

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/ads/ssv` | AdMob server-side verification callback (outside `/v1`; no client key or JWT, ECDSA signature-checked) |
| `GET` | `/v1/ads/config` | Rewarded-ad configuration and remaining ad allowance for the caller |

### System

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/health` | Container liveness |
| `GET` | `/v1/system/status` | Hardware, backend, worker capacity summary |
| `GET` | `/dashboard` | Dark management console shell (7 tabs); redirects to `/dashboard/login` without a session |
| `GET` | `/dashboard/login` | Login page for the console |
| `POST` | `/v1/dashboard/session` | Exchange the management key for an HttpOnly session cookie |
| `GET`, `DELETE` | `/v1/dashboard/session` | Report or clear the current dashboard session |

### Management — `/v1/management` (management key or dashboard session)

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET`, `PUT` | `/settings` | Read/update settings with hot reload |
| `GET`, `POST` | `/keys` | List/create client keys |
| `DELETE` | `/keys/{key_id}` | Delete a key |
| `PATCH` | `/keys/{key_id}/toggle` | Enable/disable a key |
| `GET`, `POST` | `/workers` | List/create workers |
| `DELETE` | `/workers/{worker_id}` | Delete a worker |
| `PATCH` | `/workers/{worker_id}/toggle` | Enable/disable a worker |
| `POST` | `/workers/test` | Probe an arbitrary worker URL |
| `POST` | `/cache/purge` | Delete cached WAV files |
| `GET` | `/metrics/summary` | Traffic, cache rate, latency, events |
| `GET` | `/metrics/analysis` | Analysis-tab datasets |
| `GET` | `/users` | List users with usage and slots |
| `PATCH`, `DELETE` | `/users/{user_id}` | Edit limits or delete a user |

## Speech Pipeline

1. Resolve voice and enforce clone ownership before cache or quota lookups.
2. Standard cache hit → `< 10 ms`, quota-exempt, `X-Cache-Status: HIT`.
3. Unbilled cache hit → claim, deduct quota, promote, `HIT-CLAIMED`.
4. Otherwise join or create a singleflight task; waiters do not consume concurrency slots.
5. Cache miss → quota deduction, rate limit, per-user limiter, capability-aware worker selection.
6. Client disconnect → shielded worker completion, quota rollback, unbilled file; abandoned waiters return 499.

Cache key: `sha256(voice_id + ":" + normalized_text + ":" + smart_pause + ":48000hz")`.

Timeouts: worker read 300 s (connect 10 s), queue wait 120 s, Android read 120 s.

## Worker Routing

- Health probes run every 15 s and mark workers unhealthy without touching breaker counters.
- Open circuits are skipped until the 30 s cooldown, then half-open.
- Candidates must support the requested voice (or cloning) and have free `max_concurrent` capacity.
- Lowest `active_jobs / max_concurrent` wins; ties break round-robin.
- One retry to another capable worker on 5xx/network errors; 4xx fails fast.
- Cloned voices are sent as `ref_npz_base64`; the gateway never forwards `ref_audio_path`.

## Running

Root stack:

```bash
docker compose up -d --build api
```

Standalone (from this directory):

```bash
docker compose up -d --build
# or
pip install -r requirements.txt
python main.py
```

## Testing

```bash
pytest tests/ -v
```

The suite covers auth and token revocation, 3-tier key enforcement, per-user quotas and limits, cache flows, singleflight and unbilled claims, worker health and retry behavior, clone reference resolution, settings hot reload, dashboard delivery, and Docker packaging.

Enable the pre-push hook once:

```bash
git config core.hooksPath .githooks
```
