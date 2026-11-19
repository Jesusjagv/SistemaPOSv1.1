/* ======================================================
   superadmin.js — Super Admin panel: companies + requests
   with auto-polling & real-time notifications
   ====================================================== */

(function () {
  if (!requireAuth()) return;
  const user = getUser();
  if (user.role !== 'superadmin') {
    window.location.href = '/pos.html';
    return;
  }

  const nameEl = document.getElementById('user-name');
  const roleEl = document.getElementById('user-role');
  const avatarEl = document.getElementById('user-avatar');
  if (nameEl) nameEl.textContent = user.name || 'Super Admin';
  if (roleEl) roleEl.textContent = 'Super Admin';
  if (avatarEl) avatarEl.textContent = (user.name || 'S')[0].toUpperCase();

  loadBCVRate();
})();

let companies = [];
let currentTab = 'companies';
let previousPendingCount = -1; // -1 = first load, don't notify
let pollTimer = null;

/* ── Notification Sound (generated beep) ────────── */
function playNotificationSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Two-tone chime
    [520, 660].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.15, ctx.currentTime + i * 0.15);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.15 + 0.3);
      osc.start(ctx.currentTime + i * 0.15);
      osc.stop(ctx.currentTime + i * 0.15 + 0.3);
    });
  } catch (e) {
    // Audio not available, ignore silently
  }
}

/* ── Tab Switching ──────────────────────────────── */
function switchTab(tab) {
  currentTab = tab;
  document.getElementById('tab-companies').style.display = tab === 'companies' ? '' : 'none';
  document.getElementById('tab-requests').style.display = tab === 'requests' ? '' : 'none';

  document.getElementById('nav-companies').classList.toggle('active', tab === 'companies');
  document.getElementById('nav-requests').classList.toggle('active', tab === 'requests');

  if (tab === 'requests') loadRequests();
}

