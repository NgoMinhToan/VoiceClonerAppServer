# KEEPER-Style Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the VoiceClonerAppServer management dashboard into a sleek, high-fidelity dark-themed console modeled after KEEPER (CLIProxyAPI Usage Keeper), featuring interactive cursor-hover inspection across all charts, activity heatmaps, realtime throughput monitoring, and 6 streamlined tabs.

**Architecture:** Pure zero-build frontend (FastAPI static mount serving HTML5, Tailwind CSS, Chart.js 4.4, FontAwesome) backed by an enriched `/v1/management/metrics/summary` endpoint that computes hourly worker trends, cache hit rates, RPM/CPM throughput, and detailed recent request event logs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (Async), Chart.js 4.4.1 (UMD), Tailwind CSS (CDN), FontAwesome 6.5.1, JetBrains Mono & Plus Jakarta Sans web fonts.

**Spec:** `docs/superpowers/specs/2026-10-19-keeper-style-dashboard-design.md`

## Global Constraints
- **Zero Build Step:** Frontend must remain pure static files (`index.html`, `styles.css`, `app.js`) served directly by FastAPI without Node.js, npm, or webpack build steps.
- **Strict 6-Tab Structure:** `Overview`, `Realtime`, `Request Events`, `Voice Workers`, `API Keys`, `Settings`. Analysis and Ranking tabs are excluded.
- **Universal Cursor Hover:** All charts (area trends, dual-axis throughput, outcomes stacked bar, composition donut, and all 6 sparklines) must support crosshair cursor hover (`mode: 'index', intersect: false`) with formatted dark tooltips.
- **Test Backwards Compatibility:** All existing 38 tests must pass with zero regressions (`pytest VoiceClonerAppServer/tests/ -v`).
- **3-Tier Auth Compatibility:** Admin access to management endpoints must remain protected by `X-Management-Key`.

## Review Focus
1. **Empty Log State:** When `api_usage_logs` has zero rows (fresh database), `/v1/management/metrics/summary` must not throw ZeroDivisionError and the dashboard charts must render clean baseline visuals without NaN or undefined errors.
2. **Sub-second Audio Cache Latency:** Requests with latency < 15ms must be correctly tagged as `Cache Hit` in both metrics aggregation and the Request Events table.
3. **Chart Destroy & Re-render Lifecycle:** Switching tabs or clicking refresh must safely destroy existing Chart.js instances before re-initializing to avoid memory leaks and canvas reuse errors.
4. **Interactive Tooltip Bounds:** Custom floating tooltips for the 32x7 activity heatmaps must remain within viewport bounds and vanish cleanly on `mouseleave`.
5. **Private Network Access (PNA):** The new dashboard UI assets must continue to satisfy existing CORS and Private Network Access requirements (`Access-Control-Allow-Private-Network`).

---

### Task 1: Backend Metrics Summary Extension & Cache Analytics

**Files:**
- Modify: `VoiceClonerAppServer/services/usage_tracker.py:70-155`
- Modify: `VoiceClonerAppServer/api/management.py:128-137`
- Test: `VoiceClonerAppServer/tests/test_dashboard_metrics_extension.py`

**Interfaces:**
- Consumes: `ApiUsageLog` database model and `settings_manager.get_settings()`
- Produces: Enhanced dict from `usage_tracker.get_metrics_summary(hours: int = 24)` containing:
  - `hourly_trends`: `list[dict]` with `time`, `worker`, `chars`, `requests`
  - `cache_metrics`: `dict` with `cache_hit_count`, `cache_miss_count`, `cache_hit_rate`, `cached_chars`, `raw_chars`
  - `rpm`: `float`
  - `cpm`: `int`
  - `recent_logs`: expanded `list[dict]` up to 50 items with `is_cache_hit` bool

- [ ] **Step 1: Write the failing test**

Create `VoiceClonerAppServer/tests/test_dashboard_metrics_extension.py`:
```python
import pytest
from httpx import AsyncClient, ASGITransport
from VoiceClonerAppServer.main import app

@pytest.mark.asyncio
async def test_metrics_summary_contains_keeper_extended_fields():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(
            "/v1/management/metrics/summary?hours=24",
            headers={"X-Management-Key": "vc_admin_secret_key_123"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "cache_metrics" in data
        assert "hourly_trends" in data
        assert "rpm" in data
        assert "cpm" in data
        assert "recent_logs" in data
        assert isinstance(data["cache_metrics"]["cache_hit_rate"], (int, float))
        assert isinstance(data["hourly_trends"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_metrics_extension.py -v; Set-Location VoiceClonerAppServer
```
Expected: FAIL with `AssertionError: assert 'cache_metrics' in data`

