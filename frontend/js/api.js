/* ======================================================
   api.js — Shared HTTP client for the POS backend
   ====================================================== */

const BASE_URL = '';  // Same origin (Flask serves frontend)

function getToken() {
  return localStorage.getItem('pos_token');
}

function getUser() {
  try { return JSON.parse(localStorage.getItem('pos_user')) || {}; }
  catch { return {}; }
}

function saveSession(token, user) {
  localStorage.setItem('pos_token', token);
  localStorage.setItem('pos_user', JSON.stringify(user));
}

function clearSession() {
  localStorage.removeItem('pos_token');
  localStorage.removeItem('pos_user');
}

function isLoggedIn() {
  return !!getToken();
}

function requireAuth() {
  if (!isLoggedIn()) {
    window.location.href = '/index.html';
    return false;
  }
  return true;
}

async function apiRequest(method, path, body = null) {
  const token = getToken();
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {})
    }
  };
  if (body && method !== 'GET') {
    opts.body = JSON.stringify(body);
  }

  try {
    const resp = await fetch(`${BASE_URL}/api${path}`, opts);
    const data = await resp.json();

    if (resp.status === 401) {
      clearSession();
      window.location.href = '/index.html';
      return null;
    }
    if (!resp.ok) {
      throw new Error(data.error || data.msg || `Error ${resp.status}`);
    }
    return data;
  } catch (err) {
    if (err.name === 'TypeError') {
      throw new Error('No se puede conectar con el servidor. Verifica que Flask esté corriendo.');
    }
    throw err;
  }
}

const api = {
  get:    (path)        => apiRequest('GET',    path),
  post:   (path, body)  => apiRequest('POST',   path, body),
  put:    (path, body)  => apiRequest('PUT',    path, body),
  patch:  (path, body)  => apiRequest('PATCH',  path, body),
  delete: (path)        => apiRequest('DELETE', path),
};

/* ── Toast Notification System ───────────────────── */
function showToast(message, type = 'info', duration = 3500) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span> ${message}`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('out');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

/* ── Currency Formatters ─────────────────────────── */
function fmtUSD(amount) {
  if (amount === null || amount === undefined) return '$ 0.00';
  return `$ ${parseFloat(amount).toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtBS(amount) {
  if (amount === null || amount === undefined) return 'Bs 0,00';
  return `Bs ${parseFloat(amount).toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function toBs(usd, rate) {
  return parseFloat((usd * rate).toFixed(2));
}

/* ── Init common header elements ─────────────────── */
function initHeader() {
  const user = getUser();
  const nameEl   = document.getElementById('user-name');
  const roleEl   = document.getElementById('user-role');
  const avatarEl = document.getElementById('user-avatar');

  if (nameEl)   nameEl.textContent   = user.name || 'Usuario';
  if (roleEl)   roleEl.textContent   = user.role === 'admin' ? 'Admin' : 'Cajero';
  if (avatarEl) avatarEl.textContent = (user.name || 'U')[0].toUpperCase();

  // Load BCV rate
  loadBCVRate();
}

let currentRate = 0;

async function loadBCVRate() {
  try {
    const data = await api.get('/exchange/rate');
    currentRate = data.rate;
    const el = document.getElementById('bcv-rate');
    if (el) el.textContent = data.rate.toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const dateEl = document.getElementById('bcv-date');
    if (dateEl) dateEl.textContent = data.date;
  } catch (e) {
    console.warn('BCV rate load failed:', e.message);
  }
  return currentRate;
}

function getRate() { return currentRate; }

/* ── Logout ──────────────────────────────────────── */
function logout() {
  clearSession();
  window.location.href = '/index.html';
}

/* ── Date/Time ───────────────────────────────────── */
function initClock(elementId) {
  function update() {
    const el = document.getElementById(elementId);
    if (!el) return;
    const now = new Date();
    el.textContent = now.toLocaleString('es-VE', {
      weekday: 'short', day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit'
    });
  }
  update();
  setInterval(update, 30000);
}

/* ── Modal helpers ───────────────────────────────── */
function openModal(id) {
  const m = document.getElementById(id);
  if (!m) return;
  m.classList.remove('hidden');    // quitar primero el !important
  m.style.removeProperty('display'); // limpiar cualquier inline previo
}
function closeModal(id) {
  const m = document.getElementById(id);
  if (!m) return;
  m.classList.add('hidden');       // regresar al estado oculto con clase
  m.style.removeProperty('display');
}
function closeOnOverlayClick(e) {
  if (e.target === e.currentTarget) closeModal(e.currentTarget.id);
}

/* ── Mobile Sidebar Logic ─────────────────────────── */
function toggleSidebar() {
  const nav = document.querySelector('.app-nav');
  const overlay = document.querySelector('.sidebar-overlay');
  if (nav) nav.classList.toggle('active');
  if (overlay) overlay.classList.toggle('active');
}

function closeSidebar() {
  const nav = document.querySelector('.app-nav');
  const overlay = document.querySelector('.sidebar-overlay');
  if (nav) nav.classList.remove('active');
  if (overlay) overlay.classList.remove('active');
}

// Cerrar sidebar al hacer click en el overlay
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('sidebar-overlay')) {
    closeSidebar();
  }
});

/* ── Confirm dialog ──────────────────────────────── */
function confirmAction(message, onConfirm) {
  if (window.confirm(message)) onConfirm();
}

/* ── Export global ───────────────────────────────── */
window.api         = api;
window.getToken    = getToken;
window.getUser     = getUser;
window.saveSession = saveSession;
window.clearSession = clearSession;
window.isLoggedIn  = isLoggedIn;
window.requireAuth = requireAuth;
window.showToast   = showToast;
window.fmtUSD      = fmtUSD;
window.fmtBS       = fmtBS;
window.toBs        = toBs;
window.initHeader  = initHeader;
window.loadBCVRate = loadBCVRate;
window.getRate     = getRate;
window.logout      = logout;
window.initClock   = initClock;
window.openModal   = openModal;
window.closeModal  = closeModal;
window.closeOnOverlayClick = closeOnOverlayClick;
window.confirmAction = confirmAction;
window.currentRate = currentRate;
window.toggleSidebar = toggleSidebar;
window.closeSidebar  = closeSidebar;
