/* ======================================================
   cash-sessions.js — Módulo de Gestión de Caja
   Sistema POS Venezuela
   ====================================================== */

requireAuth();
initHeader();

/* ── State ─────────────────────────────────────────── */
let currentSession  = null;   // sesión abierta actual
let currentSummary  = null;   // resumen del turno (Reporte X)
let currentReportData = null; // datos del último reporte generado

const USD_DENOMS = [100, 50, 20, 10, 5, 2, 1];
const BS_DENOMS  = [500000, 200000, 100000, 50000, 20000, 10000, 5000, 1000, 500, 200, 100, 50];

const PAYMENT_LABELS = {
  cash_usd:   { label: 'Efectivo USD',          color: '#34d399', icon: 'fa-dollar-sign' },
  cash_bs:    { label: 'Efectivo Bs',            color: '#60a5fa', icon: 'fa-bolivar-sign' },
  card:        { label: 'Tarjeta Débito/Crédito', color: '#a78bfa', icon: 'fa-credit-card' },
  pago_movil:  { label: 'Pago Móvil',            color: '#fb923c', icon: 'fa-mobile-screen' },
  transfer:    { label: 'Transferencia',          color: '#38bdf8', icon: 'fa-building-columns' },
  mixed:       { label: 'Pago Mixto',             color: '#f59e0b', icon: 'fa-shuffle' },
  credit:      { label: 'Crédito (Fiado)',        color: '#f43f5e', icon: 'fa-hand-holding-dollar' },
};

/* ── Bootstrap ─────────────────────────────────────── */
async function init() {
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('hist-from').value = today;
  document.getElementById('hist-to').value   = today;

  try {
    await checkCurrentSession();
    await loadHistory();
    renderDenominations();
  } catch (e) {
    showToast(e.message, 'error');
  }

  // Mostrar tasa en modal de apertura
  const rate = getRate();
  if (rate) updateOpenRateDisplay(rate);
}

init();

function updateOpenRateDisplay(rate) {
  const el = document.getElementById('open-rate-display');
  if (el) el.textContent = `1$ = ${rate.toLocaleString('es-VE', { minimumFractionDigits: 2 })} Bs`;
}

/* ── Check current open session ─────────────────── */
async function checkCurrentSession() {
  try {
    const data = await api.get('/cash-sessions/current');
    if (data.is_open && data.session) {
      currentSession = data.session;
      showActiveBanner(data.session);
      await refreshBannerSummary();
      document.getElementById('no-session-panel').style.display  = 'none';
      document.getElementById('btn-open-cash').style.display     = 'none';
      document.getElementById('btn-close-cash').style.display    = 'inline-flex';
      document.getElementById('btn-reporte-x').disabled = false;
    } else {
      currentSession = null;
      document.getElementById('active-session-banner').classList.add('hidden');
      document.getElementById('no-session-panel').style.display    = '';
      document.getElementById('btn-open-cash').style.display       = 'inline-flex';
      document.getElementById('btn-close-cash').style.display      = 'none';
      document.getElementById('btn-reporte-x').disabled = true;
    }
  } catch (e) {
    console.warn('Error checking session:', e.message);
  }
}

function showActiveBanner(session) {
  const banner = document.getElementById('active-session-banner');
  banner.classList.remove('hidden');
  document.getElementById('banner-session-number').textContent = session.session_number;

  const openDt = new Date(session.open_datetime);
  document.getElementById('banner-session-info').textContent =
    `${session.user_name} · Apertura: ${openDt.toLocaleString('es-VE', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })}`;

  const rate = session.exchange_rate_open;
  document.getElementById('banner-rate').textContent =
    rate ? `${rate.toLocaleString('es-VE', { minimumFractionDigits: 2 })} Bs/$` : '--';
}

