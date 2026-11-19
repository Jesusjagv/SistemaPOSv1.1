/* products.js */
requireAuth();

const user = getUser();
const isAdmin = user.role === 'admin';

initHeader();

let products   = [];
let categories = [];
// NO declarar currentRate aquí — usar la de api.js (window.currentRate)

async function init() {
  // Ocultar botones de crear/gestionar para cajero (solo admin puede crear y gestionar categorías)
  if (!isAdmin) {
    document.getElementById('btn-add').style.display = 'none';
    document.getElementById('btn-cat').style.display = 'none';
  }

  // ── Cargar categorías y productos (sin bloquear por tasa BCV) ──
  try {
    const [cats, prods] = await Promise.all([
      api.get('/products/categories/all'),
      api.get('/products?active=all')
    ]);
    categories = cats;
    products   = prods;
    populateCatFilter();
    populateCatSelect();
    renderProducts(products);
    updateStats(products);
  } catch (e) {
    showToast('Error cargando productos: ' + e.message, 'error');
    document.getElementById('products-tbody').innerHTML =
      `<tr><td colspan="10" style="text-align:center;padding:2rem;">
        <div class="empty-state">
          <i class="fa-solid fa-triangle-exclamation" style="color:var(--rose);"></i>
          <h3 style="color:var(--rose);">Error al cargar productos</h3>
          <p>${e.message}</p>
          <button class="btn btn-primary btn-sm" onclick="init()" style="margin-top:0.75rem;">
            <i class="fa-solid fa-rotate"></i> Reintentar
          </button>
        </div>
      </td></tr>`;
    ['stat-total','stat-active','stat-low','stat-zero'].forEach(id => {
      document.getElementById(id).textContent = '0';
    });
  }
}

// Cargar tasa BCV de forma totalmente independiente (no bloquea nada)
async function loadRate() {
  try {
    const rateData = await api.get('/exchange/rate');
    window.currentRate = rateData.rate;
    const rateEl = document.getElementById('bcv-rate');
    if (rateEl) rateEl.textContent = rateData.rate.toLocaleString('es-VE', {minimumFractionDigits: 2});
    // Re-renderizar tabla con precios en Bs si ya cargaron
    if (products.length) renderProducts(products);
  } catch (e) {
    console.warn('BCV rate load failed:', e.message);
  }
}

// Arrancar ambos en paralelo, completamente separados
init();
loadRate();

function populateCatFilter() {
  const sel = document.getElementById('cat-filter');
  sel.innerHTML = '<option value="">Todas las categorías</option>' +
    categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
}

function populateCatSelect() {
  const sel = document.getElementById('prod-cat');
  sel.innerHTML = '<option value="">Sin categoría</option>' +
    categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
}

function updateStats(prods) {
  const active = prods.filter(p => p.active);
  const low    = prods.filter(p => p.active && p.stock > 0 && p.stock <= p.min_stock);
  const zero   = prods.filter(p => p.active && p.stock === 0);
  document.getElementById('stat-total').textContent  = prods.length;
  document.getElementById('stat-active').textContent = active.length;
  document.getElementById('stat-low').textContent    = low.length;
  document.getElementById('stat-zero').textContent   = zero.length;
}

function filterProducts(val) {
  const term   = (document.getElementById('search-input').value || '').toLowerCase();
  const catId  = document.getElementById('cat-filter').value;
  const filtered = products.filter(p => {
    const matchSearch = !term || p.name.toLowerCase().includes(term) || (p.code && p.code.toLowerCase().includes(term));
    const matchCat    = !catId || p.category_id == catId;
    return matchSearch && matchCat;
  });
  renderProducts(filtered);
}

