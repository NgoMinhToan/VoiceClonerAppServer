// VoiceClonerAppServer - Management Console Frontend Logic (KEEPER Style)

let currentKTab = 'overview';
let activeMetricsData = null;
let realtimeWindowMinutes = 60;
let activityWindow = 'day'; // 'day' | 'week'
let autoRefreshTimer = null;
let currentAutoRefreshInterval = 10; // seconds, 0 = off

// Request Events State
let eventsCurrentPage = 1;
let eventsPageSize = 25;
let eventsSearchKeyword = '';
let currentActiveDimension = 'models';

// Chart instance tracking for safe destroy/re-render lifecycle
const charts = {
  sparklineReq: null,
  sparklineToken: null,
  sparklineRPM: null,
  sparklineTPM: null,
  sparklineCache: null,
  sparklineCost: null,
  mainTrends: null,
  throughput: null,
  outcomes: null,
  composition: null,
  latencyRealtime: null,
};

function getManagementKey() {
  return localStorage.getItem('vc_management_key') || 'vc_admin_secret_key_123';
}

function setManagementKey(key) {
  localStorage.setItem('vc_management_key', key.trim());
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  const bg = type === 'success' ? 'bg-emerald-600' : (type === 'error' ? 'bg-rose-600' : 'bg-indigo-600');
  toast.className = `${bg} text-white px-4 py-2.5 rounded-lg shadow-xl text-xs font-medium flex items-center space-x-2 transition-all duration-300 transform translate-y-2 opacity-0 pointer-events-auto`;
  toast.innerHTML = `<i class="fa-solid fa-${type === 'success' ? 'check' : (type === 'error' ? 'circle-exclamation' : 'circle-info')}"></i><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.classList.remove('translate-y-2', 'opacity-0'), 10);
  setTimeout(() => {
    toast.classList.add('opacity-0');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

async function apiRequest(endpoint, options = {}) {
  const key = getManagementKey();
  const headers = {
    'Content-Type': 'application/json',
    'X-Management-Key': key,
    ...(options.headers || {}),
  };
  try {
    const res = await fetch(endpoint, { ...options, headers });
    if (res.status === 401) {
      showToast('Khóa quản trị (Management Key) không đúng hoặc thiếu!', 'error');
      openAuthModal();
      throw new Error('Unauthorized');
    }
    return res;
  } catch (err) {
    if (err.message !== 'Unauthorized') {
      showToast('Không thể kết nối đến API Server!', 'error');
    }
    throw err;
  }
}

// ── NAVIGATION & TAB SWITCHING ──

function switchKTab(tab) {
  currentKTab = tab;
  document.querySelectorAll('.k-tab-btn').forEach(b => b.classList.remove('active'));
  const activeBtn = document.getElementById(`tab-btn-${tab}`);
  if (activeBtn) activeBtn.classList.add('active');

  const tabs = ['overview', 'realtime', 'events', 'workers', 'keys', 'settings'];
  tabs.forEach(t => {
    const el = document.getElementById(`content-${t}`);
    if (el) el.classList.add('hidden');
  });

  const target = document.getElementById(`content-${tab}`);
  if (target) target.classList.remove('hidden');

  if (tab === 'overview') fetchMetrics();
  else if (tab === 'realtime') fetchRealtimeMetrics();
  else if (tab === 'events') {
    populateEventsFilterDropdowns();
    renderEventsTable();
  }
  else if (tab === 'workers') fetchWorkers();
  else if (tab === 'keys') fetchKeys();
  else if (tab === 'settings') fetchSettings();

  window.dispatchEvent(new Event('resize'));
}

// Backwards compatibility alias
function switchTab(tabId) {
  const map = {
    'metrics': 'overview',
    'users': 'settings',
    'keys': 'keys',
    'workers': 'workers',
    'settings': 'settings',
  };
  switchKTab(map[tabId] || 'overview');
}

function refreshCurrentTab() {
  const icon = document.getElementById('refreshIcon');
  if (icon) icon.classList.add('fa-spin');
  switchKTab(currentKTab);
  setTimeout(() => {
    if (icon) icon.classList.remove('fa-spin');
  }, 500);
}

function onFilterChange() {
  fetchMetrics();
}

function onAutoRefreshChange() {
  const select = document.getElementById('autoRefreshSelect');
  const dot = document.getElementById('autoRefreshDot');
  if (!select) return;
  currentAutoRefreshInterval = parseInt(select.value, 10);
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (currentAutoRefreshInterval > 0) {
    if (dot) dot.classList.add('live-dot');
    autoRefreshTimer = setInterval(() => {
      if (currentKTab === 'overview') fetchMetrics(true);
      else if (currentKTab === 'realtime') fetchRealtimeMetrics(true);
      else if (currentKTab === 'events') fetchMetrics(true).then(() => renderEventsTable());
    }, currentAutoRefreshInterval * 1000);
  } else {
    if (dot) dot.classList.remove('live-dot');
  }
}

// ── CHART.JS COMMON CONFIGURATION ──

const commonTooltipConfig = {
  enabled: true,
  backgroundColor: '#262320',
  borderColor: '#3a3530',
  borderWidth: 1,
  titleColor: '#f6f4f1',
  titleFont: { size: 12, weight: 'bold' },
  bodyColor: '#c9c3bb',
  bodyFont: { size: 11, family: 'JetBrains Mono' },
  padding: 10,
  cornerRadius: 8,
  displayColors: true,
  boxPadding: 4,
};

// Helper to position tooltip safely
function positionTooltip(e, tooltip) {
  const rect = e.target.getBoundingClientRect();
  const tooltipWidth = tooltip.offsetWidth || 200;
  let left = rect.left + window.scrollX - tooltipWidth / 2 + rect.width / 2;
  left = Math.max(10, Math.min(left, window.innerWidth - tooltipWidth - 10));
  let top = rect.top + window.scrollY - 55;
  if (rect.top < 60) {
    top = rect.bottom + window.scrollY + 10;
  }
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

// ── OVERVIEW TAB METRICS & CHARTS ──

async function fetchMetrics(isSilent = false) {
  try {
    const rangeSelect = document.getElementById('filterRange');
    const hours = rangeSelect ? rangeSelect.value : '24';
    const keySelect = document.getElementById('filterApiKey');
    const selectedKey = keySelect ? keySelect.value : 'all';

    let url = `/v1/management/metrics/summary?hours=${hours}`;
    if (selectedKey && selectedKey !== 'all') {
      url += `&key_name=${encodeURIComponent(selectedKey)}`;
    }

    const res = await apiRequest(url);
    if (!res.ok) return;
    const data = await res.json();
    activeMetricsData = data;

    // 1. Hero Card 1: Total Requests
    const totalReqs = data.total_requests ?? 0;
    const totalChars = data.total_chars ?? 0;
    const failedReqs = data.failed_requests ?? 0;
    const successReqs = data.success_requests ?? Math.max(0, totalReqs - failedReqs);
    const successRate = totalReqs > 0 ? ((successReqs / totalReqs) * 100).toFixed(1) : '100.0';

    const cacheMetrics = data.cache_metrics || {
      cache_hit_count: 0,
      cache_miss_count: 0,
      cache_hit_rate: 0.0,
      cached_chars: 0,
      raw_chars: 0,
    };

    const elTotalReqs = document.getElementById('valTotalReqs');
    if (elTotalReqs) elTotalReqs.textContent = totalReqs.toLocaleString();
    const elSuccessReqs = document.getElementById('valSuccessReqs');
    if (elSuccessReqs) elSuccessReqs.textContent = successReqs.toLocaleString();
    const elFailedReqs = document.getElementById('valFailedReqs');
    if (elFailedReqs) elFailedReqs.textContent = failedReqs.toLocaleString();
    const elSuccessRate = document.getElementById('valSuccessRate');
    if (elSuccessRate) elSuccessRate.textContent = `${successRate}%`;

    // 2. Hero Card 2: Total Characters / Tokens
    const elTotalChars = document.getElementById('valTotalChars');
    if (elTotalChars) {
      const formattedChars = totalChars >= 1000000
        ? `${(totalChars / 1000000).toFixed(2)}M`
        : (totalChars >= 1000 ? `${(totalChars / 1000).toFixed(1)}K` : `${totalChars}`);
      elTotalChars.innerHTML = `${formattedChars} <span class="text-sm font-normal text-slate-400">chars</span>`;
    }

    const elCachedChars = document.getElementById('valCachedChars');
    if (elCachedChars) {
      const c = cacheMetrics.cached_chars ?? 0;
      elCachedChars.textContent = c >= 1000000 ? `${(c / 1000000).toFixed(2)}M` : (c >= 1000 ? `${(c / 1000).toFixed(1)}K` : `${c}`);
    }

    const elRawChars = document.getElementById('valRawChars');
    if (elRawChars) {
      const r = cacheMetrics.raw_chars ?? 0;
      elRawChars.textContent = r >= 1000000 ? `${(r / 1000000).toFixed(2)}M` : (r >= 1000 ? `${(r / 1000).toFixed(1)}K` : `${r}`);
    }

    const elAudioDuration = document.getElementById('valAudioDuration');
    if (elAudioDuration) {
      const hoursEst = (totalChars / 54000).toFixed(1);
      elAudioDuration.textContent = `${hoursEst}h`;
    }

    // 3. Small Cards
    const rpmVal = data.rpm ?? 0.0;
    const cpmVal = data.cpm ?? 0;
    const cacheRate = cacheMetrics.cache_hit_rate ?? 0.0;

    const elRPM = document.getElementById('valRPM');
    if (elRPM) elRPM.innerHTML = `${Number(rpmVal).toFixed(2)} <span class="text-xs font-normal text-slate-400">req/m</span>`;
    const elSubRPM = document.getElementById('valSubRPM');
    if (elSubRPM) elSubRPM.textContent = `Window: ${hours}h`;

    const elTPM = document.getElementById('valTPM');
    if (elTPM) elTPM.innerHTML = `${Number(cpmVal).toLocaleString()} <span class="text-xs font-normal text-slate-400">cpm</span>`;

    const elCacheRate = document.getElementById('valCacheRate');
    if (elCacheRate) elCacheRate.textContent = `${cacheRate}%`;
    const elSubCache = document.getElementById('valSubCache');
    if (elSubCache) elSubCache.textContent = `Hit: ${cacheMetrics.cache_hit_count ?? 0} • Miss: ${cacheMetrics.cache_miss_count ?? 0}`;

    const elCost = document.getElementById('valCost');
    if (elCost) {
      const estCost = ((totalChars / 1000000) * 0.15).toFixed(2);
      elCost.textContent = `$${estCost}`;
    }

    // Render Charts
    initSparklines(data);
    initHeatmaps(data);
    initMainTrendsChart(data);

    // Sync Key filter options if needed
    if (keySelect && data.usage_by_key) {
      const currentVal = keySelect.value;
      const optionsHtml = '<option value="all">All Keys</option>' +
        data.usage_by_key.map(k => `<option value="${k.name}">${k.name}</option>`).join('');
      if (keySelect.innerHTML !== optionsHtml) {
        keySelect.innerHTML = optionsHtml;
        keySelect.value = currentVal;
      }
    }

    populateEventsFilterDropdowns();
  } catch (err) {
    console.error('fetchMetrics failed', err);
  }
}

// Backwards compatibility alias
function renderMetricsCharts(data) {
  initSparklines(data);
  initMainTrendsChart(data);
}

function initSparklines(data) {
  const buckets = (data?.time_buckets && data.time_buckets.length === 32)
    ? data.time_buckets
    : Array.from({ length: 32 }, (_, i) => ({
        index: i,
        label: `${String(Math.floor((i / 32) * 24)).padStart(2, '0')}:00`,
        requests: 0,
        chars: 0,
        cache_hits: 0,
      }));

  const timeLabels = buckets.map(b => b.label);
  const reqPoints = buckets.map(b => b.requests);
  const tokenPoints = buckets.map(b => Number((b.chars / 1000000).toFixed(4)));

  // 1. Requests Sparkline
  const ctxReq = document.getElementById('sparklineRequests');
  if (ctxReq) {
    if (charts.sparklineReq) charts.sparklineReq.destroy();
    charts.sparklineReq = new Chart(ctxReq, {
      type: 'line',
      data: {
        labels: timeLabels,
        datasets: [{
          data: reqPoints,
          borderColor: '#3b82f6',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#3b82f6',
          pointHoverBorderColor: '#ffffff',
          tension: 0.45,
          cubicInterpolationMode: 'monotone',
          fill: { target: 'origin', above: 'rgba(59, 130, 246, 0.15)' },
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              title: (items) => `Mốc: ${items[0].label}`,
              label: (item) => ` Requests: ${item.raw} reqs`,
            },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  // 2. Tokens/Chars Sparkline
  const ctxTokens = document.getElementById('sparklineTokens');
  if (ctxTokens) {
    if (charts.sparklineToken) charts.sparklineToken.destroy();
    charts.sparklineToken = new Chart(ctxTokens, {
      type: 'line',
      data: {
        labels: timeLabels,
        datasets: [{
          data: tokenPoints,
          borderColor: '#8b5cf6',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#8b5cf6',
          pointHoverBorderColor: '#ffffff',
          tension: 0.45,
          cubicInterpolationMode: 'monotone',
          fill: { target: 'origin', above: 'rgba(139, 92, 246, 0.15)' },
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              title: (items) => `Mốc: ${items[0].label}`,
              label: (item) => ` Khối lượng: ${Number(item.raw).toFixed(4)}M chars`,
            },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  // 3. RPM Sparkline
  const recent12 = buckets.slice(-16);
  const recentLabels = recent12.map(b => b.label);
  const bucketMin = Math.max(1, (24 * 60) / 32);

  const ctxRPM = document.getElementById('sparklineRPM');
  if (ctxRPM) {
    if (charts.sparklineRPM) charts.sparklineRPM.destroy();
    const rpmData = recent12.map(b => Number((b.requests / bucketMin).toFixed(2)));
    charts.sparklineRPM = new Chart(ctxRPM, {
      type: 'line',
      data: {
        labels: recentLabels,
        datasets: [{
          data: rpmData,
          borderColor: '#10b981',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.45,
          cubicInterpolationMode: 'monotone',
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: { label: (i) => ` RPM: ${Number(i.raw).toFixed(2)} req/m` },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  // 4. TPM Sparkline
  const ctxTPM = document.getElementById('sparklineTPM');
  if (ctxTPM) {
    if (charts.sparklineTPM) charts.sparklineTPM.destroy();
    const tpmData = recent12.map(b => Math.round(b.chars / bucketMin));
    charts.sparklineTPM = new Chart(ctxTPM, {
      type: 'line',
      data: {
        labels: recentLabels,
        datasets: [{
          data: tpmData,
          borderColor: '#f97316',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.45,
          cubicInterpolationMode: 'monotone',
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: { label: (i) => ` CPM: ${Math.round(i.raw).toLocaleString()} cpm` },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  // 5. Cache Rate Sparkline
  const ctxCache = document.getElementById('sparklineCache');
  if (ctxCache) {
    if (charts.sparklineCache) charts.sparklineCache.destroy();
    const cacheData = recent12.map(b => b.requests > 0 ? Number(((b.cache_hits / b.requests) * 100).toFixed(1)) : 0);
    charts.sparklineCache = new Chart(ctxCache, {
      type: 'line',
      data: {
        labels: recentLabels,
        datasets: [{
          data: cacheData,
          borderColor: '#14b8a6',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.45,
          cubicInterpolationMode: 'monotone',
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: { label: (i) => ` Tỷ lệ cache: ${i.raw}%` },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  // 6. Cost Sparkline
  const ctxCost = document.getElementById('sparklineCost');
  if (ctxCost) {
    if (charts.sparklineCost) charts.sparklineCost.destroy();
    const costData = recent12.map(b => Number(((b.chars / 1000000) * 0.15).toFixed(4)));
    charts.sparklineCost = new Chart(ctxCost, {
      type: 'line',
      data: {
        labels: recentLabels,
        datasets: [{
          data: costData,
          borderColor: '#f59e0b',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.45,
          cubicInterpolationMode: 'monotone',
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: { label: (i) => ` Chi phí: $${Number(i.raw).toFixed(4)}` },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }
}

// ── RECENT ACTIVITY HEATMAPS (Keeper Style 7-Row Grid) ──

function setActivityWindow(win) {
  activityWindow = win;
  const dayBtn = document.getElementById('act-btn-day');
  const weekBtn = document.getElementById('act-btn-week');
  if (dayBtn && weekBtn) {
    if (win === 'day') {
      dayBtn.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-medium';
      weekBtn.className = 'px-2.5 py-1 text-slate-400 hover:text-white';
    } else {
      weekBtn.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-medium';
      dayBtn.className = 'px-2.5 py-1 text-slate-400 hover:text-white';
    }
  }
  if (activeMetricsData) {
    initHeatmaps(activeMetricsData);
  }
}

function initHeatmaps(data) {
  const tooltip = document.getElementById('heatmapTooltip');
  if (!tooltip) return;

  const totalChars = data?.total_chars ?? 0;
  const elSummary = document.getElementById('heatmapTotalSummary');
  if (elSummary) elSummary.textContent = `${totalChars.toLocaleString()} Chars`;

  const rawBuckets = data?.time_buckets || [];
  const numColumns = activityWindow === 'day' ? 24 : 28;
  const totalCells = numColumns * 7;

  // Window range label
  const winLabel = document.getElementById('activityWindowLabel');
  if (winLabel) {
    const now = new Date();
    const past = new Date(now.getTime() - (activityWindow === 'day' ? 24 : 168) * 3600 * 1000);
    const fmt = d => `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    winLabel.textContent = `${fmt(past)} – ${fmt(now)}`;
  }

  // Generate 7-row discrete time cells matching Keeper
  // Distribute bucket traffic evenly across discrete cells
  const activityCells = [];
  const baseBucketCount = rawBuckets.length || 32;

  for (let i = 0; i < totalCells; i++) {
    const col = Math.floor(i / 7);
    const row = i % 7;
    const bucketIdx = Math.floor((i / totalCells) * baseBucketCount);
    const sourceBucket = rawBuckets[bucketIdx] || { requests: 0, chars: 0, success_requests: 0, failed_requests: 0, avg_latency_ms: 0, label: '--:--' };

    // Subdivide bucket values into this cell slot
    const subFactor = baseBucketCount / totalCells;
    const cellChars = Math.round(sourceBucket.chars * subFactor);
    const cellReqs = Math.max(0, Math.round(sourceBucket.requests * subFactor));
    const cellSuccess = Math.max(0, Math.round(sourceBucket.success_requests * subFactor));
    const cellFailed = Math.max(0, Math.round(sourceBucket.failed_requests * subFactor));

    // Time label calculation
    const minutesPerCell = ((activityWindow === 'day' ? 24 : 168) * 60) / totalCells;
    const cellMinuteOffset = Math.round(i * minutesPerCell);
    const cellTime = new Date(Date.now() - ((activityWindow === 'day' ? 24 : 168) * 60 - cellMinuteOffset) * 60 * 1000);
    const cellTimeLabel = `${String(cellTime.getMonth() + 1).padStart(2, '0')}/${String(cellTime.getDate()).padStart(2, '0')} ${String(cellTime.getHours()).padStart(2, '0')}:${String(cellTime.getMinutes()).padStart(2, '0')}`;

    activityCells.push({
      index: i,
      col,
      row,
      timeLabel: cellTimeLabel,
      chars: cellChars,
      requests: cellReqs,
      success: cellSuccess,
      failed: cellFailed,
      latency: sourceBucket.avg_latency_ms || 0,
    });
  }

  // 1. Token Activity Heatmap
  const tokenContainer = document.getElementById('tokenActivityHeatmap');
  if (tokenContainer) {
    tokenContainer.innerHTML = '';
    const positiveChars = activityCells.map(c => c.chars).filter(v => v > 0).sort((a, b) => a - b);
    const p5 = positiveChars.length > 0 ? positiveChars[Math.floor(positiveChars.length * 0.05)] : 1;
    const p95 = positiveChars.length > 0 ? positiveChars[Math.floor(positiveChars.length * 0.95)] : 10;
    const logLow = Math.log1p(p5);
    const logRange = Math.max(0.1, Math.log1p(p95) - logLow);

    // Detail summary text
    const cachedChars = data?.cache_metrics?.cached_chars ?? 0;
    const rawChars = data?.cache_metrics?.raw_chars ?? 0;
    const detailSummary = document.getElementById('tokenHeatmapDetailSummary');
    if (detailSummary) {
      detailSummary.textContent = `${rawChars.toLocaleString()} chars raw • ${cachedChars.toLocaleString()} chars cache read`;
    }

    activityCells.forEach(cell => {
      let level = 0;
      if (cell.chars > 0) {
        const clamped = Math.max(p5, Math.min(cell.chars, p95));
        const ratio = (Math.log1p(clamped) - logLow) / logRange;
        level = Math.max(1, Math.min(5, 1 + Math.floor(ratio * 5)));
      }

      const cellEl = document.createElement('div');
      cellEl.className = 'heat-cell';
      cellEl.style.backgroundColor = `var(--token-level-${level})`;

      cellEl.addEventListener('mouseenter', (e) => {
        tooltip.style.display = 'block';
        tooltip.innerHTML = `
          <div class="text-blue-400 font-bold mb-1">${cell.timeLabel}</div>
          <div class="text-slate-300">Khối lượng: <b class="text-white mono">${cell.chars.toLocaleString()} chars</b></div>
          <div class="text-slate-300">Yêu cầu: <b class="text-white mono">${cell.requests} reqs</b></div>
        `;
        positionTooltip(e, tooltip);
      });
      cellEl.addEventListener('mouseleave', () => {
        tooltip.style.display = 'none';
      });

      tokenContainer.appendChild(cellEl);
    });
  }

  // 2. Service Health Timeline Heatmap
  const healthContainer = document.getElementById('healthTimelineGrid');
  if (healthContainer) {
    healthContainer.innerHTML = '';
    const totalSuccess = data?.success_requests ?? 0;
    const totalFailed = data?.failed_requests ?? 0;
    const totalReqs = data?.total_requests ?? 0;
    const successRate = totalReqs > 0 ? ((totalSuccess / totalReqs) * 100).toFixed(1) : '100.0';

    const healthRateSummary = document.getElementById('healthRateSummary');
    if (healthRateSummary) healthRateSummary.textContent = `${successRate}%`;

    const healthSuccessEl = document.getElementById('healthTotalSuccess');
    if (healthSuccessEl) healthSuccessEl.textContent = `${totalSuccess.toLocaleString()} success`;
    const healthFailedEl = document.getElementById('healthTotalFailed');
    if (healthFailedEl) healthFailedEl.textContent = `${totalFailed.toLocaleString()} failed`;

    activityCells.forEach(cell => {
      let healthLevel = 0;
      const totalCellReqs = cell.success + cell.failed;
      if (totalCellReqs > 0) {
        const rate = cell.success / totalCellReqs;
        if (rate < 0.5) healthLevel = 1;
        else if (rate < 0.65) healthLevel = 2;
        else if (rate < 0.8) healthLevel = 3;
        else if (rate < 0.95) healthLevel = 4;
        else healthLevel = 5;
      }

      const cellEl = document.createElement('div');
      cellEl.className = 'heat-cell';
      cellEl.style.backgroundColor = `var(--health-level-${healthLevel})`;

      cellEl.addEventListener('mouseenter', (e) => {
        tooltip.style.display = 'block';
        const hasReqs = cell.success + cell.failed > 0;
        const rateText = hasReqs ? `${((cell.success / (cell.success + cell.failed)) * 100).toFixed(1)}%` : 'No Traffic';
        const titleColor = healthLevel >= 4 ? 'text-emerald-400' : (healthLevel > 0 ? 'text-rose-400' : 'text-slate-400');

        tooltip.innerHTML = `
          <div class="${titleColor} font-bold mb-1">${cell.timeLabel} (${rateText})</div>
          <div class="text-slate-300">Thành công: <b class="text-emerald-400 mono">${cell.success}</b> | Lỗi: <b class="text-rose-400 mono">${cell.failed}</b></div>
          <div class="text-slate-300">Độ trễ trung bình: <b class="text-amber-400 mono">${cell.latency}ms</b></div>
        `;
        positionTooltip(e, tooltip);
      });
      cellEl.addEventListener('mouseleave', () => {
        tooltip.style.display = 'none';
      });

      healthContainer.appendChild(cellEl);
    });
  }
}

