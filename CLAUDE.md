# CLAUDE.md - VoiceClonerAppServer

Guidance for Claude Code and AI agents working on `VoiceClonerAppServer`.

## Project Overview
`VoiceClonerAppServer` is the central API Gateway, Reverse Proxy, Quota Controller, and Management Console inspired by the CLIProxyAPI architecture pattern.

- **Port**: 8000
- **Dynamic Configuration**: `config/settings.json` with instant runtime hot-reload.
- **Security**: 3-tier API Keys (`X-Management-Key`, `X-API-Key: vc_live_...`, upstream worker keys) + User Bearer Tokens (`Authorization: Bearer <JWT>`).
- **Load Balancing**: Dynamic Voice Discovery (`GET /internal/v1/voices`) and Capability / Voice-Aware Least-Active Load Balancer with round-robin tiebreaking.
- **Audio Caching**: Deterministic SHA-256 audio cache (`storage/audio_cache/`) delivering instant < 10ms responses with quota exemption.
- **User Authentication**: Full JWT lifecycle (`/v1/auth/register`, `/v1/auth/login`, `/v1/auth/google`, `/v1/auth/refresh`, `/v1/auth/logout`, `/v1/auth/me`).
- **Voice Cloning Management**: Multi-slot instant voice cloning (`/v1/voices/clone`) with IDOR protection.

---

## Mandatory Developer & AI Agent Rules

### 0. Initial Repository Setup
When opening or cloning this repository, ensure pre-push verification hooks are active:
```bash
git config core.hooksPath .githooks
```

### 1. Test Verification Before Push
**Rule:** You MUST run and pass all test suites before making any git commit or push:
```bash
pytest tests/ -v
```
Ensure all 31+ test cases pass without regressions.

### 2. Documentation Synchronization
**Rule:** Whenever modifying API routes, database models, load balancing algorithms, or settings, you MUST update:
- `PLAN.md`: Keep architectural roadmap and test verification matrix current.
- `README.md`: Keep API reference and environment variable tables current.

### 3. Git Branching
- Always develop on a feature branch (`refactor/...` or `feature/...`).
- Never push directly to `main` or `master` without explicit user confirmation.

---

## Testing Commands
```bash
# Run full test suite
pytest tests/ -v

# Run specific domain test suites
pytest tests/test_exceptions_and_security.py -v
pytest tests/test_capability_load_balancing.py -v
pytest tests/test_dynamic_voice_discovery.py -v
pytest tests/test_audio_cache.py -v
pytest tests/test_auth_and_voices_e2e.py -v
pytest tests/test_settings_and_3tier_auth.py -v
pytest tests/test_management_users_and_pna.py -v
pytest tests/test_dashboard_ui.py -v
pytest tests/test_docker_packaging.py -v
```