function renderProducts(list) {
  const tbody = document.getElementById('products-tbody');
  const cardsContainer = document.getElementById('products-cards');

  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="10"><div class="empty-state"><i class="fa-solid fa-box-open"></i><h3>Sin productos</h3><p>No se encontraron productos</p></div></td></tr>`;
    if (cardsContainer) cardsContainer.innerHTML = `<div class="empty-state" style="padding:2rem;text-align:center;"><i class="fa-solid fa-box-open" style="font-size:2rem;color:var(--text-muted);"></i><h3 style="margin:0.5rem 0 0.25rem;color:var(--text-primary);">Sin productos</h3><p style="color:var(--text-muted);font-size:0.85rem;">No se encontraron productos</p></div>`;
    return;
  }

  // ── Render table rows (desktop) ──
  tbody.innerHTML = list.map(p => {
    const cat      = categories.find(c => c.id === p.category_id);
    const priceBs  = (p.price_usd * (window.currentRate || 0)).toFixed(2);
    const stockCls = p.stock === 0 ? 'badge-red' : p.stock <= p.min_stock ? 'badge-amber' : 'badge-green';

    const gainUsd  = p.price_usd - (p.cost_usd || 0);
    const gainPct  = p.cost_usd > 0 ? ((gainUsd / p.cost_usd) * 100).toFixed(1) : null;
    const gainCls  = gainUsd >= 0 ? 'badge-green' : 'badge-red';
    const gainHtml = p.cost_usd > 0
      ? `<span class="badge ${gainCls}">+${fmtUSD(gainUsd)} / ${gainPct}%</span>`
      : '<span class="badge badge-gray">—</span>';

    const stockBtn = `<button class="btn btn-outline btn-sm" onclick="openStockModal(${p.id})" title="Ajustar stock">
          <i class="fa-solid fa-cubes"></i></button>`;
    const editBtn = `<button class="btn btn-outline btn-sm" onclick="openProductModal(${p.id})" title="Editar producto">
          <i class="fa-solid fa-pen"></i></button>`;
    const deleteBtn = isAdmin
      ? `<button class="btn btn-danger btn-sm" onclick="deleteProduct(${p.id}, '${p.name.replace(/'/g, "\\'")}')" title="Eliminar (solo admin)">
          <i class="fa-solid fa-trash"></i></button>`
      : '';

    const actions = `<div class="action-group">${stockBtn}${editBtn}${deleteBtn}</div>`;
    const rowCls = p.active ? '' : 'row-inactive';

    return `<tr class="${rowCls}">
      <td>
        <div style="font-weight:600;">
          ${p.name} 
          ${p.is_combo ? '<span class="badge" style="background:var(--amber);color:black;font-size:0.6rem;margin-left:0.5rem;"><i class="fa-solid fa-layer-group"></i> COMBO</span>' : ''}
          ${p.is_bulk ? '<span class="badge" style="background:#D69E2E;color:#1A202C;font-size:0.6rem;margin-left:0.5rem;font-weight:700;"><i class="fa-solid fa-weight-hanging"></i> GRANEL</span>' : ''}
        </div>
      </td>
      <td><code style="font-size:0.75rem;color:var(--text-muted);">${p.code || '—'}</code></td>
      <td>${cat ? `<span class="badge" style="background:${cat.color}22;color:${cat.color};">${cat.name}</span>` : '—'}</td>
      <td><span class="price-usd">${p.price_usd.toFixed(2)}</span></td>
      <td><span class="price-bs">${parseFloat(priceBs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span></td>
      <td style="color:var(--text-muted);font-size:0.8rem;">
        ${fmtUSD(p.cost_usd)}
        ${p.costo_unidad > 0 ? `<br><small style="color:var(--emerald-light);">${fmtUSD(p.costo_unidad)}/und</small>` : ''}
      </td>
      <td style="font-size:0.8rem;">
        ${gainHtml}
        ${p.margen_unidad_porcentaje > 0 ? `<br><small style="color:var(--violet-light);">${p.margen_unidad_porcentaje.toFixed(1)}%/und</small>` : ''}
      </td>
      <td><span class="badge ${stockCls}">${p.stock}</span></td>
      <td>${p.active ? '<span class="badge badge-green">Activo</span>' : '<span class="badge badge-gray">Inactivo</span>'}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('');

  // ── Render product cards (mobile) ──
  if (cardsContainer) {
    cardsContainer.innerHTML = list.map(p => {
      const cat      = categories.find(c => c.id === p.category_id);
      const priceBs  = (p.price_usd * (window.currentRate || 0)).toFixed(2);
      const stockCls = p.stock === 0 ? 'badge-red' : p.stock <= p.min_stock ? 'badge-amber' : 'badge-green';
      const gainUsd  = p.price_usd - (p.cost_usd || 0);
      const gainPct  = p.cost_usd > 0 ? ((gainUsd / p.cost_usd) * 100).toFixed(1) : null;
      const gainCls  = gainUsd >= 0 ? 'badge-green' : 'badge-red';
      const rowCls   = p.active ? '' : 'row-inactive';

      const editBtn = `<button class="btn btn-outline btn-sm" onclick="openProductModal(${p.id})">
          <i class="fa-solid fa-pen"></i> Editar</button>`;
      const stockBtn = `<button class="btn btn-outline btn-sm" onclick="openStockModal(${p.id})">
          <i class="fa-solid fa-cubes"></i> Stock</button>`;
      const deleteBtn = isAdmin
        ? `<button class="btn btn-danger btn-sm" onclick="deleteProduct(${p.id}, '${p.name.replace(/'/g, "\\'")}')">
            <i class="fa-solid fa-trash"></i> Eliminar</button>`
        : '';

      return `<div class="product-card ${rowCls}">
        <div class="product-card-header">
          <div class="product-card-name">
            ${p.name}
            ${p.is_combo ? '<span class="badge" style="background:var(--amber);color:#1A202C;"><i class="fa-solid fa-layer-group"></i> COMBO</span>' : ''}
            ${p.is_bulk ? '<span class="badge" style="background:#D69E2E;color:#1A202C;"><i class="fa-solid fa-weight-hanging"></i> GRANEL</span>' : ''}
          </div>
          <div class="product-card-price">
            <span class="price-usd">$${p.price_usd.toFixed(2)}</span>
            <span class="price-bs">Bs ${parseFloat(priceBs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span>
          </div>
        </div>
        <div class="product-card-details">
          ${p.code ? `<div class="product-card-detail"><i class="fa-solid fa-barcode"></i> <code>${p.code}</code></div>` : ''}
          ${cat ? `<div class="product-card-detail"><i class="fa-solid fa-tag" style="color:${cat.color};"></i> ${cat.name}</div>` : ''}
          <div class="product-card-detail"><i class="fa-solid fa-cubes"></i> Stock: <strong>${p.stock}</strong></div>
        </div>
        <div class="product-card-badges">
          <span class="badge ${stockCls}">${p.stock === 0 ? 'Sin stock' : p.stock <= p.min_stock ? 'Stock bajo' : 'Disponible'}</span>
          ${gainPct ? `<span class="badge ${gainCls}">+${gainPct}%</span>` : ''}
          ${!p.active ? '<span class="badge badge-gray">Inactivo</span>' : ''}
        </div>
        <div class="product-card-actions">
          ${editBtn}
          ${stockBtn}
          ${deleteBtn}
        </div>
      </div>`;
    }).join('');
  }
}

/* ── Modal: Product ─────────────────────────────────────── */

const PRODUCT_DRAFT_KEY = 'pos_product_draft';

function saveProductDraft() {
  const name = document.getElementById('prod-name').value.trim();
  const code = document.getElementById('prod-code').value.trim();
  const price = document.getElementById('prod-price').value;
  const cost = document.getElementById('prod-cost').value;
  const stock = document.getElementById('prod-stock').value;

  // No guardar borrador si no hay ningún dato relevante
  const hasData = name || code || price || cost || stock;
  if (!hasData) return;

  const fields = {
    name: name,
    code: code,
    cat: document.getElementById('prod-cat').value,
    price: price,
    cost: cost,
    profitPct: document.getElementById('prod-profit-pct').value,
    stock: stock,
    minstock: document.getElementById('prod-minstock').value,
    active: document.getElementById('prod-active').value,
    uxp: document.getElementById('prod-uxp').value,
    unitPrice: document.getElementById('prod-unit-price').value,
    unitMargin: document.getElementById('prod-unit-margin').value,
    isBulk: document.getElementById('prod-is-bulk').checked,
    isEdit: document.getElementById('prod-id').value || null
  };
  localStorage.setItem(PRODUCT_DRAFT_KEY, JSON.stringify(fields));
}

function loadProductDraft() {
  try {
    const raw = localStorage.getItem(PRODUCT_DRAFT_KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw);
    // No devolver borrador si no hay ningún dato relevante
    const hasData = draft.name || draft.code || draft.price || draft.cost || draft.stock;
    if (!hasData) return null;
    return draft;
  } catch { return null; }
}

function clearProductDraft() {
  localStorage.removeItem(PRODUCT_DRAFT_KEY);
}

function restoreProductDraft(draft) {
  document.getElementById('prod-name').value = draft.name || '';
  document.getElementById('prod-code').value = draft.code || '';
  document.getElementById('prod-cat').value = draft.cat || '';
  document.getElementById('prod-price').value = draft.price || '';
  document.getElementById('prod-cost').value = draft.cost || '';
  document.getElementById('prod-profit-pct').value = draft.profitPct || '';
  document.getElementById('prod-stock').value = draft.stock || '';
  document.getElementById('prod-minstock').value = draft.minstock || '5';
  document.getElementById('prod-active').value = draft.active || '1';
  document.getElementById('prod-uxp').value = draft.uxp || 1;
  document.getElementById('prod-unit-price').value = draft.unitPrice || '';
  document.getElementById('prod-unit-margin').value = draft.unitMargin || '';
  document.getElementById('prod-is-bulk').checked = draft.isBulk || false;
  updateUnitFields();
  updateProfitInfo();
  updatePriceBsPreview();
  toggleBulkFields();
}

function openProductModal(productId = null) {
  document.getElementById('prod-id').value = '';
  document.getElementById('prod-name').value = '';
  document.getElementById('prod-code').value = '';
  document.getElementById('prod-price').value = '';
  document.getElementById('prod-cost').value = '';
  document.getElementById('prod-profit-pct').value = '';
  document.getElementById('prod-stock').value = '';
  document.getElementById('prod-minstock').value = '5';
  document.getElementById('prod-active').value = '1';
  document.getElementById('prod-active-row').style.display = 'none';
  document.getElementById('bs-preview').textContent = '';
  document.getElementById('profit-estimate').textContent = '$0.00';
  // Unit conversion fields
  document.getElementById('prod-uxp').value = 1;
  document.getElementById('prod-unit-price').value = '';
  document.getElementById('prod-unit-margin').value = '';
  document.getElementById('prod-unit-cost').textContent = '$0.00';
  document.getElementById('prod-packages').value = '';
  document.getElementById('prod-stock-calc').textContent = '0 und';
  document.getElementById('prod-is-bulk').checked = false;


  if (productId) {
    const p = products.find(p => p.id === productId);
    if (!p) return;
    document.getElementById('modal-prod-title').innerHTML = '<i class="fa-solid fa-pen" style="color:var(--violet-light);"></i> Editar Producto';
    document.getElementById('prod-id').value      = p.id;
    document.getElementById('prod-name').value    = p.name;
    document.getElementById('prod-code').value    = p.code || '';
    document.getElementById('prod-cat').value     = p.category_id || '';
    document.getElementById('prod-price').value   = p.price_usd;
    document.getElementById('prod-cost').value    = p.cost_usd;
    document.getElementById('prod-stock').value   = p.stock;
    document.getElementById('prod-minstock').value = p.min_stock;
    document.getElementById('prod-active').value  = p.active;
    document.getElementById('prod-active-row').style.display = 'flex';
    document.getElementById('prod-is-bulk').checked = p.is_bulk == 1;
    // Unit conversion fields
    document.getElementById('prod-uxp').value = p.unidades_por_paquete || 1;
    if (p.precio_unidad > 0) document.getElementById('prod-unit-price').value = p.precio_unidad;
    if (p.margen_unidad_porcentaje > 0) document.getElementById('prod-unit-margin').value = p.margen_unidad_porcentaje;
    // Calculate packages from stock
    const uxp = p.unidades_por_paquete || 1;
    const packages = uxp > 0 ? Math.floor(p.stock / uxp) : 0;
    document.getElementById('prod-packages').value = packages > 0 ? packages : '';
    document.getElementById('prod-stock-calc').textContent = `${p.stock} und`;
    updateUnitFields();
    updateProfitInfo();
    updatePriceBsPreview();
    clearProductDraft();
  } else {
    document.getElementById('modal-prod-title').innerHTML = '<i class="fa-solid fa-plus" style="color:var(--violet-light);"></i> Nuevo Producto';
    document.getElementById('prod-cat').value = '';
    // Restaurar borrador si existe
    const draft = loadProductDraft();
    if (draft && !draft.isEdit) {
      restoreProductDraft(draft);
      showToast('Borrador restaurado', 'info', 2000);
    }
  }
  toggleBulkFields();
  openModal('product-modal');
}

let _updatingProfit = false;

function updateProfitInfo() {
  if (_updatingProfit) return;
  _updatingProfit = true;

  const cost  = parseFloat(document.getElementById('prod-cost').value) || 0;
  const price = parseFloat(document.getElementById('prod-price').value) || 0;

  if (cost > 0 && price > 0) {
    const pct = ((price - cost) / cost) * 100;
    document.getElementById('prod-profit-pct').value = pct.toFixed(1);
  }

  updateProfitEstimate();
  _updatingProfit = false;
}

function updatePriceFromProfitPct() {
  if (_updatingProfit) return;
  _updatingProfit = true;

  const cost = parseFloat(document.getElementById('prod-cost').value) || 0;
  const pct  = parseFloat(document.getElementById('prod-profit-pct').value) || 0;

  if (cost > 0 && pct > 0) {
    const price = cost * (1 + pct / 100);
    document.getElementById('prod-price').value = price.toFixed(2);
    // Heredar % de ganancia al campo de unidad
    document.getElementById('prod-unit-margin').value = pct.toFixed(1);
    // Actualizar precio unitario si hay unidades por paquete
    const uxp = parseInt(document.getElementById('prod-uxp').value) || 1;
    if (uxp > 1) {
      const unitCost = cost / uxp;
      const unitPrice = unitCost * (1 + pct / 100);
      document.getElementById('prod-unit-price').value = unitPrice.toFixed(2);
    }
  }

  updatePriceBsPreview();
  updateProfitEstimate();
  _updatingProfit = false;
}

function updateProfitEstimate() {
  const cost  = parseFloat(document.getElementById('prod-cost').value) || 0;
  const price = parseFloat(document.getElementById('prod-price').value) || 0;
  const el    = document.getElementById('profit-estimate');
  if (cost > 0 && price > 0) {
    const gain = price - cost;
    const pct  = ((gain / cost) * 100).toFixed(1);
    el.textContent = `${fmtUSD(gain)} / ${pct}%`;
    el.style.color = gain >= 0 ? 'var(--emerald-light)' : 'var(--rose)';
  } else {
    el.textContent = '$0.00';
    el.style.color = 'var(--emerald-light)';
  }
}

function updatePriceBsPreview() {
  const usd  = parseFloat(document.getElementById('prod-price').value) || 0;
  const rate = window.currentRate || 0;
  const bs   = usd * rate;
  const el   = document.getElementById('bs-preview');
  if (usd > 0 && rate > 0) {
    el.innerHTML = `<i class="fa-solid fa-bolivar-sign"></i> Precio en Bs: <strong>${fmtBS(bs)}</strong> (tasa BCV: ${rate.toLocaleString('es-VE',{minimumFractionDigits:2})} Bs/$)`;
  } else if (usd > 0) {
    el.textContent = 'Tasa BCV cargando...';
  } else {
    el.textContent = '';
  }
}

/* ── Unit Conversion Fields ──────────────────────────── */
let _updatingUnit = false;

function updateUnitFields() {
  if (_updatingUnit) return;
  _updatingUnit = true;

  const cost    = parseFloat(document.getElementById('prod-cost').value) || 0;
  const uxp     = parseInt(document.getElementById('prod-uxp').value) || 1;
  const unitCost = uxp > 0 ? cost / uxp : 0;
  document.getElementById('prod-unit-cost').textContent = fmtUSD(unitCost);

  // Si hay un % de ganancia configurado, calcular precio unitario automáticamente
  const pct = parseFloat(document.getElementById('prod-profit-pct').value) || 0;
  if (unitCost > 0 && pct > 0 && uxp > 1) {
    const unitPrice = unitCost * (1 + pct / 100);
    document.getElementById('prod-unit-price').value = unitPrice.toFixed(2);
    document.getElementById('prod-unit-margin').value = pct.toFixed(1);
  } else {
    // Solo recalcular margen si ya hay precio unitario
    const unitPrice = parseFloat(document.getElementById('prod-unit-price').value) || 0;
    if (unitPrice > 0 && unitCost > 0) {
      const margin = ((unitPrice - unitCost) / unitCost) * 100;
      document.getElementById('prod-unit-margin').value = margin.toFixed(1);
    }
  }

  _updatingUnit = false;
  updateStockFromPackages();
}

function updateUnitFieldsFromPrice() {
  if (_updatingUnit) return;
  _updatingUnit = true;

  const cost     = parseFloat(document.getElementById('prod-cost').value) || 0;
  const uxp      = parseInt(document.getElementById('prod-uxp').value) || 1;
  const unitCost = uxp > 0 ? cost / uxp : 0;
  document.getElementById('prod-unit-cost').textContent = fmtUSD(unitCost);

  const unitPrice = parseFloat(document.getElementById('prod-unit-price').value) || 0;
  if (unitPrice > 0 && unitCost > 0) {
    const margin = ((unitPrice - unitCost) / unitCost) * 100;
    document.getElementById('prod-unit-margin').value = margin.toFixed(1);
  } else if (unitPrice > 0) {
    document.getElementById('prod-unit-margin').value = '0.0';
  }

  _updatingUnit = false;
}

function updateUnitFieldsFromMargin() {
  if (_updatingUnit) return;
  _updatingUnit = true;

  const cost     = parseFloat(document.getElementById('prod-cost').value) || 0;
  const uxp      = parseInt(document.getElementById('prod-uxp').value) || 1;
  const unitCost = uxp > 0 ? cost / uxp : 0;
  document.getElementById('prod-unit-cost').textContent = fmtUSD(unitCost);

  const margin = parseFloat(document.getElementById('prod-unit-margin').value) || 0;
  if (margin >= 0 && unitCost > 0) {
    const price = unitCost * (1 + margin / 100);
    document.getElementById('prod-unit-price').value = price.toFixed(2);
  }

  _updatingUnit = false;
}

function updateStockFromPackages() {
  const packages = parseInt(document.getElementById('prod-packages').value) || 0;
  const uxp = parseInt(document.getElementById('prod-uxp').value) || 1;
  const totalStock = packages * uxp;
  document.getElementById('prod-stock').value = totalStock;
  document.getElementById('prod-stock-calc').textContent = `${totalStock} und`;
}

async function saveProduct() {
  const id       = document.getElementById('prod-id').value;
  const name     = document.getElementById('prod-name').value.trim();
  const code     = document.getElementById('prod-code').value.trim();
  const catId    = document.getElementById('prod-cat').value;
  const priceUsd = parseFloat(document.getElementById('prod-price').value) || 0;
  const costUsd  = parseFloat(document.getElementById('prod-cost').value) || 0;
  const stock    = parseInt(document.getElementById('prod-stock').value) || 0;
  const minStock = parseInt(document.getElementById('prod-minstock').value) || 5;
  const active   = parseInt(document.getElementById('prod-active').value);
  const uxp      = parseInt(document.getElementById('prod-uxp').value) || 1;
  const unitPrice = parseFloat(document.getElementById('prod-unit-price').value) || 0;
  const unitMargin = parseFloat(document.getElementById('prod-unit-margin').value) || 0;
  const isBulk = document.getElementById('prod-is-bulk').checked ? 1 : 0;
  // Derivar precio_paquete desde precio_unidad * uxp
  let paqPrice = null;
  if (unitPrice > 0 && uxp > 1) {
    paqPrice = parseFloat((unitPrice * uxp).toFixed(2));
  }

  if (!name)       { showToast('El nombre es requerido', 'warning'); return; }
  if (priceUsd<=0) { showToast('El precio debe ser mayor a 0', 'warning'); return; }

  const body = {
    name, code: code || null, category_id: catId || null,
    price_usd: priceUsd, cost_usd: costUsd,
    stock, min_stock: minStock, active,
    unidades_por_paquete: uxp,
    precio_unidad: unitPrice > 0 ? unitPrice : null,
    precio_paquete: paqPrice,
    margen_unidad_porcentaje: unitMargin > 0 ? unitMargin : null,
    is_bulk: isBulk
  };

  try {
    if (id) {
      await api.put(`/products/${id}`, body);
      showToast('Producto actualizado', 'success');
    } else {
      await api.post('/products', body);
      showToast('Producto creado', 'success');
    }
    clearProductDraft();
    closeModal('product-modal');
    products = await api.get('/products?active=all');
    renderProducts(products);
    updateStats(products);
  } catch (e) { showToast(e.message, 'error'); }
}

async function deleteProduct(id, name) {
  confirmAction(`¿Eliminar permanentemente "${name}"? Esta acción no se puede deshacer y fallará si el producto tiene ventas asociadas.`, async () => {
    try {
      await api.delete(`/products/${id}`);
      clearProductDraft();
      showToast('Producto eliminado permanentemente', 'success');
      products = await api.get('/products?active=all');
      renderProducts(products);
      updateStats(products);
    } catch (e) { showToast(e.message, 'error'); }
  });
}

/* ── Stock Adjustment Modal ─────────────────────────── */
let _stockProductId = null;
let _stockProductUxp = 1;
let _stockMode = 'add';

function openStockModal(productId) {
  const p = products.find(p => p.id === productId);
  if (!p) return;

  _stockProductId = productId;
  _stockProductUxp = p.unidades_por_paquete || 1;
  _stockMode = 'add';

  document.getElementById('stock-prod-id').value = productId;
  document.getElementById('stock-modal-title').textContent = 'Ajustar Stock — ' + p.name;
  document.getElementById('stock-prod-name').textContent = p.name;
  document.getElementById('stock-current').textContent = p.stock;
  document.getElementById('stock-packages').value = '';
  document.getElementById('stock-units').value = '';
  document.getElementById('stock-real').value = '';
  document.getElementById('stock-packages-info').textContent =
    `1 paquete = ${_stockProductUxp} unidades`;
  document.getElementById('stock-diff-display').style.display = 'none';

  // Costo de Reposición fields
  const costInput = document.getElementById('stock-new-cost');
  const priceInput = document.getElementById('stock-new-price');
  
  const currentCost = p.cost_usd || 0;
  const currentPrice = p.price_usd || 0;
  const currentMargin = currentCost > 0 ? (currentPrice - currentCost) / currentCost : 0;

  if (costInput) {
    costInput.value = currentCost.toFixed(2);
    // Auto-recalculate price based on old margin if cost changes
    costInput.oninput = () => {
      if (priceInput) {
        const newCost = parseFloat(costInput.value) || 0;
        const autoPrice = newCost * (1 + currentMargin);
        priceInput.value = autoPrice.toFixed(2);
      }
    };
  }
  if (priceInput) priceInput.value = currentPrice.toFixed(2);

  switchStockTab('add');
  updateStockNewTotal();
  openModal('stock-modal');
}

function switchStockTab(tab) {
  _stockMode = tab;
  const tabAdd = document.getElementById('tab-add');
  const tabAudit = document.getElementById('tab-audit');
  const contentAdd = document.getElementById('stock-tab-add');
  const contentAudit = document.getElementById('stock-tab-audit');
  const saveBtn = document.getElementById('stock-save-btn');

  if (tab === 'add') {
    tabAdd.classList.add('active');
    tabAdd.style.background = 'var(--bg-input)';
    tabAdd.style.color = 'var(--text-primary)';
    tabAudit.classList.remove('active');
    tabAudit.style.background = 'var(--bg-secondary)';
    tabAudit.style.color = 'var(--text-muted)';
    contentAdd.style.display = 'block';
    contentAudit.style.display = 'none';
    saveBtn.innerHTML = '<i class="fa-solid fa-check"></i> Aplicar';
  } else {
    tabAudit.classList.add('active');
    tabAudit.style.background = 'var(--bg-input)';
    tabAudit.style.color = 'var(--text-primary)';
    tabAdd.classList.remove('active');
    tabAdd.style.background = 'var(--bg-secondary)';
    tabAdd.style.color = 'var(--text-muted)';
    contentAdd.style.display = 'none';
    contentAudit.style.display = 'block';
    saveBtn.innerHTML = '<i class="fa-solid fa-clipboard-check"></i> Registrar Auditoría';
  }

  document.getElementById('stock-packages').value = '';
  document.getElementById('stock-units').value = '';
  document.getElementById('stock-real').value = '';
  document.getElementById('stock-diff-display').style.display = 'none';
  updateStockNewTotal();
}

function stockPackagesChanged() {
  const pkgs = parseInt(document.getElementById('stock-packages').value) || 0;
  if (pkgs > 0) document.getElementById('stock-units').value = '';
  updateStockNewTotal();
}

function stockUnitsChanged() {
  const units = parseInt(document.getElementById('stock-units').value) || 0;
  if (units > 0) document.getElementById('stock-packages').value = '';
  updateStockNewTotal();
}

function stockAuditChanged() {
  const current = parseInt(document.getElementById('stock-current').textContent) || 0;
  const real = parseFloat(document.getElementById('stock-real').value) || 0;
  const diff = real - current;
  const diffEl = document.getElementById('stock-diff-display');
  const totalEl = document.getElementById('stock-new-total');

  totalEl.textContent = real;

  if (real > 0) {
    diffEl.style.display = 'block';
    if (diff > 0) {
      diffEl.innerHTML = `<span style="color:var(--emerald-light);"><i class="fa-solid fa-arrow-up"></i> +${diff} unidades (sobrante)</span>`;
    } else if (diff < 0) {
      diffEl.innerHTML = `<span style="color:var(--rose);"><i class="fa-solid fa-arrow-down"></i> ${diff} unidades (merma)</span>`;
    } else {
      diffEl.innerHTML = `<span style="color:var(--text-muted);"><i class="fa-solid fa-check-circle"></i> Stock exacto</span>`;
    }
  } else {
    diffEl.style.display = 'none';
  }
}

function updateStockNewTotal() {
  const current = parseInt(document.getElementById('stock-current').textContent) || 0;
  if (_stockMode === 'audit') return;
  const pkgs = parseInt(document.getElementById('stock-packages').value) || 0;
  const units = parseInt(document.getElementById('stock-units').value) || 0;
  const add = (pkgs * _stockProductUxp) + units;
  document.getElementById('stock-new-total').textContent = current + add;
}

async function saveStockAdjustment() {
  try {
    if (_stockMode === 'audit') {
      const realStock = parseFloat(document.getElementById('stock-real').value);
      if (isNaN(realStock) || realStock < 0) {
        showToast('Ingresa el stock real de la balanza', 'warning');
        return;
      }
      await api.patch(`/products/${_stockProductId}/stock`, {
        mode: 'audit',
        real_stock: realStock
      });
      showToast('Auditoría registrada. Stock ajustado.', 'success');
    } else {
      const pkgs = parseInt(document.getElementById('stock-packages').value) || 0;
      const units = parseInt(document.getElementById('stock-units').value) || 0;
      if (pkgs === 0 && units === 0) {
        showToast('Ingresa paquetes o unidades', 'warning');
        return;
      }
      
      const payload = { mode: 'add' };
      
      // Adjuntar costo y precio de reposición si se modificaron o están presentes
      const newCost = document.getElementById('stock-new-cost').value;
      const newPrice = document.getElementById('stock-new-price').value;
      if (newCost !== '') payload.cost_usd = parseFloat(newCost);
      if (newPrice !== '') payload.price_usd = parseFloat(newPrice);

      if (pkgs > 0) {
        payload.packages = pkgs;
        await api.patch(`/products/${_stockProductId}/stock`, payload);
      } else {
        const current = parseInt(document.getElementById('stock-current').textContent) || 0;
        payload.stock = current + units;
        await api.patch(`/products/${_stockProductId}/stock`, payload);
      }
      showToast('Stock actualizado', 'success');
    }
    closeModal('stock-modal');
    products = await api.get('/products?active=all');
    renderProducts(products);
    updateStats(products);
  } catch (e) { showToast(e.message, 'error'); }
}

/* ── Categories Modal ──────────────────────────────────── */
function openCatModal() {
  renderCatList();
  openModal('cat-modal');
}

function renderCatList() {
  const list = document.getElementById('cat-list');
  list.innerHTML = categories.map(c => `
    <div style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem;border-bottom:1px solid var(--border);">
      <span style="width:14px;height:14px;border-radius:50%;background:${c.color};flex-shrink:0;"></span>
      <span style="flex:1;font-size:0.83rem;">${c.name}</span>
      <button class="btn btn-sm btn-danger" style="padding:0.15rem 0.4rem;font-size:0.7rem;" onclick="deleteCategory(${c.id})" title="Eliminar categoría">
        <i class="fa-solid fa-trash"></i>
      </button>
    </div>`).join('');
}

async function deleteCategory(id) {
  if (!confirm('¿Está seguro de que desea eliminar esta categoría? Los productos asociados quedarán sin categoría.')) return;
  try {
    await api.delete(`/products/categories/${id}`);
    showToast('Categoría eliminada', 'success');
    categories = await api.get('/products/categories/all');
    renderCatList();
    populateCatFilter();
    populateCatSelect();
    
    // Si queremos actualizar los productos en la vista para reflejar que no tienen categoría, podemos recargar
    products = await api.get('/products?active=all');
    renderProducts(products);
    updateStats(products);
  } catch (e) { 
    showToast(e.message, 'error'); 
  }
}


async function createCategory() {
  const name  = document.getElementById('new-cat-name').value.trim();
  const color = document.getElementById('new-cat-color').value;
  if (!name) { showToast('Nombre requerido', 'warning'); return; }
  try {
    await api.post('/products/categories/all', { name, color });
    showToast('Categoría creada', 'success');
    categories = await api.get('/products/categories/all');
    renderCatList();
    populateCatFilter();
    populateCatSelect();
    document.getElementById('new-cat-name').value = '';
  } catch (e) { showToast(e.message, 'error'); }
}

/* ── Barcode Scanner Logic (1D Native Preferred) ───── */
let qrScanner = null;
let nativeDetector = null;
let lastScannedCode = null;
let lastScanTime = 0;

// Verificar soporte para BarcodeDetector API (ML Kit nativo)
if ('BarcodeDetector' in window) {
  BarcodeDetector.getSupportedFormats().then(formats => {
    const needed = ['ean_13', 'upc_a', 'code_128', 'code_39', 'ean_8'];
    const supported = needed.filter(f => formats.includes(f));
    if (supported.length > 0) {
      nativeDetector = new BarcodeDetector({ formats: supported });
      console.log("BarcodeDetector API listo con formatos:", supported);
    }
  });
}

function openQrScanner() {
  const modal = document.getElementById('qr-modal');
  modal.classList.remove('hidden');
  
  const videoElem = document.getElementById('qr-video');
  
  // Limpiar escáner previo si existe
  if (qrScanner) {
    qrScanner.destroy();
    qrScanner = null;
  }

  // Configuración de cámara HD (720p)
  const constraints = {
    facingMode: 'environment',
    width: { ideal: 1280 },
    height: { ideal: 720 }
  };

  // Si tenemos soporte nativo (ML Kit), lo usamos como motor UNICO para evitar lag
  if (nativeDetector) {
    console.log("Iniciando escáner con motor NATIVO (Alto Rendimiento)...");
    
    navigator.mediaDevices.getUserMedia({ video: constraints }).then(stream => {
        videoElem.srcObject = stream;
        videoElem.setAttribute("playsinline", true); // iOS
        videoElem.play();
        
        startNativeScanning(videoElem);
    }).catch(err => {
        handleCameraError(err);
    });
  } 
  // Si no hay nativo, usamos Nimiq como respaldo
  else {
    console.log("Iniciando escáner con motor NIMIQ (Fallback)...");
    qrScanner = new QrScanner(
      videoElem,
      result => onScanSuccess(result.data),
      {
        highlightScanRegion: true,
        highlightCodeOutline: true,
        preferredCamera: 'environment',
        maxScansPerSecond: 10,
        calculateScanRegion: (video) => {
            const width = video.videoWidth;
            const height = video.videoHeight;
            return {
                x: (width - (width * 0.85)) / 2,
                y: (height - 180) / 2,
                width: width * 0.85,
                height: 180
            };
        }
      }
    );
    qrScanner.start().catch(err => handleCameraError(err));
  }
}

function handleCameraError(err) {
    console.error("Error al iniciar cámara:", err);
    if (String(err).includes('secure context') || (location.protocol !== 'https:' && location.hostname !== 'localhost')) {
        showToast("Error: El escáner requiere una conexión segura (HTTPS)", "error");
    } else {
        showToast("No se pudo acceder a la cámara en HD", "error");
    }
    closeQrScanner();
}

let nativeScanInterval = null;
function startNativeScanning(video) {
    if (nativeScanInterval) clearInterval(nativeScanInterval);
    nativeScanInterval = setInterval(async () => {
        if (!video.videoWidth) return; // Asegurar que el video ya cargó
        try {
            const barcodes = await nativeDetector.detect(video);
            if (barcodes.length > 0) {
                onScanSuccess(barcodes[0].rawValue);
            }
        } catch (e) {
            // Error silencioso en frames vacíos
        }
    }, 100); // 10 FPS
}

function stopScanner() {
    if (nativeScanInterval) {
        clearInterval(nativeScanInterval);
        nativeScanInterval = null;
    }
    if (qrScanner) {
        qrScanner.stop();
        qrScanner.destroy();
        qrScanner = null;
    }
    const videoElem = document.getElementById('qr-video');
    if (videoElem && videoElem.srcObject) {
        videoElem.srcObject.getTracks().forEach(track => track.stop());
        videoElem.srcObject = null;
    }
}

async function closeQrScanner() {
  await stopScanner();
  document.getElementById('qr-modal').classList.add('hidden');
}

function onScanSuccess(decodedText) {
  const code = (decodedText || '').trim();
  const now  = Date.now();
  // Evitar escaneos duplicados en menos de 2 segundos para el mismo código
  if (code === lastScannedCode && (now - lastScanTime) < 2000) {
    return;
  }

  lastScannedCode = code;
  lastScanTime = now;

  console.log("QR/Barcode Detectado (Registro):", code);
  
  // Efecto visual de flash
  const flash = document.getElementById('scanner-flash');
  if (flash) {
    flash.classList.add('active');
    setTimeout(() => flash.classList.remove('active'), 300);
  }

  showToast(`Código escaneado: ${code}`, 'success');

  // Cerrar el escáner y abrir registro
  closeQrScanner().then(() => {
    // Abrir modal de nuevo producto
    openProductModal();
    
    // Asignar el código al input
    const codeInput = document.getElementById('prod-code');
    if (codeInput) {
      codeInput.value = code;
    }
    
    // Auto-enfocar el campo del nombre del producto
    setTimeout(() => {
      const nameInput = document.getElementById('prod-name');
      if (nameInput) {
        nameInput.focus();
      }
    }, 300); // Retraso para asegurar que el modal ya es visible
  });
}
/* ── Combo Functionality ────────────────────────────────────── */
let comboIngredients = [];
let comboCategoryFilter = null;

function openComboModal() {
  document.getElementById('combo-name').value = '';
  document.getElementById('combo-code').value = '';
  document.getElementById('combo-price').value = '';
  document.getElementById('combo-search-input').value = '';
  comboCategoryFilter = null;

  // Load categories
  const catSelect = document.getElementById('combo-category');
  catSelect.innerHTML = '<option value="">Sin Categoría</option>';
  categories.forEach(c => {
    catSelect.innerHTML += `<option value="${c.id}">${c.name}</option>`;
  });

  comboIngredients = [];
  renderComboIngredients();
  renderComboCategoryChips();
  renderComboProductGrid();

  document.getElementById('combo-modal').classList.remove('hidden');
}

function renderComboCategoryChips() {
  const container = document.getElementById('combo-category-chips');
  const activeProducts = products.filter(p => !p.is_combo && p.active);
  const catsWithProducts = categories.filter(c => activeProducts.some(p => p.category_id === c.id));

  let html = `<button class="combo-chip ${comboCategoryFilter === null ? 'active' : ''}" onclick="setComboCategoryFilter(null)">Todos</button>`;
  catsWithProducts.forEach(c => {
    const count = activeProducts.filter(p => p.category_id === c.id).length;
    html += `<button class="combo-chip ${comboCategoryFilter === c.id ? 'active' : ''}" onclick="setComboCategoryFilter(${c.id})">${c.name} <span class="combo-chip-count">${count}</span></button>`;
  });
  container.innerHTML = html;
}

function setComboCategoryFilter(catId) {
  comboCategoryFilter = catId;
  renderComboCategoryChips();
  filterComboProducts();
}

function filterComboProducts() {
  const term = (document.getElementById('combo-search-input').value || '').toLowerCase().trim();
  const activeProducts = products.filter(p => !p.is_combo && p.active);

  let filtered = activeProducts;
  if (comboCategoryFilter !== null) {
    filtered = filtered.filter(p => p.category_id === comboCategoryFilter);
  }
  if (term) {
    filtered = filtered.filter(p =>
      p.name.toLowerCase().includes(term) || (p.code && p.code.toLowerCase().includes(term))
    );
  }

  document.getElementById('combo-product-count').textContent = `${filtered.length} de ${activeProducts.length} productos`;
  renderComboProductGrid(filtered);
}

function renderComboProductGrid(list) {
  const grid = document.getElementById('combo-product-grid');
  const items = list || products.filter(p => !p.is_combo && p.active);
  const total = products.filter(p => !p.is_combo && p.active).length;
  document.getElementById('combo-product-count').textContent = `${items.length} de ${total} productos`;

  if (items.length === 0) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:2rem;color:var(--text-muted);">No se encontraron productos</div>';
    return;
  }

  grid.innerHTML = items.map(p => {
    const inCombo = comboIngredients.find(i => i.product_id === p.id);
    const qty = inCombo ? inCombo.quantity : 0;
    const cat = categories.find(c => c.id === p.category_id);
    const catColor = cat ? cat.color : 'var(--violet)';
    return `
    <div class="combo-product-card ${inCombo ? 'selected' : ''}" onclick="addComboIngredient(${p.id}, '${p.name.replace(/'/g, "\\'")}', ${p.cost_usd || 0})">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.25rem;">
        <span style="font-size:0.78rem;font-weight:600;color:var(--text-primary);line-height:1.2;flex:1;">${p.name}</span>
        ${qty > 0 ? `<span class="combo-qty-badge">${qty}</span>` : ''}
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:0.68rem;color:${catColor};">${cat ? cat.name : 'Sin cat.'}</span>
        <span style="font-size:0.75rem;font-weight:600;color:var(--emerald-light);font-family:'JetBrains Mono',monospace;">$${parseFloat(p.cost_usd || 0).toFixed(2)}</span>
      </div>
      ${p.code ? `<div style="font-size:0.65rem;color:var(--text-muted);margin-top:0.15rem;">${p.code}</div>` : ''}
    </div>`;
  }).join('');
}