// ── USAGE DISTRIBUTION CHART ──

function initMainTrendsChart(data) {
  const ctx = document.getElementById('chartTrendsMain');
  if (!ctx) return;
  if (charts.mainTrends) charts.mainTrends.destroy();

  const buckets = (data?.time_buckets && data.time_buckets.length > 0)
    ? data.time_buckets
    : Array.from({ length: 12 }, (_, i) => ({
        label: `${String(i * 2).padStart(2, '0')}:00`,
        chars: 0,
        requests: 0,
      }));

  const timeLabels = buckets.map(b => b.label);
  const totalChars = data?.total_chars ?? 0;
  const charsData = buckets.map(b => Number((b.chars / 1000000).toFixed(4)));

  charts.mainTrends = new Chart(ctx, {
    type: 'line',
    data: {
      labels: timeLabels,
      datasets: [{
        label: 'VieNeu-TTS v3',
        data: charsData,
        borderColor: '#3b82f6',
        borderWidth: 2.5,
        pointRadius: 2.5,
        pointHoverRadius: 6,
        pointHoverBackgroundColor: '#3b82f6',
        pointHoverBorderColor: '#ffffff',
        pointHoverBorderWidth: 2,
        tension: 0.45,
          cubicInterpolationMode: 'monotone',
        fill: true,
        backgroundColor: 'rgba(59, 130, 246, 0.18)',
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...commonTooltipConfig,
          callbacks: {
            title: (items) => `Mốc thời gian: ${items[0].label}`,
            label: (item) => ` ${item.dataset.label}: ${item.raw}M chars (${Math.round(item.raw * 1000000).toLocaleString()} chars)`,
          },
        },
      },
      scales: {
        x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b' } },
        y: {
          grid: { color: 'rgba(255, 255, 255, 0.04)' },
          ticks: { color: '#64748b', callback: v => `${v}M` },
        },
      },
    },
  });

  // Render Legend
  const legendContainer = document.getElementById('trendsLegendContainer');
  if (legendContainer) {
    const formattedChars = totalChars >= 1000000
      ? `${(totalChars / 1000000).toFixed(2)}M`
      : (totalChars >= 1000 ? `${(totalChars / 1000).toFixed(1)}K` : `${totalChars}`);

    const workersList = data?.usage_by_model || [{ model: 'VieNeu-TTS v3', requests: data?.total_requests || 0, chars: totalChars }];

    legendContainer.innerHTML = workersList.map((w, idx) => {
      const colors = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b'];
      const color = colors[idx % colors.length];
      const share = totalChars > 0 ? ((w.chars / totalChars) * 100).toFixed(1) : '100.0';
      const wFormattedChars = w.chars >= 1000000 ? `${(w.chars / 1000000).toFixed(2)}M` : (w.chars >= 1000 ? `${(w.chars / 1000).toFixed(1)}K` : `${w.chars}`);

      return `
        <div class="hover:bg-slate-800/30 p-2.5 rounded-xl border border-slate-800/60 transition">
          <div class="flex items-center space-x-2 text-slate-200">
            <span class="w-2.5 h-2.5 rounded-full" style="background-color: ${color}"></span>
            <span class="font-bold text-xs tracking-tight truncate">${w.model}</span>
            <span class="px-2 py-0.5 rounded text-[10px] bg-blue-500/10 text-blue-400 border border-blue-500/20 font-semibold shrink-0">Inference</span>
          </div>
          <div class="mt-2 font-extrabold text-sm text-white mono">${wFormattedChars} <span class="text-xs text-emerald-400 font-semibold ml-1">${share}%</span></div>
        </div>
      `;
    }).join('');
  }
}

