/* ======================================================
   api.js — Shared HTTP client for the POS backend
   ====================================================== */

const BASE_URL = window.API_BASE_URL || '';  // Same origin for dev, configurable for desktop/cloud

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
    const text = await resp.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (parseErr) {
      if (resp.status === 401 || resp.status === 302) {
        clearSession();
        window.location.href = '/index.html';
        return null;
      }
      throw new Error('Respuesta inesperada del servidor. Tu sesión pudo haber expirado. Inicia sesión nuevamente.');
    }

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
  const icons = {
    success: '<i class="fa-solid fa-check"></i>',
    error: '<i class="fa-solid fa-xmark"></i>',
    warning: '<i class="fa-solid fa-exclamation"></i>',
    info: '<i class="fa-solid fa-info"></i>'
  };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span>${message}</span>`;
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
  if (roleEl) {
    roleEl.textContent = user.role === 'superadmin' ? 'Super Admin'
                       : user.role === 'admin' ? 'Admin'
                       : 'Cajero';
  }
  if (avatarEl) avatarEl.textContent = (user.name || 'U')[0].toUpperCase();

  // Inject Super Admin nav link for superadmin users
  if (user.role === 'superadmin') {
    const nav = document.querySelector('.app-nav');
    if (nav && !nav.querySelector('.nav-superadmin-link')) {
      const sectionLabel = nav.querySelector('.nav-section-label');
      if (sectionLabel) {
        sectionLabel.insertAdjacentHTML('afterend',
          `<a href="/superadmin.html" class="nav-item nav-superadmin-link" style="border-left:2px solid var(--violet);background:rgba(124,58,237,0.08);">
            <i class="fa-solid fa-shield-halved" style="color:var(--violet-light);"></i> Panel Admin
          </a>`
        );
      }
    }
  }

  // Hide Fiados/Caja for superadmin (now static HTML)
  if (user.role === 'superadmin') {
    document.querySelectorAll('.nav-fiados-link, .nav-caja-link').forEach(el => el.style.display = 'none');
  }

  // Load BCV rate
  loadBCVRate();
}

let currentRate = 0;
let _ratePromiseFired = false;

async function loadBCVRate(force = false) {
  if (currentRate > 0 && !force) return currentRate;
  if (_ratePromiseFired && !force) {
    return currentRate || 0;
  }
  _ratePromiseFired = true;
  try {
    const data = await api.get('/exchange/rate');
    currentRate = data.rate;
    const el = document.getElementById('bcv-rate');
    if (el) el.textContent = data.rate.toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const dateEl = document.getElementById('bcv-date');
    if (dateEl) dateEl.textContent = data.date;
  } catch (e) {
    console.warn('BCV rate load failed:', e.message);
    _ratePromiseFired = false;  // allow retry on next call
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
  m.setAttribute('role', 'dialog');
  m.setAttribute('aria-modal', 'true');
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

// Escape cierra sidebar (si está activa)
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const nav = document.querySelector('.app-nav.active');
  if (nav) closeSidebar();
});

/* ── User Dropdown ───────────────────────────────── */
function toggleUserDropdown(e) {
  if (e) {
    e.stopPropagation();
    // Solo prevenir si no se hizo clic en un enlace dentro del dropdown
    if (!e.target.closest('.dropdown-item')) {
      e.preventDefault();
    }
  }
  const dropdown = document.getElementById('user-dropdown');
  if (dropdown) {
    dropdown.classList.toggle('hidden');
  }
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
  const dropdown = document.getElementById('user-dropdown');
  if (dropdown && !dropdown.classList.contains('hidden') && !e.target.closest('.header-user')) {
    dropdown.classList.add('hidden');
  }
});

// Escape cierra dropdown usuario
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const dropdown = document.getElementById('user-dropdown');
  if (dropdown && !dropdown.classList.contains('hidden')) {
    dropdown.classList.add('hidden');
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
window.toggleUserDropdown = toggleUserDropdown;

/* ── Auto-fix for superadmin on every page ──────── */
(function() {
  const user = getUser();
  if (!user || user.role !== 'superadmin') return;

  // Fix role display if set manually by page JS
  const roleEl = document.getElementById('user-role');
  if (roleEl && roleEl.textContent !== 'Super Admin') {
    roleEl.textContent = 'Super Admin';
  }

  // Inject nav link if not on superadmin page
  const nav = document.querySelector('.app-nav');
  if (nav && !nav.querySelector('.nav-superadmin-link')) {
    const sectionLabel = nav.querySelector('.nav-section-label');
    if (sectionLabel) {
      sectionLabel.insertAdjacentHTML('afterend',
        `<a href="/superadmin.html" class="nav-item nav-superadmin-link" style="border-left:2px solid var(--violet);background:rgba(124,58,237,0.08);">
          <i class="fa-solid fa-shield-halved" style="color:var(--violet-light);"></i> Panel Admin
        </a>`
      );
    }
  }
})();

/* ── Button Ripple Effect ────────────────────────── */
document.addEventListener('click', function(e) {
  const btn = e.target.closest('.btn');
  if (!btn || btn.disabled) return;
  const ripple = document.createElement('span');
  ripple.className = 'ripple';
  const rect = btn.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height);
  ripple.style.width = ripple.style.height = size + 'px';
  ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
  ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 500);
});

/* ── Security Questions Check ──────────────────────── */
async function checkSecurityQuestions() {
  if (!isLoggedIn()) return;
  const user = getUser();
  // Superadmin no necesita preguntas
  if (user.role === 'superadmin') return;

  try {
    const data = await api.get('/auth/has-security-questions');
    if (!data.has_questions) {
      showSecurityQuestionsModal();
    }
  } catch (e) {
    console.warn('Error verificando preguntas de seguridad:', e.message);
  }
}

function showSecurityQuestionsModal() {
  if (document.getElementById('security-questions-modal')) return;

  const modal = document.createElement('div');
  modal.id = 'security-questions-modal';
  modal.style.cssText = `
    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 10000; backdrop-filter: blur(4px);
  `;

  modal.innerHTML = `
    <div style="background:#fff;width:min(90vw,480px);max-height:90vh;overflow-y:auto;">
      <div style="background:#1FB776;padding:20px 24px;display:flex;align-items:center;justify-content:space-between;">
        <h2 style="color:#fff;margin:0;font-size:1.1rem;">
          <i class="fa-solid fa-shield-halved"></i> Configura tus preguntas de seguridad
        </h2>
      </div>
      <div style="padding:24px;">
        <p style="color:#666;font-size:0.85rem;margin:0 0 1.25rem;text-align:center;">
          Para poder recuperar tu contraseña en el futuro, debes configurar al menos <strong>2 preguntas de seguridad</strong>.
        </p>

        <div id="sq-error" style="background:rgba(244,63,94,0.08);border:1px solid rgba(244,63,94,0.2);padding:10px;font-size:0.8rem;color:#e11d48;display:none;margin-bottom:1rem;"></div>

        <form id="sq-form">
          <div id="sq-questions-container"></div>

          <button type="button" id="sq-add-btn" style="
            background:none;border:1px dashed #ccc;padding:8px 12px;width:100%;
            font-size:0.82rem;color:#666;cursor:pointer;margin-bottom:1rem;
          ">
            <i class="fa-solid fa-plus"></i> Agregar otra pregunta
          </button>

          <button type="submit" id="sq-submit-btn" style="
            width:100%;padding:12px;background:#1FB776;color:#fff;border:none;
            font-size:0.9rem;font-weight:600;cursor:pointer;display:flex;
            align-items:center;justify-content:center;gap:8px;
          ">
            <i class="fa-solid fa-check"></i> Guardar Preguntas
          </button>
        </form>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  let predefinedQuestions = [
    "¿Cuál es el nombre de tu mascota?",
    "¿En qué ciudad naciste?",
    "¿Cuál es tu color favorito?",
    "¿Cuál es tu comida favorita?",
    "¿Cuál es tu hobby favorito?",
    "¿Cuál es el nombre de tu mejor amigo?",
    "¿En qué escuela estudiaste?",
    "¿Cuál es tu deporte favorito?"
  ];

  try {
    api.get('/auth/security-questions/all').then(data => {
      predefinedQuestions = data.questions;
    });
  } catch (e) {}

  let questionCount = 0;

  function addQuestion() {
    if (questionCount >= 5) return;
    questionCount++;

    const container = document.getElementById('sq-questions-container');
    const div = document.createElement('div');
    div.style.marginBottom = '1rem';
    div.innerHTML = `
      <label style="font-size:0.78rem;font-weight:600;color:#6D6D6D;display:block;margin-bottom:4px;">
        Pregunta ${questionCount}
      </label>
      <select class="sq-question-select" style="
        width:100%;padding:10px;background:#F0F0F0;border:1px solid transparent;
        font-size:0.85rem;margin-bottom:6px;outline:none;
      " required>
        <option value="">Selecciona una pregunta...</option>
        ${predefinedQuestions.map(q => '<option value="' + q + '">' + q + '</option>').join('')}
      </select>
      <input type="text" class="sq-answer-input" placeholder="Tu respuesta (recuerdala bien)" style="
        width:100%;padding:10px;background:#F0F0F0;border:1px solid transparent;
        font-size:0.85rem;outline:none;
      " required>
    `;
    container.appendChild(div);

    if (questionCount >= 5) {
      document.getElementById('sq-add-btn').style.display = 'none';
    }
  }

  addQuestion();
  addQuestion();

  document.getElementById('sq-add-btn').addEventListener('click', addQuestion);

  document.getElementById('sq-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = document.getElementById('sq-error');
    errBox.style.display = 'none';

    const selects = document.querySelectorAll('.sq-question-select');
    const inputs = document.querySelectorAll('.sq-answer-input');
    const questions = [];

    for (let i = 0; i < selects.length; i++) {
      const question = selects[i].value;
      const answer = inputs[i].value.trim();

      if (!question) {
        errBox.textContent = 'Selecciona todas las preguntas';
        errBox.style.display = 'block';
        return;
      }
      if (!answer) {
        errBox.textContent = 'Responde todas las preguntas';
        errBox.style.display = 'block';
        return;
      }
      questions.push({ question, answer });
    }

    const uniqueQuestions = new Set(questions.map(q => q.question));
    if (uniqueQuestions.size !== questions.length) {
      errBox.textContent = 'No puedes repetir la misma pregunta';
      errBox.style.display = 'block';
      return;
    }

    const btn = document.getElementById('sq-submit-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Guardando...';

    try {
      await api.put('/auth/security-questions', { questions });
      showToast('Preguntas de seguridad configuradas correctamente', 'success');
      modal.remove();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.style.display = 'block';
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-check"></i> Guardar Preguntas';
    }
  });
}

// Verificar al cargar cada página (excepto login y registro)
if (isLoggedIn() && !window.location.pathname.includes('index.html') && !window.location.pathname.includes('register.html')) {
  setTimeout(checkSecurityQuestions, 500);
}