function addComboIngredient(id, name, cost) {
  const existing = comboIngredients.find(i => i.product_id === id);
  if (existing) {
    existing.quantity += 1;
  } else {
    comboIngredients.push({ product_id: id, name, cost, quantity: 1 });
  }
  renderComboIngredients();
  filterComboProducts();
}

function removeComboIngredient(id) {
  comboIngredients = comboIngredients.filter(i => i.product_id !== id);
  renderComboIngredients();
  filterComboProducts();
}

function updateIngredientQty(id, qty) {
  const item = comboIngredients.find(i => i.product_id === id);
  if (item) {
    item.quantity = parseFloat(qty) || 1;
    if (item.quantity <= 0) removeComboIngredient(id);
    renderComboIngredients();
    filterComboProducts();
  }
}

function renderComboIngredients() {
  const tbody = document.getElementById('combo-items-tbody');
  const emptyRow = document.getElementById('combo-empty-row');
  
  if (comboIngredients.length === 0) {
    tbody.innerHTML = '<tr id="combo-empty-row"><td colspan="4" style="text-align:center;color:var(--text-muted);">No hay ingredientes seleccionados</td></tr>';
  } else {
    tbody.innerHTML = comboIngredients.map(ing => `
      <tr>
        <td>${ing.name}</td>
        <td><input type="number" class="form-control form-control-sm" style="width:70px;" value="${ing.quantity}" onchange="updateIngredientQty(${ing.product_id}, this.value)" min="0.1" step="0.1"></td>
        <td>$${(ing.cost * ing.quantity).toFixed(2)}</td>
        <td><button class="btn-icon" style="color:var(--red);" onclick="removeComboIngredient(${ing.product_id})"><i class="fa-solid fa-trash"></i></button></td>
      </tr>
    `).join('');
  }
  
  updateComboCost();
}