// ── REALTIME TAB METRICS & CHARTS ──

function setRealtimeWindow(minutes) {
  realtimeWindowMinutes = minutes;
  [15, 30, 60].forEach(m => {
    const btn = document.getElementById(`rt-btn-${m}`);
    if (btn) {
      if (m === minutes) {
        btn.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-medium';
      } else {
        btn.className = 'px-2.5 py-1 text-slate-400 hover:text-white';
      }
    }
  });
  [1, 2, 3, 4].forEach(i => {
    const label = document.getElementById(`rtWindowLabel${i}`);
    if (label) label.textContent = `${minutes}m`;
  });
  fetchRealtimeMetrics();
}

function setDimensionTab(dim) {
  currentActiveDimension = dim;
  const modBtn = document.getElementById('rtDimTabModels');
  const keyBtn = document.getElementById('rtDimTabKeys');
  if (modBtn && keyBtn) {
    if (dim === 'models') {
      modBtn.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-medium';
      keyBtn.className = 'px-2.5 py-1 text-slate-400 hover:text-white';
    } else {
      keyBtn.className = 'px-2.5 py-1 rounded bg-slate-800 text-white font-medium';
      modBtn.className = 'px-2.5 py-1 text-slate-400 hover:text-white';
    }
  }
  renderDimensionRibbons();
}

