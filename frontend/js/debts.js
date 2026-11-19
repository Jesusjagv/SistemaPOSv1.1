/* ======================================================
   debts.js — Cuentas por Cobrar / Gestión de Fiados
   ====================================================== */

let allDebts = [];
let currentDetailDebtId = null;

(function () {
  if (!requireAuth()) return;

  initHeader();
  loadDebts().then(() => {
    // Refresh display once BCV rate loads
    loadBCVRate().then(rate => {
      if (rate > 0) {
        renderDebts(allDebts);
        updateStats(allDebts);
      }
    });
  });
  checkOverdueAlert();
})();

// ── Load debts ─────────────────────────────────────────

async function loadDebts() {
  try {
    allDebts = await api.get('/debts');
    renderDebts(allDebts);
    updateStats(allDebts);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function renderDebts(list) {
  const tbody = document.getElementById('debts-tbody');
  const rate = getRate();
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:2rem;color:var(--text-muted);">No hay deudas registradas</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(d => {
    const bal = parseFloat(d.balance_usd);
    const balBs = rate > 0 ? bal * rate : 0;
    const statusClass = d.status === 'Pagada' ? 'badge-active'
                      : d.status === 'Vencida' ? 'badge-inactive'
                      : 'badge-pending';
    const statusLabel = d.status === 'Pagada' ? 'Pagada'
                      : d.status === 'Vencida' ? 'Vencida'
                      : 'Pendiente';
    const due = d.due_date ? d.due_date.substring(0, 10) : '—';
    const isOverdue = d.status === 'Vencida';
    return `<tr${isOverdue ? ' style="background:rgba(244,63,94,0.05);"' : ''}>
      <td><span style="color:var(--text-muted);font-size:0.8rem;">#${d.id}</span></td>
      <td><strong>${escHtml(d.customer_name)}</strong></td>
      <td>${escHtml(d.concept || '—')}</td>
      <td>${fmtUSD(d.original_amount_usd)}</td>
      <td><strong>${fmtUSD(bal)}</strong></td>
      <td>${fmtBS(balBs)}</td>
      <td style="font-size:0.8rem;">${due}</td>
      <td><span class="badge ${statusClass}">${statusLabel}</span></td>
      <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
        <button class="btn btn-sm btn-ghost" onclick="openDetail(${d.id})" title="Ver detalle" style="font-size:0.95rem;">
          <i class="fa-solid fa-eye"></i>
        </button>
        ${bal > 0 ? `
        <button class="btn btn-sm btn-ghost" onclick="openPayFromList(${d.id})" title="Registrar pago" style="color:var(--green);font-size:0.95rem;">
          <i class="fa-solid fa-dollar-sign"></i>
        </button>
        <button class="btn btn-sm btn-ghost" onclick="openReminder(${d.id})" title="Recordatorio WhatsApp" style="color:#25D366;font-size:0.95rem;">
          <i class="fa-brands fa-whatsapp"></i>
        </button>` : ''}
      </td>
    </tr>`;
  }).join('');
}

function updateStats(list) {
  const rate = getRate();
  const total = list.reduce((s, d) => s + parseFloat(d.balance_usd || 0), 0);
  const totalBs = rate > 0 ? total * rate : 0;
  const pending = list.filter(d => d.status === 'Pendiente').length;
  const overdue = list.filter(d => d.status === 'Vencida').length;
  document.getElementById('stat-total').textContent = fmtUSD(total);
  document.getElementById('stat-total-bs').textContent = rate > 0
    ? `${fmtBS(totalBs)}  @  ${rate.toFixed(2)}`
    : 'Bs --';
  document.getElementById('stat-pending').textContent = pending;
  document.getElementById('stat-overdue').textContent = overdue;
}

function filterDebts(val) {
  const term = ((val || document.getElementById('search-input').value || '')).toLowerCase();
  const status = document.getElementById('status-filter').value;
  let filtered = allDebts;
  if (term) {
    filtered = filtered.filter(d =>
      (d.customer_name || '').toLowerCase().includes(term) ||
      (d.concept || '').toLowerCase().includes(term)
    );
  }
  if (status) {
    filtered = filtered.filter(d => d.status === status);
  }
  renderDebts(filtered);
}