function updateComboCost() {
  const totalCost = comboIngredients.reduce((sum, ing) => sum + (ing.cost * ing.quantity), 0);
  document.getElementById('combo-total-cost').innerText = `$${totalCost.toFixed(2)}`;
}

async function saveCombo() {
  const name = document.getElementById('combo-name').value.trim();
  const code = document.getElementById('combo-code').value.trim();
  const category_id = document.getElementById('combo-category').value;
  const price = parseFloat(document.getElementById('combo-price').value) || 0;
  
  if (!name) return showToast('El nombre del combo es requerido', 'error');
  if (price <= 0) return showToast('El precio debe ser mayor a 0', 'error');
  if (comboIngredients.length === 0) return showToast('Debes añadir al menos 1 producto al combo', 'error');
  
  const totalCost = comboIngredients.reduce((sum, ing) => sum + (ing.cost * ing.quantity), 0);
  
  const payload = {
    name: name,
    code: code || null,
    category_id: category_id || null,
    price_usd: price,
    cost_usd: totalCost,
    stock: 0,
    min_stock: 0,
    is_combo: 1,
    ingredients: comboIngredients
  };
  
  try {
    const btn = document.querySelector('#combo-modal .btn-primary');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Guardando...';
    
    await api.post('/products', payload);
    showToast('Combo creado exitosamente', 'success');
    closeModal('combo-modal');
    products = await api.get('/products?active=all');
    renderProducts(products);
    updateStats(products);
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    const btn = document.querySelector('#combo-modal .btn-primary');
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-save"></i> Guardar Combo';
  }
}

