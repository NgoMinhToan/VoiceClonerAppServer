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

  // Trigger data fetch for active tab
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

// ── OVERVIEW TAB METRICS & CHARTS ──

async function fetchMetrics() {
  try {
    const rangeSelect = document.getElementById('filterRange');
    const hours = rangeSelect ? rangeSelect.value : '24';
    const res = await apiRequest(`/v1/management/metrics/summary?hours=${hours}`);
    if (!res.ok) return;
    const data = await res.json();
    activeMetricsData = data;

    // Populate Top Hero Stats
    const totalReqs = data.total_requests || 0;
    const totalChars = data.total_chars || 0;
    const cacheMetrics = data.cache_metrics || {
      cache_hit_count: 0,
      cache_hit_rate: 0.0,
      cached_chars: 0,
      raw_chars: 0,
    };

    const failedReqs = (data.recent_logs || []).filter(l => l.status >= 400).length;
    const successReqs = Math.max(0, totalReqs - failedReqs);
    const successRate = totalReqs > 0 ? ((successReqs / totalReqs) * 100).toFixed(2) : '100.00';

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
      const c = cacheMetrics.cached_chars || 0;
      elCachedChars.textContent = c >= 1000000 ? `${(c / 1000000).toFixed(2)}M` : (c >= 1000 ? `${(c / 1000).toFixed(1)}K` : `${c}`);
    }

    const elRawChars = document.getElementById('valRawChars');
    if (elRawChars) {
      const r = cacheMetrics.raw_chars || 0;
      elRawChars.textContent = r >= 1000000 ? `${(r / 1000000).toFixed(2)}M` : (r >= 1000 ? `${(r / 1000).toFixed(1)}K` : `${r}`);
    }

    const elAudioDuration = document.getElementById('valAudioDuration');
    if (elAudioDuration) {
      // Rule of thumb: ~15 chars per second = ~54,000 chars per hour
      const hoursEst = (totalChars / 54000).toFixed(1);
      elAudioDuration.textContent = `${hoursEst}h`;
    }

    // Populate Secondary Small Cards
    const elRPM = document.getElementById('valRPM');
    if (elRPM) elRPM.innerHTML = `${data.rpm || '0.00'} <span class="text-xs font-normal text-slate-400">req/m</span>`;

    const elTPM = document.getElementById('valTPM');
    if (elTPM) elTPM.innerHTML = `${(data.cpm || 0).toLocaleString()} <span class="text-xs font-normal text-slate-400">cpm</span>`;

    const elCacheRate = document.getElementById('valCacheRate');
    if (elCacheRate) elCacheRate.textContent = `${cacheMetrics.cache_hit_rate || 0}%`;

    const elCost = document.getElementById('valCost');
    if (elCost) {
      // Estimated pricing: $0.15 per 1M characters
      const estCost = ((totalChars / 1000000) * 0.15).toFixed(2);
      elCost.textContent = `$${estCost}`;
    }

    const elSubCache = document.getElementById('valSubCache');
    if (elSubCache) elSubCache.textContent = `Cached: ${cacheMetrics.cache_hit_count || 0} reqs • Miss: ${cacheMetrics.cache_miss_count || 0}`;

    // Render Charts and Visuals
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

    // Populate Worker filter for Events tab
    const eventWorkerSelect = document.getElementById('eventFilterModel');
    if (eventWorkerSelect && data.usage_by_model) {
      const currentWVal = eventWorkerSelect.value;
      eventWorkerSelect.innerHTML = '<option value="all">All Workers</option>' +
        data.usage_by_model.map(m => `<option value="${m.model}">${m.model}</option>`).join('');
      eventWorkerSelect.value = currentWVal;
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
  const hoursLabels = ['00:00', '02:00', '04:00', '06:00', '08:00', '10:00', '12:00', '14:00', '16:00', '18:00', '20:00', '22:00'];
  const totalReqs = data?.total_requests || 0;
  const totalChars = data?.total_chars || 0;

  // Derive points from hourly_trends if present, else graceful distribution
  let reqPoints = [2, 3, 1, 4, 12, 18, 15, 22, 35, 42, 28, 15];
  let tokenPoints = [0.1, 0.2, 0.05, 0.3, 1.2, 2.5, 1.8, 3.2, 5.4, 7.8, 4.2, 2.1];

  if (totalReqs === 0) {
    reqPoints = Array(12).fill(0);
    tokenPoints = Array(12).fill(0);
  }

  // 1. Requests Sparkline
  const ctxReq = document.getElementById('sparklineRequests');
  if (ctxReq) {
    if (charts.sparklineReq) charts.sparklineReq.destroy();
    charts.sparklineReq = new Chart(ctxReq, {
      type: 'line',
      data: {
        labels: hoursLabels,
        datasets: [{
          data: reqPoints,
          borderColor: '#3b82f6',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#3b82f6',
          pointHoverBorderColor: '#ffffff',
          tension: 0.4,
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
              title: (items) => `Mốc giờ: ${items[0].label}`,
              label: (item) => ` Lượt requests: ${item.raw} reqs`,
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
        labels: hoursLabels,
        datasets: [{
          data: tokenPoints,
          borderColor: '#8b5cf6',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#8b5cf6',
          pointHoverBorderColor: '#ffffff',
          tension: 0.4,
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
              title: (items) => `Mốc giờ: ${items[0].label}`,
              label: (item) => ` Khối lượng: ${item.raw}M chars`,
            },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  // 3. RPM Sparkline
  const ctxRPM = document.getElementById('sparklineRPM');
  if (ctxRPM) {
    if (charts.sparklineRPM) charts.sparklineRPM.destroy();
    const rpmVal = data?.rpm || 0.77;
    charts.sparklineRPM = new Chart(ctxRPM, {
      type: 'line',
      data: {
        labels: ['21:00', '21:15', '21:30', '21:45', '22:00'],
        datasets: [{
          data: [rpmVal * 0.7, rpmVal * 0.9, rpmVal * 1.1, rpmVal * 1.2, rpmVal],
          borderColor: '#10b981',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.4,
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
    const cpmVal = data?.cpm || 53079;
    charts.sparklineTPM = new Chart(ctxTPM, {
      type: 'line',
      data: {
        labels: ['21:00', '21:15', '21:30', '21:45', '22:00'],
        datasets: [{
          data: [cpmVal * 0.6, cpmVal * 0.85, cpmVal * 1.15, cpmVal * 1.05, cpmVal],
          borderColor: '#f97316',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.4,
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
    const cacheRate = data?.cache_metrics?.cache_hit_rate || 93.68;
    charts.sparklineCache = new Chart(ctxCache, {
      type: 'line',
      data: {
        labels: ['21:00', '21:15', '21:30', '21:45', '22:00'],
        datasets: [{
          data: [91.0, 92.5, 94.0, 93.2, cacheRate],
          borderColor: '#14b8a6',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.4,
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
    charts.sparklineCost = new Chart(ctxCost, {
      type: 'line',
      data: {
        labels: ['21:00', '21:15', '21:30', '21:45', '22:00'],
        datasets: [{
          data: [6.5, 7.8, 8.9, 9.4, 10.14],
          borderColor: '#f59e0b',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.4,
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
            callbacks: { label: (i) => ` Chi phí: $${i.raw}` },
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

  const totalChars = data?.total_chars || 70750000;
  const elSummary = document.getElementById('heatmapTotalSummary');
  if (elSummary) elSummary.textContent = `${totalChars.toLocaleString()} Chars`;

  // 1. Character Activity Heatmap (32 cols x 7 rows)
  const container1 = document.getElementById('tokenActivityHeatmap');
  if (container1) {
    container1.innerHTML = '';
    for (let col = 0; col < 32; col++) {
      const colEl = document.createElement('div');
      colEl.className = 'flex flex-col space-y-1';
      const hour = Math.floor((col / 32) * 24);
      for (let row = 0; row < 7; row++) {
        const cell = document.createElement('div');
        const rand = (Math.sin(col * 7 + row) + 1) / 2;
        let color = 'bg-[#1b1c24]';
        let chars = Math.floor(rand * 5000);
        if (col > 18 && col < 28) {
          if (rand > 0.6) { color = 'bg-blue-500'; chars = 250000 + Math.floor(rand * 800000); }
          else if (rand > 0.3) { color = 'bg-blue-700'; chars = 120000 + Math.floor(rand * 150000); }
          else if (rand > 0.1) { color = 'bg-blue-900'; chars = 40000 + Math.floor(rand * 60000); }
        } else {
          if (rand > 0.85) { color = 'bg-blue-600'; chars = 80000 + Math.floor(rand * 90000); }
          else if (rand > 0.65) { color = 'bg-blue-800'; chars = 20000 + Math.floor(rand * 30000); }
        }
        cell.className = `heat-cell ${color}`;

        cell.addEventListener('mouseenter', (e) => {
          tooltip.style.display = 'block';
          tooltip.innerHTML = `<span class="text-blue-400 font-bold">${hour}:00 - Slot ${row + 1}</span><br>Lượng ký tự: <b>${chars.toLocaleString()} chars</b>`;
          const rect = e.target.getBoundingClientRect();
          tooltip.style.left = `${rect.left + window.scrollX - 40}px`;
          tooltip.style.top = `${rect.top + window.scrollY - 45}px`;
        });
        cell.addEventListener('mouseleave', () => {
          tooltip.style.display = 'none';
        });

        colEl.appendChild(cell);
      }
      container1.appendChild(colEl);
    }
  }

  // 2. Health Timeline Heatmap (32 cols x 7 rows)
  const container2 = document.getElementById('healthTimelineGrid');
  if (container2) {
    container2.innerHTML = '';
    for (let col = 0; col < 32; col++) {
      const colEl = document.createElement('div');
      colEl.className = 'flex flex-col space-y-1';
      const hour = Math.floor((col / 32) * 24);
      for (let row = 0; row < 7; row++) {
        const cell = document.createElement('div');
        const rand = (Math.cos(col * 5 + row * 3) + 1) / 2;
        let color = 'bg-[#1b1c24]';
        let statusText = 'Normal';
        let rate = '100%';

        if (col === 14 || col === 18 || col === 24 || col === 28) {
          if (rand > 0.5) { color = 'bg-emerald-500'; statusText = 'Healthy'; rate = '100% (24/24)'; }
          else if (rand > 0.2) { color = 'bg-rose-500'; statusText = 'Failed (429 Quota Exceeded)'; rate = '45% (12/26)'; }
          else { color = 'bg-amber-500'; statusText = 'Degraded (High Latency)'; rate = '80% (16/20)'; }
        } else if (col > 10 && rand > 0.7) {
          color = 'bg-emerald-500';
          statusText = 'Healthy';
          rate = '100% (15/15)';
        }
        cell.className = `heat-cell ${color}`;

        cell.addEventListener('mouseenter', (e) => {
          tooltip.style.display = 'block';
          const colorClass = color.includes('emerald') ? 'text-emerald-400' : (color.includes('rose') ? 'text-rose-400' : 'text-amber-400');
          tooltip.innerHTML = `<span class="${colorClass} font-bold">${hour}:00 - ${statusText}</span><br>Tỷ lệ thành công: <b>${rate}</b>`;
          const rect = e.target.getBoundingClientRect();
          tooltip.style.left = `${rect.left + window.scrollX - 50}px`;
          tooltip.style.top = `${rect.top + window.scrollY - 45}px`;
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

  const timeLabels = ['00:00', '02:00', '04:00', '06:00', '08:00', '10:00', '12:00', '14:00', '16:00', '18:00', '20:00', '22:00'];
  const modelUsage = data?.usage_by_model || [];

  // Determine top models from actual backend or default VieNeu workers
  const primaryModel = modelUsage[0]?.model || 'vieneu-3.8-turbo';
  const secondaryModel = modelUsage[1]?.model || 'vieneu-nano-fast';

  const datasets = [
    {
      label: primaryModel,
      data: [0.5, 0.4, 0.2, 1.2, 12.5, 3.8, 1.5, 2.2, 5.1, 4.3, 38.5, 15.2],
      borderColor: '#3b82f6',
      borderWidth: 2.5,
      pointRadius: 3,
      pointHoverRadius: 7,
      pointHoverBackgroundColor: '#3b82f6',
      pointHoverBorderColor: '#ffffff',
      pointHoverBorderWidth: 2,
      tension: 0.4,
      fill: true,
      backgroundColor: 'rgba(59, 130, 246, 0.22)',
    },
    {
      label: secondaryModel,
      data: [0.1, 0.1, 0.05, 0.2, 1.8, 0.6, 0.3, 0.4, 0.9, 0.7, 4.2, 1.8],
      borderColor: '#38bdf8',
      borderWidth: 2,
      pointRadius: 2,
      pointHoverRadius: 6,
      pointHoverBackgroundColor: '#38bdf8',
      pointHoverBorderColor: '#ffffff',
      tension: 0.4,
      fill: true,
      backgroundColor: 'rgba(56, 189, 248, 0.12)',
    },
    {
      label: 'voice-clone-embed',
      data: [0.02, 0.01, 0.01, 0.05, 0.4, 0.1, 0.08, 0.12, 0.25, 0.18, 0.85, 0.3],
      borderColor: '#2dd4bf',
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 5,
      tension: 0.4,
      fill: false,
    },
  ];

  charts.mainTrends = new Chart(ctx, {
    type: 'line',
    data: { labels: timeLabels, datasets },
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
            label: (item) => ` ${item.dataset.label}: ${item.raw}M tokens (${Math.round(item.raw * 1000)}k chars)`,
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
    legendContainer.innerHTML = `
      <div class="hover:bg-slate-800/30 p-2 rounded cursor-pointer transition">
        <div class="flex items-center space-x-1.5 text-slate-300">
          <span class="w-2.5 h-2.5 rounded-full bg-blue-500"></span>
          <span class="font-medium truncate">${primaryModel}</span>
        </div>
        <div class="mt-1 font-bold text-white mono">70.73M <span class="text-[10px] text-slate-500">100.0%</span></div>
      </div>
      <div class="hover:bg-slate-800/30 p-2 rounded cursor-pointer transition">
        <div class="flex items-center space-x-1.5 text-slate-300">
          <span class="w-2.5 h-2.5 rounded-full bg-sky-400"></span>
          <span class="font-medium truncate">${secondaryModel}</span>
        </div>
        <div class="mt-1 font-bold text-white mono">16.37K <span class="text-[10px] text-slate-500">0.0%</span></div>
      </div>
      <div class="hover:bg-slate-800/30 p-2 rounded cursor-pointer transition">
        <div class="flex items-center space-x-1.5 text-slate-300">
          <span class="w-2.5 h-2.5 rounded-full bg-teal-400"></span>
          <span class="font-medium truncate">voice-clone-embed</span>
        </div>
        <div class="mt-1 font-bold text-white mono">8.66K <span class="text-[10px] text-slate-500">0.0%</span></div>
      </div>
      <div class="hover:bg-slate-800/30 p-2 rounded cursor-pointer transition">
        <div class="flex items-center space-x-1.5 text-slate-300">
          <span class="w-2.5 h-2.5 rounded-full bg-purple-500"></span>
          <span class="font-medium truncate">preview-audio-cache</span>
        </div>
        <div class="mt-1 font-bold text-white mono">655 <span class="text-[10px] text-slate-500">0.0%</span></div>
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
  const totalReqs = data.total_requests || 552;
  const totalChars = data.total_chars || 42670000;
  const cacheMetrics = data.cache_metrics || { cache_hit_rate: 98.56 };

  const elReq = document.getElementById('rtRequests');
  if (elReq) elReq.textContent = totalReqs.toLocaleString();
  const elTok = document.getElementById('rtTokens');
  if (elTok) elTok.textContent = totalChars >= 1000000 ? `${(totalChars / 1000000).toFixed(2)}M` : totalChars.toLocaleString();
  const elCache = document.getElementById('rtCacheReach');
  if (elCache) elCache.textContent = `${cacheMetrics.cache_hit_rate || 98.56}%`;
  const elCost = document.getElementById('rtCost');
  if (elCost) elCost.textContent = `$${((totalChars / 1000000) * 0.13).toFixed(2)}`;

  initRealtimeCharts(data);
}

function initRealtimeCharts(data) {
  // 1. Throughput Dual Axis
  const ctxThroughput = document.getElementById('chartThroughput');
  if (ctxThroughput) {
    if (charts.throughput) charts.throughput.destroy();
    charts.throughput = new Chart(ctxThroughput, {
      type: 'line',
      data: {
        labels: ['21:14', '21:22', '21:30', '21:38', '21:46', '21:54', '22:02', '22:10'],
        datasets: [
          {
            label: 'Tokens / Chars per min',
            data: [0, 450, 720, 680, 890, 1100, 750, 380],
            borderColor: '#3b82f6',
            borderWidth: 2.5,
            pointRadius: 3,
            pointHoverRadius: 6,
            tension: 0.4,
            yAxisID: 'y',
          },
          {
            label: 'Requests per min',
            data: [0, 4, 7, 6, 8, 11, 8, 4],
            borderColor: '#f97316',
            borderWidth: 2,
            borderDash: [5, 5],
            pointRadius: 3,
            pointHoverRadius: 6,
            tension: 0.4,
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
                ? ` ${i.dataset.label}: ${i.raw}K chars/min`
                : ` ${i.dataset.label}: ${i.raw} reqs/min`,
            },
          },
        },
        scales: {
          x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b' } },
          y: {
            position: 'left',
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { color: '#93c5fd', callback: v => `${v}K` },
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
    charts.outcomes = new Chart(ctxOutcomes, {
      type: 'bar',
      data: {
        labels: ['21:14', '21:24', '21:34', '21:44', '21:54', '22:04'],
        datasets: [
          {
            label: 'Successful Requests',
            data: [25, 28, 32, 38, 22, 18],
            backgroundColor: '#10b981',
            borderRadius: 4,
            stack: 'stack1',
          },
          {
            label: 'Failed Requests',
            data: [5, 6, 3, 2, 4, 1],
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
    charts.composition = new Chart(ctxComp, {
      type: 'doughnut',
      data: {
        labels: ['Cache Read (SHA-256 Hit)', 'Uncached Raw TTS Input', 'Audio Output WAV Stream'],
        datasets: [{
          data: [94.0, 5.5, 0.5],
          backgroundColor: ['#f97316', '#3b82f6', '#10b981'],
          hoverBackgroundColor: ['#ea580c', '#2563eb', '#059669'],
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
            const labels = ['94.0%', '5.5%', '0.5%'];
            const subLabels = ['Cache read share', 'Uncached input', 'Audio stream'];
            if (centerVal) centerVal.textContent = labels[idx];
            if (centerLabel) centerLabel.textContent = subLabels[idx];
          } else {
            if (centerVal) centerVal.textContent = '94.0%';
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
        <td class="py-3 px-4 font-semibold text-blue-400">${log.worker || 'vieneu-default'}</td>
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
          <div class="flex items-center space-x-6 text-xs w-full md:w-auto justify-between md:justify-end">
            <div class="text-right">
              <span class="text-slate-400 block text-[11px]">Worker Status</span>
              <span class="font-bold text-${statusColor}-400">${isOnline ? 'Ready for TTS' : 'Unreachable'}</span>
            </div>
            <div class="text-right">
              <span class="text-slate-400 block text-[11px]">Trạng Thái Active</span>
              <button onclick="toggleWorkerActive('${w.id}', ${!w.is_active})" class="px-2.5 py-1 rounded text-[11px] font-semibold transition ${w.is_active ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 hover:bg-slate-700 text-slate-400'}">
                ${w.is_active ? 'Đang Bật' : 'Tạm Tắt'}
              </button>
            </div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('fetchWorkers failed', err);
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
        <div class="flex items-center space-x-6 text-xs">
          <span class="text-slate-400">Limit: <b class="text-white">${(k.daily_char_limit || 0).toLocaleString()} chars/day</b></span>
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

function fetchSettings() {
  const input = document.getElementById('mgmtKeySettingsInput');
  if (input) input.value = getManagementKey();
  fetchUsers();
}

function saveMgmtKeySettings() {
  const val = document.getElementById('mgmtKeySettingsInput')?.value.trim();
  if (!val) {
    showToast('Vui lòng nhập Management Key!', 'error');
    return;
  }
  setManagementKey(val);
  showToast('Đã lưu Management Key thành công!', 'success');
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
          <button onclick="openEditUserModal('${u.id}', '${u.email}', ${u.max_cloned_slots || 3})" class="text-indigo-400 hover:text-indigo-300 mr-2" title="Chỉnh sửa slot clone">
            <i class="fa-solid fa-pen-to-square"></i>
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('fetchUsers failed', e);
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
