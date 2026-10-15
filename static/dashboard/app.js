// VoiceClonerAppServer - Management Console Frontend Logic (CLIProxyAPI Style)

let currentTab = 'metrics';
let usageLineChart = null;
let modelDoughnutChart = null;

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
  toast.className = `${bg} text-white px-4 py-2.5 rounded-lg shadow-xl text-sm font-medium flex items-center space-x-2 transition-all duration-300 transform translate-y-2 opacity-0`;
  toast.innerHTML = `<i class="fa-solid fa-${type === 'success' ? 'check' : (type === 'error' ? 'circle-exclamation' : 'circle-info')}"></i><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  }, 10);
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

function switchTab(tabId) {
  currentTab = tabId;
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.sidebar-nav-item').forEach(el => el.classList.remove('active'));

  const target = document.getElementById(`tab-content-${tabId}`);
  const navBtn = document.getElementById(`nav-btn-${tabId}`);
  if (target) target.classList.remove('hidden');
  if (navBtn) navBtn.classList.add('active');

  if (tabId === 'metrics') fetchMetrics();
  else if (tabId === 'users') fetchUsers();
  else if (tabId === 'keys') fetchKeys();
  else if (tabId === 'workers') fetchWorkers();
  else if (tabId === 'settings') fetchSettings();
}

async function fetchMetrics() {
  try {
    const res = await apiRequest('/v1/management/metrics/summary?hours=24');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('statTotalRequests').textContent = (data.total_requests || 0).toLocaleString();
    document.getElementById('statTotalChars').textContent = (data.total_chars || 0).toLocaleString();
    document.getElementById('statAvgLatency').textContent = `${data.average_latency_ms || 0} ms`;
    document.getElementById('statActiveKeys').textContent = data.active_keys_count || 0;
    document.getElementById('statActiveWorkers').textContent = data.active_workers_count || 0;

    renderMetricsCharts(data);
    renderRecentLogs(data.recent_logs || []);
  } catch (e) {
    console.error('fetchMetrics error', e);
  }
}

function renderMetricsCharts(data) {
  // 1. Line Chart
  const ctxLine = document.getElementById('chartUsageLine');
  if (ctxLine) {
    if (usageLineChart) usageLineChart.destroy();

    const keyUsage = data.usage_by_key || [];
    const labels = keyUsage.map(k => k.name);
    const charData = keyUsage.map(k => k.chars);
    const reqData = keyUsage.map(k => k.requests);

    usageLineChart = new Chart(ctxLine, {
      type: 'bar',
      data: {
        labels: labels.length ? labels : ['Chưa có dữ liệu'],
        datasets: [
          {
            label: 'Số Ký Tự TTS',
            data: charData.length ? charData : [0],
            backgroundColor: 'rgba(99, 102, 241, 0.65)',
            borderColor: '#6366f1',
            borderWidth: 1,
            yAxisID: 'y',
          },
          {
            label: 'Số Lượt Request',
            data: reqData.length ? reqData : [0],
            backgroundColor: 'rgba(16, 185, 129, 0.65)',
            borderColor: '#10b981',
            borderWidth: 1,
            yAxisID: 'y1',
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#94a3b8' } }
        },
        scales: {
          x: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } },
          y: { position: 'left', ticks: { color: '#818cf8' }, grid: { color: '#1e293b' } },
          y1: { position: 'right', ticks: { color: '#34d399' }, grid: { drawOnChartArea: false } }
        }
      }
    });
  }

  // 2. Doughnut Chart for Model Distribution
  const ctxDoughnut = document.getElementById('chartModelDoughnut');
  if (ctxDoughnut) {
    if (modelDoughnutChart) modelDoughnutChart.destroy();

    const modelUsage = data.usage_by_model || [];
    const labels = modelUsage.map(m => m.model);
    const values = modelUsage.map(m => m.requests);

    modelDoughnutChart = new Chart(ctxDoughnut, {
      type: 'doughnut',
      data: {
        labels: labels.length ? labels : ['Chưa có dữ liệu'],
        datasets: [{
          data: values.length ? values : [1],
          backgroundColor: ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6'],
          borderColor: '#111827',
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { color: '#94a3b8' } }
        }
      }
    });
  }
}

function renderRecentLogs(logs) {
  const tbody = document.getElementById('recentLogsTableBody');
  if (!tbody) return;
  if (!logs.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-500 text-xs">Chưa có request nào được ghi nhận.</td></tr>`;
    return;
  }
  tbody.innerHTML = logs.map(l => `
    <tr class="table-row-hover border-b border-slate-800 text-xs">
      <td class="py-2.5 px-3 text-slate-400">${new Date(l.time).toLocaleTimeString()}</td>
      <td class="py-2.5 px-3 font-medium text-slate-200">${l.key_name}</td>
      <td class="py-2.5 px-3 mono text-indigo-400">${l.endpoint}</td>
      <td class="py-2.5 px-3 text-slate-300">${l.worker || 'local'}</td>
      <td class="py-2.5 px-3 font-semibold text-slate-100">${(l.chars || 0).toLocaleString()}</td>
      <td class="py-2.5 px-3 text-slate-400">${l.latency_ms} ms</td>
      <td class="py-2.5 px-3">
        <span class="px-2 py-0.5 rounded text-[10px] font-semibold ${l.status === 200 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'}">${l.status}</span>
      </td>
    </tr>
  `).join('');
}