- [ ] **Step 3: Implement enhanced metrics calculation in `services/usage_tracker.py`**

In `services/usage_tracker.py:get_metrics_summary()`:
1. Query `ApiUsageLog` where `timestamp >= since`.
2. Compute `cache_hit_count` (where `latency_ms < 15.0`), `cache_miss_count`, and `cache_hit_rate` (percentage).
3. Compute `rpm = round(total_requests / max(1, hours * 60), 2)` and `cpm = int(total_chars / max(1, hours * 60))`.
4. Group recent logs by hour and worker to produce `hourly_trends`.
5. Expand recent logs query limit from 20 to 50, adding `"is_cache_hit": row.latency_ms < 15.0`.
6. Maintain in-memory fallback computation when database query fails so zero-log environments return safe zero values without crashing.

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_metrics_extension.py -v; Set-Location VoiceClonerAppServer
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add VoiceClonerAppServer/services/usage_tracker.py VoiceClonerAppServer/tests/test_dashboard_metrics_extension.py
git commit -m "feat(metrics): extend metrics summary with cache rates, throughput, and hourly trends"
```

---

### Task 2: Stylesheet & Theme Assets for KEEPER Dark Aesthetics

**Files:**
- Modify: `VoiceClonerAppServer/static/dashboard/styles.css`
- Test: `VoiceClonerAppServer/tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: Modern CSS variables and Google Fonts (`Plus Jakarta Sans`, `JetBrains Mono`)
- Produces: CSS utility classes for KEEPER UI:
  - `.k-card`, `.k-tab-btn`, `.k-tab-btn.active`
  - Glowing accents: `.k-glow-blue`, `.k-glow-purple`, `.k-glow-green`, `.k-glow-orange`, `.k-glow-teal`, `.k-glow-amber`
  - Heatmap components: `.heat-cell`, `#heatmapTooltip`

- [ ] **Step 1: Write test to verify new stylesheet tokens exist**

Add test to `VoiceClonerAppServer/tests/test_dashboard_ui.py`:
```python
def test_dashboard_styles_contains_keeper_classes():
    from pathlib import Path
    css = Path("VoiceClonerAppServer/static/dashboard/styles.css").read_text(encoding="utf-8")
    assert ".k-card" in css
    assert ".k-glow-blue" in css
    assert ".heat-cell" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -k test_dashboard_styles_contains_keeper_classes -v; Set-Location VoiceClonerAppServer
```
Expected: FAIL with `AssertionError: assert '.k-card' in css`

- [ ] **Step 3: Update `static/dashboard/styles.css`**

Add complete KEEPER styling definitions:
- Root color tokens (`--bg-dark: #0d0e12`, `--card-bg: #14151b`, `--card-border: #1e2029`).
- `.k-card` with 16px radius, subtle border, smooth hover transitions.
- Pill navigation styles `.k-tab-btn` and `.k-tab-btn.active`.
- Colored glow top borders (`.k-glow-blue`, `.k-glow-purple`, `.k-glow-green`, `.k-glow-orange`, `.k-glow-teal`, `.k-glow-amber`).
- Heatmap cell squares (`.heat-cell`) with scale hover effect.
- Heatmap floating tooltip styles (`#heatmapTooltip`).
- Custom sleek dark scrollbar.

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -k test_dashboard_styles_contains_keeper_classes -v; Set-Location VoiceClonerAppServer
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add VoiceClonerAppServer/static/dashboard/styles.css VoiceClonerAppServer/tests/test_dashboard_ui.py
git commit -m "style(dashboard): add KEEPER dark theme tokens, glowing card borders, and heatmap styles"
```

---

### Task 3: KEEPER Dashboard HTML Layout with 6 Tabs

**Files:**
- Modify: `VoiceClonerAppServer/static/dashboard/index.html`
- Test: `VoiceClonerAppServer/tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: Tailwind CSS CDN, Chart.js 4.4 UMD, FontAwesome 6.5.1
- Produces: Complete responsive single-page application structure at `/dashboard`:
  - Brand header & secondary navigation bar with 6 tabs
  - Content containers: `#content-overview`, `#content-realtime`, `#content-events`, `#content-workers`, `#content-keys`, `#content-settings`
  - Canvas elements for all charts and sparklines

