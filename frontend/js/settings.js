/* ======================================================
   settings.js — Lógica del módulo de Configuración
   ====================================================== */

requireAuth();
initHeader();

/* ── Cargar datos del usuario en perfil ─────────────── */
function loadProfile() {
  const user = getUser();
  const initial = (user.name || 'U')[0].toUpperCase();

  const el = (id) => document.getElementById(id);

  if (el('profile-avatar-big')) el('profile-avatar-big').textContent = initial;
  if (el('profile-name'))       el('profile-name').textContent       = user.name    || '--';
  if (el('profile-username'))   el('profile-username').textContent   = user.username || user.name || '--';
  if (el('profile-company'))    el('profile-company').textContent    = user.company_id ? `#${user.company_id}` : 'N/A';

  const roleLabels = {
    superadmin: 'Super Admin',
    admin:      'Administrador',
    cashier:    'Cajero'
  };
  const roleLabel = roleLabels[user.role] || user.role || '--';
  if (el('profile-role'))   el('profile-role').textContent   = roleLabel;
  if (el('profile-role-2')) el('profile-role-2').textContent = roleLabel;
}

/* ── Tabs ────────────────────────────────────────────── */
function switchTab(tab) {
  // Desactivar todos los tabs y paneles
  document.querySelectorAll('.settings-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));

  document.getElementById(`tab-${tab}`)?.classList.add('active');
  document.getElementById(`panel-${tab}`)?.classList.add('active');
}

/* ── Mostrar/ocultar contraseña ──────────────────────── */
function togglePwdVisibility(inputId, iconId) {
  const input = document.getElementById(inputId);
  const icon  = document.getElementById(iconId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (icon) icon.className = 'fa-solid fa-eye-slash';
  } else {
    input.type = 'password';
    if (icon) icon.className = 'fa-solid fa-eye';
  }
}

/* ── Medidor de fortaleza de contraseña ──────────────── */
function evalPasswordStrength(value) {
  const fill  = document.getElementById('pwd-strength-fill');
  const label = document.getElementById('pwd-strength-label');
  if (!fill || !label) return;

  let score = 0;
  if (value.length >= 8)               score++;
  if (/[A-Z]/.test(value))             score++;
  if (/[0-9]/.test(value))             score++;
  if (/[^A-Za-z0-9]/.test(value))      score++;

  const levels = [
    { pct: '0%',   color: 'var(--border)',       text: '' },
    { pct: '25%',  color: '#ef4444',             text: '🔴 Muy débil' },
    { pct: '50%',  color: '#f97316',             text: '🟠 Débil' },
    { pct: '75%',  color: '#eab308',             text: '🟡 Aceptable' },
    { pct: '100%', color: 'var(--emerald-light)', text: '🟢 Segura' }
  ];

  const lvl = levels[score];
  fill.style.width           = lvl.pct;
  fill.style.backgroundColor = lvl.color;
  label.textContent          = lvl.text;
}

/* ── Verificar coincidencia de contraseñas ───────────── */
function checkConfirmMatch() {
  const newPwd     = document.getElementById('new-pwd')?.value     || '';
  const confirmPwd = document.getElementById('confirm-pwd')?.value || '';
  const hint       = document.getElementById('match-hint');
  const confirmEl  = document.getElementById('confirm-pwd');
  if (!hint || !confirmEl) return;

  if (!confirmPwd) {
    hint.style.display = 'none';
    confirmEl.style.borderColor = '';
    return;
  }

  if (newPwd === confirmPwd) {
    hint.textContent  = '✅ Las contraseñas coinciden';
    hint.style.color  = 'var(--emerald-light)';
    hint.style.display = 'block';
    confirmEl.style.borderColor = 'var(--emerald)';
  } else {
    hint.textContent  = '❌ Las contraseñas no coinciden';
    hint.style.color  = '#ef4444';
    hint.style.display = 'block';
    confirmEl.style.borderColor = '#ef4444';
  }
}

/* ── Cambiar contraseña ──────────────────────────────── */
async function changePassword() {
  const oldPwd     = document.getElementById('old-pwd')?.value     || '';
  const newPwd     = document.getElementById('new-pwd')?.value     || '';
  const confirmPwd = document.getElementById('confirm-pwd')?.value || '';

  // Validaciones en cliente
  if (!oldPwd || !newPwd || !confirmPwd) {
    showToast('Completa todos los campos', 'warning');
    return;
  }
  if (newPwd.length < 8) {
    showToast('La nueva contraseña debe tener al menos 8 caracteres', 'warning');
    return;
  }
  if (newPwd !== confirmPwd) {
    showToast('Las contraseñas nuevas no coinciden', 'warning');
    return;
  }
  if (oldPwd === newPwd) {
    showToast('La nueva contraseña debe ser diferente a la actual', 'warning');
    return;
  }

  const btn = document.getElementById('btn-change-pwd');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Actualizando...';

  try {
    await api.put('/auth/me/password', {
      old_password: oldPwd,
      new_password: newPwd
    });

    showToast('✅ Contraseña actualizada correctamente', 'success');

    // Limpiar el formulario
    document.getElementById('pwd-form').reset();
    document.getElementById('pwd-strength-fill').style.width = '0%';
    document.getElementById('pwd-strength-label').textContent = '';
    document.getElementById('match-hint').style.display = 'none';
    document.getElementById('confirm-pwd').style.borderColor = '';

  } catch (e) {
    showToast(e.message || 'Error al actualizar la contraseña', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

/* ── Apariencia / Tema ───────────────────────────────── */
function applyThemeChoice(theme) {
  const isDark = theme === 'dark';

  // Aplicar / quitar clase dark en <html>
  document.documentElement.classList.toggle('dark', isDark);

  // Actualizar ícono del header
  const icon = document.getElementById('theme-icon');
  if (icon) icon.className = isDark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';

  // Persistir
  localStorage.setItem('theme', theme);

  // Actualizar indicadores visuales
  document.querySelectorAll('.theme-option').forEach(el => el.classList.remove('selected'));
  document.getElementById(`opt-${theme}`)?.classList.add('selected');
  document.getElementById('check-light').style.display = theme === 'light' ? 'block' : 'none';
  document.getElementById('check-dark').style.display  = theme === 'dark'  ? 'block' : 'none';
}

function syncThemeUI() {
  const stored = localStorage.getItem('theme') || 'light';
  const isDark  = document.documentElement.classList.contains('dark');
  const current = isDark ? 'dark' : (stored === 'dark' ? 'dark' : 'light');

  document.getElementById(`opt-${current}`)?.classList.add('selected');
  document.getElementById('check-light').style.display = current === 'light' ? 'block' : 'none';
  document.getElementById('check-dark').style.display  = current === 'dark'  ? 'block' : 'none';
}

/* ── Init ────────────────────────────────────────────── */
loadProfile();
syncThemeUI();
loadIvaSettings();
loadTasaSettings();

/* ── IVA Settings ─────────────────────────────────────── */
function loadIvaSettings() {
  const enabled = localStorage.getItem('pos_iva_enabled');
  const pct = localStorage.getItem('pos_iva_pct');

  const toggle = document.getElementById('settings-iva-toggle');
  const pctSection = document.getElementById('iva-pct-section');
  const pctInput = document.getElementById('settings-iva-pct');
  const statusEl = document.getElementById('iva-status');

  if (!toggle) return;

  // Default: IVA enabled at 16%
  const isEnabled = enabled === null ? true : enabled === 'true';
  const ivaPct = pct === null ? 16 : parseFloat(pct);

  toggle.checked = isEnabled;
  pctInput.value = ivaPct;
  pctSection.style.display = isEnabled ? 'block' : 'none';

  updateIvaStatus(isEnabled, ivaPct);
}

function saveIvaSetting() {
  const toggle = document.getElementById('settings-iva-toggle');
  const pctInput = document.getElementById('settings-iva-pct');
  const pctSection = document.getElementById('iva-pct-section');

  const isEnabled = toggle.checked;
  const ivaPct = parseFloat(pctInput.value) || 0;

  localStorage.setItem('pos_iva_enabled', isEnabled);
  localStorage.setItem('pos_iva_pct', ivaPct);

  pctSection.style.display = isEnabled ? 'block' : 'none';

  updateIvaStatus(isEnabled, ivaPct);
  showToast(isEnabled ? `IVA activado al ${ivaPct}%` : 'IVA desactivado', 'success');
}

function updateIvaStatus(isEnabled, pct) {
  const statusEl = document.getElementById('iva-status');
  if (!statusEl) return;

  if (isEnabled) {
    statusEl.style.background = 'rgba(16,185,129,0.1)';
    statusEl.style.color = 'var(--emerald-light)';
    statusEl.style.border = '1px solid rgba(16,185,129,0.3)';
    statusEl.innerHTML = `<i class="fa-solid fa-circle-check"></i> IVA activo al ${pct}% — Se cobrará en todas las ventas`;
  } else {
    statusEl.style.background = 'rgba(239,68,68,0.1)';
    statusEl.style.color = '#ef4444';
    statusEl.style.border = '1px solid rgba(239,68,68,0.3)';
    statusEl.innerHTML = `<i class="fa-solid fa-circle-xmark"></i> IVA desactivado — No se cobrará impuesto`;
  }
}

/* ── Exchange Rate Settings ──────────────────────────── */
let _tasaCurrentMode = 'auto';

async function loadTasaSettings() {
  const user = getUser();
  if (user.role !== 'admin') {
    const tasaTab = document.getElementById('tab-tasa');
    if (tasaTab) tasaTab.style.display = 'none';
    return;
  }

  try {
    const [settings, rateData] = await Promise.all([
      api.get('/settings/exchange-rate'),
      api.get('/exchange/rate')
    ]);

    _tasaCurrentMode = settings.mode || 'auto';
    const toggle = document.getElementById('tasa-mode-toggle');
    const manualSection = document.getElementById('tasa-manual-section');
    const manualInput = document.getElementById('tasa-manual-input');
    const currentVal = document.getElementById('tasa-current-value');
    const currentSrc = document.getElementById('tasa-current-source');

    if (toggle) toggle.checked = _tasaCurrentMode === 'auto';
    if (manualSection) manualSection.style.display = _tasaCurrentMode === 'manual' ? 'block' : 'none';
    if (manualInput) manualInput.value = settings.manual_rate || '';
    if (currentVal) currentVal.textContent = `1$ = ${rateData.rate.toLocaleString('es-VE', {minimumFractionDigits: 2})} Bs`;
    if (currentSrc) {
      const srcLabels = {
        'Manual': 'Tasa configurada manualmente',
        've.dolarapi.com': 'Fuente: ve.dolarapi.com',
        'pydolarve.org': 'Fuente: pydolarve.org',
        'BCV scraping': 'Fuente: BCV scraping',
        'Respaldo manual': 'Tasa de respaldo (no se pudo obtener del BCV)'
      };
      currentSrc.textContent = srcLabels[rateData.source] || `Fuente: ${rateData.source}`;
    }
  } catch (e) {
    console.warn('Error loading tasa settings:', e.message);
  }
}

function onTasaModeChange() {
  const toggle = document.getElementById('tasa-mode-toggle');
  const manualSection = document.getElementById('tasa-manual-section');
  if (manualSection) manualSection.style.display = toggle.checked ? 'none' : 'block';
}

async function saveTasaSettings() {
  const toggle = document.getElementById('tasa-mode-toggle');
  const manualInput = document.getElementById('tasa-manual-input');
  const statusEl = document.getElementById('tasa-status');

  const mode = toggle.checked ? 'auto' : 'manual';
  const body = { mode };

  if (mode === 'manual') {
    const rate = parseFloat(manualInput.value);
    if (!rate || rate <= 0) {
      showToast('Ingresa una tasa válida mayor a 0', 'warning');
      return;
    }
    body.manual_rate = rate;
  }

  try {
    const result = await api.put('/settings/exchange-rate', body);
    _tasaCurrentMode = mode;

    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.style.background = 'rgba(16,185,129,0.1)';
      statusEl.style.color = 'var(--emerald-light)';
      statusEl.style.border = '1px solid rgba(16,185,129,0.3)';
      statusEl.innerHTML = `<i class="fa-solid fa-circle-check"></i> ${result.message}`;
    }

    showToast(result.message, 'success');

    // Force reload the rate in the header
    if (typeof loadBCVRate === 'function') {
      await loadBCVRate(true);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.style.background = 'rgba(239,68,68,0.1)';
      statusEl.style.color = '#ef4444';
      statusEl.style.border = '1px solid rgba(239,68,68,0.3)';
      statusEl.innerHTML = `<i class="fa-solid fa-circle-xmark"></i> ${e.message}`;
    }
    showToast(e.message, 'error');
  }
}