// ── USERS MANAGEMENT ──

async function fetchUsers() {
  try {
    const res = await apiRequest('/v1/management/users');
    if (!res.ok) return;
    const users = await res.json();
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;

    if (!users.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-500 text-sm">Chưa có người dùng nào đăng ký trong database.</td></tr>`;
      return;
    }

    tbody.innerHTML = users.map(u => `
      <tr class="table-row-hover border-b border-slate-800 text-sm">
        <td class="py-3 px-4">
          <div class="flex items-center space-x-2.5">
            <div class="w-8 h-8 rounded-full bg-indigo-600/20 text-indigo-400 flex items-center justify-center font-bold text-xs">
              ${(u.full_name || u.email || 'U')[0].toUpperCase()}
            </div>
            <div>
              <div class="font-bold text-slate-200 text-xs">${u.full_name || 'Chưa đặt tên'}</div>
              <div class="text-[11px] text-slate-400 mono">${u.email}</div>
            </div>
          </div>
        </td>
        <td class="py-3 px-4">
          <span class="px-2 py-0.5 rounded text-[10px] font-semibold ${u.auth_provider === 'google' ? 'bg-rose-500/10 text-rose-300 border border-rose-500/30' : 'bg-slate-700 text-slate-300'}">
            ${u.auth_provider === 'google' ? '<i class="fa-brands fa-google mr-1"></i> Google' : '<i class="fa-solid fa-envelope mr-1"></i> Email'}
          </span>
        </td>
        <td class="py-3 px-4 font-semibold text-slate-100">${u.max_cloned_slots} slots</td>
        <td class="py-3 px-4 font-semibold text-emerald-400">${(u.tts_chars_today || 0).toLocaleString()} ký tự</td>
        <td class="py-3 px-4 text-xs text-slate-500">${u.created_at ? new Date(u.created_at).toLocaleDateString() : 'N/A'}</td>
        <td class="py-3 px-4 text-right space-x-2">
          <button onclick="openEditUserModal('${u.id}', '${u.full_name || u.email}', ${u.max_cloned_slots})" class="px-2.5 py-1 text-xs rounded bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 transition">
            <i class="fa-solid fa-pen mr-1"></i> Sửa Slot
          </button>
          <button onclick="deleteUser('${u.id}')" class="px-2.5 py-1 text-xs rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition">
            <i class="fa-solid fa-trash-can mr-1"></i> Xóa
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('fetchUsers error', e);
  }
}

function openEditUserModal(userId, fullName, slots) {
  document.getElementById('editUserId').value = userId;
  document.getElementById('editUserDisplayName').textContent = fullName;
  document.getElementById('editUserSlots').value = slots;
  document.getElementById('modalEditUser').classList.remove('hidden');
}

async function saveUserEdit() {
  const userId = document.getElementById('editUserId').value;
  const slots = parseInt(document.getElementById('editUserSlots').value) || 3;
  try {
    const res = await apiRequest(`/v1/management/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify({ max_cloned_slots: slots })
    });
    if (res.ok) {
      showToast('Đã cập nhật slot người dùng thành công!', 'success');
      document.getElementById('modalEditUser').classList.add('hidden');
      fetchUsers();
    }
  } catch (e) {
    console.error(e);
  }
}

