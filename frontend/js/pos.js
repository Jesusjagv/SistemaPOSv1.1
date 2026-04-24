/* ======================================================
   pos.js — POS Screen Logic
   ====================================================== */

requireAuth();
initHeader();
initClock('clock');

/* ── State ───────────────────────────────────────────── */
let cart = [];
let allProducts = [];
let categories  = [];
let selectedCustomer = null;
let payMethod = 'cash_usd';
let saleRate  = 0;

/* ── Bootstrap ─────────────────────────────────────── */
async function init() {
  try {
    // Load rate first
    const rateData = await api.get('/exchange/rate');
    saleRate = rateData.rate;
    document.getElementById('bcv-rate').textContent =
      rateData.rate.toLocaleString('es-VE', { minimumFractionDigits: 2 });
    
    const user = getUser();
    if (user && user.role === 'admin') {
      document.getElementById('iva-control-row').style.display = 'flex';
    }

    // Load categories and products in parallel
    const [cats, prods] = await Promise.all([
      api.get('/products/categories/all'),
      api.get('/products?active=1')
    ]);
    categories   = cats;
    allProducts  = prods;

    renderCategories();
    renderProducts(allProducts);
    initResizer(); // Inicializar divisor ajustable
  } catch (e) {
    showToast(e.message, 'error');
    document.getElementById('products-grid').innerHTML =
      `<div class="empty-state"><i class="fa-solid fa-triangle-exclamation"></i><p>${e.message}</p></div>`;
  }
}

init();

/* ── Category rendering ──────────────────────────────── */
function renderCategories() {
  const bar = document.getElementById('cat-bar');
  // Keep first "Todos" button
  const allBtn = bar.querySelector('[data-cat=""]');
  bar.innerHTML = '';
  bar.appendChild(allBtn);

  categories.forEach(c => {
    const btn = document.createElement('button');
    btn.className = 'cat-btn';
    btn.dataset.cat = c.id;
    btn.style.borderColor = c.color + '66';
    btn.onclick = () => filterCategory(btn, c.id);
    btn.innerHTML = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${c.color};margin-right:4px;"></span>${c.name}`;
    bar.appendChild(btn);
  });
}