async function refreshBannerSummary() {
  if (!currentSession) return;
  try {
    const data = await api.get('/cash-sessions/current/summary');
    currentSummary = data;
    const t = data.summary?.totals || {};
    document.getElementById('banner-sales-count').textContent = t.total_sales || 0;
    document.getElementById('banner-total-usd').textContent =
      `$${parseFloat(t.total_usd || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
    document.getElementById('banner-total-bs').textContent =
      `Bs ${parseFloat(t.total_bs || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  } catch (e) {
    console.warn('Error refreshing summary:', e);
  }
}

/* ── Open Session ───────────────────────────────── */
async function confirmOpenSession() {
  const btn = document.getElementById('btn-confirm-open');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Abriendo...';

  const opening_amount_usd = parseFloat(document.getElementById('open-usd').value) || 0;
  const opening_amount_bs  = parseFloat(document.getElementById('open-bs').value)  || 0;
  const notes_open = document.getElementById('open-notes').value.trim();

  try {
    const result = await api.post('/cash-sessions/open', {
      opening_amount_usd,
      opening_amount_bs,
      notes_open,
    });

    showToast(`✅ Caja ${result.session_number} abierta exitosamente`, 'success');
    closeModal('modal-apertura');

    // Reset form
    document.getElementById('open-usd').value   = '0';
    document.getElementById('open-bs').value    = '0';
    document.getElementById('open-notes').value = '';

    await checkCurrentSession();
    await loadHistory();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-lock-open"></i> Abrir Caja';
  }
}

/* ── Reporte X ──────────────────────────────────── */
async function showReporteX() {
  if (!currentSession) {
    showToast('No hay caja abierta', 'warning');
    return;
  }
  try {
    showToast('Generando Reporte X...', 'info', 1500);
    const data = await api.get('/cash-sessions/current/summary');
    currentSummary = data;
    currentReportData = data;

    document.getElementById('reporte-modal-title-text').textContent = 'Reporte X — Estado del Turno';
    document.getElementById('reporte-content').innerHTML =
      buildZReportHTML(data.session, data.summary, [], [], 'X');

    openModal('modal-reporte');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/* ── Closing Flow ───────────────────────────────── */
async function startClosingFlow() {
  if (!currentSession) {
    showToast('No hay caja abierta', 'warning');
    return;
  }

  try {
    // Obtener resumen actualizado
    const data = await api.get('/cash-sessions/current/summary');
    currentSummary = data;

    // Llenar modal de cierre
    fillCloseModal(data);
    openModal('modal-cierre');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function fillCloseModal(data) {
  const t = data.summary?.totals || {};
  const cancelled = data.summary?.cancelled || {};
  const byMethod  = data.summary?.by_payment_method || [];

  // Summary cards
  document.getElementById('cs-total-sales').textContent = t.total_sales || 0;
  document.getElementById('cs-total-usd').textContent =
    `$${parseFloat(t.total_usd || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  document.getElementById('cs-total-bs').textContent =
    `Bs ${parseFloat(t.total_bs || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  document.getElementById('cs-cancelled').textContent =
    `${cancelled.count || 0} (${fmtUSD(cancelled.amount_usd || 0)})`;

  // Payment breakdown
  const bp = document.getElementById('cs-payment-breakdown');
  if (!byMethod.length) {
    bp.innerHTML = '<div style="font-size:0.8rem;color:var(--text-muted);padding:0.5rem 0;">Sin ventas en este turno</div>';
  } else {
    bp.innerHTML = byMethod.map(pm => {
      const info = PAYMENT_LABELS[pm.payment_method] || { label: pm.payment_method, color: '#94a3b8', icon: 'fa-money-bill' };
      return `
      <div class="cs-payment-row">
        <div class="cs-payment-method">
          <div class="method-dot" style="background:${info.color};"></div>
          <i class="fa-solid ${info.icon}" style="color:${info.color};font-size:0.75rem;"></i>
          <span>${info.label}</span>
          <span style="color:var(--text-muted);font-size:0.72rem;">(${pm.count} vta${pm.count !== 1 ? 's' : ''})</span>
        </div>
        <div class="cs-payment-values">
          <span style="color:var(--usd-color);">$${parseFloat(pm.total_usd).toLocaleString('es-VE', { minimumFractionDigits: 2 })}</span>
          <span style="color:var(--bs-color);">Bs ${parseFloat(pm.total_bs).toLocaleString('es-VE', { minimumFractionDigits: 2 })}</span>
        </div>
      </div>`;
    }).join('');
  }

  // Calcular efectivo esperado
  const session = data.session || {};
  let expectedUsd = parseFloat(session.opening_amount_usd || 0);
  let expectedBs  = parseFloat(session.opening_amount_bs  || 0);
  const cashMethods = ['cash_usd', 'cash_bs', 'mixed'];
  byMethod.forEach(pm => {
    if (cashMethods.includes(pm.payment_method)) {
      expectedUsd += parseFloat(pm.paid_usd || pm.total_usd || 0);
      expectedBs  += parseFloat(pm.paid_bs  || pm.total_bs  || 0);
    }
  });

  document.getElementById('expected-usd-display').textContent =
    `$${expectedUsd.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  document.getElementById('expected-bs-display').textContent =
    `Bs ${expectedBs.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;

  // Store expected for difference calc
  document.getElementById('close-usd').dataset.expected = expectedUsd;
  document.getElementById('close-bs').dataset.expected  = expectedBs;

  // Pre-fill with expected values
  document.getElementById('close-usd').value = expectedUsd.toFixed(2);
  document.getElementById('close-bs').value  = expectedBs.toFixed(2);
  calcDifference();
}

function calcDifference() {
  const closeUsd = parseFloat(document.getElementById('close-usd').value) || 0;
  const closeBs  = parseFloat(document.getElementById('close-bs').value)  || 0;
  const expUsd   = parseFloat(document.getElementById('close-usd').dataset.expected) || 0;
  const expBs    = parseFloat(document.getElementById('close-bs').dataset.expected)  || 0;

  const diffUsd = closeUsd - expUsd;
  const diffBs  = closeBs  - expBs;

  const panel = document.getElementById('difference-panel');
  panel.style.display = 'block';

  const usdEl = document.getElementById('diff-usd-display');
  const bsEl  = document.getElementById('diff-bs-display');

  function diffClass(val) {
    if (Math.abs(val) < 0.01) return 'diff-ok';
    if (Math.abs(val) <= 5)   return 'diff-warn';
    return 'diff-error';
  }

  usdEl.textContent  = `${diffUsd >= 0 ? '+' : ''}$${diffUsd.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  usdEl.className    = diffClass(diffUsd);
  bsEl.textContent   = `${diffBs  >= 0 ? '+' : ''}Bs ${diffBs.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  bsEl.className     = diffClass(diffBs);
}

/* ── Denominations ──────────────────────────────── */
let denomOpen = false;

function toggleDenominations() {
  denomOpen = !denomOpen;
  const panel = document.getElementById('denominations-panel');
  const icon  = document.getElementById('denom-toggle-icon');
  panel.style.display = denomOpen ? 'block' : 'none';
  icon.style.transform = denomOpen ? 'rotate(180deg)' : '';
}

function renderDenominations() {
  // USD denominations
  const usdList = document.getElementById('denom-usd-list');
  usdList.innerHTML = USD_DENOMS.map(d => `
    <div class="denom-row" data-currency="USD" data-denom="${d}">
      <span class="denom-label">$ ${d.toLocaleString()}</span>
      <input type="number" min="0" value="0" id="denom-usd-${d}"
        oninput="updateDenomSubtotal(this,'USD',${d})" placeholder="0">
      <span class="denom-subtotal" id="dsub-usd-${d}">$0.00</span>
    </div>
  `).join('');

  // BS denominations
  const bsList = document.getElementById('denom-bs-list');
  bsList.innerHTML = BS_DENOMS.map(d => {
    const label = d >= 1000 ? `Bs ${(d/1000).toFixed(0)}K` : `Bs ${d}`;
    return `
    <div class="denom-row" data-currency="BS" data-denom="${d}">
      <span class="denom-label">${label}</span>
      <input type="number" min="0" value="0" id="denom-bs-${d}"
        oninput="updateDenomSubtotal(this,'BS',${d})" placeholder="0">
      <span class="denom-subtotal" id="dsub-bs-${d}">Bs 0</span>
    </div>`;
  }).join('');
}

function updateDenomSubtotal(input, currency, denom) {
  const qty = parseInt(input.value) || 0;
  const sub = qty * denom;
  const el  = document.getElementById(`dsub-${currency.toLowerCase()}-${denom}`);
  if (el) {
    el.textContent = currency === 'USD'
      ? `$${sub.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`
      : `Bs ${sub.toLocaleString('es-VE')}`;
  }
  recalcDenomTotals();
}

function recalcDenomTotals() {
  let totalUsd = 0;
  USD_DENOMS.forEach(d => {
    const qty = parseInt(document.getElementById(`denom-usd-${d}`)?.value) || 0;
    totalUsd += qty * d;
  });
  let totalBs = 0;
  BS_DENOMS.forEach(d => {
    const qty = parseInt(document.getElementById(`denom-bs-${d}`)?.value) || 0;
    totalBs += qty * d;
  });

  document.getElementById('denom-total-usd').textContent =
    `$${totalUsd.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
  document.getElementById('denom-total-bs').textContent =
    `Bs ${totalBs.toLocaleString('es-VE', { minimumFractionDigits: 2 })}`;
}

function applyDenomTotals() {
  let totalUsd = 0;
  USD_DENOMS.forEach(d => {
    const qty = parseInt(document.getElementById(`denom-usd-${d}`)?.value) || 0;
    totalUsd += qty * d;
  });
  let totalBs = 0;
  BS_DENOMS.forEach(d => {
    const qty = parseInt(document.getElementById(`denom-bs-${d}`)?.value) || 0;
    totalBs += qty * d;
  });

  document.getElementById('close-usd').value = totalUsd.toFixed(2);
  document.getElementById('close-bs').value  = totalBs.toFixed(2);
  calcDifference();
  showToast('Totales aplicados al arqueo ✓', 'success', 2000);
}

function getDenominationData() {
  const result = [];
  USD_DENOMS.forEach(d => {
    const qty = parseInt(document.getElementById(`denom-usd-${d}`)?.value) || 0;
    if (qty > 0) result.push({ currency: 'USD', denomination: d, quantity: qty });
  });
  BS_DENOMS.forEach(d => {
    const qty = parseInt(document.getElementById(`denom-bs-${d}`)?.value) || 0;
    if (qty > 0) result.push({ currency: 'BS', denomination: d, quantity: qty });
  });
  return result;
}

/* ── Confirm Close ──────────────────────────────── */
async function confirmCloseSession() {
  if (!currentSession) return;

  const btn = document.getElementById('btn-confirm-close');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Procesando...';

  const closing_amount_usd = parseFloat(document.getElementById('close-usd').value) || 0;
  const closing_amount_bs  = parseFloat(document.getElementById('close-bs').value)  || 0;
  const notes_close        = document.getElementById('close-notes').value.trim();
  const denominations      = getDenominationData();

  try {
    const result = await api.post(`/cash-sessions/${currentSession.id}/close`, {
      closing_amount_usd,
      closing_amount_bs,
      notes_close,
      denominations,
    });

    closeModal('modal-cierre');

    if (result.difference_alert) {
      showToast(`⚠️ Alerta: Diferencia de ${fmtUSD(Math.abs(result.session.difference_usd))} detectada.`, 'warning', 6000);
    }

    showToast('Caja cerrada exitosamente. Reporte Z generado.', 'success');

    // Show Z Report
    currentReportData = result;
    document.getElementById('reporte-modal-title-text').textContent = 'Reporte Z — Cierre de Caja';
    document.getElementById('reporte-content').innerHTML =
      buildZReportHTML(result.session, result.summary, result.denominations, result.payments, 'Z');

    openModal('modal-reporte');

    // Refresh state
    currentSession = null;
    await checkCurrentSession();
    await loadHistory();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-cash-register"></i> Confirmar Cierre y Generar Reporte Z';
  }
}

/* ── Build Z/X Report HTML ──────────────────────── */
function buildZReportHTML(session, summary, denominations, payments, type = 'Z') {
  if (!session) return '<div style="padding:2rem;text-align:center;color:var(--text-muted);">Sin datos de sesión</div>';

  const t         = summary?.totals || {};
  const cancelled = summary?.cancelled || {};
  const byMethod  = summary?.by_payment_method || payments || [];
  const openDt    = new Date(session.open_datetime);
  const closeDt   = session.close_datetime ? new Date(session.close_datetime) : new Date();

  const isZ = type === 'Z';
  const badgeClass = isZ ? 'z-badge' : 'x-badge';
  const badgeText  = isZ ? 'REPORTE Z' : 'REPORTE X';

  const diffUsd = parseFloat(session.difference_usd || 0);
  const diffCls = Math.abs(diffUsd) < 0.01 ? 'z-difference-ok'
                : Math.abs(diffUsd) <= 5   ? 'z-difference-warn'
                : 'z-difference-error';
  const diffIcon = Math.abs(diffUsd) < 0.01 ? '✅' : Math.abs(diffUsd) <= 5 ? '⚠️' : '❌';

  // Denominations section
  let denomHtml = '';
  if (denominations && denominations.length > 0) {
    const usd = denominations.filter(d => d.currency === 'USD');
    const bs  = denominations.filter(d => d.currency === 'BS');
    denomHtml = `
    <div class="z-section">
      <div class="z-section-title">Arqueo por Denominación</div>
      ${usd.length ? `<div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:0.25rem;">— USD —</div>
        ${usd.map(d => `<div class="z-row"><span>$${d.denomination} × ${d.quantity}</span><span>$${parseFloat(d.subtotal).toLocaleString('es-VE', {minimumFractionDigits:2})}</span></div>`).join('')}` : ''}
      ${bs.length  ? `<div style="font-size:0.7rem;color:var(--text-muted);margin:0.5rem 0 0.25rem;">— Bs —</div>
        ${bs.map(d => `<div class="z-row"><span>Bs ${d.denomination.toLocaleString()} × ${d.quantity}</span><span>Bs ${parseFloat(d.subtotal).toLocaleString('es-VE')}</span></div>`).join('')}` : ''}
    </div>`;
  }

  return `
  <div class="z-report">
    <div class="z-report-header">
      <div class="${badgeClass}">${badgeText}</div>
      <h2>CIERRE DE CAJA</h2>
      <div class="z-report-info">Sistema POS Venezuela — v1.1</div>
      <div class="z-report-info">Generado: ${new Date().toLocaleString('es-VE')}</div>
    </div>

    <div class="z-section">
      <div class="z-section-title">Información del Turno</div>
      <div class="z-row"><span>N° Turno:</span><span style="font-family:monospace;">${session.session_number}</span></div>
      <div class="z-row"><span>Cajero:</span><span>${session.user_name || '--'}</span></div>
      <div class="z-row"><span>Apertura:</span><span>${openDt.toLocaleString('es-VE')}</span></div>
      <div class="z-row"><span>${isZ ? 'Cierre' : 'Generado'}:</span><span>${closeDt.toLocaleString('es-VE')}</span></div>
      <div class="z-row"><span>Tasa BCV Apertura:</span><span>1$ = ${parseFloat(session.exchange_rate_open || 0).toLocaleString('es-VE', {minimumFractionDigits:2})} Bs</span></div>
      ${isZ && session.exchange_rate_close ? `<div class="z-row"><span>Tasa BCV Cierre:</span><span>1$ = ${parseFloat(session.exchange_rate_close).toLocaleString('es-VE', {minimumFractionDigits:2})} Bs</span></div>` : ''}
    </div>

    <div class="z-section">
      <div class="z-section-title">Resumen de Ventas</div>
      <div class="z-row"><span>Total Transacciones:</span><span>${t.total_sales || 0}</span></div>
      <div class="z-row"><span>Subtotal (sin IVA):</span><span>${fmtUSD(t.subtotal_usd || 0)}</span></div>
      <div class="z-row"><span>IVA Cobrado:</span><span>${fmtUSD(t.tax_usd || 0)}</span></div>
      <div class="z-row"><span>Descuentos:</span><span>- ${fmtUSD(t.discount_usd || 0)}</span></div>
      <div class="z-row z-row-total"><span>TOTAL USD:</span><span>${fmtUSD(t.total_usd || 0)}</span></div>
      <div class="z-row" style="color:var(--bs-color);"><span>TOTAL Bs:</span><span>${fmtBS(t.total_bs || 0)}</span></div>
      <div class="z-row" style="color:#fb7185;font-size:0.75rem;margin-top:0.25rem;"><span>Ventas Anuladas:</span><span>${cancelled.count || 0} — ${fmtUSD(cancelled.amount_usd || 0)}</span></div>
    </div>

    <div class="z-section">
      <div class="z-section-title">Desglose por Método de Pago</div>
      ${byMethod.length === 0
        ? '<div style="font-size:0.78rem;color:var(--text-muted);">Sin ventas en este turno</div>'
        : byMethod.map(pm => {
            const info = PAYMENT_LABELS[pm.payment_method] || { label: pm.payment_method };
            return `<div class="z-row">
              <span>${info.label} (${pm.count || 0})</span>
              <span>${fmtUSD(pm.total_usd || 0)} / ${fmtBS(pm.total_bs || 0)}</span>
            </div>`;
          }).join('')
      }
    </div>

    ${isZ ? `
    <div class="z-section">
      <div class="z-section-title">Arqueo de Caja</div>
      <div class="z-row"><span>Fondo Inicial USD:</span><span>${fmtUSD(session.opening_amount_usd || 0)}</span></div>
      <div class="z-row"><span>Fondo Inicial Bs:</span><span>${fmtBS(session.opening_amount_bs || 0)}</span></div>
      <div class="z-row"><span>Efectivo Esperado USD:</span><span>${fmtUSD(session.expected_cash_usd || 0)}</span></div>
      <div class="z-row"><span>Efectivo Esperado Bs:</span><span>${fmtBS(session.expected_cash_bs || 0)}</span></div>
      <div class="z-row z-row-total"><span>Conteo Físico USD:</span><span>${fmtUSD(session.closing_amount_usd || 0)}</span></div>
      <div class="z-row z-row-total"><span>Conteo Físico Bs:</span><span>${fmtBS(session.closing_amount_bs || 0)}</span></div>
      <div class="${diffCls} z-difference-row">
        <span>${diffIcon} Diferencia USD:</span>
        <span>${diffUsd >= 0 ? '+' : ''}${fmtUSD(diffUsd)}</span>
      </div>
      ${session.difference_bs !== undefined && session.difference_bs !== null ? `
      <div class="${diffCls} z-difference-row" style="margin-top:0.25rem;">
        <span>Diferencia Bs:</span>
        <span>${parseFloat(session.difference_bs) >= 0 ? '+' : ''}${fmtBS(session.difference_bs)}</span>
      </div>` : ''}
      ${session.notes_close ? `<div class="z-row" style="margin-top:0.5rem;font-style:italic;"><span>Obs:</span><span>${session.notes_close}</span></div>` : ''}
    </div>
    ` : ''}

    ${denomHtml}

    ${isZ ? `
    <div class="z-signature-area">
      <div class="z-signature-line">Cajero: ${session.user_name || ''}<br>_______________</div>
      <div class="z-signature-line">Supervisor<br>_______________</div>
    </div>
    ` : ''}
  </div>`;
}

/* ── Load History ───────────────────────────────── */
async function loadHistory() {
  const from   = document.getElementById('hist-from').value;
  const to     = document.getElementById('hist-to').value;
  const tbody  = document.getElementById('history-tbody');
  tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:1.5rem;color:var(--text-muted);">Cargando...</td></tr>';

  try {
    const rows = await api.get(`/cash-sessions?from=${from}&to=${to}`);

    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:2rem;color:var(--text-muted);">No se encontraron turnos en este período</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map(s => {
      const openDt  = new Date(s.open_datetime);
      const closeDt = s.close_datetime ? new Date(s.close_datetime) : null;
      const diffUsd = parseFloat(s.difference_usd || 0);
      const diffCls = Math.abs(diffUsd) < 0.01 ? 'text-green'
                    : Math.abs(diffUsd) <= 5    ? '' // amber
                    : 'text-red';
      const diffStr = s.status === 'closed'
        ? `<span class="${diffCls}" style="${Math.abs(diffUsd) > 0.01 && Math.abs(diffUsd) <= 5 ? 'color:var(--amber)' : ''}">${diffUsd >= 0 ? '+' : ''}${fmtUSD(diffUsd)}</span>`
        : '<span style="color:var(--text-muted);">—</span>';

      const statusBadge = s.status === 'open'
        ? '<span class="badge badge-open">🟢 Abierta</span>'
        : s.status === 'pending_review'
        ? '<span class="badge badge-review">🟡 En Revisión</span>'
        : '<span class="badge badge-closed">⚫ Cerrada</span>';

      const user = getUser();
      const canReopen = user.role === 'admin' && s.status === 'closed';

      return `
      <tr>
        <td><span style="font-family:monospace;font-size:0.78rem;">${s.session_number}</span></td>
        <td>${s.user_name || '--'}</td>
        <td style="font-size:0.78rem;">${openDt.toLocaleString('es-VE', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</td>
        <td style="font-size:0.78rem;">${closeDt ? closeDt.toLocaleString('es-VE', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—'}</td>
        <td style="text-align:center;">${s.total_sales || 0}</td>
        <td style="font-family:monospace;color:var(--usd-color);">${fmtUSD(s.total_revenue_usd || 0)}</td>
        <td>${diffStr}</td>
        <td>${statusBadge}</td>
        <td style="text-align:center;">
          <div style="display:flex;gap:0.35rem;justify-content:center;flex-wrap:wrap;">
            <button class="btn btn-outline btn-sm" onclick="viewSessionDetail(${s.id})" title="Ver detalle">
              <i class="fa-solid fa-eye"></i>
            </button>
            ${canReopen ? `<button class="btn btn-outline btn-sm" onclick="reopenSession(${s.id})" title="Reabrir" style="color:var(--amber);border-color:var(--amber);">
              <i class="fa-solid fa-lock-open"></i>
            </button>` : ''}
          </div>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:2rem;color:#fb7185;">${e.message}</td></tr>`;
  }
}

/* ── View session detail ────────────────────────── */
async function viewSessionDetail(sid) {
  try {
    const data = await api.get(`/cash-sessions/${sid}`);
    document.getElementById('detail-session-number').textContent = data.session.session_number;
    document.getElementById('session-detail-content').innerHTML =
      buildZReportHTML(data.session, data.summary, data.denominations, data.payments,
                       data.session.status === 'closed' ? 'Z' : 'X');

    currentReportData = data;
    openModal('modal-session-detail');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/* ── Reopen session (Admin) ─────────────────────── */
async function reopenSession(sid) {
  if (!confirm('¿Deseas reabrir esta caja? Esta acción es de responsabilidad del administrador.')) return;
  try {
    await api.post(`/cash-sessions/${sid}/reopen`, {});
    showToast('Caja reabierta exitosamente', 'success');
    await checkCurrentSession();
    await loadHistory();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/* ── Print ──────────────────────────────────────── */
function printReport() {
  window.print();
}

/* ── Export globals ─────────────────────────────── */
window.confirmOpenSession  = confirmOpenSession;
window.startClosingFlow    = startClosingFlow;
window.confirmCloseSession = confirmCloseSession;
window.showReporteX        = showReporteX;
window.loadHistory         = loadHistory;
window.toggleDenominations = toggleDenominations;
window.updateDenomSubtotal = updateDenomSubtotal;
window.applyDenomTotals    = applyDenomTotals;
window.calcDifference      = calcDifference;
window.viewSessionDetail   = viewSessionDetail;
window.reopenSession       = reopenSession;
window.printReport         = printReport;
