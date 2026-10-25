// VoiceClonerAppServer - Management Console Frontend Logic (KEEPER Style)

let currentKTab = 'overview';
let activeMetricsData = null;
let realtimeWindowMinutes = 60;

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
  else if (tab === 'events') renderEventsTable();
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
    showToast('Dữ liệu đã được làm mới!', 'success');
  }, 500);
}

function onFilterChange() {
  fetchMetrics();
}

// ── CHART.JS COMMON CONFIGURATION ──

const commonTooltipConfig = {
  enabled: true,
  backgroundColor: '#1b1d26',
  borderColor: '#2e3240',
  borderWidth: 1,
  titleColor: '#f8fafc',
  titleFont: { size: 12, weight: 'bold' },
  bodyColor: '#cbd5e1',
  bodyFont: { size: 11, family: 'JetBrains Mono' },
  padding: 10,
  cornerRadius: 8,
  displayColors: true,
  boxPadding: 4,
};

// Helper to position tooltip safely within screen boundaries
function positionTooltip(e, tooltip) {
  const rect = e.target.getBoundingClientRect();
  const tooltipWidth = tooltip.offsetWidth || 180;
  let left = rect.left + window.scrollX - tooltipWidth / 2 + rect.width / 2;
  left = Math.max(10, Math.min(left, window.innerWidth - tooltipWidth - 10));
  let top = rect.top + window.scrollY - 45;
  if (rect.top < 50) {
    top = rect.bottom + window.scrollY + 8;
  }
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

// ── OVERVIEW TAB METRICS & CHARTS ──

async function fetchMetrics() {
  try {
    const rangeSelect = document.getElementById('filterRange');
    const hours = rangeSelect ? rangeSelect.value : '24';
    const res = await apiRequest(`/v1/management/metrics/summary?hours=${hours}`);
    if (!res.ok) return;
    const data = await res.json();
    activeMetricsData = data;

    // Direct accurate calculation from server aggregates
    const totalReqs = data.total_requests ?? 0;
    const totalChars = data.total_chars ?? 0;
    const failedReqs = data.failed_requests ?? 0;
    const successReqs = data.success_requests ?? Math.max(0, totalReqs - failedReqs);
    const successRate = totalReqs > 0 ? ((successReqs / totalReqs) * 100).toFixed(2) : '100.00';

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

    // Populate Secondary Small Cards with Nullish Coalescing
    const rpmVal = data.rpm ?? 0.0;
    const cpmVal = data.cpm ?? 0;
    const cacheRate = cacheMetrics.cache_hit_rate ?? 0.0;

    const elRPM = document.getElementById('valRPM');
    if (elRPM) elRPM.innerHTML = `${Number(rpmVal).toFixed(2)} <span class="text-xs font-normal text-slate-400">req/m</span>`;

    const elTPM = document.getElementById('valTPM');
    if (elTPM) elTPM.innerHTML = `${Number(cpmVal).toLocaleString()} <span class="text-xs font-normal text-slate-400">cpm</span>`;

    const elCacheRate = document.getElementById('valCacheRate');
    if (elCacheRate) elCacheRate.textContent = `${cacheRate}%`;

    const elCost = document.getElementById('valCost');
    if (elCost) {
      const estCost = ((totalChars / 1000000) * 0.15).toFixed(2);
      elCost.textContent = `$${estCost}`;
    }

    const elSubCache = document.getElementById('valSubCache');
    if (elSubCache) elSubCache.textContent = `Cached: ${cacheMetrics.cache_hit_count ?? 0} reqs • Miss: ${cacheMetrics.cache_miss_count ?? 0}`;

    // Render Charts
    initSparklines(data);
    initHeatmaps(data);
    initMainTrendsChart(data);

    // Sync Key filter options
    const keySelect = document.getElementById('filterApiKey');
    if (keySelect && data.usage_by_key) {
      const currentVal = keySelect.value;
      keySelect.innerHTML = '<option value="all">All Keys</option>' +
        data.usage_by_key.map(k => `<option value="${k.name}">${k.name}</option>`).join('');
      keySelect.value = currentVal;
    }

    renderEventsTable();
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
          tension: 0.3,
          fill: { target: 'origin', above: 'rgba(59, 130, 246, 0.12)' },
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
          tension: 0.3,
          fill: { target: 'origin', above: 'rgba(139, 92, 246, 0.12)' },
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

  // 3. RPM Sparkline (over recent 12 buckets)
  const recent12 = buckets.slice(-12);
  const recentLabels = recent12.map(b => b.label);
  const bucketMin = (24 * 60) / 32; // ~45 min per bucket

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
          tension: 0.3,
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
          tension: 0.3,
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
          tension: 0.3,
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
          tension: 0.3,
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

function initHeatmaps(data) {
  const tooltip = document.getElementById('heatmapTooltip');
  if (!tooltip) return;

  const totalChars = data?.total_chars ?? 0;
  const elSummary = document.getElementById('heatmapTotalSummary');
  if (elSummary) elSummary.textContent = `${totalChars.toLocaleString()} Chars`;

  const buckets = (data?.time_buckets && data.time_buckets.length === 32)
    ? data.time_buckets
    : Array.from({ length: 32 }, (_, i) => ({
        index: i,
        label: `${String(Math.floor((i / 32) * 24)).padStart(2, '0')}:00`,
        requests: 0,
        success_requests: 0,
        failed_requests: 0,
        chars: 0,
        cache_hits: 0,
        avg_latency_ms: 0.0,
      }));

  const maxChars = Math.max(...buckets.map(b => b.chars), 1);

  // 1. Character Activity Heatmap (32 cols x 7 rows)
  const container1 = document.getElementById('tokenActivityHeatmap');
  if (container1) {
    container1.innerHTML = '';
    for (let col = 0; col < 32; col++) {
      const bucket = buckets[col];
      const colEl = document.createElement('div');
      colEl.className = 'flex flex-col space-y-1';

      // Number of active rows (1 to 7) based on real chars
      const activeRows = bucket.requests > 0
        ? Math.min(7, Math.max(1, Math.ceil((bucket.chars / maxChars) * 7)))
        : 0;

      const ratio = bucket.chars / maxChars;
      let activeColor = 'bg-blue-600';
      if (ratio > 0.6) activeColor = 'bg-blue-400';
      else if (ratio > 0.25) activeColor = 'bg-blue-500';

      for (let row = 0; row < 7; row++) {
        const cell = document.createElement('div');
        // Bottom-up filling (row 6 is bottom, 0 is top)
        const isCellFilled = (6 - row) < activeRows;
        const color = isCellFilled ? activeColor : 'bg-[#181922]';
        cell.className = `heat-cell ${color}`;

        cell.addEventListener('mouseenter', (e) => {
          tooltip.style.display = 'block';
          tooltip.innerHTML = `<span class="text-blue-400 font-bold">Mốc: ${bucket.label}</span><br>Ký tự: <b>${bucket.chars.toLocaleString()} chars</b><br>Requests: <b>${bucket.requests} reqs</b>`;
          positionTooltip(e, tooltip);
        });
        cell.addEventListener('mouseleave', () => {
          tooltip.style.display = 'none';
        });

        colEl.appendChild(cell);
      }
      container1.appendChild(colEl);
    }
  }

  // 2. Request Health Timeline Heatmap (32 cols x 7 rows - Synchronized with Token Activity)
  const container2 = document.getElementById('healthTimelineGrid');
  if (container2) {
    container2.innerHTML = '';
    for (let col = 0; col < 32; col++) {
      const bucket = buckets[col];
      const colEl = document.createElement('div');
      colEl.className = 'flex flex-col space-y-1';

      const hasTraffic = bucket.requests > 0;
      const isHealthy = hasTraffic && bucket.failed_requests === 0;
      const isDegraded = hasTraffic && bucket.failed_requests > 0;

      // Match the exact same active rows as Token Activity
      const activeRows = hasTraffic
        ? Math.min(7, Math.max(1, Math.ceil((bucket.chars / maxChars) * 7)))
        : 0;

      for (let row = 0; row < 7; row++) {
        const cell = document.createElement('div');
        const isCellFilled = (6 - row) < activeRows;
        let color = 'bg-[#181922]';

        if (isCellFilled) {
          if (isHealthy) {
            color = 'bg-emerald-500';
          } else {
            const failRatio = bucket.failed_requests / bucket.requests;
            color = failRatio > 0.3 ? 'bg-rose-500' : 'bg-amber-500';
          }
        }

        cell.className = `heat-cell ${color}`;

        cell.addEventListener('mouseenter', (e) => {
          tooltip.style.display = 'block';
          const titleColor = isHealthy ? 'text-emerald-400' : (isDegraded ? 'text-rose-400' : 'text-slate-400');
          const statusLabel = isHealthy ? 'Healthy (100%)' : (isDegraded ? `Có lỗi (${bucket.failed_requests} failed)` : 'Không có lưu lượng');
          tooltip.innerHTML = `<span class="${titleColor} font-bold">Mốc: ${bucket.label} - ${statusLabel}</span><br>Thành công: <b>${bucket.success_requests}</b> | Lỗi: <b>${bucket.failed_requests}</b><br>Độ trễ TB: <b>${bucket.avg_latency_ms}ms</b>`;
          positionTooltip(e, tooltip);
        });
        cell.addEventListener('mouseleave', () => {
          tooltip.style.display = 'none';
        });

        colEl.appendChild(cell);
      }
      container2.appendChild(colEl);
    }
  }
}

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
        pointRadius: 3,
        pointHoverRadius: 7,
        pointHoverBackgroundColor: '#3b82f6',
        pointHoverBorderColor: '#ffffff',
        pointHoverBorderWidth: 2,
        tension: 0.3,
        fill: true,
        backgroundColor: 'rgba(59, 130, 246, 0.22)',
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
            label: (item) => ` ${item.dataset.label}: ${item.raw}M tokens (${Math.round(item.raw * 1000000).toLocaleString()} chars)`,
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

  // Render Legend: Only real VieNeu-TTS v3 model
  const legendContainer = document.getElementById('trendsLegendContainer');
  if (legendContainer) {
    const formattedChars = totalChars >= 1000000
      ? `${(totalChars / 1000000).toFixed(2)}M`
      : (totalChars >= 1000 ? `${(totalChars / 1000).toFixed(1)}K` : `${totalChars}`);

    legendContainer.innerHTML = `
      <div class="hover:bg-slate-800/30 p-2.5 rounded-xl border border-slate-800/60 cursor-pointer transition max-w-xs">
        <div class="flex items-center space-x-2 text-slate-200">
          <span class="w-2.5 h-2.5 rounded-full bg-blue-500 shadow-sm shadow-blue-500/50"></span>
          <span class="font-bold text-xs tracking-tight">VieNeu-TTS v3</span>
          <span class="px-2 py-0.5 rounded text-[10px] bg-blue-500/10 text-blue-400 border border-blue-500/20 font-semibold">Active Model</span>
        </div>
        <div class="mt-2 font-extrabold text-sm text-white mono">${formattedChars} <span class="text-xs text-emerald-400 font-semibold ml-1">100.0% traffic</span></div>
      </div>
    `;
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

function fetchRealtimeMetrics() {
  const data = activeMetricsData || {};
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
  if (elCost) elCost.textContent = `$${((totalChars / 1000000) * 0.13).toFixed(2)}`;

  initRealtimeCharts(data);
}

function initRealtimeCharts(data) {
  const buckets = (data?.time_buckets && data.time_buckets.length > 0) ? data.time_buckets : [];
  const recentBuckets = buckets.slice(-8);
  const throughputLabels = recentBuckets.length > 0 ? recentBuckets.map(b => b.label) : ['21:14', '21:22', '21:30', '21:38', '21:46', '21:54', '22:02', '22:10'];
  const bucketMin = (24 * 60) / 32;
  const tpmPoints = recentBuckets.length > 0 ? recentBuckets.map(b => Math.round(b.chars / bucketMin)) : Array(8).fill(0);
  const rpmPoints = recentBuckets.length > 0 ? recentBuckets.map(b => Number((b.requests / bucketMin).toFixed(2))) : Array(8).fill(0);

  // 1. Throughput Dual Axis
  const ctxThroughput = document.getElementById('chartThroughput');
  if (ctxThroughput) {
    if (charts.throughput) charts.throughput.destroy();
    charts.throughput = new Chart(ctxThroughput, {
      type: 'line',
      data: {
        labels: throughputLabels,
        datasets: [
          {
            label: 'Chars per min',
            data: tpmPoints,
            borderColor: '#3b82f6',
            borderWidth: 2.5,
            pointRadius: 3,
            pointHoverRadius: 6,
            tension: 0.3,
            yAxisID: 'y',
          },
          {
            label: 'Requests per min',
            data: rpmPoints,
            borderColor: '#f97316',
            borderWidth: 2,
            borderDash: [5, 5],
            pointRadius: 3,
            pointHoverRadius: 6,
            tension: 0.3,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              label: (i) => i.datasetIndex === 0
                ? ` ${i.dataset.label}: ${i.raw} chars/min`
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
    const outcomeBuckets = buckets.slice(-6);
    const outcomeLabels = outcomeBuckets.length > 0 ? outcomeBuckets.map(b => b.label) : ['21:14', '21:24', '21:34', '21:44', '21:54', '22:04'];
    const successPoints = outcomeBuckets.length > 0 ? outcomeBuckets.map(b => b.success_requests) : Array(6).fill(0);
    const failedPoints = outcomeBuckets.length > 0 ? outcomeBuckets.map(b => b.failed_requests) : Array(6).fill(0);

    charts.outcomes = new Chart(ctxOutcomes, {
      type: 'bar',
      data: {
        labels: outcomeLabels,
        datasets: [
          {
            label: 'Successful Requests',
            data: successPoints,
            backgroundColor: '#10b981',
            borderRadius: 4,
            stack: 'stack1',
          },
          {
            label: 'Failed Requests',
            data: failedPoints,
            backgroundColor: '#f43f5e',
            borderRadius: 4,
            stack: 'stack1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              title: (items) => `Bucket: ${items[0].label}`,
              label: (item) => ` ${item.dataset.label}: ${item.raw} reqs`,
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#64748b' } },
          y: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b' } },
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
    const totalReqs = data?.total_requests ?? 0;

    charts.composition = new Chart(ctxComp, {
      type: 'doughnut',
      data: {
        labels: ['Cache Read (SHA-256 Hit)', 'VieNeu-TTS v3 Raw Inference'],
        datasets: [{
          data: totalReqs === 0 ? [100, 0] : [hitRate, rawRate],
          backgroundColor: ['#14b8a6', '#3b82f6'],
          hoverBackgroundColor: ['#0d9488', '#2563eb'],
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '75%',
        plugins: {
          legend: { display: false },
          tooltip: {
            ...commonTooltipConfig,
            callbacks: {
              label: (item) => ` ${item.label}: ${item.raw}%`,
            },
          },
        },
        onHover: (event, elements) => {
          const centerVal = document.getElementById('donutCenterVal');
          const centerLabel = document.getElementById('donutCenterLabel');
          if (elements.length > 0) {
            const idx = elements[0].index;
            const labels = [`${hitRate}%`, `${rawRate}%`];
            const subLabels = ['Cache read share', 'VieNeu-TTS v3 Raw'];
            if (centerVal) centerVal.textContent = labels[idx];
            if (centerLabel) centerLabel.textContent = subLabels[idx];
          } else {
            if (centerVal) centerVal.textContent = `${hitRate}%`;
            if (centerLabel) centerLabel.textContent = 'Cache read share';
          }
        },
      },
    });
  }
}

// ── TAB 3: REQUEST EVENTS TABLE & EXPORT ──

function renderEventsTable() {
  const tbody = document.getElementById('eventsTableBody');
  if (!tbody) return;

  const logs = activeMetricsData?.recent_logs || [];
  const modelFilter = document.getElementById('eventFilterModel')?.value || 'all';
  const statusFilter = document.getElementById('eventFilterStatus')?.value || 'all';

  let filtered = logs.filter(log => {
    if (modelFilter !== 'all' && (log.worker || 'unknown') !== modelFilter) return false;
    if (statusFilter === 'success' && (log.status >= 400 || log.is_cache_hit)) return false;
    if (statusFilter === 'cache' && !log.is_cache_hit) return false;
    if (statusFilter === 'error' && log.status < 400) return false;
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-slate-500 text-xs">Không tìm thấy sự kiện nào phù hợp với bộ lọc.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(log => {
    const isSuccess = log.status >= 200 && log.status < 400;
    const badgeColor = log.is_cache_hit
      ? 'bg-teal-500/10 text-teal-400 border-teal-500/20'
      : (isSuccess ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20');
    const badgeText = log.is_cache_hit ? 'Cache Hit' : (isSuccess ? `Success (${log.status})` : `Failed (${log.status})`);
    const timeStr = log.time ? log.time.slice(11, 19) : '--:--:--';

    return `
      <tr class="hover:bg-slate-800/40 transition">
        <td class="py-3 px-4 mono text-slate-300">${timeStr}</td>
        <td class="py-3 px-4 mono text-slate-400">${log.key_name || 'Anonymous'}</td>
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
      </tr>
    `;
  }).join('');
}

function clearEventFilters() {
  const m = document.getElementById('eventFilterModel');
  if (m) m.value = 'all';
  const s = document.getElementById('eventFilterStatus');
  if (s) s.value = 'all';
  renderEventsTable();
}

function exportEventsCsv() {
  const logs = activeMetricsData?.recent_logs || [];
  if (logs.length === 0) {
    showToast('Chưa có dữ liệu để xuất CSV!', 'info');
    return;
  }
  const headers = ['ID', 'Timestamp', 'ClientKey', 'Worker', 'Status', 'Endpoint', 'LatencyMs', 'Chars', 'CacheHit'];
  const rows = logs.map(l => [
    l.id,
    l.time,
    `"${l.key_name || ''}"`,
    `"${l.worker || ''}"`,
    l.status,
    `"${l.endpoint || ''}"`,
    l.latency_ms,
    l.chars,
    l.is_cache_hit ? 1 : 0
  ].join(','));
  const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows].join('\n');
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement('a');
  link.setAttribute('href', encodedUri);
  link.setAttribute('download', `voicecloner-events-${new Date().toISOString().slice(0, 10)}.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  showToast('Đã xuất file CSV thành công!', 'success');
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
      container.innerHTML = `<div class="k-card p-6 text-center text-slate-500 text-xs">Chưa cấu hình worker node nào trong settings.json</div>`;
      return;
    }

    container.innerHTML = workers.map(w => {
      const isOnline = w.telemetry && w.telemetry.status === 'online';
      const statusColor = isOnline ? 'emerald' : 'rose';
      const latencyStr = isOnline && w.telemetry.latency_ms ? `${w.telemetry.latency_ms}ms` : (isOnline ? 'online' : 'offline');

      return `
        <div class="k-card p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 hover:border-slate-700 transition">
          <div class="flex items-center space-x-4">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-emerald-600 to-teal-500 flex items-center justify-center text-white shrink-0">
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
              <span class="text-xs text-slate-500 font-mono mt-0.5 block">${w.url}</span>
            </div>
          </div>
          <div class="flex items-center space-x-3 text-xs w-full md:w-auto justify-between md:justify-end">
            <button onclick="pingWorkerTest('${w.url}', '')" class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center space-x-1">
              <i class="fa-solid fa-bolt text-amber-400"></i>
              <span>Test Ping</span>
            </button>
            <button onclick="toggleWorkerActive('${w.id}', ${!w.is_active})" class="px-3 py-1.5 rounded-lg text-xs font-semibold transition ${w.is_active ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 hover:bg-slate-700 text-slate-400'}">
              ${w.is_active ? 'Đang Bật' : 'Tạm Tắt'}
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

async function toggleWorkerActive(workerId, newStatus) {
  try {
    const res = await apiRequest('/v1/management/settings');
    if (!res.ok) return;
    const settings = await res.json();
    const workers = settings.model_workers || [];
    const target = workers.find(w => w.id === workerId);
    if (target) {
      target.is_active = newStatus;
      const updateRes = await apiRequest('/v1/management/settings', {
        method: 'PUT',
        body: JSON.stringify({ model_workers: workers }),
      });
      if (updateRes.ok) {
        showToast(`Đã ${newStatus ? 'bật' : 'tắt'} worker ${workerId}!`, 'success');
        fetchWorkers();
      }
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
      <div class="flex items-center justify-between p-3.5 bg-[#101116] rounded-xl border border-slate-800 hover:border-slate-700 transition">
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-xs text-white">${k.name}</span>
            <span class="px-2 py-0.5 rounded text-[10px] font-medium ${k.is_active ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}">
              ${k.is_active ? 'Active' : 'Disabled'}
            </span>
          </div>
          <span class="text-xs font-mono text-slate-400 mt-1 block">${k.key}</span>
        </div>
        <div class="flex items-center space-x-4 text-xs">
          <span class="text-slate-400">Limit: <b class="text-white">${(k.daily_char_limit || 0).toLocaleString()} chars/day</b></span>
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

// ── TAB 6: SETTINGS & USERS MANAGEMENT ──

async function fetchSettings() {
  const input = document.getElementById('mgmtKeySettingsInput');
  if (input) input.value = getManagementKey();
  try {
    const res = await apiRequest('/v1/management/settings');
    if (res.ok) {
      const data = await res.json();
      if (data && data.concurrency) {
        const speechInput = document.getElementById('maxSpeechCharLimitInput');
        if (speechInput) speechInput.value = data.concurrency.max_speech_char_limit ?? 2000;
        const previewInput = document.getElementById('maxPreviewCharLimitInput');
        if (previewInput) previewInput.value = data.concurrency.max_preview_char_limit ?? 250;
        const dailyLimitInput = document.getElementById('dailyTtsCharLimitInput');
        if (dailyLimitInput) dailyLimitInput.value = data.concurrency.daily_tts_char_limit ?? 100000;
        const rateLimitInput = document.getElementById('rateLimitRequestsInput');
        if (rateLimitInput) rateLimitInput.value = data.concurrency.rate_limit_requests_per_minute ?? 60;
      }
    }
  } catch (e) {
    console.error('Failed to fetch system settings', e);
  }
  fetchUsers();
}

async function saveSystemSettings() {
  const speechLimit = parseInt(document.getElementById('maxSpeechCharLimitInput')?.value || '2000', 10);
  const previewLimit = parseInt(document.getElementById('maxPreviewCharLimitInput')?.value || '250', 10);
  const dailyLimit = parseInt(document.getElementById('dailyTtsCharLimitInput')?.value || '100000', 10);
  const rateLimit = parseInt(document.getElementById('rateLimitRequestsInput')?.value || '60', 10);

  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify({
        concurrency: {
          max_speech_char_limit: speechLimit,
          max_preview_char_limit: previewLimit,
          daily_tts_char_limit: dailyLimit,
          rate_limit_requests_per_minute: rateLimit,
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

async function saveMgmtKeySettings() {
  const val = document.getElementById('mgmtKeySettingsInput')?.value.trim();
  if (!val) {
    showToast('Vui lòng nhập Management Key!', 'error');
    return;
  }
  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify({ security: { management_key: val } }),
    });
    if (res.ok) {
      setManagementKey(val);
      showToast('Đã lưu và cập nhật Management Key trên hệ thống!', 'success');
    } else {
      showToast('Không thể cập nhật Management Key trên server!', 'error');
    }
  } catch (e) {
    showToast('Lỗi gửi cập nhật Management Key', 'error');
  }
}

async function fetchUsers() {
  const tbody = document.getElementById('usersTableBody');
  if (!tbody) return;
  try {
    const res = await apiRequest('/v1/management/users');
    if (!res.ok) return;
    const users = await res.json();

    if (users.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-500">Chưa có tài khoản người dùng nào được đăng ký.</td></tr>`;
      return;
    }

    tbody.innerHTML = users.map(u => `
      <tr class="hover:bg-slate-800/40 transition">
        <td class="py-3 px-4 font-mono text-white">${u.email}</td>
        <td class="py-3 px-4 text-slate-300">${u.full_name || '--'}</td>
        <td class="py-3 px-4 text-slate-400 capitalize">${u.auth_provider || 'local'}</td>
        <td class="py-3 px-4 mono text-blue-400 font-semibold">${u.cloned_voices_count || 0}</td>
        <td class="py-3 px-4 mono text-white">${u.max_cloned_slots || 3}</td>
        <td class="py-3 px-4 mono text-emerald-400">${(u.tts_chars_today || 0).toLocaleString()}</td>
        <td class="py-3 px-4 text-right">
          <button onclick="openEditUserModal('${u.id}', '${u.email}', ${u.max_cloned_slots || 3})" class="text-indigo-400 hover:text-indigo-300 mr-3" title="Chỉnh sửa slot clone">
            <i class="fa-solid fa-pen-to-square"></i>
          </button>
          <button onclick="deleteUserAccount('${u.id}', '${u.email}')" class="text-rose-400 hover:text-rose-300" title="Xóa tài khoản">
            <i class="fa-solid fa-trash-can"></i>
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('fetchUsers failed', e);
  }
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

// ── AUTH MODAL ──

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
  switchKTab('overview');
});