- [ ] **Step 1: Write test for 6 tabs and KEEPER layout in `test_dashboard_ui.py`**

Add test to `VoiceClonerAppServer/tests/test_dashboard_ui.py`:
```python
def test_dashboard_html_contains_six_keeper_tabs():
    from pathlib import Path
    html = Path("VoiceClonerAppServer/static/dashboard/index.html").read_text(encoding="utf-8")
    assert "VOICE CLONER" in html
    assert "tab-btn-overview" in html
    assert "tab-btn-realtime" in html
    assert "tab-btn-events" in html
    assert "tab-btn-workers" in html
    assert "tab-btn-keys" in html
    assert "tab-btn-settings" in html
    # Verify Analysis and Ranking are NOT present as tabs
    assert "tab-btn-analysis" not in html
    assert "tab-btn-ranking" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -k test_dashboard_html_contains_six_keeper_tabs -v; Set-Location VoiceClonerAppServer
```
Expected: FAIL with `AssertionError: assert 'tab-btn-overview' in html`

- [ ] **Step 3: Replace `static/dashboard/index.html` with KEEPER-inspired structure**

Implement:
1. Top Brand Header: Logo icon, `VOICE CLONER` title, `KEEPER PROXY GATEWAY` subtitle, Studio Demo link, Swagger link.
2. Secondary Nav Bar: 6 tab buttons (`Overview`, `Realtime`, `Request Events`, `Voice Workers`, `API Keys`, `Settings`), API key filter, Range filter, and animated refresh button.
3. Tab 1 `#content-overview`:
   - 2 Hero cards (`sparklineRequests`, `sparklineTokens`)
   - 4 Small KPI cards (`sparklineRPM`, `sparklineTPM`, `sparklineCache`, `sparklineCost`)
   - Recent Activity: `#tokenActivityHeatmap` and `#healthTimelineGrid`
   - Usage Distribution: `#chartTrendsMain` canvas and model breakdown legend
4. Tab 2 `#content-realtime`:
   - 4 Realtime KPI cards (Window requests, chars, cache reach, window cost)
   - Throughput canvas `#chartThroughput`
   - Outcomes canvas `#chartOutcomes`
   - Token & Cache Composition canvas `#chartComposition` with `#donutCenterVal`
5. Tab 3 `#content-events`: Filter bar and table `#eventsTableBody`
6. Tab 4 `#content-workers`: List of VieNeu-TTS workers with live health badges and active toggles
7. Tab 5 `#content-keys`: Client API key table, generate modal
8. Tab 6 `#content-settings`: Management key input, user accounts table with slot modification modal
9. Toast container `#toastContainer` and floating heatmap tooltip `#heatmapTooltip`.

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -v; Set-Location VoiceClonerAppServer
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add VoiceClonerAppServer/static/dashboard/index.html VoiceClonerAppServer/tests/test_dashboard_ui.py
git commit -m "feat(dashboard): rewrite HTML layout with 6 KEEPER tabs, sparklines, and activity heatmaps"
```

---

### Task 4: Interactive Frontend Controller & Chart.js Implementation

**Files:**
- Modify: `VoiceClonerAppServer/static/dashboard/app.js`
- Test: `VoiceClonerAppServer/tests/test_dashboard_ui.py`

**Interfaces:**
- Consumes: `/v1/management/metrics/summary`, `/v1/management/workers`, `/v1/management/keys`, `/v1/management/users`
- Produces:
  - `switchKTab(tabName)`: Clean tab switcher with chart resizing
  - Chart instances with cursor hover inspection (`interaction: { mode: 'index', intersect: false }`)
  - Sparklines renderer with value tooltips
  - Heatmap builder with coordinate hover popover
  - Event log table renderer with search & filter
  - Management API operations (worker toggle, user slot update, client key creation/revocation)

- [ ] **Step 1: Write test verifying Chart.js initialization logic in `app.js`**

Add test to `VoiceClonerAppServer/tests/test_dashboard_ui.py`:
```python
def test_dashboard_js_implements_keeper_logic():
    from pathlib import Path
    js = Path("VoiceClonerAppServer/static/dashboard/app.js").read_text(encoding="utf-8")
    assert "switchKTab" in js
    assert "initSparklines" in js
    assert "initHeatmaps" in js
    assert "chartTrendsMain" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -k test_dashboard_js_implements_keeper_logic -v; Set-Location VoiceClonerAppServer