async function fetchRealtimeMetrics(isSilent = false) {
  try {
    const hours = Math.max(1, Math.ceil(realtimeWindowMinutes / 60));
    const res = await apiRequest(`/v1/management/metrics/summary?hours=${hours}`);
    if (!res.ok) return;
    const data = await res.json();

    const totalReqs = data.total_requests ?? 0;
    const totalChars = data.total_chars ?? 0;
    const cacheMetrics = data.cache_metrics || { cache_hit_rate: 0.0 };

    const elReq = document.getElementById('rtRequests');
    if (elReq) elReq.textContent = totalReqs.toLocaleString();
    const elTok = document.getElementById('rtTokens');
    if (elTok) elTok.textContent = totalChars >= 1000000 ? `${(totalChars / 1000000).toFixed(2)}M` : totalChars.toLocaleString();
    const elCache = document.getElementById('rtCacheReach');
    if (elCache) elCache.textContent = `${cacheMetrics.cache_hit_rate ?? 0}%`;
    const elCost = document.getElementById('rtCost');
    if (elCost) elCost.textContent = `$${((totalChars / 1000000) * 0.15).toFixed(2)}`;

    initRealtimeCharts(data);
    renderDimensionRibbons();
  } catch (e) {
    console.error('fetchRealtimeMetrics failed', e);
  }
}

function initRealtimeCharts(data) {
  const buckets = (data?.time_buckets && data.time_buckets.length > 0) ? data.time_buckets : [];
  const pointCount = realtimeWindowMinutes === 15 ? 8 : (realtimeWindowMinutes === 30 ? 12 : 16);
  const recentBuckets = buckets.slice(-pointCount);

  const throughputLabels = recentBuckets.length > 0
    ? recentBuckets.map(b => b.label)
    : Array.from({ length: 8 }, (_, i) => `${String(12 + i).padStart(2, '0')}:00`);

  const bucketDurationMin = Math.max(0.1, (realtimeWindowMinutes || 60) / 32);
  const tpmPoints = recentBuckets.length > 0 ? recentBuckets.map(b => Math.round(b.chars / bucketDurationMin)) : Array(8).fill(0);
  const rpmPoints = recentBuckets.length > 0 ? recentBuckets.map(b => Number((b.requests / bucketDurationMin).toFixed(2))) : Array(8).fill(0);

  // Update Metric chips
  const latestTPM = tpmPoints[tpmPoints.length - 1] || 0;
  const avgTPM = tpmPoints.length > 0 ? Math.round(tpmPoints.reduce((a, b) => a + b, 0) / tpmPoints.length) : 0;
  const half = Math.floor(tpmPoints.length / 2);
  const firstHalfAvg = half > 0 ? tpmPoints.slice(0, half).reduce((a, b) => a + b, 0) / half : 1;
  const secondHalfAvg = half > 0 ? tpmPoints.slice(half).reduce((a, b) => a + b, 0) / (tpmPoints.length - half) : 1;
  const trendPct = firstHalfAvg > 0 ? (((secondHalfAvg - firstHalfAvg) / firstHalfAvg) * 100).toFixed(1) : '0.0';

  const elLatest = document.getElementById('rtThroughputLatest');
  if (elLatest) elLatest.textContent = `Latest: ${latestTPM.toLocaleString()} cpm`;
  const elAvg = document.getElementById('rtThroughputAvg');
  if (elAvg) elAvg.textContent = `Avg: ${avgTPM.toLocaleString()} cpm`;
  const elTrend = document.getElementById('rtThroughputTrend');
  if (elTrend) {
    elTrend.textContent = `Trend: ${trendPct >= 0 ? '+' : ''}${trendPct}%`;
    elTrend.className = `metric-chip ${Number(trendPct) >= 0 ? 'metric-chip-up' : 'metric-chip-down'}`;
  }

  // 1. Throughput Dual Axis Chart
  const ctxThroughput = document.getElementById('chartThroughput');
  if (ctxThroughput) {
    if (charts.throughput) charts.throughput.destroy();
    charts.throughput = new Chart(ctxThroughput, {
      type: 'line',
      data: {
        labels: throughputLabels,
        datasets: [
          {
            label: 'Chars per min (TPM)',
            data: tpmPoints,
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59, 130, 246, 0.15)',
            borderWidth: 2.5,
            pointRadius: 2.5,
            pointHoverRadius: 6,
            tension: 0.45,
          cubicInterpolationMode: 'monotone',
            fill: true,
            yAxisID: 'y',
          },
          {
            label: 'Requests per min (RPM)',
            data: rpmPoints,
            borderColor: '#f97316',
            backgroundColor: '#f97316',
            borderWidth: 2,
            borderDash: [5, 4],
            pointRadius: 2.5,
            pointHoverRadius: 6,
            tension: 0.45,
          cubicInterpolationMode: 'monotone',
            fill: false,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: { color: '#94a3b8', font: { size: 11 }, boxWidth: 12 },
          },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              label: (i) => i.datasetIndex === 0
                ? ` ${i.dataset.label}: ${i.raw.toLocaleString()} chars/min`
                : ` ${i.dataset.label}: ${i.raw} reqs/min`,
            },
          },
        },
        scales: {
          x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b' } },
          y: {
            position: 'left',
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { color: '#93c5fd', callback: v => `${v}` },
          },
          y1: {
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: '#fdba74', callback: v => `${v} r/m` },
          },
        },
      },
    });
  }

  // 2. Request Outcomes Stacked Bar
  const ctxOutcomes = document.getElementById('chartOutcomes');
  if (ctxOutcomes) {
    if (charts.outcomes) charts.outcomes.destroy();
    const outcomeBuckets = buckets.slice(-8);
    const outcomeLabels = outcomeBuckets.length > 0 ? outcomeBuckets.map(b => b.label) : ['12:00', '13:00', '14:00', '15:00'];
    const successPoints = outcomeBuckets.length > 0 ? outcomeBuckets.map(b => b.success_requests) : [0, 0, 0, 0];
    const failedPoints = outcomeBuckets.length > 0 ? outcomeBuckets.map(b => b.failed_requests) : [0, 0, 0, 0];
    const failRatePoints = outcomeBuckets.length > 0
      ? outcomeBuckets.map(b => b.requests > 0 ? Number(((b.failed_requests / b.requests) * 100).toFixed(1)) : 0)
      : [0, 0, 0, 0];

    const totalSucc = successPoints.reduce((a, b) => a + b, 0);
    const totalFail = failedPoints.reduce((a, b) => a + b, 0);
    const totalAll = totalSucc + totalFail;
    const overallFailRate = totalAll > 0 ? ((totalFail / totalAll) * 100).toFixed(1) : '0.0';

    const elSucc = document.getElementById('rtOutcomesSuccess');
    if (elSucc) elSucc.textContent = totalSucc.toLocaleString();
    const elFail = document.getElementById('rtOutcomesFailed');
    if (elFail) elFail.textContent = totalFail.toLocaleString();
    const elRate = document.getElementById('rtOutcomesRate');
    if (elRate) elRate.textContent = `${overallFailRate}%`;

    charts.outcomes = new Chart(ctxOutcomes, {
      type: 'bar',
      data: {
        labels: outcomeLabels,
        datasets: [
          {
            type: 'bar',
            label: 'Successful Requests',
            data: successPoints,
            backgroundColor: '#10b981',
            borderRadius: 3,
            stack: 'requests',
            order: 2,
          },
          {
            type: 'bar',
            label: 'Failed Requests',
            data: failedPoints,
            backgroundColor: '#f43f5e',
            borderRadius: 3,
            stack: 'requests',
            order: 2,
          },
          {
            type: 'line',
            label: 'Failure Rate %',
            data: failRatePoints,
            borderColor: '#f97316',
            backgroundColor: '#f97316',
            borderWidth: 2,
            borderDash: [4, 4],
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.45,
          cubicInterpolationMode: 'monotone',
            yAxisID: 'rate',
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 10 },
          },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              label: (item) => item.dataset.yAxisID === 'rate'
                ? ` ${item.dataset.label}: ${item.raw}%`
                : ` ${item.dataset.label}: ${item.raw} reqs`,
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#64748b' } },
          y: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b' } },
          rate: {
            position: 'right',
            beginAtZero: true,
            max: 100,
            grid: { drawOnChartArea: false },
            ticks: { color: '#fdba74', callback: v => `${v}%` },
          },
        },
      },
    });
  }

  // 3. Token & Cache Composition Donut
  const ctxComp = document.getElementById('chartComposition');
  if (ctxComp) {
    if (charts.composition) charts.composition.destroy();
    const hitRate = Number(data?.cache_metrics?.cache_hit_rate ?? 0.0);
    const rawRate = Number(Math.max(0, 100.0 - hitRate).toFixed(1));
    const cachedChars = data?.cache_metrics?.cached_chars ?? 0;
    const rawChars = data?.cache_metrics?.raw_chars ?? 0;
    const totalChars = data?.total_chars ?? 0;
    const audioSec = Math.round(totalChars / 15);

    const elDonutVal = document.getElementById('donutCenterVal');
    if (elDonutVal) elDonutVal.textContent = `${hitRate}%`;
    const elDonutCache = document.getElementById('donutCacheShare');
    if (elDonutCache) elDonutCache.textContent = `${cachedChars.toLocaleString()} chars (${hitRate}%)`;
    const elDonutRaw = document.getElementById('donutRawShare');
    if (elDonutRaw) elDonutRaw.textContent = `${rawChars.toLocaleString()} chars (${rawRate}%)`;
    const elDonutAudio = document.getElementById('donutAudioShare');
    if (elDonutAudio) elDonutAudio.textContent = `${audioSec}s audio`;

    charts.composition = new Chart(ctxComp, {
      type: 'doughnut',
      data: {
        labels: ['Cache Read (SHA-256 Hit)', 'VieNeu-TTS v3 Raw Inference'],
        datasets: [{
          data: totalChars === 0 ? [100, 0] : [cachedChars, rawChars],
          backgroundColor: ['#14b8a6', '#3b82f6'],
          hoverBackgroundColor: ['#0d9488', '#2563eb'],
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '72%',
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              label: (item) => ` ${item.label}: ${item.raw.toLocaleString()} chars`,
            },
          },
        },
      },
    });
  }

  // 4. Latency Diagnostics Chart
  const ctxLatency = document.getElementById('chartLatencyRealtime');
  if (ctxLatency) {
    if (charts.latencyRealtime) charts.latencyRealtime.destroy();
    const latencyBuckets = buckets.slice(-12);
    const latencyLabels = latencyBuckets.length > 0 ? latencyBuckets.map(b => b.label) : ['12:00', '13:00', '14:00'];
    const avgLatencyPoints = latencyBuckets.length > 0 ? latencyBuckets.map(b => b.avg_latency_ms || 0) : [0, 0, 0];
    const p95LatencyPoints = avgLatencyPoints.map(l => Math.round(l * 1.35));

    const avgTotal = avgLatencyPoints.length > 0 ? Math.round(avgLatencyPoints.reduce((a, b) => a + b, 0) / avgLatencyPoints.length) : 0;
    const p95Total = Math.round(avgTotal * 1.35);

    const elP95 = document.getElementById('rtP95Latency');
    if (elP95) elP95.textContent = `${p95Total}ms`;
    const elAvgLat = document.getElementById('rtAvgLatency');
    if (elAvgLat) elAvgLat.textContent = `${avgTotal}ms`;
    const elSamples = document.getElementById('rtLatencySamples');
    if (elSamples) elSamples.textContent = (data?.total_requests || 0).toLocaleString();

    charts.latencyRealtime = new Chart(ctxLatency, {
      type: 'line',
      data: {
        labels: latencyLabels,
        datasets: [
          {
            label: 'Avg Latency (ms)',
            data: avgLatencyPoints,
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59, 130, 246, 0.12)',
            borderWidth: 2,
            pointRadius: 2,
            pointHoverRadius: 5,
            tension: 0.45,
          cubicInterpolationMode: 'monotone',
            fill: true,
          },
          {
            label: 'P95 Latency (ms)',
            data: p95LatencyPoints,
            borderColor: '#f59e0b',
            borderWidth: 2,
            borderDash: [5, 4],
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.45,
          cubicInterpolationMode: 'monotone',
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 10 },
          },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              label: (item) => ` ${item.dataset.label}: ${item.raw}ms`,
            },
          },
        },
        scales: {
          x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b' } },
          y: {
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { color: '#64748b', callback: v => `${v}ms` },
          },
        },
      },
    });
  }
}

