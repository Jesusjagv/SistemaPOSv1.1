/* customers.js */
requireAuth();
initHeader();

let customers = [];

async function init() {
  try {
    customers = await api.get('/customers');
    renderCustomers(customers);
    updateStats(customers);
  } catch (e) { showToast(e.message, 'error'); }
}
init();

function updateStats(list) {
  document.getElementById('stat-total').textContent    = list.length;
  document.getElementById('stat-withdoc').textContent  = list.filter(c => c.doc_number).length;
  document.getElementById('stat-withphone').textContent = list.filter(c => c.phone).length;
}

let searchTimeout;
function searchCustomers(term) {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(async () => {
    try {
      const results = await api.get(`/customers?search=${encodeURIComponent(term)}`);
      customers = results;
      renderCustomers(results);
    } catch (e) { showToast(e.message, 'error'); }
  }, 300);
}

function renderCustomers(list) {
  const grid = document.getElementById('customer-grid');
  if (!list.length) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
      <i class="fa-solid fa-user-slash"></i><h3>Sin clientes</h3><p>No se encontraron clientes</p>
    </div>`;
    return;
  }
  grid.innerHTML = list.map(c => `
    <div class="customer-card" onclick="openDetail(${c.id})">
      <div class="customer-card-avatar">${c.name[0].toUpperCase()}</div>
      <div class="customer-card-name">${c.name}</div>
      <div class="customer-card-doc">${c.doc_type}-${c.doc_number || 'S/N'}</div>
      <div class="customer-card-info">
        ${c.phone   ? `<div><i class="fa-solid fa-phone"></i> ${c.phone}</div>` : ''}
        ${c.email   ? `<div><i class="fa-solid fa-envelope"></i> ${c.email}</div>` : ''}
        ${c.address ? `<div><i class="fa-solid fa-location-dot"></i> ${c.address}</div>` : ''}
      </div>
      <div style="display:flex;gap:0.35rem;margin-top:0.75rem;" onclick="event.stopPropagation()">
        <button class="btn btn-outline btn-sm" onclick="openCustomerModal(${c.id})">
          <i class="fa-solid fa-pen"></i> Editar
        </button>
        <button class="btn btn-danger btn-sm" onclick="deleteCustomer(${c.id},'${c.name.replace(/'/g,"\\'")}')">
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>
    </div>`).join('');
}

/* ── Modal ──────────────────────────────────────────────── */
function openCustomerModal(customerId = null) {
  ['cust-id','cust-name','cust-docnum','cust-phone','cust-email','cust-address'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('cust-doctype').value = 'V';

  if (customerId) {
    const c = customers.find(x => x.id === customerId);
    if (!c) return;
    document.getElementById('modal-cust-title').innerHTML = '<i class="fa-solid fa-pen" style="color:var(--violet-light);"></i> Editar Cliente';
    document.getElementById('cust-id').value      = c.id;
    document.getElementById('cust-name').value    = c.name;
    document.getElementById('cust-doctype').value = c.doc_type || 'V';
    document.getElementById('cust-docnum').value  = c.doc_number || '';
    document.getElementById('cust-phone').value   = c.phone || '';
    document.getElementById('cust-email').value   = c.email || '';
    document.getElementById('cust-address').value = c.address || '';
  } else {
    document.getElementById('modal-cust-title').innerHTML = '<i class="fa-solid fa-user-plus" style="color:var(--violet-light);"></i> Nuevo Cliente';
  }
  openModal('customer-modal');
}

async function saveCustomer() {
  const id      = document.getElementById('cust-id').value;
  const name    = document.getElementById('cust-name').value.trim();
  if (!name) { showToast('El nombre es requerido', 'warning'); return; }

  const body = {
    name,
    doc_type:   document.getElementById('cust-doctype').value,
    doc_number: document.getElementById('cust-docnum').value.trim() || null,
    phone:      document.getElementById('cust-phone').value.trim() || null,
    email:      document.getElementById('cust-email').value.trim() || null,
    address:    document.getElementById('cust-address').value.trim() || null,
  };
  try {
    if (id) {
      await api.put(`/customers/${id}`, body);
      showToast('Cliente actualizado', 'success');
    } else {
      await api.post('/customers', body);
      showToast('Cliente registrado', 'success');
    }
    closeModal('customer-modal');
    customers = await api.get('/customers');
    renderCustomers(customers);
    updateStats(customers);
  } catch (e) { showToast(e.message, 'error'); }
}

async function deleteCustomer(id, name) {
  confirmAction(`¿Eliminar al cliente "${name}"?`, async () => {
    try {
      await api.delete(`/customers/${id}`);
      showToast('Cliente eliminado', 'success');
      customers = customers.filter(c => c.id !== id);
      renderCustomers(customers);
      updateStats(customers);
    } catch (e) { showToast(e.message, 'error'); }
  });
}

async function openDetail(id) {
  try {
    const c = await api.get(`/customers/${id}`);
    const body = document.getElementById('detail-body');
    body.innerHTML = `
      <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
        <div class="customer-card-avatar" style="width:54px;height:54px;font-size:1.3rem;">${c.name[0].toUpperCase()}</div>
        <div>
          <div style="font-size:1.1rem;font-weight:700;">${c.name}</div>
          <div style="color:var(--text-muted);font-size:0.8rem;">${c.doc_type}-${c.doc_number || 'Sin documento'}</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;font-size:0.82rem;margin-bottom:1rem;">
        ${c.phone   ? `<div><b>Teléfono:</b> ${c.phone}</div>` : ''}
        ${c.email   ? `<div><b>Email:</b> ${c.email}</div>` : ''}
        ${c.address ? `<div style="grid-column:1/-1;"><b>Dirección:</b> ${c.address}</div>` : ''}
      </div>
      <div style="font-weight:600;margin-bottom:0.5rem;">Últimas compras</div>
      ${c.recent_sales.length ? `
      <div class="table-wrapper">
        <table>
          <thead><tr><th>Factura</th><th>Total $</th><th>Total Bs</th><th>Método</th><th>Fecha</th></tr></thead>
          <tbody>${c.recent_sales.map(s => `
            <tr>
              <td>${s.sale_number}</td>
              <td class="price-usd">${parseFloat(s.total_usd).toFixed(2)}</td>
              <td class="price-bs" style="font-size:0.75rem;">${parseFloat(s.total_bs).toLocaleString('es-VE',{minimumFractionDigits:2})}</td>
              <td>${s.payment_method}</td>
              <td style="color:var(--text-muted);font-size:0.75rem;">${new Date(s.created_at).toLocaleDateString('es-VE')}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>` : '<div class="empty-state"><i class="fa-solid fa-receipt"></i><p>Sin compras registradas</p></div>'}
    `;
    openModal('detail-modal');
  } catch (e) { showToast(e.message, 'error'); }
}