async function deleteUser(userId) {
  if (!confirm('Bạn có chắc chắn muốn xóa tài khoản người dùng này không?')) return;
  try {
    const res = await apiRequest(`/v1/management/users/${userId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Đã xóa người dùng thành công!', 'success');
      fetchUsers();
    }
  } catch (e) {
    console.error(e);
  }
}

// ── CLIENT KEYS ──

async function fetchKeys() {
  try {
    const res = await apiRequest('/v1/management/keys');
    if (!res.ok) return;
    const keys = await res.json();
    const tbody = document.getElementById('keysTableBody');
    if (!tbody) return;

    if (!keys.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-500 text-sm">Chưa có Client API Key nào. Hãy tạo key mới.</td></tr>`;
      return;
    }

    tbody.innerHTML = keys.map(k => `
      <tr class="table-row-hover border-b border-slate-800 text-sm">
        <td class="py-3 px-4 font-semibold text-slate-200">${k.name}</td>
        <td class="py-3 px-4 mono text-xs text-indigo-300">
          <span>${k.key.slice(0, 12)}••••••••••••••••</span>
          <button onclick="navigator.clipboard.writeText('${k.key}'); showToast('Đã sao chép API Key!', 'success')" class="ml-2 text-slate-400 hover:text-white" title="Copy Key">
            <i class="fa-regular fa-copy"></i>
          </button>
        </td>
        <td class="py-3 px-4 text-xs text-slate-400">${(k.daily_char_limit || 0).toLocaleString()} ký tự/ngày</td>
        <td class="py-3 px-4">
          <button onclick="toggleKey('${k.id}', ${!k.is_active})" class="px-2.5 py-1 rounded text-xs font-semibold ${k.is_active ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-700 text-slate-400'}">
            ${k.is_active ? 'Đang hoạt động' : 'Tạm khóa'}
          </button>
        </td>
        <td class="py-3 px-4 text-xs text-slate-500">${k.created_at ? new Date(k.created_at).toLocaleDateString() : 'N/A'}</td>
        <td class="py-3 px-4 text-right">
          <button onclick="revokeKey('${k.id}')" class="px-2.5 py-1 text-xs rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 transition">
            <i class="fa-solid fa-trash-can mr-1"></i> Thu hồi
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('fetchKeys error', e);
  }
}

async function createKey(name, quota) {
  try {
    const res = await apiRequest('/v1/management/keys', {
      method: 'POST',
      body: JSON.stringify({ name, daily_char_limit: parseInt(quota) || 50000 })
    });
    if (res.ok) {
      const data = await res.json();
      showToast('Đã tạo API Key thành công!', 'success');
      showNewKeyModal(data.name, data.key);
      fetchKeys();
    }
  } catch (e) {
    console.error(e);
  }
}

async function revokeKey(keyId) {
  if (!confirm('Bạn có chắc chắn muốn thu hồi và xóa Client API Key này không?')) return;
  try {
    const res = await apiRequest(`/v1/management/keys/${keyId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Đã xóa API Key thành công', 'success');
      fetchKeys();
    }
  } catch (e) {
    console.error(e);
  }
}

async function toggleKey(keyId, newState) {
  try {
    const res = await apiRequest(`/v1/management/keys/${keyId}/toggle?is_active=${newState}`, { method: 'PATCH' });
    if (res.ok) {
      showToast('Đã cập nhật trạng thái API Key', 'success');
      fetchKeys();
    }
  } catch (e) {
    console.error(e);
  }
}

// ── WORKERS ──

async function fetchWorkers() {
  try {
    const res = await apiRequest('/v1/management/workers');
    if (!res.ok) return;
    const workers = await res.json();
    const tbody = document.getElementById('workersTableBody');
    if (!tbody) return;

    if (!workers.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-500 text-sm">Chưa có Worker Node nào được cấu hình.</td></tr>`;
      return;
    }

    tbody.innerHTML = workers.map(w => {
      const isOnline = w.telemetry && w.telemetry.status === 'online';
      return `
        <tr class="table-row-hover border-b border-slate-800 text-sm">
          <td class="py-3 px-4 font-semibold text-slate-200">${w.id}</td>
          <td class="py-3 px-4 mono text-xs text-indigo-300">${w.url}</td>
          <td class="py-3 px-4 mono text-xs text-slate-400">${w.api_key_masked}</td>
          <td class="py-3 px-4 text-xs font-semibold text-slate-300">Weight: ${w.weight}</td>
          <td class="py-3 px-4">
            <span class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${isOnline ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'}">
              <span class="w-2 h-2 rounded-full ${isOnline ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}"></span>
              <span>${isOnline ? `Online (${w.telemetry.latency_ms} ms)` : 'Offline'}</span>
            </span>
          </td>
          <td class="py-3 px-4 text-right">
            <button onclick="testWorker('${w.url}')" class="px-2.5 py-1 text-xs rounded bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 transition">
              <i class="fa-solid fa-bolt mr-1"></i> Ping Test
            </button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error(e);
  }
}

async function testWorker(url) {
  try {
    showToast(`Đang ping kiểm tra ${url}...`, 'info');
    const res = await apiRequest('/v1/management/workers/test', {
      method: 'POST',
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (data.status === 'online') {
      showToast(`Worker Online! Độ trễ: ${data.latency_ms} ms (Mode: ${data.engine_mode})`, 'success');
    } else {
      showToast(`Worker Offline: ${data.error || 'Connection refused'}`, 'error');
    }
  } catch (e) {
    showToast('Lỗi khi thực hiện ping test', 'error');
  }
}

// ── SETTINGS ──

async function fetchSettings() {
  try {
    const res = await apiRequest('/v1/management/settings');
    if (!res.ok) return;
    const s = await res.json();

    document.getElementById('cfgHost').value = s.server?.host || '0.0.0.0';
    document.getElementById('cfgPort').value = s.server?.port || 8000;
    document.getElementById('cfgManagementKey').value = s.security?.management_key || '';
    document.getElementById('cfgRequireClientKey').checked = !!s.security?.require_client_api_key;
    document.getElementById('cfgMaxConcurrent').value = s.concurrency?.max_concurrent_tts || 2;
    document.getElementById('cfgMaxQueue').value = s.concurrency?.max_queue_waiters || 8;
    document.getElementById('cfgDailyLimit').value = s.concurrency?.daily_tts_char_limit || 100000;
    document.getElementById('cfgRateLimit').value = s.concurrency?.rate_limit_requests_per_minute || 60;
    document.getElementById('cfgCacheSize').value = s.audio_cache?.max_size_mb || 2048;
    document.getElementById('cfgCacheTtl').value = s.audio_cache?.ttl_days || 7;

    document.getElementById('jsonSettingsViewer').textContent = JSON.stringify(s, null, 2);
  } catch (e) {
    console.error(e);
  }
}

async function saveSettings(e) {
  if (e) e.preventDefault();
  const payload = {
    server: {
      host: document.getElementById('cfgHost').value.trim(),
      port: parseInt(document.getElementById('cfgPort').value) || 8000,
    },
    security: {
      management_key: document.getElementById('cfgManagementKey').value.trim(),
      require_client_api_key: document.getElementById('cfgRequireClientKey').checked,
    },
    concurrency: {
      max_concurrent_tts: parseInt(document.getElementById('cfgMaxConcurrent').value) || 2,
      max_queue_waiters: parseInt(document.getElementById('cfgMaxQueue').value) || 8,
      daily_tts_char_limit: parseInt(document.getElementById('cfgDailyLimit').value) || 100000,
      rate_limit_requests_per_minute: parseInt(document.getElementById('cfgRateLimit').value) || 60,
    },
    audio_cache: {
      max_size_mb: parseInt(document.getElementById('cfgCacheSize').value) || 2048,
      ttl_days: parseInt(document.getElementById('cfgCacheTtl').value) || 7,
      clean_interval_sec: 3600,
    }
  };

  try {
    const res = await apiRequest('/v1/management/settings', {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      showToast('Cấu hình đã được lưu và Hot-Reload thành công!', 'success');
      if (payload.security.management_key) {
        setManagementKey(payload.security.management_key);
      }
      fetchSettings();
    }
  } catch (e) {
    console.error(e);
  }
}

function showNewKeyModal(name, key) {
  const modal = document.getElementById('newKeyModal');
  if (!modal) return;
  document.getElementById('newKeyDisplayName').textContent = name;
  document.getElementById('newKeyTokenVal').value = key;
  modal.classList.remove('hidden');
}

function closeNewKeyModal() {
  document.getElementById('newKeyModal').classList.add('hidden');
}

function openAuthModal() {
  const modal = document.getElementById('authModal');
  if (!modal) return;
  document.getElementById('authModalKeyInput').value = getManagementKey();
  modal.classList.remove('hidden');
}

function saveAuthKey() {
  const val = document.getElementById('authModalKeyInput').value.trim();
  setManagementKey(val);
  document.getElementById('authModal').classList.add('hidden');
  showToast('Đã lưu Management Key!', 'success');
  switchTab(currentTab);
}

document.addEventListener('DOMContentLoaded', () => {
  const linkWebDemo = document.getElementById('linkWebDemo');
  if (linkWebDemo) {
    linkWebDemo.href = `${window.location.protocol}//${window.location.hostname}:3000`;
  }
  switchTab('metrics');
});