/* ── Companies ──────────────────────────────────── */
async function loadCompanies() {
  try {
    companies = await api.get('/superadmin/');
    renderCompanies(companies);
    updateStats(companies);
    updateRequestsBadge(companies);
    checkForNewRegistrations(companies);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function renderCompanies(list) {
  const tbody = document.getElementById('companies-tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:2rem;color:var(--text-muted);">No hay empresas registradas</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(c => {
    const active = c.active === 1 || c.active === true;
    const status = c.status || (active ? 'active' : 'rejected');
    const statusMap = {
      pending: { label: 'Demo', cls: 'badge-warning' },
      active:  { label: 'Activa', cls: 'badge-active' },
      rejected:{ label: 'Rechazada', cls: 'badge-inactive' },
      expired: { label: 'Expirada', cls: 'badge-inactive' }
    };
    const s = statusMap[status] || statusMap.active;
    const trial = c.trial_days_left !== null && c.trial_days_left !== undefined
      ? `<span style="font-size:0.8rem;color:var(--text-muted);">${c.trial_days_left}d</span>`
      : '<span style="font-size:0.8rem;color:var(--text-muted);">--</span>';

    return `<tr>
      <td><span style="color:var(--text-muted);font-size:0.8rem;">#${c.id}</span></td>
      <td><strong>${escHtml(c.name)}</strong></td>
      <td><span class="badge ${s.cls}">${s.label}</span></td>
      <td>${trial}</td>
      <td>${c.user_count}</td>
      <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
        <button class="btn btn-sm btn-outline" style="border-color:var(--violet-light);color:var(--violet-light);" onclick="openEditModal(${c.id})" title="Editar empresa">
          <i class="fa-solid fa-pen"></i>
        </button>
        ${status === 'expired' || status === 'pending'
          ? `<button class="btn btn-sm btn-outline" style="border-color:#fbbf24;color:#fbbf24;" onclick="openExtendModal(${c.id}, '${escHtml(c.name)}')" title="Extender demo">
              <i class="fa-solid fa-clock"></i>
            </button>`
          : ''
        }
        ${status === 'pending'
          ? `<button class="btn btn-sm btn-outline" style="border-color:var(--emerald-light);color:var(--emerald-light);" onclick="approveCompany(${c.id})" title="Aprobar">
              <i class="fa-solid fa-check"></i>
            </button>
            <button class="btn btn-sm btn-outline" style="border-color:var(--rose);color:var(--rose);" onclick="rejectCompany(${c.id})" title="Rechazar">
              <i class="fa-solid fa-xmark"></i>
            </button>`
          : ''
        }
        ${active
          ? `<button class="btn btn-sm btn-outline" style="border-color:var(--rose);color:var(--rose);" onclick="confirmToggle(${c.id}, 0)" title="Desactivar">
              <i class="fa-solid fa-ban"></i>
            </button>`
          : `<button class="btn btn-sm btn-outline" style="border-color:var(--emerald-light);color:var(--emerald-light);" onclick="confirmToggle(${c.id}, 1)" title="Activar">
              <i class="fa-solid fa-check"></i>
            </button>`
        }
      </td>
    </tr>`;
  }).join('');
}

function updateStats(list) {
  const total = list.length;
  const active = list.filter(c => c.status === 'active' || (c.status == null && (c.active === 1 || c.active === true))).length;
  const demo = list.filter(c => c.status === 'pending').length;
  const users = list.reduce((sum, c) => sum + (c.user_count || 0), 0);

  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-active').textContent = active;
  document.getElementById('stat-demo').textContent = demo;
  document.getElementById('stat-users').textContent = users;
}

function updateRequestsBadge(list) {
  const pending = list.filter(c => c.status === 'pending').length;
  const badge = document.getElementById('requests-badge');
  if (pending > 0) {
    badge.textContent = pending;
    badge.style.display = 'inline';
    badge.classList.add('badge-pulse');
  } else {
    badge.style.display = 'none';
    badge.classList.remove('badge-pulse');
  }
}

/* ── Detect new registrations ──────────────────── */
function checkForNewRegistrations(list) {
  const currentPending = list.filter(c => c.status === 'pending').length;

  if (previousPendingCount === -1) {
    // First load — just store count, don't notify
    previousPendingCount = currentPending;
    return;
  }

  if (currentPending > previousPendingCount) {
    const newCount = currentPending - previousPendingCount;
    const msg = newCount === 1
      ? '🔔 ¡Nueva solicitud de registro!'
      : `🔔 ¡${newCount} nuevas solicitudes de registro!`;
    showToast(msg, 'info', 5000);
    playNotificationSound();

    // If on requests tab, auto-refresh it
    if (currentTab === 'requests') {
      loadRequests();
    }
  }

  previousPendingCount = currentPending;
}

/* ── Auto-polling (every 30s) ──────────────────── */
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      companies = await api.get('/superadmin/');
      renderCompanies(companies);
      updateStats(companies);
      updateRequestsBadge(companies);
      checkForNewRegistrations(companies);

      // Also refresh requests tab if active
      if (currentTab === 'requests') {
        renderRequests(companies);
      }
    } catch (e) {
      // Silent fail on poll — don't spam errors
      console.warn('Poll failed:', e.message);
    }
  }, 30000);
}

function filterCompanies(val) {
  const term = (val || '').toLowerCase();
  if (!term) {
    renderCompanies(companies);
    return;
  }
  const filtered = companies.filter(c =>
    c.name.toLowerCase().includes(term) ||
    (c.admin_name && c.admin_name.toLowerCase().includes(term))
  );
  renderCompanies(filtered);
}

/* ── Requests Tab ───────────────────────────────── */
async function loadRequests() {
  const tbody = document.getElementById('requests-tbody');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:2rem;"><div class="spinner"></div></td></tr>';

  try {
    const all = await api.get('/superadmin/');
    companies = all; // keep in sync
    renderRequests(all);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:2rem;color:var(--rose);">Error: ${e.message}</td></tr>`;
  }
}

