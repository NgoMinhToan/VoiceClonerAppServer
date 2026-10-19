# Design Document: KEEPER-Style Dashboard Redesign for VoiceClonerAppServer

**Date:** 2026-10-19  
**Status:** Approved for Implementation Planning  
**Author:** AI Agent & Minh Toan  
**Component:** `VoiceClonerAppServer` (Gateway & Management Console)

---

## 1. Intent & The Why

`VoiceClonerAppServer` is the central API Gateway, Load Balancer, and Quota Controller for the Voice Cloner platform. It manages upstream VieNeu-TTS inference workers, handles multi-tier authentication (Management Key, Client Keys, JWT Bearer tokens), and caches generated speech in a deterministic SHA-256 audio cache.

The original dashboard (`static/dashboard/`) was a functional but plain sidebar layout with basic charts. The goal of this redesign is to upgrade the console into a high-fidelity, polished, dark-themed control center modeled after **KEEPER** (CPA Usage Keeper / CLIProxyAPI integration) — featuring sleek glassmorphic cards, glowing borders, real-time throughput metrics, activity heatmaps, and comprehensive cursor-hover inspection across all charts.

---

## 2. Goals & Scope

### 2.1 Goals
1. **KEEPER Aesthetics & Dark Theme:**
   - Deep dark palette (`#0d0e12`, `#14151b`, `#1e2029`).
   - Glowing accent borders on key metrics (Blue for Requests, Purple for Characters/Tokens, Green for RPM, Orange for CPM, Teal for Cache, Amber for Cost).
   - Clean top-bar navigation matching the KEEPER layout.
2. **Interactive Hover on All Charts:**
   - Every chart (Main trends, Throughput, Outcomes, Composition Donut, and all Sparklines) supports crosshair cursor hover (`interaction: { mode: 'index', intersect: false }`) with rich dark tooltips.
   - Recent Activity heatmaps (Character Activity & Request Health Timeline) show popovers with timestamp, character count, and error details on hover.
3. **Streamlined 6-Tab Architecture:**
   - **Overview:** Hero KPI cards, 4 mini sparkline cards, Recent Activity heatmaps, and Model/Worker Trends area chart.
   - **Realtime:** Live metrics (15m/30m/60m window), Throughput dual-axis chart, Request Outcomes stacked bar, Token/Cache composition donut.
   - **Request Events:** Searchable, filterable event log table (Model, Source, Status, Latency, Cache, Client IP).
   - **Voice Workers:** Upstream VieNeu-TTS worker nodes (ping latency, weight, active toggle, quota).
   - **API Keys:** Management of Client API Keys (`vc_live_...`) with daily quotas.
   - **Settings:** Management key configuration, user quota management, and `settings.json` hot-reload.
4. **Zero-Build Architecture:**
   - Pure HTML5 + Vanilla ES6 + Tailwind CSS CDN + Chart.js 4.4 + FontAwesome.
   - Directly served by FastAPI at `/dashboard` with zero Node.js/bundler build steps.
5. **Full Backwards Compatibility:**
   - All 38 existing test cases (including `test_dashboard_ui.py`) must pass with zero regressions.

### 2.2 Non-Goals
- **Analysis & Ranking Tabs:** Explicitly excluded per user instructions.
- **Complex Financial Billing Engine:** Cost calculation uses a configurable estimation formula or mock multiplier as requested by user ("Có thể bỏ qua biểu đồ chi phí hoặc gán đại giá trị cho nó").

### 2.3 Anti-Goals
- Do not introduce heavy frontend frameworks (React/Vue/Next.js) that would complicate Docker packaging or require a Node runtime.
- Do not remove or break existing management features (worker pinging, user slot edits, key revocation).

---

## 3. Detailed Component & UI Specifications

### 3.1 Header & Secondary Navigation
- **Brand Header:**
  - Logo icon: Gradient square (indigo-purple-pink) with wave icon.
  - Title: `VOICE CLONER` with subtitle `KEEPER PROXY GATEWAY`.
  - Right tools: Link to Studio Demo (`http://localhost:3000`), Language switcher, Swagger Docs (`/docs`), Theme toggle.
- **Secondary Nav Bar:**
  - Tab pills: `Overview` (active default), `Realtime`, `Request Events`, `Voice Workers`, `API Keys`, `Settings`.
  - Filter controls: API Key dropdown, Range dropdown (Today, Yesterday, 7 Days, 30 Days), Refresh button with spin animation.

### 3.2 Tab 1: Overview
- **Hero KPI Cards (Top Row, 2 columns):**
  - **Total Requests:** Total count, Success count (green dot), Failed count (red dot), Success Rate %, glowing blue border, mini sparkline with hover.
  - **Total Characters / Tokens:** Total character count, Cache Read count (purple), Raw inference count, Estimated audio duration (hours), glowing purple border, mini sparkline with hover.