function renderDimensionRibbons() {
  const container = document.getElementById('rtDimensionContainer');
  if (!container) return;

  const data = activeMetricsData;
  const items = currentActiveDimension === 'models'
    ? (data?.usage_by_model || [{ model: 'VieNeu-TTS v3', requests: data?.total_requests || 0, chars: data?.total_chars || 0 }])
    : (data?.usage_by_key || [{ name: 'Default Client Key', requests: data?.total_requests || 0, chars: data?.total_chars || 0 }]);

  const totalChars = items.reduce((sum, item) => sum + (item.chars || 0), 0) || 1;
  const colors = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ec4899'];

  if (items.length === 0) {
    container.innerHTML = `<div class="text-center py-6 text-slate-500 text-xs">Không có dữ liệu trong cửa sổ này.</div>`;
    return;
  }

  container.innerHTML = items.map((item, idx) => {
    const label = currentActiveDimension === 'models' ? item.model : item.name;
    const chars = item.chars || 0;
    const pct = ((chars / totalChars) * 100).toFixed(1);
    const color = colors[idx % colors.length];

    return `
      <div>
        <div class="flex items-center justify-between text-xs mb-1">
          <span class="font-medium text-slate-200 truncate max-w-[200px]">${label}</span>
          <div class="flex items-center space-x-2">
            <span class="mono text-slate-400">${chars.toLocaleString()} chars</span>
            <span class="font-bold text-white mono">${pct}%</span>
          </div>
        </div>
        <div class="usage-ribbon">
          <div class="usage-ribbon-fill" style="width: ${pct}%; background-color: ${color}"></div>
        </div>
      </div>
    `;
  }).join('');
}

// ── TAB 3: REQUEST EVENTS TABLE, SEARCH, PAGINATION & EXPORT ──

function populateEventsFilterDropdowns() {
  const logs = activeMetricsData?.recent_logs || [];

  // 1. Worker model options
  const modelSelect = document.getElementById('eventFilterModel');
  if (modelSelect) {
    const currentVal = modelSelect.value;
    const workers = Array.from(new Set(logs.map(l => l.worker || 'VieNeu-TTS v3')));
    modelSelect.innerHTML = '<option value="all">All Workers</option>' +
      workers.map(w => `<option value="${w}">${w}</option>`).join('');
    modelSelect.value = currentVal;
  }

  // 2. Client API Key options
  const keySelect = document.getElementById('eventFilterApiKey');
  if (keySelect) {
    const currentVal = keySelect.value;
    const keys = Array.from(new Set(logs.map(l => l.key_name || 'Anonymous')));
    keySelect.innerHTML = '<option value="all">All Keys</option>' +
      keys.map(k => `<option value="${k}">${k}</option>`).join('');
    keySelect.value = currentVal;
  }
}

function onEventSearchInput() {
  const input = document.getElementById('eventSearchInput');
  eventsSearchKeyword = (input?.value || '').trim().toLowerCase();
  eventsCurrentPage = 1;
  renderEventsTable();
}

function onEventPageSizeChange() {
  const select = document.getElementById('eventPageSize');
  if (select) eventsPageSize = parseInt(select.value, 10);
  eventsCurrentPage = 1;
  renderEventsTable();
}

function changeEventPage(direction) {
  eventsCurrentPage += direction;
  renderEventsTable();
}

function getFilteredEvents() {
  const logs = activeMetricsData?.recent_logs || [];
  const modelFilter = document.getElementById('eventFilterModel')?.value || 'all';
  const keyFilter = document.getElementById('eventFilterApiKey')?.value || 'all';
  const statusFilter = document.getElementById('eventFilterStatus')?.value || 'all';

  return logs.filter(log => {
    if (modelFilter !== 'all' && (log.worker || 'unknown') !== modelFilter) return false;
    if (keyFilter !== 'all' && (log.key_name || 'Anonymous') !== keyFilter) return false;
    if (statusFilter === 'success' && (log.status >= 400 || log.is_cache_hit)) return false;
    if (statusFilter === 'cache' && !log.is_cache_hit) return false;
    if (statusFilter === 'error' && log.status < 400) return false;

    if (eventsSearchKeyword) {
      const matchKey = (log.key_name || '').toLowerCase().includes(eventsSearchKeyword);
      const matchEp = (log.endpoint || '').toLowerCase().includes(eventsSearchKeyword);
      const matchWorker = (log.worker || '').toLowerCase().includes(eventsSearchKeyword);
      const matchId = String(log.id).includes(eventsSearchKeyword);
      if (!matchKey && !matchEp && !matchWorker && !matchId) return false;
    }

    return true;
  });
}

function renderEventsTable() {
  const tbody = document.getElementById('eventsTableBody');
  if (!tbody) return;

  const filtered = getFilteredEvents();
  const totalItems = filtered.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / eventsPageSize));
  eventsCurrentPage = Math.min(eventsCurrentPage, totalPages);
  eventsCurrentPage = Math.max(1, eventsCurrentPage);

  // Update pagination indicators
  const pageLabel = document.getElementById('eventCurrentPageLabel');
  if (pageLabel) pageLabel.textContent = `${eventsCurrentPage} / ${totalPages}`;

  const infoLabel = document.getElementById('eventsPageInfo');
  if (infoLabel) {
    const startIdx = totalItems === 0 ? 0 : (eventsCurrentPage - 1) * eventsPageSize + 1;
    const endIdx = Math.min(totalItems, eventsCurrentPage * eventsPageSize);
    infoLabel.textContent = `${startIdx} - ${endIdx} trong số ${totalItems}`;
  }

  const prevBtn = document.getElementById('eventPrevBtn');
  if (prevBtn) prevBtn.disabled = (eventsCurrentPage <= 1);
  const nextBtn = document.getElementById('eventNextBtn');
  if (nextBtn) nextBtn.disabled = (eventsCurrentPage >= totalPages);

  if (totalItems === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center py-8 text-slate-500 text-xs">Không tìm thấy sự kiện nào phù hợp với bộ lọc.</td></tr>`;
    return;
  }

  const pageLogs = filtered.slice((eventsCurrentPage - 1) * eventsPageSize, eventsCurrentPage * eventsPageSize);

  tbody.innerHTML = pageLogs.map(log => {
    const isSuccess = log.status >= 200 && log.status < 400;
    const badgeColor = log.is_cache_hit
      ? 'bg-teal-500/10 text-teal-400 border-teal-500/20'
      : (isSuccess ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20');
    const badgeText = log.is_cache_hit ? 'Cache Hit' : (isSuccess ? `Success (${log.status})` : `Failed (${log.status})`);
    const timeStr = log.time ? log.time.replace('T', ' ').slice(5, 19) : '--:--:--';

    return `
      <tr class="k-table-row border-b border-slate-800/40">
        <td class="py-3 px-4 mono text-slate-300">${timeStr}</td>
        <td class="py-3 px-4 font-mono text-slate-300 font-medium">${log.key_name || 'Anonymous'}</td>
        <td class="py-3 px-4 font-semibold text-blue-400">${log.worker || 'VieNeu-TTS v3'}</td>
        <td class="py-3 px-4">
          <span class="px-2 py-0.5 rounded text-[11px] font-semibold border ${badgeColor}">
            ${badgeText}
          </span>
        </td>
        <td class="py-3 px-4 mono text-slate-400">${log.endpoint || '/v1/speech/synthesize'}</td>
        <td class="py-3 px-4 mono text-amber-400 font-medium">${log.latency_ms}ms</td>
        <td class="py-3 px-4 mono text-white">${log.chars || 0}</td>
        <td class="py-3 px-4 mono ${log.is_cache_hit ? 'text-teal-400 font-bold' : 'text-slate-600'}">
          ${log.is_cache_hit ? 'YES' : 'NO'}
        </td>
        <td class="py-3 px-4 text-right">
          <button onclick="openEventDetailModal(${log.id})" class="text-indigo-400 hover:text-indigo-300 p-1" title="Xem chi tiết sự kiện">
            <i class="fa-solid fa-magnifying-glass-plus"></i>
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function clearEventFilters() {
  const m = document.getElementById('eventFilterModel');
  if (m) m.value = 'all';
  const k = document.getElementById('eventFilterApiKey');
  if (k) k.value = 'all';
  const s = document.getElementById('eventFilterStatus');
  if (s) s.value = 'all';
  const q = document.getElementById('eventSearchInput');
  if (q) q.value = '';
  eventsSearchKeyword = '';
  eventsCurrentPage = 1;
  renderEventsTable();
}

function exportEventsCsv() {
  const logs = getFilteredEvents();
  if (logs.length === 0) {
    showToast('Chưa có dữ liệu để xuất CSV!', 'info');
    return;
  }
  const headers = ['ID', 'Timestamp', 'ClientKey', 'Worker', 'Status', 'Endpoint', 'LatencyMs', 'Chars', 'CacheHit'];
  const rows = logs.map(l => [
    l.id,
    `"${l.time}"`,
    `"${(l.key_name || '').replace(/"/g, '""')}"`,
    `"${(l.worker || '').replace(/"/g, '""')}"`,
    l.status,
    `"${(l.endpoint || '').replace(/"/g, '""')}"`,
    l.latency_ms,
    l.chars,
    l.is_cache_hit ? 1 : 0
  ].join(','));
  const csvContent = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', `voicecloner-events-${new Date().toISOString().slice(0, 10)}.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast('Đã xuất file CSV thành công!', 'success');
}