/* ── Product rendering ───────────────────────────────── */
function renderProducts(products) {
  const grid = document.getElementById('products-grid');
  if (!products.length) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
      <i class="fa-solid fa-box-open"></i>
      <h3>Sin productos</h3>
      <p>No se encontraron productos con ese criterio</p>
    </div>`;
    return;
  }

  grid.innerHTML = products.map(p => {
    // Calculamos el stock disponible restando lo que ya está en el carrito
    const itemInCart = cart.find(i => i.product_id === p.id);
    const inCartQty = itemInCart ? itemInCart.qty : 0;
    const availableStock = Math.max(0, p.stock - inCartQty);

    const priceBs  = (p.price_usd * saleRate).toFixed(2);
    const stockCls = availableStock === 0 ? 'zero' : availableStock <= p.min_stock ? 'low' : '';
    const outCls   = availableStock === 0 ? 'out-of-stock' : '';
    const cat      = categories.find(c => c.id === p.category_id);
    const catColor = cat ? cat.color : '#6b7280';

    return `
    <div class="product-card ${outCls}" onclick="addToCart(${p.id})" title="${p.name}">
      <div class="product-cat-dot" style="background:${catColor};"></div>
      <div class="product-name">${p.name}</div>
      ${p.code ? `<div class="product-code">${p.code}</div>` : ''}
      <div class="product-price-usd">${p.price_usd.toFixed(2)}</div>
      <div class="product-price-bs">${parseFloat(priceBs).toLocaleString('es-VE',{minimumFractionDigits:2})}</div>
      <div class="product-stock ${stockCls}">${availableStock === 0 ? 'Sin stock' : availableStock}</div>
    </div>`;
  }).join('');
}

/* ── Search / Filter ──────────────────────────────────── */
let searchTimeout;
document.getElementById('product-search').addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => applyFilters(), 250);
});

function clearSearch() {
  document.getElementById('product-search').value = '';
  applyFilters();
}

let activeCatId = '';
function filterCategory(btn, catId) {
  document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeCatId = catId ? parseInt(catId) : '';
  applyFilters();
}

function applyFilters() {
  const term = document.getElementById('product-search').value.toLowerCase();
  const filtered = allProducts.filter(p => {
    const matchSearch = !term || p.name.toLowerCase().includes(term) || (p.code && p.code.toLowerCase().includes(term));
    const matchCat    = activeCatId === '' || p.category_id === activeCatId;
    return matchSearch && matchCat;
  });
  renderProducts(filtered);
}

/* ── Cart Logic ──────────────────────────────────────── */
function addToCart(productId) {
  const product = allProducts.find(p => p.id === productId);
  if (!product || product.stock <= 0) return;

  const existing = cart.find(i => i.product_id === productId);
  if (existing) {
    if (existing.qty >= product.stock) {
      showToast(`Stock máximo alcanzado (${product.stock})`, 'warning');
      return;
    }
    existing.qty++;
  } else {
    cart.push({
      product_id:   product.id,
      name:         product.name,
      code:         product.code,
      price_usd:    product.price_usd,
      stock:        product.stock,
      qty:          1,
      discount_pct: 0
    });
  }
  renderCart();
}

function removeFromCart(idx) {
  cart.splice(idx, 1);
  renderCart();
}

function clearCart() {
  cart = [];
  renderCart();
}

function updateQty(idx, newQty) {
  const item = cart[idx];
  const qty  = parseFloat(newQty);
  if (isNaN(qty) || qty <= 0) { removeFromCart(idx); return; }
  if (qty > item.stock) {
    showToast(`Solo hay ${item.stock} en stock`, 'warning');
    cart[idx].qty = item.stock;
  } else {
    cart[idx].qty = qty;
  }
  recalcTotals();
  applyFilters(); // Actualizar visualmente la grilla de productos
}

function updateDisc(idx, val) {
  let disc = parseFloat(val) || 0;
  disc = Math.min(100, Math.max(0, disc));
  cart[idx].discount_pct = disc;
  recalcTotals();
}

/* ── Cart rendering ──────────────────────────────────── */
function renderCart() {
  const container = document.getElementById('cart-items');
  const cartCount  = document.getElementById('cart-count');
  const clearBtn   = document.getElementById('clear-cart-btn');
  const totalsEl   = document.getElementById('cart-totals');
  const paymentEl  = document.getElementById('cart-payment');

  cartCount.textContent = cart.reduce((s, i) => s + i.qty, 0);
  clearBtn.disabled = cart.length === 0;

  if (cart.length === 0) {
    container.innerHTML = `
      <div class="cart-empty">
        <i class="fa-solid fa-cart-shopping"></i>
        <span>Carrito vacío</span>
        <span style="font-size:0.72rem;">Agrega productos haciendo clic</span>
      </div>`;
    totalsEl.style.display  = 'none';
    paymentEl.style.display = 'none';
    document.getElementById('charge-btn').disabled = true;
    return;
  }

  container.innerHTML = cart.map((item, idx) => {
    const rate       = saleRate;
    const net        = item.price_usd * (1 - item.discount_pct / 100);
    const totalUsd   = net * item.qty;
    const totalBs    = totalUsd * rate;
    return `
    <div class="cart-item">
      <div class="cart-item-name">${item.name}</div>
      <div class="cart-item-controls">
        <button class="qty-btn" onclick="changeQty(${idx},-1)"><i class="fa-solid fa-minus"></i></button>
        <input type="number" class="qty-input" value="${item.qty}" min="1" max="${item.stock}"
          onchange="updateQty(${idx}, this.value)" onblur="updateQty(${idx}, this.value)">
        <button class="qty-btn" onclick="changeQty(${idx},1)"><i class="fa-solid fa-plus"></i></button>
      </div>
      <div class="cart-item-disc">
        <i class="fa-solid fa-tag" style="color:var(--amber);font-size:0.65rem;"></i>
        Desc:
        <input type="number" class="disc-input" value="${item.discount_pct}" min="0" max="100"
          oninput="updateDisc(${idx}, this.value)">%
        &nbsp;<span style="color:var(--text-muted);">${fmtUSD(item.price_usd)}/u</span>
      </div>
      <div class="cart-item-total">
        <div>
          <div class="price-usd">$ ${totalUsd.toFixed(2)}</div>
          <div class="price-bs">Bs ${totalBs.toLocaleString('es-VE',{minimumFractionDigits:2})}</div>
        </div>
        <button class="cart-item-del" onclick="removeFromCart(${idx})" title="Eliminar Producto">
          <i class="fa-solid fa-trash-can"></i>
        </button>
      </div>
    </div>`;
  }).join('');

  totalsEl.style.display  = 'flex';
  paymentEl.style.display = 'block';
  recalcTotals();
  applyFilters(); // Refrescar stock de la grilla de productos
}

function changeQty(idx, delta) {
  const newQty = cart[idx].qty + delta;
  updateQty(idx, newQty);
  renderCart();
}

/* ── Totals calculation ───────────────────────────────── */
let totals = {};

function toggleIvaInput() {
  const isEnabled = document.getElementById('iva-toggle').checked;
  document.getElementById('iva-pct').disabled = !isEnabled;
  recalcTotals();
}

function recalcTotals() {
  const rate     = saleRate;
  const discPct  = parseFloat(document.getElementById('order-discount').value) || 0;
  
  const isIvaEnabled = document.getElementById('iva-toggle').checked;
  const ivaInputValue = parseFloat(document.getElementById('iva-pct').value) || 0;
  const taxPct = isIvaEnabled ? ivaInputValue : 0;
  const TAX_RATE = taxPct / 100;

  let subtotalUsd = cart.reduce((s, i) => {
    const net = i.price_usd * (1 - i.discount_pct / 100);
    return s + net * i.qty;
  }, 0);

  const discountUsd = subtotalUsd * (discPct / 100);
  const afterDisc   = subtotalUsd - discountUsd;
  const taxUsd      = afterDisc * TAX_RATE;
  const totalUsd    = afterDisc + taxUsd;
  const totalBs     = totalUsd * rate;

  totals = { subtotalUsd, discountUsd, taxUsd, totalUsd, totalBs, discPct };

  // Update DOM
  document.getElementById('tax-label').textContent = `IVA (${taxPct}%):`;
  document.getElementById('t-subtotal-usd').textContent  = subtotalUsd.toFixed(2);
  document.getElementById('t-subtotal-bs').textContent   = (subtotalUsd * rate).toLocaleString('es-VE',{minimumFractionDigits:2});
  document.getElementById('t-discount-usd').textContent  = `- ${fmtUSD(discountUsd)}`;
  document.getElementById('t-discount-bs').textContent   = `- ${fmtBS(discountUsd * rate)}`;
  document.getElementById('t-tax-usd').textContent       = `+ ${fmtUSD(taxUsd)}`;
  document.getElementById('t-tax-bs').textContent        = `+ ${fmtBS(taxUsd * rate)}`;
  document.getElementById('t-total-usd').textContent     = fmtUSD(totalUsd);
  document.getElementById('t-total-bs').textContent      = fmtBS(totalBs);

  calcChange();
}

/* ── Payment method ───────────────────────────────────── */
function selectPayMethod(btn) {
  document.querySelectorAll('.pay-method-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  payMethod = btn.dataset.method;

  const usdRow = document.getElementById('usd-pay-row');
  const bsRow  = document.getElementById('bs-pay-row');

  usdRow.style.display = 'flex';
  bsRow.style.display  = 'none';

  if (payMethod === 'cash_bs' || payMethod === 'pago_movil') {
    usdRow.style.display = 'none';
    bsRow.style.display  = 'flex';
  } else if (payMethod === 'mixed') {
    usdRow.style.display = 'flex';
    bsRow.style.display  = 'flex';
  }

  const refRow = document.getElementById('ref-pay-row');
  if (payMethod === 'pago_movil') {
    refRow.style.display = 'flex';
  } else {
    refRow.style.display = 'none';
  }

  // Auto-fill exact amount for card or pago movil
  if (payMethod === 'card') {
    document.getElementById('pay-usd').value = totals.totalUsd ? totals.totalUsd.toFixed(2) : '';
  } else if (payMethod === 'pago_movil') {
    document.getElementById('pay-bs').value = totals.totalBs ? totals.totalBs.toFixed(2) : '';
  }
  calcChange();
}

function calcChange() {
  if (!totals.totalUsd) return;
  const rate    = saleRate;
  const paidUsd = parseFloat(document.getElementById('pay-usd').value) || 0;
  const paidBs  = parseFloat(document.getElementById('pay-bs').value)  || 0;

  const totalPaidUsd = paidUsd + (paidBs / rate);
  const enoughPaid    = totalPaidUsd >= (totals.totalUsd - 0.001);

  document.getElementById('charge-btn').disabled = !enoughPaid || cart.length === 0;
}

/* ── Customer ─────────────────────────────────────────── */
function openCustomerSearch() {
  openModal('customer-modal');
  document.getElementById('cust-search-input').value = '';
  document.getElementById('customer-results').innerHTML = `
    <div class="empty-state"><i class="fa-solid fa-user-slash"></i><p>Escribe para buscar clientes</p></div>`;
  setTimeout(() => document.getElementById('cust-search-input').focus(), 100);
}

let custTimeout;
async function searchCustomers(term) {
  clearTimeout(custTimeout);
  custTimeout = setTimeout(async () => {
    if (!term.trim()) {
      document.getElementById('customer-results').innerHTML =
        `<div class="empty-state"><i class="fa-solid fa-user-slash"></i><p>Escribe para buscar clientes</p></div>`;
      return;
    }
    try {
      const results = await api.get(`/customers?search=${encodeURIComponent(term)}`);
      const container = document.getElementById('customer-results');
      if (!results.length) {
        container.innerHTML = `<div class="empty-state"><i class="fa-solid fa-magnifying-glass"></i><p>Sin resultados</p></div>`;
        return;
      }
      container.innerHTML = results.map(c => `
        <div onclick="selectCustomer(${JSON.stringify(c).replace(/"/g,'&quot;')})"
          style="padding:0.65rem 1rem;border-bottom:1px solid var(--border);cursor:pointer;transition:var(--transition);"
          onmouseover="this.style.background='var(--bg-card-hover)'" onmouseout="this.style.background=''">
          <div style="font-weight:600;font-size:0.85rem;">${c.name}</div>
          <div style="font-size:0.73rem;color:var(--text-muted);">${c.doc_type}-${c.doc_number || 'S/N'} &bull; ${c.phone || ''}</div>
        </div>
      `).join('');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }, 300);
}

function selectCustomer(c) {
  selectedCustomer = c;
  document.getElementById('customer-display').textContent = `${c.name} (${c.doc_type}-${c.doc_number || 'S/N'})`;
  document.getElementById('customer-display').style.color = 'var(--text-primary)';
  document.getElementById('clear-customer-btn').style.display = 'block';
  closeModal('customer-modal');
}

function clearCustomer() {
  selectedCustomer = null;
  document.getElementById('customer-display').textContent = 'Consumidor Final';
  document.getElementById('customer-display').style.color = '';
  document.getElementById('clear-customer-btn').style.display = 'none';
}

/* ── Process sale ──────────────────────────────────────── */
async function processSale() {
  if (!cart.length) return;
  const btn = document.getElementById('charge-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Procesando...';

  const paidUsd = parseFloat(document.getElementById('pay-usd').value) || 0;
  const paidBs  = parseFloat(document.getElementById('pay-bs').value)  || 0;
  const discPct = parseFloat(document.getElementById('order-discount').value) || 0;
  let   payRef  = document.getElementById('pay-ref').value.trim();

  // Guardar la referencia en "notes"
  const notesText = (payMethod === 'pago_movil' && payRef) ? `Ref PM: ${payRef}` : '';

  const saleData = {
    items: cart.map(i => ({
      product_id:      i.product_id,
      quantity:        i.qty,
      discount_percent: i.discount_pct
    })),
    exchange_rate:    saleRate,
    customer_id:      selectedCustomer?.id || null,
    customer_name:    selectedCustomer?.name || 'Consumidor Final',
    payment_method:   payMethod,
    amount_paid_usd:  paidUsd,
    amount_paid_bs:   paidBs,
    discount_percent: discPct,
    tax_percent:      document.getElementById('iva-toggle').checked ? (parseFloat(document.getElementById('iva-pct').value)||0) : 0,
    notes:            notesText
  };

  try {
    const result = await api.post('/sales', saleData);
    // Fetch full sale to show receipt
    const sale   = await api.get(`/sales/${result.sale_id}`);
    showReceipt(sale, result);
    showToast('¡Venta registrada exitosamente!', 'success');

    // Refresh stock in product list
    allProducts = await api.get('/products?active=1');
    renderProducts(allProducts);
    applyFilters();

  } catch (e) {
    showToast(e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-check-circle"></i> COBRAR';
  }
}

/* ── Receipt ────────────────────────────────────────────── */
function showReceipt(sale, result) {
  const rate    = sale.exchange_rate;
  const paidUsd = sale.amount_paid_usd;
  const paidBs  = sale.amount_paid_bs;
  const now     = new Date(sale.created_at || Date.now());

  const methodLabels = {
    cash_usd: 'Efectivo USD',
    cash_bs:  'Efectivo Bs',
    card:     'Tarjeta de Débito/Crédito',
    pago_movil: 'Pago Móvil',
    mixed:    'Pago Mixto'
  };

  const receiptHtml = `
  <div class="receipt">
    <div class="receipt-center">
      <div class="receipt-barcode">
        ${Array.from({length:30}, (_,i) => `<span style="width:${i%3===0?3:i%5===0?1:2}px;opacity:${0.5 + Math.random()*0.5}"></span>`).join('')}
      </div>
      <div class="receipt-title">🏪 SISTEMA POS v1.0</div>
      <div style="font-size:0.72rem;color:var(--text-muted);">Venezuela — RIF: J-00000000-0</div>
      <div style="font-size:0.72rem;color:var(--text-muted);">IVA: 16% — Código de Control: ${Math.random().toString(36).slice(2,8).toUpperCase()}</div>
    </div>
    <hr class="receipt-hr">
    <div class="receipt-row"><span>Factura N°:</span><strong>${sale.sale_number}</strong></div>
    <div class="receipt-row"><span>Fecha:</span><span>${now.toLocaleString('es-VE')}</span></div>
    <div class="receipt-row"><span>Cajero:</span><span>${sale.user_name || '-'}</span></div>
    <div class="receipt-row"><span>Cliente:</span><span>${sale.customer_name || 'Consumidor Final'}</span></div>
    <div class="receipt-row"><span>Tasa BCV:</span><span>1$ = ${rate.toLocaleString('es-VE',{minimumFractionDigits:2})} Bs</span></div>
    <hr class="receipt-hr">
    <div style="font-weight:600;margin-bottom:0.35rem;font-size:0.78rem;color:var(--text-muted);">ARTÍCULOS:</div>
    ${sale.items.map(i => `
    <div style="margin-bottom:0.5rem;">
      <div>${i.product_name}${i.product_code ? ` (${i.product_code})` : ''}</div>
      <div class="receipt-row" style="color:var(--text-muted);font-size:0.75rem;">
        <span>${i.quantity} x ${fmtUSD(i.price_usd)}</span>
        <div style="text-align:right;">
          <div style="color:var(--usd-color);">${fmtUSD(i.total_usd)}</div>
          <div style="color:var(--bs-color);font-size:0.68rem;">${fmtBS(i.total_bs)}</div>
        </div>
      </div>
      ${i.discount_percent > 0 ? `<div style="color:var(--amber);font-size:0.7rem;text-align:right;">Desc: ${i.discount_percent}%</div>` : ''}
    </div>`).join('')}
    <hr class="receipt-hr">
    <div class="receipt-row"><span>Sub-total:</span>
      <span>${fmtUSD(sale.subtotal_usd)} / ${fmtBS(sale.subtotal_usd * rate)}</span></div>
    ${sale.discount_usd > 0 ? `<div class="receipt-row" style="color:var(--amber);">
      <span>Descuento:</span><span>- ${fmtUSD(sale.discount_usd)} / - ${fmtBS(sale.discount_usd * rate)}</span></div>` : ''}
    <div class="receipt-row" style="color:#fb7185;">
      <span>${sale.tax_usd > 0 ? `IVA (${Math.round(sale.tax_usd / (sale.subtotal_usd - sale.discount_usd) * 100)}%)` : 'IVA (0%)'}:</span><span>${fmtUSD(sale.tax_usd)} / ${fmtBS(sale.tax_usd * rate)}</span></div>
    <hr class="receipt-hr">
    <div class="receipt-total-row" style="font-size:1rem;margin-bottom:0.15rem;">
      <span>TOTAL:</span><span style="color:var(--usd-color);">${fmtUSD(sale.total_usd)}</span>
    </div>
    <div class="receipt-total-row" style="font-size:0.85rem;color:var(--bs-color);">
      <span></span><span>${fmtBS(sale.total_bs)}</span>
    </div>
    <hr class="receipt-hr">
    <div class="receipt-row"><span>Método pago:</span><span>${methodLabels[sale.payment_method] || sale.payment_method}</span></div>
    ${sale.notes && sale.notes.includes('Ref PM') ? `<div class="receipt-row"><span style="font-size:0.75rem;">Operación:</span><span style="font-size:0.75rem;font-weight:600;">${sale.notes.replace('Ref PM: ', '')}</span></div>` : ''}
    ${paidUsd > 0 ? `<div class="receipt-row"><span>Pago $:</span><span style="color:var(--usd-color);">${fmtUSD(paidUsd)}</span></div>`:''}
    ${paidBs > 0  ? `<div class="receipt-row"><span>Pago Bs:</span><span style="color:var(--bs-color);">${fmtBS(paidBs)}</span></div>`:''}

    <hr class="receipt-hr">
    <div class="receipt-center" style="color:var(--text-muted);font-size:0.7rem;">
      <div>¡Gracias por su compra!</div>
      <div>Este documento no tiene valor fiscal</div>
      <div style="margin-top:0.5rem;font-size:0.65rem;">Sistema POS v1.0 — Venezuela</div>
    </div>
  </div>`;

  document.getElementById('receipt-content').innerHTML = receiptHtml;
  openModal('receipt-modal');
}

/* ── New sale ──────────────────────────────────────────── */
function newSale() {
  cart = [];
  selectedCustomer = null;
  payMethod = 'cash_usd';
  document.getElementById('order-discount').value = '0';
  document.getElementById('iva-toggle').checked = true;
  document.getElementById('iva-pct').value = '16';
  document.getElementById('iva-pct').disabled = false;
  document.getElementById('pay-usd').value = '';
  document.getElementById('pay-bs').value  = '';
  document.getElementById('pay-ref').value = '';
  document.querySelectorAll('.pay-method-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-method="cash_usd"]').classList.add('active');
  document.getElementById('bs-pay-row').style.display = 'none';
  document.getElementById('usd-pay-row').style.display = 'flex';
  clearCustomer();
  renderCart();
  closeModal('receipt-modal');
  // En movil, regresar a la vista de productos despues de una nueva venta
  if (window.innerWidth <= 1024) switchPosView('products');
}

/* ── POS Mobile Navigation ────────────────────────────── */
function switchPosView(view) {
  const shell = document.querySelector('.pos-shell');
  const btns = document.querySelectorAll('.pos-mobile-nav button');
  
  if (view === 'cart') {
    shell.classList.add('view-cart');
    btns[0].classList.remove('active');
    btns[1].classList.add('active');
  } else {
    shell.classList.remove('view-cart');
    btns[0].classList.add('active');
    btns[1].classList.remove('active');
  }
}

window.switchPosView = switchPosView;


/* ── Force rate update ──────────────────────────────────── */
async function forceRateUpdate() {
  try {
    showToast('Actualizando tasa BCV...', 'info', 2000);
    const data = await api.post('/exchange/rate/force', {});
    saleRate = data.rate;
    document.getElementById('bcv-rate').textContent =
      data.rate.toLocaleString('es-VE', { minimumFractionDigits: 2 });
    showToast(data.message, 'success');
    renderCart();
  } catch (e) {
    showToast(e.message, 'error');
  }
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
    }, 100); // 10 FPS (suficiente para barras sin sobrecargar CPU)
}

async function stopScanner() {
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
  const now = Date.now();
  // Evitar escaneos duplicados en menos de 2 segundos para el mismo código
  if (decodedText === lastScannedCode && (now - lastScanTime) < 2000) {
    return;
  }

  lastScannedCode = decodedText;
  lastScanTime = now;

  console.log("Nimiq QR Detectado:", decodedText);
  
  // Efecto visual de flash
  const flash = document.getElementById('scanner-flash');
  if (flash) {
    flash.classList.add('active');
    setTimeout(() => flash.classList.remove('active'), 300);
  }

  // Buscar producto por código
  const product = allProducts.find(p => p.code === decodedText);
  
  if (product) {
    addToCart(product.id);
    showToast(`Agregado: ${product.name}`, 'success');
  } else {
    showToast(`Producto no encontrado: ${decodedText}`, 'warning');
  }
}

/* ── Resizable Sidebar Logic ───────────────────────────── */
function initResizer() {
  const resizer = document.getElementById('pos-resizer');
  const body = document.querySelector('.pos-body');
  
  if (!resizer || !body) return;

  // Cargar ancho guardado
  const savedWidth = localStorage.getItem('pos_cart_width');
  if (savedWidth) {
    body.style.setProperty('--cart-width', savedWidth);
  }

  resizer.addEventListener('mousedown', (e) => {
    e.preventDefault();
    document.body.style.cursor = 'col-resize';
    resizer.classList.add('dragging');

    const onMouseMove = (e) => {
      // Calcular ancho desde la derecha de la pantalla
      let newWidth = window.innerWidth - e.clientX;
      
      // Límites de seguridad
      const minWidth = 320;
      const maxWidth = window.innerWidth * 0.7;

      if (newWidth < minWidth) newWidth = minWidth;
      if (newWidth > maxWidth) newWidth = maxWidth;

      const finalWidth = `${newWidth}px`;
      body.style.setProperty('--cart-width', finalWidth);
      localStorage.setItem('pos_cart_width', finalWidth);
    };

    const onMouseUp = () => {
      document.body.style.cursor = '';
      resizer.classList.remove('dragging');
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  });
}