// ── Register debt ──────────────────────────────────────

let customersCache = [];

async function openRegisterModal() {
  document.getElementById('reg-amount-usd').value = '';
  document.getElementById('reg-amount-bs').value = '';
  document.getElementById('reg-due-date').value = '';
  document.getElementById('reg-concept').value = '';
  document.getElementById('reg-limit-info').textContent = '';
  document.getElementById('reg-rate-info').textContent = '';

  const sel = document.getElementById('reg-customer');
  sel.innerHTML = '<option value="">Cargando clientes...</option>';
  sel.disabled = true;
  openModal('register-modal');
  loadBCVRate();

  try {
    customersCache = await api.get('/customers');
    sel.innerHTML = '<option value="">Selecciona un cliente...</option>'
      + customersCache.map(c =>
        `<option value="${c.id}">${escHtml(c.name)}${c.doc_number ? ' — ' + escHtml(c.doc_number) : ''}</option>`
      ).join('');
    sel.disabled = false;
    sel.onchange = updateCreditInfo;
  } catch (e) {
    sel.innerHTML = '<option value="">Error cargando clientes</option>';
    showToast(e.message, 'error');
  }
}

let _lastRate = null;

async function calcBsFromUsd() {
  const usdInput = document.getElementById('reg-amount-usd');
  const bsInput = document.getElementById('reg-amount-bs');
  const info = document.getElementById('reg-rate-info');
  const usd = parseFloat(usdInput.value) || 0;
  if (usd > 0) {
    const rate = await loadBCVRate();
    _lastRate = rate;
    bsInput.value = (usd * rate).toFixed(2);
    info.textContent = `Tasa BCV: 1$ = ${rate.toFixed(2)} Bs · ${fmtBS(usd * rate)}`;
  } else {
    info.textContent = '';
  }
}

async function calcUsdFromBs() {
  const usdInput = document.getElementById('reg-amount-usd');
  const bsInput = document.getElementById('reg-amount-bs');
  const info = document.getElementById('reg-rate-info');
  const bs = parseFloat(bsInput.value) || 0;
  if (bs > 0) {
    const rate = await loadBCVRate();
    _lastRate = rate;
    usdInput.value = (bs / rate).toFixed(2);
    info.textContent = `Tasa BCV: 1$ = ${rate.toFixed(2)} Bs · ${fmtUSD(bs / rate)}`;
  } else {
    info.textContent = '';
  }
}

function updateCreditInfo() {
  const cid = parseInt(document.getElementById('reg-customer').value);
  const info = document.getElementById('reg-limit-info');
  if (!cid) { info.textContent = ''; return; }
  const customer = customersCache.find(c => c.id === cid);
  if (!customer) return;
  const limit = parseFloat(customer.credit_limit_usd || 0);
  const status = customer.credit_status || 'Solvente';
  const statusIcon = status === 'Solvente' ? '🟢' : status === 'En Mora' ? '🟡' : '🔴';
  if (limit > 0) {
    info.innerHTML = `${statusIcon} Límite de crédito: <strong>${fmtUSD(limit)}</strong> | Estado: <strong>${status}</strong>`;
  } else {
    info.innerHTML = `${statusIcon} Estado: <strong>${status}</strong> (sin límite definido)`;
  }
}