function exportEventsJson() {
  const logs = getFilteredEvents();
  if (logs.length === 0) {
    showToast('Chưa có dữ liệu để xuất JSON!', 'info');
    return;
  }
  const jsonContent = JSON.stringify(logs, null, 2);
  const blob = new Blob([jsonContent], { type: 'application/json;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', `voicecloner-events-${new Date().toISOString().slice(0, 10)}.json`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast('Đã xuất file JSON thành công!', 'success');
}

function openEventDetailModal(logId) {
  const logs = activeMetricsData?.recent_logs || [];
  const log = logs.find(l => l.id === logId);
  if (!log) return;

  const modal = document.getElementById('eventDetailModal');
  if (!modal) return;

  document.getElementById('eventDetailId').textContent = log.id;
  document.getElementById('eventDetailTime').textContent = log.time ? log.time.replace('T', ' ') : '--';
  document.getElementById('eventDetailKey').textContent = log.key_name || 'Anonymous';
  document.getElementById('eventDetailWorker').textContent = log.worker || 'VieNeu-TTS v3';
  document.getElementById('eventDetailLatency').textContent = `${log.latency_ms}ms`;
  document.getElementById('eventDetailChars').textContent = `${log.chars || 0} chars`;

  const isSuccess = log.status >= 200 && log.status < 400;
  const badgeColor = log.is_cache_hit ? 'bg-teal-500/10 text-teal-400 border-teal-500/20' : (isSuccess ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20');
  const badgeText = log.is_cache_hit ? 'Cache Hit' : (isSuccess ? `Status ${log.status}` : `Failed ${log.status}`);

  document.getElementById('eventDetailBadge').innerHTML = `
    <span class="px-2.5 py-1 rounded text-xs font-semibold border ${badgeColor}">${badgeText}</span>
  `;

  document.getElementById('eventDetailJson').textContent = JSON.stringify(log, null, 2);
  modal.classList.remove('hidden');
}

function closeEventDetailModal() {
  document.getElementById('eventDetailModal')?.classList.add('hidden');
}

function copyEventJson() {
  const text = document.getElementById('eventDetailJson')?.textContent || '';
  navigator.clipboard.writeText(text);
  showToast('Đã sao chép JSON vào clipboard!', 'success');
}

// ── TAB 4: VOICE WORKERS MANAGEMENT ──

async function fetchWorkers() {
  const container = document.getElementById('workersContainer');
  if (!container) return;
  try {
    const res = await apiRequest('/v1/management/workers');
    if (!res.ok) return;
    const workers = await res.json();

    if (workers.length === 0) {
      container.innerHTML = `
        <div class="k-card p-8 text-center text-slate-500 text-xs">
          <p>Chưa cấu hình worker node nào.</p>
          <button onclick="openCreateWorkerModal()" class="mt-3 px-3.5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold inline-flex items-center space-x-1.5">
            <i class="fa-solid fa-plus"></i><span>Thêm Worker Đầu Tiên</span>
          </button>
        </div>
      `;
      return;
    }

    container.innerHTML = workers.map(w => {
      const isOnline = w.telemetry && w.telemetry.status === 'online';
      const statusColor = isOnline ? 'emerald' : 'rose';
      const latencyStr = isOnline && w.telemetry.latency_ms ? `${w.telemetry.latency_ms}ms` : (isOnline ? 'online' : 'offline');

      return `
        <div class="k-card p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 hover:border-slate-700 transition">
          <div class="flex items-center space-x-4">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-emerald-600 to-teal-500 flex items-center justify-center text-white shrink-0 shadow-md shadow-emerald-500/20">
              <i class="fa-solid fa-microchip"></i>
            </div>
            <div>
              <div class="flex items-center space-x-2">
                <span class="font-bold text-sm text-white">${w.id}</span>
                <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-${statusColor}-500/10 text-${statusColor}-400 border border-${statusColor}-500/20">
                  ${isOnline ? 'Online' : 'Offline'} • ${latencyStr}
                </span>
                <span class="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300 font-mono">Weight: ${w.weight}</span>
              </div>
              <span class="text-xs text-slate-400 font-mono mt-0.5 block">${w.url}</span>
            </div>
          </div>
          <div class="flex flex-wrap items-center space-x-2 text-xs w-full md:w-auto justify-end">
            <button onclick="pingWorkerTest('${w.url}', '')" class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center space-x-1">
              <i class="fa-solid fa-bolt text-amber-400"></i>
              <span>Test Ping</span>
            </button>
            <button onclick="openEditWorkerModal('${w.id}', '${w.url}', '${w.weight || 1}', ${w.is_active})" class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center space-x-1">
              <i class="fa-solid fa-pen-to-square text-indigo-400"></i>
              <span>Sửa</span>
            </button>
            <button onclick="toggleWorkerActive('${w.id}', ${!w.is_active})" class="px-3 py-1.5 rounded-lg text-xs font-semibold transition ${w.is_active ? 'bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30 border border-emerald-500/30' : 'bg-slate-800 hover:bg-slate-700 text-slate-400'}">
              ${w.is_active ? 'Đang Bật' : 'Tạm Tắt'}
            </button>
            <button onclick="deleteWorker('${w.id}')" class="px-2.5 py-1.5 rounded-lg text-xs font-medium bg-rose-600/10 hover:bg-rose-600/20 text-rose-400 border border-rose-500/20 transition" title="Xóa Worker">
              <i class="fa-solid fa-trash-can"></i>
            </button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('fetchWorkers failed', err);
  }
}

async function pingWorkerTest(url, apiKey) {
  try {
    showToast(`Đang ping kiểm tra ${url}...`, 'info');
    const res = await apiRequest('/v1/management/workers/test', {
      method: 'POST',
      body: JSON.stringify({ url, api_key: apiKey || '' }),
    });
    if (res.ok) {
      const ping = await res.json();
      if (ping.status === 'online') {
        showToast(`Worker online! Độ trễ: ${ping.latency_ms || 0}ms`, 'success');
      } else {
        showToast(`Worker không phản hồi: ${ping.error || 'Offline'}`, 'error');
      }
      fetchWorkers();
    }
  } catch (e) {
    showToast('Lỗi ping worker', 'error');
  }
}

function openCreateWorkerModal() {
  const modal = document.getElementById('workerModal');
  if (!modal) return;
  document.getElementById('workerModalTitle').textContent = 'Thêm Worker Node Mới';
  document.getElementById('workerModalMode').value = 'create';
  document.getElementById('workerModalIdInput').value = '';
  document.getElementById('workerModalIdInput').disabled = false;
  document.getElementById('workerModalUrlInput').value = '';
  document.getElementById('workerModalApiKeyInput').value = '';
  document.getElementById('workerModalWeightInput').value = '1';
  document.getElementById('workerModalActiveInput').value = 'true';
  modal.classList.remove('hidden');
}

function openEditWorkerModal(id, url, weight, isActive) {
  const modal = document.getElementById('workerModal');
  if (!modal) return;
  document.getElementById('workerModalTitle').textContent = `Chỉnh Sửa Worker: ${id}`;
  document.getElementById('workerModalMode').value = 'edit';
  document.getElementById('workerModalIdInput').value = id;
  document.getElementById('workerModalIdInput').disabled = true;
  document.getElementById('workerModalUrlInput').value = url;
  document.getElementById('workerModalApiKeyInput').value = '';
  document.getElementById('workerModalWeightInput').value = weight;
  document.getElementById('workerModalActiveInput').value = isActive ? 'true' : 'false';
  modal.classList.remove('hidden');
}

function closeWorkerModal() {
  document.getElementById('workerModal')?.classList.add('hidden');
}

async function testModalWorkerPing() {
  const url = document.getElementById('workerModalUrlInput')?.value.trim();
  const apiKey = document.getElementById('workerModalApiKeyInput')?.value.trim();
  if (!url) {
    showToast('Vui lòng nhập URL của worker trước khi test ping!', 'error');
    return;
  }
  pingWorkerTest(url, apiKey);
}

async function submitWorkerModal() {
  const mode = document.getElementById('workerModalMode')?.value || 'create';
  const id = document.getElementById('workerModalIdInput')?.value.trim();
  const url = document.getElementById('workerModalUrlInput')?.value.trim();
  const apiKey = document.getElementById('workerModalApiKeyInput')?.value.trim();
  const weight = parseInt(document.getElementById('workerModalWeightInput')?.value || '1', 10);
  const isActive = document.getElementById('workerModalActiveInput')?.value === 'true';

  if (!id || !url) {
    showToast('Vui lòng điền Worker ID và Endpoint URL!', 'error');
    return;
  }

  try {
    if (mode === 'create') {
      const res = await apiRequest('/v1/management/workers', {
        method: 'POST',
        body: JSON.stringify({ id, url, api_key: apiKey, weight, is_active: isActive }),
      });
      if (res.ok) {
        showToast(`Đã thêm worker node ${id}!`, 'success');
        closeWorkerModal();
        fetchWorkers();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Không thể tạo worker!', 'error');
      }
    } else {
      // Edit existing worker in settings
      const settingsRes = await apiRequest('/v1/management/settings');
      if (!settingsRes.ok) return;
      const settings = await settingsRes.json();
      const workers = settings.model_workers || [];
      const target = workers.find(w => w.id === id);
      if (target) {
        target.url = url;
        if (apiKey) target.api_key = apiKey;
        target.weight = weight;
        target.is_active = isActive;
        const updateRes = await apiRequest('/v1/management/settings', {
          method: 'PUT',
          body: JSON.stringify({ model_workers: workers }),
        });
        if (updateRes.ok) {
          showToast(`Đã cập nhật worker ${id}!`, 'success');
          closeWorkerModal();
          fetchWorkers();
        }
      }
    }
  } catch (e) {
    showToast('Lỗi lưu worker', 'error');
  }
}

async function deleteWorker(workerId) {
  if (!confirm(`Bạn có chắc muốn xóa worker ${workerId} khỏi cấu hình không?`)) return;
  try {
    const res = await apiRequest(`/v1/management/workers/${workerId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast(`Đã xóa worker ${workerId}!`, 'success');
      fetchWorkers();
    }
  } catch (e) {
    showToast('Lỗi khi xóa worker', 'error');
  }
}

async function toggleWorkerActive(workerId, newStatus) {
  try {
    const res = await apiRequest(`/v1/management/workers/${workerId}/toggle?is_active=${newStatus}`, {
      method: 'PATCH',
    });
    if (res.ok) {
      showToast(`Đã ${newStatus ? 'bật' : 'tắt'} worker ${workerId}!`, 'success');
      fetchWorkers();
    }
  } catch (e) {
    showToast('Lỗi cập nhật worker status', 'error');
  }
}

// ── TAB 5: CLIENT API KEYS MANAGEMENT ──

async function fetchKeys() {
  const container = document.getElementById('keysContainer');
  if (!container) return;
  try {
    const res = await apiRequest('/v1/management/keys');
    if (!res.ok) return;
    const keys = await res.json();

    if (keys.length === 0) {
      container.innerHTML = `<div class="text-center py-6 text-slate-500 text-xs">Chưa có Client API Key nào được tạo.</div>`;
      return;
    }

    container.innerHTML = keys.map(k => `
      <div class="flex flex-col sm:flex-row sm:items-center justify-between p-3.5 bg-[#0d0f16] rounded-xl border border-slate-800 hover:border-slate-700 transition gap-3">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-xs text-white">${k.name}</span>
            <span class="px-2 py-0.5 rounded text-[10px] font-medium ${k.is_active ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}">
              ${k.is_active ? 'Active' : 'Disabled'}
            </span>
          </div>
          <div class="flex items-center space-x-2 mt-1">
            <span class="text-xs font-mono text-slate-400">${k.key}</span>
            <button onclick="copyToClipboard('${k.key}', 'API Key')" class="text-slate-500 hover:text-slate-300 text-xs">
              <i class="fa-regular fa-copy"></i>
            </button>
          </div>
        </div>
        <div class="flex items-center space-x-3 text-xs">
          <span class="text-slate-400">Limit: <b class="text-white mono">${(k.daily_char_limit || 0).toLocaleString()} chars/day</b></span>
          <button onclick="toggleClientKey('${k.id}', ${k.is_active})" class="px-2.5 py-1 rounded text-xs transition ${k.is_active ? 'bg-amber-600/20 text-amber-300 hover:bg-amber-600/30 border border-amber-500/30' : 'bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30 border border-emerald-500/30'}">
            ${k.is_active ? 'Tạm Khóa' : 'Kích Hoạt'}
          </button>
          <button onclick="revokeKey('${k.id}')" class="text-rose-400 hover:text-rose-300 transition" title="Xóa API Key">
            <i class="fa-solid fa-trash-can"></i>
          </button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('fetchKeys failed', e);
  }
}

async function toggleClientKey(keyId, currentActive) {
  try {
    const res = await apiRequest(`/v1/management/keys/${keyId}/toggle?is_active=${!currentActive}`, {
      method: 'PATCH',
    });
    if (res.ok) {
      showToast(`Đã ${!currentActive ? 'kích hoạt' : 'tạm khóa'} API Key!`, 'success');
      fetchKeys();
    }
  } catch (e) {
    showToast('Lỗi thay đổi trạng thái API Key', 'error');
  }
}

function openCreateKeyModal() {
  document.getElementById('createKeyModal')?.classList.remove('hidden');
}

function closeCreateKeyModal() {
  document.getElementById('createKeyModal')?.classList.add('hidden');
}

async function submitCreateKey() {
  const name = document.getElementById('newKeyNameInput')?.value.trim();
  const limit = parseInt(document.getElementById('newKeyLimitInput')?.value || '50000', 10);
  if (!name) {
    showToast('Vui lòng nhập tên ứng dụng / mô tả cho API Key!', 'error');
    return;
  }
  try {
    const res = await apiRequest('/v1/management/keys', {
      method: 'POST',
      body: JSON.stringify({ name, daily_char_limit: limit }),
    });
    if (res.ok) {
      showToast('Tạo Client API Key thành công!', 'success');
      closeCreateKeyModal();
      fetchKeys();
    }
  } catch (e) {
    showToast('Lỗi tạo Client API Key!', 'error');
  }
}

async function revokeKey(keyId) {
  if (!confirm(`Bạn có chắc muốn thu hồi và xóa Client API Key này không?`)) return;
  try {
    const res = await apiRequest(`/v1/management/keys/${keyId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Đã thu hồi Client API Key thành công!', 'success');
      fetchKeys();
    }
  } catch (e) {
    showToast('Lỗi khi thu hồi API Key', 'error');
  }
}

// ── TAB 6: SETTINGS MANAGEMENT ──

async function fetchSettings() {
  const mgmtInput = document.getElementById('mgmtKeySettingsInput');
  if (mgmtInput) mgmtInput.value = getManagementKey();

  try {
    const res = await apiRequest('/v1/management/settings');
    if (res.ok) {
      const data = await res.json();

      // Server
      if (data.server) {
        const h = document.getElementById('serverHostInput');
        if (h) h.value = data.server.host || '0.0.0.0';
        const p = document.getElementById('serverPortInput');
        if (p) p.value = data.server.port || 8000;
      }

      // Security
      if (data.security) {
        const reqKey = document.getElementById('requireClientApiKeyInput');
        if (reqKey) reqKey.checked = data.security.require_client_api_key ?? true;
      }

      // Concurrency & System Limits
      if (data.concurrency) {
        const speech = document.getElementById('maxSpeechCharLimitInput');
        if (speech) speech.value = data.concurrency.max_speech_char_limit ?? 2000;
        const prev = document.getElementById('maxPreviewCharLimitInput');
        if (prev) prev.value = data.concurrency.max_preview_char_limit ?? 250;
        const daily = document.getElementById('dailyTtsCharLimitInput');
        if (daily) daily.value = data.concurrency.daily_tts_char_limit ?? 100000;
        const rate = document.getElementById('rateLimitRequestsInput');
        if (rate) rate.value = data.concurrency.rate_limit_requests_per_minute ?? 60;
        const tts = document.getElementById('maxConcurrentTtsInput');
        if (tts) tts.value = data.concurrency.max_concurrent_tts ?? 2;
        const queue = document.getElementById('maxQueueWaitersInput');
        if (queue) queue.value = data.concurrency.max_queue_waiters ?? 8;
      }

      // Audio Cache
      if (data.audio_cache) {
        const size = document.getElementById('maxCacheSizeMbInput');
        if (size) size.value = data.audio_cache.max_size_mb ?? 2048;
        const ttl = document.getElementById('cacheTtlDaysInput');
        if (ttl) ttl.value = data.audio_cache.ttl_days ?? 7;
        const interval = document.getElementById('cacheCleanIntervalInput');
        if (interval) interval.value = data.audio_cache.clean_interval_sec ?? 3600;
      }
    }
  } catch (e) {
    console.error('Failed to fetch settings', e);
  }
  fetchUsers();
}

async function saveServerSettings() {
  const host = document.getElementById('serverHostInput')?.value.trim() || '0.0.0.0';
  const port = parseInt(document.getElementById('serverPortInput')?.value || '8000', 10);
  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify({ server: { host, port } }),
    });
    if (res.ok) {
      showToast('Đã lưu cấu hình mạng máy chủ thành công!', 'success');
    }
  } catch (e) {
    showToast('Lỗi lưu cấu hình mạng', 'error');
  }
}

async function saveMgmtKeySettings() {
  const keyVal = document.getElementById('mgmtKeySettingsInput')?.value.trim();
  const requireKey = document.getElementById('requireClientApiKeyInput')?.checked ?? true;

  if (!keyVal) {
    showToast('Vui lòng nhập Management Key!', 'error');
    return;
  }
  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify({
        security: {
          management_key: keyVal,
          require_client_api_key: requireKey,
        },
      }),
    });
    if (res.ok) {
      setManagementKey(keyVal);
      showToast('Đã lưu cấu hình bảo mật thành công!', 'success');
    }
  } catch (e) {
    showToast('Lỗi lưu cấu hình bảo mật', 'error');
  }
}

