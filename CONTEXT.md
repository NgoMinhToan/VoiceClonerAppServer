# Project Context: VoiceClonerAppServer

> **Important for Agents:** Keep this file up to date! Whenever you make a git commit in `VoiceClonerAppServer`, update this file to reflect the latest state, architecture modifications, completed tasks, and upcoming priorities.

---

## 1. Project Summary

`VoiceClonerAppServer` is the central API Gateway, Reverse Proxy, Quota Controller, and Management Console for the Voice Cloner platform.

- **Port**: 8000
- **Dynamic Configuration**: `config/settings.json` with instant runtime hot-reload via `settings_manager.py`.
- **Security**: 3-tier API Keys (`X-Management-Key`, `X-API-Key: vc_live_...`, upstream worker keys) + User Bearer Tokens (`Authorization: Bearer <JWT>`).
- **Load Balancing**: Dynamic Voice Discovery (`GET /internal/v1/voices`) and Capability / Voice-Aware Least-Active Load Balancer with round-robin tiebreaking.
- **Audio Caching**: Deterministic SHA-256 audio cache (`storage/audio_cache/`) delivering instant < 10ms responses with quota exemption.
- **User Authentication**: Full JWT lifecycle (`/v1/auth/register`, `/v1/auth/login`, `/v1/auth/google`, `/v1/auth/refresh`, `/v1/auth/logout`, `/v1/auth/me`).
- **Voice Cloning Management**: Multi-slot instant voice cloning (`/v1/voices/clone`) with IDOR protection.
- **Management Dashboard**: Dark Obsidian Control Center (`/dashboard`) with Chart.js analytics and real-time telemetry.

---

## 2. Authentication, Quota & Concurrency Architecture

- **Auth Providers**: Email/Password (bcrypt + JWT) and Google Sign-In (OAuth2 / Google ID tokens).
- **Tokens**: Access token (15 mins), Refresh token (7 days). Token revocation backed by Redis blacklist.
- **Daily Quotas & Limits**:
  - Daily TTS character limit: `daily_tts_char_limit` (Redis atomic counter, resets at midnight).
  - Rate limit: `rate_limit_requests_per_minute` per user / key.
  - Cloned voice slot limit: strictly 3 slots per user (configurable in Settings).
  - Maximum input character length checks: `max_speech_char_limit` for speech and `max_preview_char_limit` for preview.
- **Concurrency**:
  - TTS (`/v1/audio/speech`): Controlled by `tts_slot_guard` (asyncio.Semaphore, dynamic resizing) with queueing and client disconnect cancellation.
  - Audio Cache HIT: Served in `< 10ms` from disk, quota-exempt.
  - Audio Cache MISS: Atomic quota deduction, routed to upstream VieNeu-TTS workers, automated quota rollback on failure.

---

## 3. Completed Work

### Management Dashboard Revamp & Analysis Tab Integration (Oct 2026)
- **Restoration of Specialized Analysis Tab (`#content-analysis`)**:
  - Implemented 6 analytical visualization cards modeled after `cpa-usage-keeper`'s `AnalysisPanel.tsx`:
    1. *Token / TTS Character Usage Over Time*: Mixed stacked bar (Input Chars `#2563eb`, Raw Inference Chars `#16a34a`, Cache Read Chars `#d97706`) + requests dashed line (`#ff5a40`) + estimated cost line (`#14b8a6`) on secondary Y-axis, complete with Total Tokens, Total Cost, Cost / 1M Chars KPI counters and average line reference.
    2. *Usage Distribution*: Dimension switcher (API Key | Model Worker | User) with Doughnut chart and detailed ranked list displaying Cache Rate %, Requests, Cost, RPM, and TPM.
    3. *Top Models*: Hourly stacked bar chart broken down by upstream VieNeu-TTS worker nodes with model performance metrics.
    4. *Latency Diagnostics*: Scatter plot correlating request text length (`char_count`) with response latency (`latency_ms`) accompanied by p95 Latency threshold line, average latency, and sample counters.
    5. *Model Speed & Efficiency*: End-to-end output speed (chars/sec) bar chart by worker and quadrant efficiency matrix.
    6. *API Key & Models Heatmap*: 2D matrix table crossing Client API Keys with Worker Models, shaded with volume-based color gradients from Low to High.
- **Calendar Day Bounds & Local Timezone Alignment**:
  - Resolved timeline bug where "Today" previously used rolling `now - 24h`: `usage_tracker.py` now accepts `tz_offset_minutes` from the client browser and computes calendar day bounds starting strictly at `00:00:00` local time today through `23:59:59` (24 hourly buckets: `00:00`, `01:00`, ..., `23:00`).
  - Added dedicated logic for "Yesterday" (yesterday's 24 hours with exact `since <= t < until` bounds), "7d" (7 daily buckets), and "30d" (30 daily buckets).
- **Activity Heatmap Discrete 364-Block Mapping (Bugfix)**:
  - Eliminated the whole-column smear bug where all 7 vertical cells in an hourly column lit up simultaneously: partitioned the time window into 364 discrete blocks (52 columns x 7 rows, ~3.95 minutes per cell). Requests are mapped directly to their individual block index based on exact timestamps, so only cells with active requests illuminate while inactive cells remain dark.
  - Corrected fixed-position tooltip offset in `positionTooltip()` to prevent covering the cursor or target cell.
  - Restored color status legend indicators by isolating `.legend-cell` from flex-scaling `.heat-cell` rules.
  - Eliminated sparkline tooltip clipping by setting `overflow: visible` on `.k-sparkline-box` and removing `overflow: hidden` on `.k-card`.
- **Dark Obsidian Aesthetics & Chart Card Stretching**:
  - Fixed background color mismatch by replacing muddy espresso tones with authentic dark obsidian tokens: `#0f1013` (canvas/page), `#17181d` (cards), `#1f2127` (recessed sparklines/inputs), and `#262932` (borders).
  - Restructured Chart.js canvas containers with `position: relative`, `width: 100%`, and `maintainAspectRatio: false`, allowing charts and sparklines to stretch 100% edge-to-edge inside their cards without awkward padding cutoffs.
- **Responsive 7-Tab Dock Toolbar & Brand Normalization**:
  - Desktop view (>= 1024px): Displays all 7 tabs (`Overview`, `Analysis`, `Realtime`, `Request Events`, `Voice Workers`, `API Keys`, `Settings`) as 1-click pill buttons with smooth active pill indicators.
  - Mobile/Tablet view (< 1024px): Automatically collapses the navigation into a sleek "Switch page" dropdown button with an animated popup menu matching `cpa-usage-keeper`.
  - Purged all mentions of `CLIProxyAPI` across HTML, CSS, JavaScript, and API route metadata, unifying under `VOICE CLONER - AI Voice Gateway Console`.
- **Verification**: Updated `test_dashboard_ui.py` (asserting `tab-btn-analysis`, `chartTokenUsageAnalysis`, `CLIProxyAPI not in html`) and `test_dashboard_metrics_extension.py` (validating `analysis` payload and `/v1/management/metrics/analysis` route). Verified 49/49 unit tests passing with 100% success rate, verified in headless browser via Playwright across desktop and mobile viewports.