- **Secondary KPI Cards (Row 2, 4 columns):**
  - **RPM (Requests Per Minute):** Green accent, live rate, sparkline with tooltip.
  - **TPM / CPM (Chars Per Minute):** Orange accent, character rate, sparkline with tooltip.
  - **Audio Cache Rate (%):** Teal accent, percentage of requests served from SHA-256 cache, sparkline with tooltip.
  - **Total Cost / Value:** Amber accent, estimated dollar value ($10.14) based on character tiers, sparkline with tooltip.
- **Recent Activity Section (Row 3, 2 columns):**
  - **Character Activity Heatmap:** 32-column x 7-row grid of cells shaded from slate to dark blue to bright blue, with hover tooltip showing hour and char count.
  - **Request Health Timeline:** 32-column x 7-row grid showing health status (emerald = healthy, amber = degraded/latency, rose = failed/429), hover tooltip showing error breakdown.
- **Usage Distribution Section (Row 4):**
  - Sub-filter tabs: Models (Workers), API Key, Users.
  - Smooth spline stacked area chart with gradient fills representing VieNeu-TTS models (`vieneu-3.8-turbo`, `vieneu-nano-fast`, `voice-clone-embed`, etc.).
  - Interactive legend with color dots, model names, total tokens, and percentages.

### 3.3 Tab 2: Realtime
- **Window Filter:** 15m, 30m, 60m toggle.
- **Top Row Metrics:** Window requests, Window chars, Cache reach %, Window cost.
- **Throughput Dual-Axis Chart:**
  - Left Y-axis: Tokens/chars per minute (blue curve).
  - Right Y-axis: Requests per minute (orange dashed curve).
  - Crosshair tooltips showing exact rates.
- **Request Outcomes:**
  - Stacked bar chart (emerald for successful, rose for failed) with bucket labels and hover inspection.
- **Token & Cache Composition:**
  - Donut chart (Cache read, Uncached input, Audio stream) with interactive center text update on slice hover.

### 3.4 Tab 3: Request Events
- **Data Source & Strategy:**
  - Derived from `ApiUsageLog` records via `/v1/management/metrics/summary` (expanded to 50-100 recent entries).
  - Client-side filtering & search by Model Worker, Status (200 / Cache Hit / 4xx/5xx), and Client Key.
  - Columns: Timestamp, API Key, Source / User, Model Worker, Result Badge, Endpoint, Latency, Chars, Cache Hit, Client IP.
  - Latency < 15ms is tagged as `Cache Hit` (served directly from disk cache).

### 3.5 Tab 4: Voice Workers (Upstream VieNeu-TTS Nodes)
- Node status cards with icon, URL, active badge, weight, ping latency (ms).
- Live metrics per node: Requests processed, success rate, character throughput.
- Active toggle switch (calling `/v1/management/settings` hot-reload).
- Add new worker dialog.

### 3.6 Tab 5: Client API Keys
- List of configured client keys from `settings.json`.
- Key name, masked key string, daily character quota, current usage bar.
- Create new client key modal (`/v1/management/keys`).
- Revoke key button with confirmation.

### 3.7 Tab 6: Settings & Users
- Management Key password input & update button.
- Registered User Accounts table: Email, Full name, Auth provider, Cloned voices count, Max slots, Today's usage.
- Edit user slot modal & delete user option.
- Raw `settings.json` viewer with hot-reload save.

---

## 4. Backend Enhancements & Data Endpoints

1. **Extend `/v1/management/metrics/summary`:**
   - In `services/usage_tracker.py`, enrich the summary response to include:
     - `hourly_trends`: List of time buckets with request counts and character counts partitioned by worker/model.
     - `cache_metrics`: Cache hit count, cache hit rate, raw synthesis count.
     - `rpm`: Realtime calculated requests per minute over recent window.
     - `cpm`: Realtime characters per minute over recent window.
     - `recent_logs`: Expanded from 20 to 50-100 entries with client IP (captured from request when available) and cache flag.
2. **Graceful Fallbacks & Empty State Resilience:**
   - If PostgreSQL or in-memory logs have sparse data (e.g. during fresh startup or in automated test environments), provide realistic baseline points so charts render cleanly with zero visual glitching.

---

## 5. Verification Plan

1. **Unit & Integration Tests:**
   - Run `pytest VoiceClonerAppServer/tests/ -v` from repo root to ensure all 38 existing tests pass.
   - Update `tests/test_dashboard_ui.py` to assert new KEEPER title and elements exist.
2. **Visual & Interaction Verification:**
   - Verify all 6 tabs switch cleanly.
   - Verify cursor hover tooltips appear on all charts, sparklines, and heatmap cells.
   - Verify responsive layout.
3. **Documentation Sync:**
   - Update `VoiceClonerAppServer/PLAN.md` and `VoiceClonerAppServer/README.md`.