```
Expected: FAIL with `AssertionError: assert 'switchKTab' in js`

- [ ] **Step 3: Implement complete frontend logic in `static/dashboard/app.js`**

Implement:
1. `switchKTab(tab)`: Updates active button classes, hides non-active content divs, displays target tab, and dispatches window resize so Chart.js canvases re-fit seamlessly.
2. Tooltip Configuration: Common `commonTooltipConfig` with dark background `#1b1d26`, `#2e3240` border, JetBrains Mono font, and custom callbacks.
3. `initSparklines(data)`:
   - Requests sparkline (blue `#3b82f6` with fill)
   - Tokens sparkline (purple `#8b5cf6` with fill)
   - RPM sparkline (green `#10b981`)
   - CPM sparkline (orange `#f97316`)
   - Cache sparkline (teal `#14b8a6`)
   - Cost sparkline (amber `#f59e0b`)
   - All configured with `pointHoverRadius: 5` and custom title/label callbacks.
4. `initHeatmaps(data)`:
   - 32 cols x 7 rows for Character Activity (slate -> blue levels).
   - 32 cols x 7 rows for Health Timeline (emerald, amber, rose levels).
   - Event listeners for `mouseenter` and `mouseleave` updating `#heatmapTooltip`.
5. `initMainTrendsChart(data)`:
   - Smooth spline line chart with area fill for `vieneu-3.8-turbo`, `vieneu-nano-fast`, and `voice-clone-embed`.
   - Crosshair hover `interaction: { mode: 'index', intersect: false }`.
6. `initRealtimeCharts(data)`:
   - Throughput dual-axis chart (Tokens/min on left Y, Requests/min on right Y).
   - Outcomes stacked bar chart (Successful vs Failed).
   - Token & Cache composition donut with live center text update on slice hover.
7. `fetchMetrics()`, `fetchWorkers()`, `fetchKeys()`, `fetchUsers()`:
   - Real-time data binding with graceful defaults when logs are sparse.
8. Worker toggle, Client Key creation/revocation, User max slot edit modal, Management key update.

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/test_dashboard_ui.py -v; Set-Location VoiceClonerAppServer
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add VoiceClonerAppServer/static/dashboard/app.js VoiceClonerAppServer/tests/test_dashboard_ui.py
git commit -m "feat(dashboard): implement interactive Chart.js graphs, sparklines, and heatmaps with cursor hover"
```

---

### Task 5: Full Regression Testing & Visual Quality Check

**Files:**
- Modify: `VoiceClonerAppServer/tests/test_dashboard_ui.py`
- Verify: All tests across `VoiceClonerAppServer/tests/`

- [ ] **Step 1: Run complete test suite from repository root**

Run:
```powershell
Set-Location ..; $env:PYTHONPATH = "."; & "D:\Project\vieneu-dubbling\.venv\Scripts\python.exe" -m pytest VoiceClonerAppServer/tests/ -v; Set-Location VoiceClonerAppServer
```
Expected: All 39+ tests pass (100% success rate).

- [ ] **Step 2: Verify static asset loading through TestClient**

Verify `test_dashboard_endpoint_serves_html` and `test_dashboard_static_assets_delivered` return status 200 and correct MIME types (`text/html`, `text/css`, `application/javascript`).

- [ ] **Step 3: Commit**

```bash
git add VoiceClonerAppServer/tests/test_dashboard_ui.py
git commit -m "test(dashboard): verify full test suite passes with redesigned KEEPER console"
```

---

### Task 6: Documentation Synchronization

**Files:**
- Modify: `VoiceClonerAppServer/PLAN.md`
- Modify: `VoiceClonerAppServer/README.md`

- [ ] **Step 1: Update `VoiceClonerAppServer/PLAN.md`**

Update:
- Mark KEEPER-style dashboard redesign as implemented.
- Update test verification matrix to record new test cases and 100% pass status.

- [ ] **Step 2: Update `VoiceClonerAppServer/README.md`**

Update:
- Describe the new KEEPER-inspired dark dashboard features (6 tabs, cursor-hover charts, activity heatmaps, real-time throughput).
- Update API endpoints table if new query fields are documented.

- [ ] **Step 3: Commit**

```bash
git add VoiceClonerAppServer/PLAN.md VoiceClonerAppServer/README.md
git commit -m "docs: synchronize PLAN.md and README.md with KEEPER dashboard architecture"
```