function toggleBulkFields() {
  const isBulk = document.getElementById('prod-is-bulk').checked;
  const pkgFields = document.getElementById('pkg-fields');
  const bulkCalculator = document.getElementById('bulk-calculator');
  const lblRegistroTipo = document.getElementById('lbl-registro-tipo');
  const lblPriceUsd = document.getElementById('lbl-price-usd');
  const lblCostUsd = document.getElementById('lbl-cost-usd');
  const lblStockActual = document.getElementById('lbl-stock-actual');
  const lblMinStock = document.getElementById('lbl-min-stock');
  const rowCodeCat = document.getElementById('row-code-cat');

  if (isBulk) {
    if(pkgFields) pkgFields.style.display = 'none';
    if(bulkCalculator) bulkCalculator.style.display = 'block';
    if(rowCodeCat) rowCodeCat.style.display = 'none';
    if(lblRegistroTipo) {
      lblRegistroTipo.textContent = 'REGISTRO POR KILOS/MONTOS';
      lblRegistroTipo.style.color = 'var(--amber)';
    }
    if(lblPriceUsd) lblPriceUsd.textContent = 'Precio por Kg (USD) *';
    if(lblCostUsd) lblCostUsd.textContent = 'Costo por Kg (USD)';
    if(lblStockActual) lblStockActual.textContent = 'Inventario Inicial (Kg)';
    if(lblMinStock) lblMinStock.textContent = 'Stock Mínimo (Kg)';
  } else {
    if(pkgFields) pkgFields.style.display = 'block';
    if(bulkCalculator) bulkCalculator.style.display = 'none';
    if(rowCodeCat) rowCodeCat.style.display = 'grid'; // .form-row is CSS grid
    if(lblRegistroTipo) {
      lblRegistroTipo.textContent = 'REGISTRO POR PAQUETES';
      lblRegistroTipo.style.color = 'var(--text-muted)';
    }
    if(lblPriceUsd) lblPriceUsd.textContent = 'Precio de Venta (USD) *';
    if(lblCostUsd) lblCostUsd.textContent = 'Precio de Costo (USD)';
    if(lblStockActual) lblStockActual.textContent = 'Stock Actual (unidades sueltas)';
    if(lblMinStock) lblMinStock.textContent = 'Stock Mínimo (alerta)';
  }
}

