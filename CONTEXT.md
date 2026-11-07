# Project Context: VoiceClonerAppServer

Current state of the gateway. Git history is the changelog.

## 1. Role

Central API gateway and reverse proxy for the Voice Cloner platform. It terminates client authentication, enforces quotas and rate limits, caches audio, selects workers, and serves the management dashboard.

- Port: `8000`
- Configuration: `config/settings.json`, hot-reloaded on modification
- Persistence: PostgreSQL (SQLAlchemy async) with SQLite fallback; Redis for quotas, rate limits, and token revocation, with an in-memory fallback

## 2. Modules

| Module | Responsibility |
| :--- | :--- |
| `api/auth.py` | Registration, login, Google auth, refresh, logout, profile |
| `api/voices.py` | Catalog, preview, cloning, update, delete, reference audio |
| `api/speech.py` | Speech pipeline and preview pipeline |
| `api/management.py` | Keys, workers, settings, users, metrics, cache purge |
| `api/system.py` | Health and system status |
| `core/security.py` | JWT dependencies, management key, client key |
| `core/redis_client.py` | Daily quota, sliding-window rate limit, token blacklist |
| `core/settings_manager.py` | Pydantic settings, disk watcher, change hooks |
| `services/model_dispatcher.py` | Health state, breaker, capability filtering, utilization routing |
| `services/voice_service.py` | Dynamic discovery and catalog aggregation |
| `services/cache_service.py` | Standard and unbilled cache lifecycle |
| `services/inflight_manager.py` | Singleflight tasks and claim locks |
| `services/concurrency_limiter.py` | Per-user concurrency and queue |
| `services/clone_reference.py` | Base64 `.npz` resolution and WAV backfill |
| `services/usage_tracker.py` | Usage logs and dashboard analytics |

## 3. Security and Limits

- 3-tier keys plus user JWTs. All `/v1/*` client routes require an active client key; management routes require the management key; speech, preview, and clone require a user token.
- Access tokens 15 minutes, refresh tokens 7 days; revoked tokens are blacklisted in Redis.
- Default per-user limits: 3 cloned slots, 50,000 chars/day, 60 requests/minute, 2,000 chars per speech, 250 chars per preview, 1 concurrent stream, 1 queue waiter.
- Limits are copied onto each user row at registration and editable per user through the dashboard or `PATCH /v1/management/users/{id}`.
- Rate limiting uses a Cloudflare-style sliding-window counter with weighted previous-minute data.

## 4. Speech Pipeline

- Step 0: voice resolution and clone ownership check happen before cache or quota work.
- Standard cache: `storage/audio_cache/{sha256}.wav`, served under 10 ms, quota-exempt.
- Unbilled cache: `unbilled_{sha256}.wav`, produced when the client disconnects; claimed and promoted on retry.
- Singleflight: one worker job per cache key; waiters share an `asyncio.Future` without consuming concurrency slots.
- Quota and rate limits are checked per user; failures roll back quota.
- Client disconnect is detected by an active `receive()` watcher plus a `X-Client-Timeout` fallback; abandoned waiters return 499.
- Cache key: `sha256(voice_id:normalized_text:smart_pause:48000hz)`. Speed and pitch are client-side.

## 5. Worker Routing

- Discovery calls `GET /internal/v1/voices` on active workers at boot, on settings changes, and after a worker recovers.
- Health probes run every 15 s (3 s timeout) and update `healthy` without changing breaker counters.
- Open circuits are excluded until the 30 s cooldown; then half-open. Unhealthy closed workers are excluded.
- Candidates must support the voice (or cloning) and have free capacity. Lowest utilization wins; ties round-robin.
- One retry to a different capable worker on 5xx/network errors; `WorkerInputError` (4xx) fails fast with no strike.
- Cloned voices are resolved to `ref_npz_base64` by `clone_reference.py`; the gateway never forwards `ref_audio_path`.

## 6. Dashboard

- `/dashboard` is a 7-tab dark console: Overview, Analysis, Realtime, Request Events, Voice Workers, API Keys, Settings.
- Metrics are backed by `api_usage_logs` and `/v1/management/metrics/{summary,analysis}`.
- Calendar day bounds honor a client timezone offset; the activity heatmap maps requests to discrete time blocks.

## 7. Known Constraints and Gaps

- Worker HTTP read timeout is 300 s; queue wait and Android read timeout are 120 s. Keep these aligned when changing clients.
- Serials and voice names come from workers; the gateway keeps a cold-boot fallback catalog only.
- The pre-push hook calls bare `python`; on Windows, run the suite with the root `.venv`.
- Cross-cutting platform work is tracked in `PLAN.md`.

## 8. Verification

```bash
pytest tests/ -v
```

Current suite: 116 tests across 20 modules. Run before every commit; the pre-push hook does the same.
