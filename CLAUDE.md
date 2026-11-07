# CLAUDE.md - VoiceClonerAppServer

Guidance for AI agents working in the gateway repository.

## Architecture

- `main.py` — FastAPI app, lifespan (DB init, discovery, health monitor), PNA middleware, client-key middleware, static dashboard mount.
- `api/` — `auth.py`, `voices.py`, `speech.py`, `management.py`, `system.py`.
- `core/` — `database.py`, `redis_client.py` (quota/rate limit/blacklist), `settings_manager.py` (hot reload), `security.py` (JWT and key dependencies).
- `services/` — dispatcher, voice discovery, cache, singleflight, per-user limiter, clone reference, usage tracker.
- `models/` — SQLAlchemy models and Pydantic schemas.
- `config/settings.json` — runtime source of truth for keys, workers, limits, cache.
- `static/dashboard/` — vanilla JS/CSS/HTML management console.

## Commands

```bash
pytest tests/ -v
pytest tests/test_inflight_speech_e2e.py -v
pytest tests/test_worker_health_routing.py -v
docker compose up -d --build
docker compose up -d --build api
```

From the repository root, use `.\.venv\Scripts\python.exe -m pytest VoiceClonerAppServer/tests/ -v` on Windows.

## Invariants

- `/v1/*` requires a valid client key; management paths require `X-Management-Key`; speech/preview/clone require a user JWT.
- Voice ownership (IDOR) is checked before cache or in-flight lookups.
- Cache key: `sha256(voice_id:normalized_text:smart_pause:48000hz)`; never add speed or pitch.
- Standard cache hits are quota-exempt; unbilled cache claims deduct quota and promote atomically.
- Only 5xx and network errors strike the circuit breaker; 4xx is an input error and is not retried.
- Health probes never mutate breaker counters.
- Cloned voices are forwarded as `ref_npz_base64`; never forward `ref_audio_path`.
- Worker read timeout 300 s, queue wait 120 s; keep client timeouts aligned.

## Repository Rules

- Run `pytest tests/ -v` before committing; never push a failing suite.
- Update `README.md` for API/config changes and `CONTEXT.md` for architecture/behavior changes.
- Activate hooks once per clone: `git config core.hooksPath .githooks`.
- Develop on a feature branch; do not push directly to `master` without confirmation.