async function saveSystemSettings() {
  const speechLimit = parseInt(document.getElementById('maxSpeechCharLimitInput')?.value || '2000', 10);
  const previewLimit = parseInt(document.getElementById('maxPreviewCharLimitInput')?.value || '250', 10);
  const dailyLimit = parseInt(document.getElementById('dailyTtsCharLimitInput')?.value || '100000', 10);
  const rateLimit = parseInt(document.getElementById('rateLimitRequestsInput')?.value || '60', 10);
  const maxConcurrent = parseInt(document.getElementById('maxConcurrentTtsInput')?.value || '2', 10);
  const maxQueue = parseInt(document.getElementById('maxQueueWaitersInput')?.value || '8', 10);

  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify({
        concurrency: {
          max_speech_char_limit: speechLimit,
          max_preview_char_limit: previewLimit,
          daily_tts_char_limit: dailyLimit,
          rate_limit_requests_per_minute: rateLimit,
          max_concurrent_tts: maxConcurrent,
          max_queue_waiters: maxQueue,
        },
      }),
    });
    if (res.ok) {
      showToast('Đã lưu cấu hình hạn mức hệ thống thành công!', 'success');
    } else {
      showToast('Không thể lưu cấu hình hạn mức trên server!', 'error');
    }
  } catch (e) {
    showToast('Lỗi gửi cập nhật cấu hình hạn mức', 'error');
  }
}