function calculateBulkCost() {
  const totalCost = parseFloat(document.getElementById('bulk-total-cost').value) || 0;
  const realWeight = parseFloat(document.getElementById('bulk-real-weight').value) || 0;
  const calculatedCostEl = document.getElementById('bulk-calculated-cost');
  const prodCostInput = document.getElementById('prod-cost');
  const prodStockInput = document.getElementById('prod-stock');
  
  if (totalCost > 0 && realWeight > 0) {
    const costPerKg = (totalCost / realWeight).toFixed(2);
    calculatedCostEl.textContent = `$${costPerKg} / Kg`;
    // Autocompletar el campo principal de costo
    if (prodCostInput) {
      prodCostInput.value = costPerKg;
      // Disparar eventos para recalcular ganancia
      updatePriceFromProfitPct();
      updateProfitInfo();
    }
    // Autocompletar inventario inicial (solo si está vacío o si se está modificando la calculadora)
    if (prodStockInput) {
      prodStockInput.value = realWeight;
    }
  } else {
    calculatedCostEl.textContent = `$0.00 / Kg`;
  }
}

/* ── Auto-save draft on input changes ────────────────── */
(function() {
  const draftFields = [
    'prod-name', 'prod-code', 'prod-cat', 'prod-price', 'prod-cost',
    'prod-profit-pct', 'prod-stock', 'prod-minstock', 'prod-active',
    'prod-uxp', 'prod-unit-price', 'prod-unit-margin', 'prod-is-bulk'
  ];

  draftFields.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const event = el.type === 'checkbox' ? 'change' : 'input';
    el.addEventListener(event, () => {
      if (document.getElementById('product-modal') && !document.getElementById('product-modal').classList.contains('hidden')) {
        saveProductDraft();
      }
    });
  });
})();