function renderRequests(all) {
  const tbody = document.getElementById('requests-tbody');
  const pending = all.filter(c => c.status === 'pending');
  const rejected = all.filter(c => c.status === 'rejected');

  document.getElementById('stat-pending').textContent = pending.length;
  document.getElementById('stat-rejected').textContent = rejected.length;

  // Approved today count
  const today = new Date().toISOString().split('T')[0];
  const approvedToday = all.filter(c => c.status === 'active' && c.trial_start && c.trial_start.startsWith(today)).length;
  document.getElementById('stat-approved-today').textContent = approvedToday;

  if (!pending.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:2rem;color:var(--text-muted);">
      <div style="font-size:2.5rem;margin-bottom:0.75rem;">✅</div>
      <div style="font-weight:600;">No hay solicitudes pendientes</div>
      <div style="font-size:0.8rem;margin-top:0.25rem;">Todas las solicitudes han sido procesadas</div>
    </td></tr>`;
    return;
  }

  tbody.innerHTML = pending.map(c => {
    const created = c.trial_start ? new Date(c.trial_start).toLocaleDateString('es-VE') : '--';
    const trial = c.trial_days_left !== null ? `${c.trial_days_left} dias` : '--';
    const adminName = c.admin_name || '--';

    return `<tr>
      <td><strong>${escHtml(c.name)}</strong></td>
      <td style="font-size:0.85rem;">${escHtml(adminName)}</td>
      <td style="font-size:0.85rem;">${escHtml(c.email || '--')}</td>
      <td style="font-size:0.85rem;">${created}</td>
      <td>
        <span class="badge badge-warning" style="font-size:0.75rem;">
          <i class="fa-solid fa-clock"></i> ${trial}
        </span>
      </td>
      <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
        <button class="btn btn-sm btn-outline" style="border-color:var(--emerald-light);color:var(--emerald-light);" onclick="approveCompany(${c.id})" title="Aprobar">
          <i class="fa-solid fa-check"></i> Aprobar
        </button>
        <button class="btn btn-sm btn-outline" style="border-color:#fbbf24;color:#fbbf24;" onclick="openExtendModal(${c.id}, '${escHtml(c.name)}')" title="Extender demo">
          <i class="fa-solid fa-clock"></i> Extender
        </button>
        <button class="btn btn-sm btn-outline" style="border-color:var(--rose);color:var(--rose);" onclick="rejectCompany(${c.id})" title="Rechazar">
          <i class="fa-solid fa-xmark"></i> Rechazar
        </button>
      </td>
    </tr>`;
  }).join('');
}

/* ── Company Actions ────────────────────────────── */
function openRegisterModal() {
  document.getElementById('reg-company-name').value = '';
  document.getElementById('reg-admin-name').value = '';
  document.getElementById('reg-admin-username').value = '';
  document.getElementById('reg-admin-password').value = '';
  openModal('register-modal');
}

async function registerCompany() {
  const company_name  = document.getElementById('reg-company-name').value.trim();
  const admin_name     = document.getElementById('reg-admin-name').value.trim();
  const admin_username = document.getElementById('reg-admin-username').value.trim();
  const admin_password = document.getElementById('reg-admin-password').value;

  if (!company_name || !admin_name || !admin_username || !admin_password) {
    showToast('Todos los campos son obligatorios', 'warning');
    return;
  }

  try {
    await api.post('/superadmin/', { company_name, admin_name, admin_username, admin_password });
    showToast('Empresa registrada exitosamente', 'success');
    closeModal('register-modal');
    await loadCompanies();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function confirmToggle(id, newStatus) {
  const company = companies.find(c => c.id === id);
  if (!company) return;
  const action = newStatus ? 'ACTIVAR' : 'DESACTIVAR';
  const msg = `Estas seguro de ${action.toLowerCase()} la empresa "${company.name}"?`;
  confirmAction(msg, async () => {
    try {
      await api.put(`/superadmin/${id}`, { active: newStatus });
      showToast(`Empresa ${newStatus ? 'activada' : 'desactivada'}`, 'success');
      await loadCompanies();
    } catch (e) {
      showToast(e.message, 'error');
    }
  });
}

async function approveCompany(id) {
  confirmAction('Aprobar esta empresa y dar acceso completo?', async () => {
    try {
      await api.put(`/superadmin/${id}/approve`, {});
      showToast('Empresa aprobada', 'success');
      await loadCompanies();
      if (currentTab === 'requests') await loadRequests();
    } catch (e) {
      showToast(e.message, 'error');
    }
  });
}

async function rejectCompany(id) {
  confirmAction('Rechazar esta empresa? El usuario-admin sera desactivado.', async () => {
    try {
      await api.put(`/superadmin/${id}/reject`, {});
      showToast('Empresa rechazada', 'success');
      await loadCompanies();
      if (currentTab === 'requests') await loadRequests();
    } catch (e) {
      showToast(e.message, 'error');
    }
  });
}

let extendCompanyId = null;
function openExtendModal(id, name) {
  extendCompanyId = id;
  document.getElementById('extend-company-name').textContent = name;
  document.getElementById('extend-days-input').value = 7;
  document.querySelectorAll('.extend-days-btn').forEach(b => b.classList.remove('btn-primary'));
  openModal('extend-modal');
}

function setExtendDays(days) {
  document.getElementById('extend-days-input').value = days;
  document.querySelectorAll('.extend-days-btn').forEach(b => {
    b.classList.toggle('btn-primary', parseInt(b.dataset.days) === days);
    b.classList.toggle('btn-outline', parseInt(b.dataset.days) !== days);
  });
}

async function confirmExtendTrial() {
  if (!extendCompanyId) return;
  const days = parseInt(document.getElementById('extend-days-input').value) || 7;
  try {
    await api.put(`/superadmin/${extendCompanyId}/extend-trial`, { days });
    showToast(`Demo extendida ${days} dias`, 'success');
    closeModal('extend-modal');
    await loadCompanies();
    if (currentTab === 'requests') await loadRequests();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/* ── Edit Company ───────────────────────────────── */
function openEditModal(id) {
  const company = companies.find(c => c.id === id);
  if (!company) return;
  document.getElementById('edit-company-name').value = company.name;
  document.getElementById('edit-modal').dataset.companyId = id;

  const tbody = document.getElementById('edit-users-tbody');
  tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:1rem;"><div class="spinner"></div></td></tr>';

  openModal('edit-modal');

  api.get(`/superadmin/${id}`).then(data => {
    const users = data.users || [];
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:1rem;color:var(--text-muted);">Sin usuarios</td></tr>';
      return;
    }
    tbody.innerHTML = users.map(u =>
      `<tr>
        <td><code>${escHtml(u.username)}</code></td>
        <td>${escHtml(u.name)}</td>
        <td style="font-size:0.85rem;">${escHtml(u.email || '--')}</td>
        <td><span class="badge ${u.active ? 'badge-active' : 'badge-inactive'}">${u.active ? 'Activo' : 'Inactivo'}</span></td>
      </tr>`
    ).join('');
  }).catch(e => {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:1rem;color:var(--rose);">Error: ${e.message}</td></tr>`;
  });
}

async function saveEditCompany() {
  const id = parseInt(document.getElementById('edit-modal').dataset.companyId);
  const name = document.getElementById('edit-company-name').value.trim();
  if (!name) {
    showToast('El nombre de la empresa es obligatorio', 'warning');
    return;
  }
  try {
    await api.put(`/superadmin/${id}`, { name });
    showToast('Empresa actualizada', 'success');
    closeModal('edit-modal');
    await loadCompanies();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/* ── Utils ──────────────────────────────────────── */
function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

/* ── Init ───────────────────────────────────────── */
loadCompanies();
startPolling();