async function registerDebt() {
  const customer_id = parseInt(document.getElementById('reg-customer').value);
  const amount_usd = parseFloat(document.getElementById('reg-amount-usd').value);
  const amount_bs = parseFloat(document.getElementById('reg-amount-bs').value);
  const due_date = document.getElementById('reg-due-date').value;
  const concept = document.getElementById('reg-concept').value.trim();

  if (!customer_id || !due_date) {
    showToast('Completa todos los campos obligatorios (*)', 'warning');
    return;
  }
  if ((!amount_usd || amount_usd <= 0) && (!amount_bs || amount_bs <= 0)) {
    showToast('Indica un monto en USD o Bs', 'warning');
    return;
  }

  try {
    await api.post('/debts', { customer_id, amount_usd: amount_usd || 0, amount_bs: amount_bs || 0, due_date, concept: concept || undefined });
    showToast('Deuda registrada exitosamente', 'success');
    closeModal('register-modal');
    await loadDebts();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// ── Detail view ────────────────────────────────────────

async function openDetail(id) {
  currentDetailDebtId = id;
  const body = document.getElementById('detail-body');
  body.innerHTML = '<div style="text-align:center;padding:1rem;"><div class="spinner"></div></div>';
  openModal('detail-modal');

  try {
    const debt = await api.get(`/debts/${id}`);
    const bal = parseFloat(debt.balance_usd);
    const due = debt.due_date ? debt.due_date.substring(0, 10) : '—';
    const statusClass = debt.status === 'Pagada' ? 'badge-active'
                      : debt.status === 'Vencida' ? 'badge-inactive'
                      : 'badge-pending';

    let paymentsHtml = '';
    if (debt.payments && debt.payments.length) {
      paymentsHtml = debt.payments.map(p => {
        const methodLabels = { cash: 'Efectivo', tarjeta: 'Tarjeta', pago_movil: 'Pago Móvil', transferencia: 'Transferencia' };
        const methodLabel = methodLabels[p.payment_method] || p.payment_method;
        return `<tr>
          <td>${p.created_at ? p.created_at.substring(0, 10) : '—'}</td>
          <td>${fmtUSD(p.amount_paid_usd)}</td>
          <td>${p.amount_paid_bs > 0 ? fmtBS(p.amount_paid_bs) : '—'}</td>
          <td>${methodLabel}</td>
        </tr>`;
      }).join('');
    } else {
      paymentsHtml = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">Sin pagos registrados</td></tr>';
    }

    const origBs = parseFloat(debt.original_amount_bs || 0);
    const origRate = parseFloat(debt.exchange_rate_at_creation || 0);
    let origStr = fmtUSD(debt.original_amount_usd);
    if (origBs > 0) origStr += ` (${fmtBS(origBs)})`;
    if (origRate > 0) origStr += ` @ ${origRate.toFixed(2)}`;

    const currentRate = getRate();
    const balBsNow = currentRate > 0 ? bal * currentRate : 0;
    const balBsLabel = currentRate > 0
      ? `${fmtBS(balBsNow)} <span style="font-size:0.7rem;color:var(--text-muted);">@ ${currentRate.toFixed(2)}</span>`
      : 'Bs --';

    body.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin-bottom:1rem;">
        <div><strong>Cliente:</strong> ${escHtml(debt.customer_name)}</div>
        <div><strong>Estado:</strong> <span class="badge ${statusClass}">${debt.status}</span></div>
        <div><strong>Monto Original:</strong> ${origStr}</div>
        <div><strong>Saldo Actual:</strong> <strong>${fmtUSD(bal)}</strong> <span style="color:var(--text-muted);font-size:0.78rem;">(${balBsLabel})</span></div>
        <div><strong>Vencimiento:</strong> ${due}</div>
        <div><strong>Concepto:</strong> ${escHtml(debt.concept || '—')}</div>
      </div>
      <div style="font-weight:600;font-size:0.85rem;color:var(--text-secondary);margin-bottom:0.5rem;">
        <i class="fa-solid fa-receipt"></i> Historial de Pagos
      </div>
      <div class="card" style="padding:0;">
        <div class="table-wrapper">
          <table style="width:100%;">
            <thead><tr><th>Fecha</th><th>USD</th><th>VES</th><th>Método</th></tr></thead>
            <tbody>${paymentsHtml}</tbody>
          </table>
        </div>
      </div>
    `;

    const payBtn = document.getElementById('detail-pay-btn');
    payBtn.style.display = bal > 0 ? 'inline-flex' : 'none';
  } catch (e) {
    body.innerHTML = `<div style="color:var(--rose);padding:1rem;">Error: ${escHtml(e.message)}</div>`;
  }
}

// ── Payment ────────────────────────────────────────────

let payingDebtId = null;

function openPayFromList(id) {
  payingDebtId = id;
  openPayModal();
}

async function openPayModal() {
  const id = payingDebtId || currentDetailDebtId;
  if (!id) return;

  try {
    const debt = await api.get(`/debts/${id}`);
    const bal = parseFloat(debt.balance_usd);
    const rate = getRate();
    const balBs = rate > 0 ? bal * rate : 0;

    document.getElementById('pay-customer-name').textContent = debt.customer_name;
    document.getElementById('pay-current-balance').innerHTML = rate > 0
      ? `${fmtUSD(bal)} <span style="color:var(--text-muted);font-size:0.78rem;">(${fmtBS(balBs)} @ ${rate.toFixed(2)})</span>`
      : fmtUSD(bal);
    document.getElementById('pay-modal').dataset.debtId = id;
    document.getElementById('pay-modal').dataset.balance = debt.balance_usd;

    // Reset payment entries to single
    const container = document.getElementById('payments-container');
    container.innerHTML = getPaymentEntryHtml(0);
    closeModal('detail-modal');
    openModal('pay-modal');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function getPaymentEntryHtml(index) {
  return `
    <div class="payment-entry" style="border:1px solid var(--border);border-radius:8px;padding:0.75rem;margin-bottom:0.75rem;" data-index="${index}">
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Método de Pago *</label>
          <select class="form-control payment-method" onchange="togglePaymentRef(this)">
            <option value="cash">Efectivo</option>
            <option value="tarjeta">Tarjeta</option>
            <option value="pago_movil">Pago Móvil</option>
            <option value="transferencia">Transferencia</option>
            <option value="mixed">Mixto USD/VES</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Monto USD</label>
          <input type="number" step="0.01" min="0" class="form-control pay-usd" placeholder="0.00" oninput="calcUsdEquiv(this)">
        </div>
        <div class="form-group">
          <label class="form-label">Monto VES</label>
          <input type="number" step="0.01" min="0" class="form-control pay-bs" placeholder="0.00" oninput="calcBsEquiv(this)">
        </div>
      </div>
      <div class="form-row ref-fields" style="display:none;">
        <div class="form-group">
          <label class="form-label">Referencia *</label>
          <input class="form-control pay-ref" placeholder="N° de referencia">
        </div>
        <div class="form-group">
          <label class="form-label">Banco Origen *</label>
          <input class="form-control pay-bank-origin" placeholder="Ej: Banco Mercantil">
        </div>
        <div class="form-group">
          <label class="form-label">Banco Destino *</label>
          <input class="form-control pay-bank-dest" placeholder="Ej: Banco de Venezuela">
        </div>
      </div>
      <div class="pay-usd-equiv" style="font-size:0.78rem;color:var(--text-muted);margin-top:0.35rem;"></div>
    </div>`;
}

function addPaymentEntry() {
  const container = document.getElementById('payments-container');
  const count = container.children.length;
  container.insertAdjacentHTML('beforeend', getPaymentEntryHtml(count));
}

function togglePaymentRef(sel) {
  const entry = sel.closest('.payment-entry');
  const refFields = entry.querySelector('.ref-fields');
  const method = sel.value;
  refFields.style.display = (method === 'pago_movil' || method === 'transferencia') ? 'grid' : 'none';
}

async function calcUsdEquiv(input) {
  const entry = input.closest('.payment-entry');
  const bsInput = entry.querySelector('.pay-bs');
  const equivDiv = entry.querySelector('.pay-usd-equiv');
  const bs = parseFloat(bsInput.value) || 0;
  if (bs > 0) {
    const rate = await loadBCVRate();
    const equivUsd = bs / rate;
    equivDiv.textContent = `↔️ Equivale a ${fmtUSD(equivUsd)} (tasa: ${rate.toFixed(2)})`;
  } else {
    equivDiv.textContent = '';
  }
}

async function calcBsEquiv(input) {
  const entry = input.closest('.payment-entry');
  const usdInput = entry.querySelector('.pay-usd');
  const equivDiv = entry.querySelector('.pay-usd-equiv');
  const usd = parseFloat(usdInput.value) || 0;
  if (usd > 0) {
    const rate = await loadBCVRate();
    const equivBs = usd * rate;
    equivDiv.textContent = `↔️ Equivale a ${fmtBS(equivBs)} (tasa: ${rate.toFixed(2)})`;
  } else {
    equivDiv.textContent = '';
  }
}

async function submitPayment() {
  const debtId = document.getElementById('pay-modal').dataset.debtId;
  const balance = parseFloat(document.getElementById('pay-modal').dataset.balance);
  const entries = document.querySelectorAll('.payment-entry');
  const payments = [];
  let errors = [];

  for (const entry of entries) {
    const method = entry.querySelector('.payment-method').value;
    const usd = parseFloat(entry.querySelector('.pay-usd').value) || 0;
    const bs = parseFloat(entry.querySelector('.pay-bs').value) || 0;
    const ref = entry.querySelector('.pay-ref')?.value?.trim() || '';
    const bankOrigin = entry.querySelector('.pay-bank-origin')?.value?.trim() || '';
    const bankDest = entry.querySelector('.pay-bank-dest')?.value?.trim() || '';

    if (usd === 0 && bs === 0) {
      errors.push('Cada pago debe tener un monto en USD o VES');
      continue;
    }

    if ((method === 'pago_movil' || method === 'transferencia') && !ref) {
      errors.push('Referencia es obligatoria para Pago Móvil/Transferencia');
      continue;
    }

    payments.push({
      payment_method: method,
      amount_usd: usd,
      amount_bs: bs,
      reference_number: ref || undefined,
      bank_origin: bankOrigin || undefined,
      bank_destination: bankDest || undefined
    });
  }

  if (errors.length) {
    showToast(errors[0], 'warning');
    return;
  }

  if (!payments.length) {
    showToast('Agrega al menos un pago', 'warning');
    return;
  }

  try {
    const result = await api.post(`/debts/${debtId}/pay`, { payments });
    showToast(`Pago registrado: ${result.message}`, 'success');
    closeModal('pay-modal');
    await loadDebts();
    // If we came from detail, refresh it
    if (currentDetailDebtId) {
      openDetail(currentDetailDebtId);
    }
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// ── WhatsApp Reminder ─────────────────────────────────

let reminderData = null;

async function openReminder(id) {
  try {
    const data = await api.get(`/debts/${id}/reminder`);
    reminderData = data;
    document.getElementById('reminder-text').value = data.message;
    const waBtn = document.getElementById('reminder-wa-btn');
    if (data.phone) {
      waBtn.style.display = 'block';
    } else {
      waBtn.style.display = 'none';
      document.getElementById('reminder-text').insertAdjacentHTML('afterend',
        '<div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.5rem;">⚠️ El cliente no tiene teléfono registrado. Copia el mensaje manualmente.</div>'
      );
    }
    openModal('reminder-modal');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function normalizePhone(raw) {
  let num = raw.replace(/[^0-9]/g, '');
  if (!num) return '';
  // Strip leading 00 (e.g. 0058412...)
  if (num.startsWith('00')) num = num.slice(2);
  // If starts with local prefix 0 (e.g. 0412... → 58412...)
  if (num.startsWith('0')) num = '58' + num.slice(1);
  // If starts with 58 but is too short, pad it (should not happen)
  else if (!num.startsWith('58') && !num.startsWith('1')) num = '58' + num;
  // Minimum valid length for VE (58 + 10 digits = 12 chars)
  return num;
}

function sendWhatsApp() {
  if (!reminderData) return;
  const phone = normalizePhone(reminderData.phone || '');
  const text = encodeURIComponent(reminderData.message);
  const url = phone ? `https://wa.me/${phone}?text=${text}` : `https://wa.me/?text=${text}`;
  window.open(url, '_blank');
}

// ── Overdue alert (dashboard) ─────────────────────────

async function checkOverdueAlert() {
  try {
    const data = await api.get('/debts/overdue/count');
    if (data.count > 0) {
      showToast(`⚠️ Tienes ${data.count} deuda(s) vencida(s) pendiente(s) de cobro.`, 'warning', 6000);
    }
  } catch (e) {
    // silently ignore
  }
}

// ── Helper ────────────────────────────────────────────

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