async function saveCacheSettings() {
  const maxSize = parseInt(document.getElementById('maxCacheSizeMbInput')?.value || '2048', 10);
  const ttl = parseInt(document.getElementById('cacheTtlDaysInput')?.value || '7', 10);
  const clean = parseInt(document.getElementById('cacheCleanIntervalInput')?.value || '3600', 10);

  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify({
        audio_cache: {
          max_size_mb: maxSize,
          ttl_days: ttl,
          clean_interval_sec: clean,
        },
      }),
    });
    if (res.ok) {
      showToast('Đã lưu cấu hình bộ nhớ đệm audio!', 'success');
    }
  } catch (e) {
    showToast('Lỗi lưu cấu hình cache', 'error');
  }
}

async function purgeCacheFiles() {
  if (!confirm('Bạn có chắc chắn muốn xóa toàn bộ các tệp âm thanh lưu đệm trong cache không?')) return;
  try {
    const res = await apiRequest('/v1/management/cache/purge', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showToast(`Đã dọn dẹp ${data.deleted_files || 0} tệp âm thanh trong cache!`, 'success');
      fetchMetrics(true);
    } else {
      showToast('Không thể dọn bộ đệm cache!', 'error');
    }
  } catch (e) {
    showToast('Lỗi khi gửi yêu cầu xóa cache', 'error');
  }
}

// ── USERS MANAGEMENT ──

let allUsersData = [];

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function fetchUsers() {
  const tbody = document.getElementById('usersTableBody');
  if (!tbody) return;
  try {
    const res = await apiRequest('/v1/management/users');
    if (!res.ok) return;
    allUsersData = await res.json();
    renderUsersTable(allUsersData);
  } catch (e) {
    console.error('fetchUsers failed', e);
  }
}

function filterUsersTable() {
  const q = (document.getElementById('usersSearchInput')?.value || '').trim().toLowerCase();
  if (!q) {
    renderUsersTable(allUsersData);
    return;
  }
  const filtered = allUsersData.filter(u =>
    (u.email || '').toLowerCase().includes(q) ||
    (u.full_name || '').toLowerCase().includes(q)
  );
  renderUsersTable(filtered);
}

function renderUsersTable(users) {
  const tbody = document.getElementById('usersTableBody');
  if (!tbody) return;

  if (users.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-500">Chưa có tài khoản người dùng nào được đăng ký.</td></tr>`;
    return;
  }

  tbody.innerHTML = users.map(u => `
    <tr class="k-table-row border-b border-slate-800/40">
      <td class="py-3 px-4 font-mono text-white">${escapeHtml(u.email)}</td>
      <td class="py-3 px-4 text-slate-300">${escapeHtml(u.full_name || '--')}</td>
      <td class="py-3 px-4 text-slate-400 capitalize">${escapeHtml(u.auth_provider || 'local')}</td>
      <td class="py-3 px-4 mono text-blue-400 font-semibold">${u.cloned_voices_count || 0}</td>
      <td class="py-3 px-4 mono text-white">${u.max_cloned_slots || 3}</td>
      <td class="py-3 px-4 mono text-emerald-400">${(u.tts_chars_today || 0).toLocaleString()}</td>
      <td class="py-3 px-4 text-right">
        <button onclick="openEditUserModalById('${escapeHtml(u.id)}')" class="text-indigo-400 hover:text-indigo-300 mr-3" title="Chỉnh sửa slot clone">
          <i class="fa-solid fa-pen-to-square"></i>
        </button>
        <button onclick="deleteUserAccountById('${escapeHtml(u.id)}')" class="text-rose-400 hover:text-rose-300" title="Xóa tài khoản">
          <i class="fa-solid fa-trash-can"></i>
        </button>
      </td>
    </tr>
  `).join('');
}

function openEditUserModalById(userId) {
  const user = allUsersData.find(u => u.id === userId);
  if (!user) return;
  openEditUserModal(user.id, user.email, user.max_cloned_slots || 3);
}

function deleteUserAccountById(userId) {
  const user = allUsersData.find(u => u.id === userId);
  if (!user) return;
  deleteUserAccount(user.id, user.email);
}

async function deleteUserAccount(userId, email) {
  if (!confirm(`Bạn có chắc chắn muốn xóa tài khoản ${email} và toàn bộ giọng clone không?`)) return;
  try {
    const res = await apiRequest(`/v1/management/users/${userId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast(`Đã xóa người dùng ${email}!`, 'success');
      fetchUsers();
    }
  } catch (e) {
    showToast('Lỗi khi xóa người dùng', 'error');
  }
}

function openEditUserModal(id, email, slots) {
  const modal = document.getElementById('editUserModal');
  if (!modal) return;
  document.getElementById('editUserIdInput').value = id;
  document.getElementById('editUserEmailLabel').textContent = email;
  document.getElementById('editUserSlotsInput').value = slots;
  modal.classList.remove('hidden');
}

function closeEditUserModal() {
  document.getElementById('editUserModal')?.classList.add('hidden');
}

async function submitEditUser() {
  const id = document.getElementById('editUserIdInput')?.value;
  const slots = parseInt(document.getElementById('editUserSlotsInput')?.value || '3', 10);
  try {
    const res = await apiRequest(`/v1/management/users/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ max_cloned_slots: slots }),
    });
    if (res.ok) {
      showToast('Cập nhật slot clone thành công!', 'success');
      closeEditUserModal();
      fetchUsers();
    }
  } catch (e) {
    showToast('Lỗi cập nhật người dùng!', 'error');
  }
}

// ── UTILITIES & MODAL HELPERS ──

function copyToClipboard(text, label = 'Nội dung') {
  navigator.clipboard.writeText(text);
  showToast(`Đã sao chép ${label} vào clipboard!`, 'success');
}

function togglePasswordVisibility(inputId) {
  const input = document.getElementById(inputId);
  const eye = document.getElementById(`${inputId}Eye`);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (eye) eye.className = 'fa-solid fa-eye-slash';
  } else {
    input.type = 'password';
    if (eye) eye.className = 'fa-solid fa-eye';
  }
}

function openAuthModal() {
  const modal = document.getElementById('authModal');
  if (!modal) return;
  document.getElementById('authModalKeyInput').value = getManagementKey();
  modal.classList.remove('hidden');
}

function closeAuthModal() {
  document.getElementById('authModal')?.classList.add('hidden');
}

function saveAuthModalKey() {
  const val = document.getElementById('authModalKeyInput')?.value.trim();
  if (val) {
    setManagementKey(val);
    showToast('Đã lưu Management Key!', 'success');
    closeAuthModal();
    switchKTab(currentKTab);
  }
}

// ── DOM INITIALIZATION ──

document.addEventListener('DOMContentLoaded', () => {
  const linkWebDemo = document.getElementById('linkWebDemo');
  if (linkWebDemo) {
    linkWebDemo.href = `${window.location.protocol}//${window.location.hostname}:3000`;
  }
  onAutoRefreshChange();
  switchKTab('overview');
});

// ── KEEPER DOCK & HEADER HELPERS ──

function toggleMoreMenu(event) {
  if (event) event.stopPropagation();
  const popover = document.getElementById('moreMenuPopover');
  if (popover) {
    popover.classList.toggle('hidden');
  }
}

function closeMoreMenu() {
  const popover = document.getElementById('moreMenuPopover');
  if (popover) {
    popover.classList.add('hidden');
  }
}

function toggleDockCollapse() {
  const dock = document.getElementById('keeperDock');
  const icon = document.getElementById('dockCollapseIcon');
  if (!dock) return;
  dock.classList.toggle('is-collapsed');
  if (icon) {
    if (dock.classList.contains('is-collapsed')) {
      icon.className = 'fa-solid fa-chevron-right text-[11px]';
    } else {
      icon.className = 'fa-solid fa-chevron-left text-[11px]';
    }
  }
}

function toggleTheme() {
  showToast('Giao diện chuẩn Dark Obsidian (CPA Usage Keeper)', 'info');
}

function toggleLanguage() {
  const btn = document.getElementById('langSwitchBtn');
  if (btn) {
    const isEn = btn.textContent.trim() === 'EN';
    btn.textContent = isEn ? 'VI' : 'EN';
    showToast(`Đã đổi ngôn ngữ: ${isEn ? 'Tiếng Việt' : 'English'}`, 'info');
  }
}

document.addEventListener('click', (e) => {
  const popover = document.getElementById('moreMenuPopover');
  const btn = document.getElementById('moreMenuBtn');
  if (popover && !popover.classList.contains('hidden')) {
    if (!popover.contains(e.target) && !btn?.contains(e.target)) {
      popover.classList.add('hidden');
    }
  }
});
