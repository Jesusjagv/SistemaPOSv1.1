/* ======================================================
   pos.js — POS Screen Logic
   ====================================================== */

requireAuth();
initHeader();
initClock('clock');

/* ── State ───────────────────────────────────────────── */
let modoVentaGlobal = 'unidad';
let cart = [];
let allProducts = [];
let categories  = [];
let selectedCustomer = null;
let payMethod = 'cash_usd';
let saleRate  = 0;
let kbdCartIndex = -1; // índice de línea del carrito seleccionada con teclado

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
      // Sync IVA toggle with settings from localStorage
      const ivaEnabled = localStorage.getItem('pos_iva_enabled');
      const ivaPct = localStorage.getItem('pos_iva_pct');
      const isEnabled = ivaEnabled === null ? true : ivaEnabled === 'true';
      const pctValue = ivaPct === null ? 16 : parseFloat(ivaPct);
      document.getElementById('iva-toggle').checked = isEnabled;
      document.getElementById('iva-pct').value = pctValue;
      document.getElementById('iva-pct').disabled = !isEnabled;
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

    // Comprobar turno de caja
    await checkCashSession();
  } catch (e) {
    showToast(e.message, 'error');
    document.getElementById('products-grid').innerHTML =
      `<div class="empty-state"><i class="fa-solid fa-triangle-exclamation"></i><p>${e.message}</p></div>`;
  }
}

init();

/* ── Check Cash Session ──────────────────────────────── */
async function checkCashSession() {
  try {
    const data = await api.get('/cash-sessions/current');
    if (data.is_open && data.session) {
      document.getElementById('pos-session-widget').style.display = 'block';
      document.getElementById('pos-no-session-warn').style.display = 'none';
      document.getElementById('pos-session-num').textContent = data.session.session_number;
      document.getElementById('pos-session-sales').textContent = `${data.session.total_sales || 0} ventas`;
    } else {
      document.getElementById('pos-session-widget').style.display = 'none';
      document.getElementById('pos-no-session-warn').style.display = 'block';
    }
  } catch (e) {
    console.warn('Error checking cash session:', e);
  }
}

/* ── Category rendering ──────────────────────────────── */
function renderCategories() {
  const bar = document.getElementById('cat-bar');
  // Keep first "Todos" button and the sale-mode toggle div
  const allBtn = bar.querySelector('[data-cat=""]');
  const modeToggle = bar.querySelector('#btn-modo-venta')?.parentElement;
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

  if (modeToggle) bar.appendChild(modeToggle);
}

/* ── Placeholder Image Generator ──────────────────────── */
function getPlaceholderImage(name) {
  const n = (name || '').toLowerCase();

  // Keyword → gradient + icon mapping
  const rules = [
    { keywords: ['agua', 'water', 'botella', 'bebida', 'soda', 'refresco', 'coca', 'pepsi', 'jugo', 'gaseosa'],
      gradient: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', icon: 'fa-solid fa-droplet' },
    { keywords: ['arroz', 'rice', 'pasta', 'fideo', 'harina', 'trigo', 'cereal'],
      gradient: 'linear-gradient(135deg, #f5af19 0%, #f12711 100%)', icon: 'fa-solid fa-wheat-awn' },
    { keywords: ['carne', 'res', 'pollo', 'cerdo', 'pavo', 'jamón', 'jamon', 'salchicha'],
      gradient: 'linear-gradient(135deg, #eb3349 0%, #f45c43 100%)', icon: 'fa-solid fa-drumstick-bite' },
    { keywords: ['queso', 'cheese', 'mantequilla', 'leche', 'yogurt', 'yogur', 'crema'],
      gradient: 'linear-gradient(135deg, #f7971e 0%, #ffd200 100%)', icon: 'fa-solid fa-cheese' },
    { keywords: ['shampoo', 'jabón', 'jabon', 'gel', 'crema', 'locion', 'desodorante', 'pasta', 'dental', 'cepillo'],
      gradient: 'linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)', icon: 'fa-solid fa-pump-soap' },
    { keywords: ['café', 'cafe', 'te', 'té', 'chocolate'],
      gradient: 'linear-gradient(135deg, #3c1053 0%, #ad5389 100%)', icon: 'fa-solid fa-mug-hot' },
    { keywords: ['pan', 'tortilla', 'galleta', 'bollo', 'pastel', 'torta', 'donut'],
      gradient: 'linear-gradient(135deg, #e6b980 0%, #eacda3 100%)', icon: 'fa-solid fa-bread-slice' },
    { keywords: ['cerveza', 'beer', 'vino', 'ron', 'whisky', 'licor', 'alcohol'],
      gradient: 'linear-gradient(135deg, #fbc2eb 0%, #a6c1ee 100%)', icon: 'fa-solid fa-beer-mug-empty' },
    { keywords: ['aceite', 'vinagre', 'sal', 'pimienta', 'especia', 'condimento', 'salsa'],
      gradient: 'linear-gradient(135deg, #d4fc79 0%, #96e6a1 100%)', icon: 'fa-solid fa-flask' },
    { keywords: ['huevo', 'uevo'],
      gradient: 'linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%)', icon: 'fa-solid fa-egg' },
    { keywords: ['fruta', 'manzana', 'platano', 'banana', 'naranja', 'limon', 'uva', 'fresa', 'sandia', 'melon', 'pera', 'coco'],
      gradient: 'linear-gradient(135deg, #a8e063 0%, #56ab2f 100%)', icon: 'fa-solid fa-apple-whole' },
    { keywords: ['verdura', 'tomate', 'cebolla', 'papa', 'lechuga', 'zanahoria', 'maiz', 'chile', 'pimenton'],
      gradient: 'linear-gradient(135deg, #11998e 0%, #38ef7d 100%)', icon: 'fa-solid fa-leaf' },
    { keywords: ['limpieza', 'detergente', 'cloro', 'escoba', 'trapeador', 'esponja', 'bolsa'],
      gradient: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', icon: 'fa-solid fa-spray-can-sparkles' },
    { keywords: ['snack', 'papa', 'dorito', 'chip', 'pochoclo', 'palomita', 'caramelo', 'chicle', 'chocolate'],
      gradient: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)', icon: 'fa-solid fa-cookie' },
    { keywords: ['congelado', 'helado', 'hielo', 'freeze'],
      gradient: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)', icon: 'fa-solid fa-snowflake' },
    { keywords: ['lacteo', 'yogur'],
      gradient: 'linear-gradient(135deg, #fddb92 0%, #d1fdff 100%)', icon: 'fa-solid fa-glass-water' },
  ];

  for (const rule of rules) {
    if (rule.keywords.some(kw => n.includes(kw))) {
      return { gradient: rule.gradient, icon: rule.icon };
    }
  }

  // Default fallback
  return { gradient: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', icon: 'fa-solid fa-box' };
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
    let consumedStock = 0;
    cart.forEach(i => {
      if (i.product_id === p.id) {
        const uxp = parseInt(i.unidades_por_paquete) || 1;
        consumedStock += i.tipo_venta === 'paquete' ? i.qty * uxp : i.qty;
      }
    });
    const availableStock = Math.max(0, p.stock - consumedStock);
    const uxp = Math.max(parseInt(p.unidades_por_paquete) || 1, 1);
    const availablePackages = Math.floor(availableStock / uxp);
    const canAdd = modoVentaGlobal === 'unidad' ? availableStock > 0 : availablePackages > 0;

    const unitPrice   = getUnitPrice(p);
    const packagePrice = uxp > 1 ? getPackagePrice(p) : unitPrice;
    const unitPriceBs = (unitPrice * saleRate).toFixed(2);
    const packPriceBs = uxp > 1 ? (packagePrice * saleRate).toFixed(2) : unitPriceBs;

    const stockCls = availableStock === 0 ? 'zero' : availableStock <= p.min_stock ? 'low' : '';
    const outCls   = !canAdd ? ' out-of-stock' : '';
    const cat      = categories.find(c => c.id === p.category_id);
    const catColor = cat ? cat.color : '#6b7280';
    const showPaq  = uxp > 1;

    const img = getPlaceholderImage(p.name);

    return `
    <button type="button" class="product-card${outCls}" style="border-left:4px solid ${catColor};" onclick="addToCart(${p.id},'${modoVentaGlobal}')" title="${p.name}" data-roving data-product-id="${p.id}" role="option" aria-label="${p.name}">
      <div class="product-card-img">
        <div class="product-image" style="background:${img.gradient};">
          <i class="${img.icon}"></i>
        </div>
        <div class="product-stock ${stockCls}">${availableStock === 0 ? 'Sin stock' : p.is_bulk ? availableStock.toFixed(2) + ' Kg' : modoVentaGlobal === 'paquete' && showPaq ? (availablePackages + ' paq') : availableStock + ' und'}</div>
        ${p.is_combo ? '<span style="position:absolute;top:0.5rem;left:0.5rem;background:var(--amber);color:#000;font-size:0.55rem;padding:0.1rem 0.35rem;border-radius:4px;font-weight:700;z-index:2;"><i class="fa-solid fa-layer-group"></i> Combo</span>' : ''}
        ${p.is_bulk ? '<span style="position:absolute;top:0.5rem;left:0.5rem;background:#D69E2E;color:#1A202C;font-size:0.55rem;padding:0.1rem 0.35rem;border-radius:0px;font-weight:700;z-index:2;"><i class="fa-solid fa-weight-hanging"></i> Granel</span>' : ''}
      </div>
      <div class="product-card-info">
        <div class="product-name">
          <span class="product-icon"><i class="${img.icon}"></i></span>
          ${p.name}
        </div>
        ${p.code ? `<div class="product-code">${p.code}</div>` : ''}
        <div class="product-prices">
          <div class="product-price-line"><span class="price-label">Und:</span><span class="product-price-usd">${unitPrice.toFixed(2)}</span><span class="product-price-sep">|</span><span class="product-price-bs">${parseFloat(unitPriceBs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span></div>
          ${showPaq ? `<div class="product-price-line"><span class="price-label">Paq:</span><span class="product-price-usd">${packagePrice.toFixed(2)}</span><span class="product-price-sep">|</span><span class="product-price-bs">${parseFloat(packPriceBs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span></div>` : ''}
        </div>
      </div>
    </button>`;
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
    const matchMode   = modoVentaGlobal !== 'paquete' || (parseInt(p.unidades_por_paquete) || 1) > 1;
    return matchSearch && matchCat && matchMode;
  });
  renderProducts(filtered);
}

/* ── Mode toggle (solo visual/filtro) ──────────────────── */
function alternarModoVentaGlobal() {
  modoVentaGlobal = modoVentaGlobal === 'unidad' ? 'paquete' : 'unidad';
  const btn = document.getElementById('btn-modo-venta');
  const txt = document.getElementById('modo-venta-text');
  if (modoVentaGlobal === 'paquete') {
    btn.classList.add('modo-paquete');
    txt.textContent = 'Por Paquete';
    btn.querySelector('i').className = 'fa-solid fa-boxes';
  } else {
    btn.classList.remove('modo-paquete');
    txt.textContent = 'Por Unidad';
    btn.querySelector('i').className = 'fa-solid fa-cube';
  }
  applyFilters();
}

function getUnitPrice(product) {
  if (product.precio_unidad > 0) return parseFloat(product.precio_unidad);
  const uxp = Math.max(parseInt(product.unidades_por_paquete) || 1, 1);
  if (uxp > 1) {
    const paqPrice = product.precio_paquete > 0 ? parseFloat(product.precio_paquete) : parseFloat(product.price_usd);
    return parseFloat((paqPrice / uxp).toFixed(4));
  }
  return parseFloat(product.price_usd);
}
function getPackagePrice(product) {
  if (product.precio_paquete > 0) return parseFloat(product.precio_paquete);
  const uxp = Math.max(parseInt(product.unidades_por_paquete) || 1, 1);
  if (uxp > 1 && product.precio_unidad > 0) return parseFloat((product.precio_unidad * uxp).toFixed(2));
  return parseFloat(product.price_usd);
}

/* ── Cart Logic ──────────────────────────────────────── */
function getConsumedStock(productId) {
  let consumed = 0;
  cart.forEach(i => {
    if (i.product_id === productId) {
      const uxp = parseInt(i.unidades_por_paquete) || 1;
      consumed += i.tipo_venta === 'paquete' ? i.qty * uxp : i.qty;
    }
  });
  return consumed;
}

/* ── Bulk Product Sale ──────────────────────────────────── */
let _bulkProduct = null;
let _bulkMode = 'qty';
let _bulkCurrency = 'usd';

function addToCart(productId, tipoVenta) {
  const product = allProducts.find(p => p.id === productId);
  if (!product || product.stock <= 0) return;

  if (product.is_bulk == 1) {
    openBulkModal(product);
    return;
  }

  const uxp = parseInt(product.unidades_por_paquete) || 1;
  const price = tipoVenta === 'paquete' ? getPackagePrice(product) : getUnitPrice(product);
  const existing = cart.find(i => i.product_id === productId && i.tipo_venta === tipoVenta);

  const addStock = tipoVenta === 'paquete' ? uxp : 1;
  const consumed = getConsumedStock(productId);

  if (consumed + addStock > product.stock) {
    showToast(`Stock máximo alcanzado (${product.stock} unidades)`, 'warning');
    return;
  }

  if (tipoVenta === 'paquete' && uxp > product.stock) {
    showToast(`Stock insuficiente: necesitas ${uxp} unidades, solo hay ${product.stock}`, 'warning');
    return;
  }

  if (existing) {
    existing.qty++;
  } else {
    cart.push({
      product_id:          product.id,
      name:                product.name,
      code:                product.code,
      price_usd:           price,
      stock:               product.stock,
      unidades_por_paquete: uxp,
      qty:                 1,
      tipo_venta:          tipoVenta,
      discount_pct:        0,
      is_bulk:             0
    });
  }
  renderCart();
}

function openBulkModal(product) {
  _bulkProduct = product;
  _bulkMode = 'qty';
  _bulkCurrency = 'usd';

  document.getElementById('bulk-modal-title').textContent = product.name;
  document.getElementById('bulk-product-info').textContent =
    `${product.code ? product.code + ' — ' : ''}Precio: $${product.price_usd.toFixed(2)}/Kg | Stock: ${product.stock.toFixed(2)} Kg`;

  document.getElementById('bulk-qty-input').value = '';
  document.getElementById('bulk-amount-input').value = '';
  document.getElementById('bulk-total-preview').textContent = '$0.00';
  document.getElementById('bulk-total-preview-bs').textContent = '';

  const rate = saleRate || window.currentRate || 0;
  document.getElementById('bulk-rate-info').textContent =
    `Tasa BCV: ${rate ? rate.toLocaleString('es-VE', {minimumFractionDigits: 2}) : 'N/A'} Bs/$`;

  setBulkCurrency('usd');
  setBulkMode('qty');
  openModal('bulk-modal');
  setTimeout(() => document.getElementById('bulk-qty-input').focus(), 200);
}

function setBulkMode(mode) {
  _bulkMode = mode;
  const btnQty = document.getElementById('bulk-btn-qty');
  const btnAmount = document.getElementById('bulk-btn-amount');
  const sectionQty = document.getElementById('bulk-qty-section');
  const sectionAmount = document.getElementById('bulk-amount-section');

  if (mode === 'qty') {
    btnQty.classList.add('active');
    btnAmount.classList.remove('active');
    sectionQty.style.display = 'block';
    sectionAmount.style.display = 'none';
    document.getElementById('bulk-qty-input').value = '';
    document.getElementById('bulk-total-preview').textContent = '$0.00';
    document.getElementById('bulk-total-preview-bs').textContent = '';
    setTimeout(() => document.getElementById('bulk-qty-input').focus(), 100);
  } else {
    btnAmount.classList.add('active');
    btnQty.classList.remove('active');
    sectionQty.style.display = 'none';
    sectionAmount.style.display = 'block';
    document.getElementById('bulk-amount-input').value = '';
    document.getElementById('bulk-total-preview').textContent = '$0.00';
    document.getElementById('bulk-total-preview-bs').textContent = '';
    setTimeout(() => document.getElementById('bulk-amount-input').focus(), 100);
  }
}

function setBulkCurrency(cur) {
  _bulkCurrency = cur;
  const btnUsd = document.getElementById('bulk-cur-usd');
  const btnBs = document.getElementById('bulk-cur-bs');
  const symbol = document.getElementById('bulk-currency-symbol');
  const input = document.getElementById('bulk-amount-input');

  if (cur === 'usd') {
    btnUsd.style.background = 'var(--violet)';
    btnUsd.style.color = 'white';
    btnBs.style.background = 'var(--bg-input)';
    btnBs.style.color = 'var(--text-muted)';
    symbol.textContent = '$';
    symbol.style.color = 'var(--emerald-light)';
    input.placeholder = '0.00';
  } else {
    btnBs.style.background = 'var(--amber)';
    btnBs.style.color = 'black';
    btnUsd.style.background = 'var(--bg-input)';
    btnUsd.style.color = 'var(--text-muted)';
    symbol.textContent = 'Bs';
    symbol.style.color = 'var(--bs-color)';
    input.placeholder = '0.00';
  }
  input.value = '';
  document.getElementById('bulk-total-preview').textContent = '$0.00';
  document.getElementById('bulk-total-preview-bs').textContent = '';
  setTimeout(() => input.focus(), 50);
}

function _calcBulkAmount() {
  const rate = saleRate || window.currentRate || 0;
  const pricePerUnit = _bulkProduct.price_usd;
  let totalUsd = 0;
  let qty = 0;

  if (_bulkMode === 'qty') {
    qty = parseFloat(document.getElementById('bulk-qty-input').value) || 0;
    totalUsd = qty * pricePerUnit;
  } else {
    const amountInput = parseFloat(document.getElementById('bulk-amount-input').value) || 0;
    if (_bulkCurrency === 'bs') {
      const amountUsd = rate > 0 ? amountInput / rate : 0;
      totalUsd = amountUsd;
      qty = pricePerUnit > 0 ? amountUsd / pricePerUnit : 0;
    } else {
      totalUsd = amountInput;
      qty = pricePerUnit > 0 ? amountInput / pricePerUnit : 0;
    }
  }

  return { qty, totalUsd, rate };
}

function updateBulkPreview() {
  if (!_bulkProduct) return;
  const { qty, totalUsd, rate } = _calcBulkAmount();

  document.getElementById('bulk-total-preview').textContent = `$${totalUsd.toFixed(2)}`;
  if (rate > 0) {
    document.getElementById('bulk-total-preview-bs').textContent = `Bs ${(totalUsd * rate).toLocaleString('es-VE', {minimumFractionDigits: 2})}`;
  } else {
    document.getElementById('bulk-total-preview-bs').textContent = '';
  }
}

function confirmBulkAdd() {
  if (!_bulkProduct) return;

  const { qty, totalUsd, rate } = _calcBulkAmount();

  if (qty <= 0) {
    showToast('Ingresa una cantidad o monto válido', 'warning');
    return;
  }

  if (qty > _bulkProduct.stock) {
    showToast(`Stock insuficiente: solo hay ${_bulkProduct.stock.toFixed(2)} Kg`, 'warning');
    return;
  }

  const consumed = getConsumedStock(_bulkProduct.id);
  if (consumed + qty > _bulkProduct.stock) {
    showToast(`Stock máximo alcanzado (${_bulkProduct.stock.toFixed(2)} Kg)`, 'warning');
    return;
  }

  const existing = cart.find(i => i.product_id === _bulkProduct.id && i.tipo_venta === 'unidad');
  if (existing) {
    existing.qty = parseFloat((existing.qty + qty).toFixed(3));
  } else {
    cart.push({
      product_id:          _bulkProduct.id,
      name:                _bulkProduct.name,
      code:                _bulkProduct.code,
      price_usd:           _bulkProduct.price_usd,
      stock:               _bulkProduct.stock,
      unidades_por_paquete: 1,
      qty:                 parseFloat(qty.toFixed(3)),
      tipo_venta:          'unidad',
      discount_pct:        0,
      is_bulk:             1
    });
  }

  closeModal('bulk-modal');
  renderCart();
  const curLabel = _bulkCurrency === 'bs' ? `Bs ${(totalUsd * rate).toFixed(2)}` : `$${totalUsd.toFixed(2)}`;
  showToast(`${_bulkProduct.name}: ${qty.toFixed(3)} Kg — ${curLabel}`, 'success');
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

  const uxp = parseInt(item.unidades_por_paquete) || 1;
  const stockPerUnit = item.tipo_venta === 'paquete' ? uxp : 1;

  // Stock restante considerando otros items del mismo producto en el carrito
  const otherConsumed = getConsumedStock(item.product_id) - (item.qty * stockPerUnit);
  const remaining = Math.max(0, item.stock - otherConsumed);
  const maxQty = Math.floor(remaining / stockPerUnit);

  if (qty > maxQty) {
    showToast(`Máximo ${maxQty} ${item.tipo_venta === 'paquete' ? 'paquetes' : 'unidades'} (${remaining} unds restantes)`, 'warning');
    cart[idx].qty = Math.max(1, maxQty);
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
    applyFilters();
    return;
  }

  container.innerHTML = cart.map((item, idx) => {
    const rate       = saleRate;
    const net        = item.price_usd * (1 - item.discount_pct / 100);
    const totalUsd   = net * item.qty;
    const totalBs    = totalUsd * rate;
    const isBulk     = item.is_bulk === 1;
    const unitLabel  = isBulk ? 'Kg' : (item.tipo_venta === 'paquete' ? 'Paq' : 'Und');
    const badgeClass = isBulk ? 'badge-granel' : (item.tipo_venta === 'paquete' ? 'badge-paquete' : 'badge-unidad');
    const selectedCls = idx === kbdCartIndex ? ' kbd-selected' : '';
    return `
    <div class="cart-item${selectedCls}" data-cart-idx="${idx}" role="listitem" tabindex="0"
      onfocus="setKbdCartIndex(${idx})" onclick="setKbdCartIndex(${idx})">
      <div class="cart-item-name">${item.name}
        <span class="badge ${badgeClass}" style="font-size:0.6rem;margin-left:0.35rem;">${unitLabel}</span>
      </div>
      <div class="cart-item-controls">
        <button class="qty-btn" onclick="changeQty(${idx},-1)"><i class="fa-solid fa-minus"></i></button>
        <input type="number" class="qty-input" value="${item.qty}" min="1" step="${isBulk ? '0.001' : '1'}"
          onchange="updateQty(${idx}, this.value)" onblur="updateQty(${idx}, this.value)">
        <button class="qty-btn" onclick="changeQty(${idx},1)"><i class="fa-solid fa-plus"></i></button>
      </div>
      <div class="cart-item-disc">
        <i class="fa-solid fa-tag" style="color:var(--amber);font-size:0.65rem;"></i>
        Desc:
        <input type="number" class="disc-input" value="${item.discount_pct}" min="0" max="100"
          oninput="updateDisc(${idx}, this.value)">%
        &nbsp;<span style="color:var(--text-muted);">${fmtUSD(item.price_usd)}/${unitLabel}</span>
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
  if (newQty <= 0) {
    removeFromCart(idx);
  } else {
    updateQty(idx, newQty);
    renderCart();
  }
}

/* ── Totals calculation ───────────────────────────────── */
let totals = {};
let userModifiedPayment = false;

function toggleIvaInput() {
  const isEnabled = document.getElementById('iva-toggle').checked;
  document.getElementById('iva-pct').disabled = !isEnabled;
  // Save to localStorage so it persists across pages
  localStorage.setItem('pos_iva_enabled', isEnabled);
  recalcTotals();
}

function onIvaPctChange() {
  const pct = parseFloat(document.getElementById('iva-pct').value) || 0;
  localStorage.setItem('pos_iva_pct', pct);
  recalcTotals();
}

function recalcTotals() {
  const rate     = saleRate;
  const discPct  = parseFloat(document.getElementById('order-discount').value) || 0;
  
  // Leer IVA desde Configuración (localStorage)
  const ivaEnabled = localStorage.getItem('pos_iva_enabled');
  const ivaPctSetting = localStorage.getItem('pos_iva_pct');
  const isIvaEnabled = ivaEnabled === null ? true : ivaEnabled === 'true';
  const ivaPctValue = ivaPctSetting === null ? 16 : parseFloat(ivaPctSetting);
  const taxPct = isIvaEnabled ? ivaPctValue : 0;
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

  // Auto-fill exact amount if user hasn't manually typed an amount
  if (!userModifiedPayment) {
    if (payMethod === 'card' || payMethod === 'cash_usd') {
      document.getElementById('pay-usd').value = totalUsd ? totalUsd.toFixed(2) : '';
      document.getElementById('pay-bs').value = '';
    } else if (payMethod === 'pago_movil' || payMethod === 'cash_bs') {
      document.getElementById('pay-bs').value = totalBs ? totalBs.toFixed(2) : '';
      document.getElementById('pay-usd').value = '';
    }
  }

  calcChange();
}

/* ── Payment method ───────────────────────────────────── */
function selectPayMethod(btn) {
  document.querySelectorAll('.pay-method-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  payMethod = btn.dataset.method;
  userModifiedPayment = false; // Reset modification flag on method change

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

  // Limpiar campos al cambiar de método para evitar cruce de montos
  document.getElementById('pay-usd').value = '';
  document.getElementById('pay-bs').value = '';

  // Auto-fill exact amount for all non-mixed methods
  if (payMethod === 'card' || payMethod === 'cash_usd') {
    document.getElementById('pay-usd').value = totals.totalUsd ? totals.totalUsd.toFixed(2) : '';
  } else if (payMethod === 'pago_movil' || payMethod === 'cash_bs') {
    document.getElementById('pay-bs').value = totals.totalBs ? totals.totalBs.toFixed(2) : '';
  } else if (payMethod === 'mixed') {
    // En pago mixto, no auto-llenar — el usuario decide cuánto paga en cada moneda
    document.getElementById('pay-usd').value = '';
    document.getElementById('pay-bs').value = '';
  }
  
  calcChange();
}

// Function to call when user types in payment inputs
let isAutoFilling = false;

function onPaymentInputModified() {
  if (isAutoFilling) return;
  userModifiedPayment = true;

  const rate = saleRate;
  if (!rate || !totals.totalUsd) return;

  const paidUsd = parseFloat(document.getElementById('pay-usd').value) || 0;
  const paidBs  = parseFloat(document.getElementById('pay-bs').value)  || 0;

  isAutoFilling = true;

  if (payMethod === 'mixed') {
    // Si escribió USD → calcular BS restante
    if (paidUsd > 0 && document.activeElement === document.getElementById('pay-usd')) {
      const remainingUsd = Math.max(0, totals.totalUsd - paidUsd);
      const remainingBs = remainingUsd * rate;
      document.getElementById('pay-bs').value = remainingBs > 0 ? remainingBs.toFixed(2) : '';
    }
    // Si escribió BS → calcular USD restante
    else if (paidBs > 0 && document.activeElement === document.getElementById('pay-bs')) {
      const remainingBs = Math.max(0, totals.totalBs - paidBs);
      const remainingUsd = remainingBs / rate;
      document.getElementById('pay-usd').value = remainingUsd > 0 ? remainingUsd.toFixed(2) : '';
    }
  }

  isAutoFilling = false;
  calcChange();
}

function calcChange() {
  if (!totals.totalUsd) return;
  const rate     = saleRate;
  const paidUsd  = parseFloat(document.getElementById('pay-usd').value) || 0;
  const paidBs   = parseFloat(document.getElementById('pay-bs').value)  || 0;

  const currentPaidBs = paidBs;
  const totalPaidUsd = paidUsd + currentPaidBs / rate;
  const changeUsd    = Math.max(0, totalPaidUsd - totals.totalUsd);
  const changeBs     = changeUsd * rate;
  
  // Evitar bug de decimales al comparar (truncando a 2 decimales reales)
  const roundedPaid = Math.round(totalPaidUsd * 100);
  const roundedTotal = Math.round(totals.totalUsd * 100);
  const enoughPaid   = roundedPaid >= roundedTotal;

  const changeEl    = document.getElementById('change-display');
  const changeUsdEl = document.getElementById('change-usd-display');
  const changeBsEl  = document.getElementById('change-bs-display');

  if (enoughPaid && roundedPaid > roundedTotal) {
    changeEl.style.display = 'block';
    if (payMethod === 'mixed' || payMethod === 'cash_bs' || payMethod === 'pago_movil') {
      changeUsdEl.style.display = 'none';
      changeBsEl.style.display  = 'block';
      changeBsEl.textContent    = fmtBS(changeBs);
    } else {
      changeUsdEl.style.display = 'block';
      changeBsEl.style.display  = 'none';
      changeUsdEl.textContent   = fmtUSD(changeUsd);
    }
  } else {
    changeEl.style.display = 'none';
  }

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
      container.innerHTML = results.map((c, i) => `
        <div class="cust-result-item" role="option" tabindex="-1" data-cust-idx="${i}"
          onclick="selectCustomer(${JSON.stringify(c).replace(/"/g,'&quot;')})"
          onkeydown="if(event.key==='Enter'){event.preventDefault();selectCustomer(${JSON.stringify(c).replace(/"/g,'&quot;')})}"
          onmouseover="this.classList.add('kbd-focused')" onmouseout="this.classList.remove('kbd-focused')">
          <div style="font-weight:600;font-size:0.85rem;">${c.name}</div>
          <div style="font-size:0.73rem;color:var(--text-muted);">${c.doc_type}-${c.doc_number || 'S/N'} &bull; ${c.phone || ''}</div>
        </div>
      `).join('');
      // Primer resultado focable con flechas
      const first = container.querySelector('.cust-result-item');
      if (first) first.tabIndex = 0;
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

  // Validar referencia obligatoria para Pago Móvil
  if (payMethod === 'pago_movil' && !payRef) {
    showToast('Debe ingresar el número de referencia del Pago Móvil.', 'error');
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-check-circle"></i> COBRAR';
    return;
  }

  // Guardar la referencia en "notes"
  const notesText = (payMethod === 'pago_movil' && payRef) ? `Ref PM: ${payRef}` : '';

  const saleData = {
    items: cart.map(i => ({
      product_id:      i.product_id,
      quantity:        i.qty,
      discount_percent: i.discount_pct,
      tipo_venta:      i.tipo_venta
    })),
    exchange_rate:    saleRate,
    customer_id:      selectedCustomer?.id || null,
    customer_name:    selectedCustomer?.name || 'Consumidor Final',
    payment_method:   payMethod,
    amount_paid_usd:  paidUsd,
    amount_paid_bs:   paidBs,
    discount_percent: discPct,
    tax_percent:      (() => {
      const enabled = localStorage.getItem('pos_iva_enabled');
      const pct = localStorage.getItem('pos_iva_pct');
      const isEnabled = enabled === null ? true : enabled === 'true';
      return isEnabled ? (parseFloat(pct) || 16) : 0;
    })(),
    notes:            notesText
  };

  try {
    const result = await api.post('/sales', saleData);
    // Fetch full sale to show receipt
    const sale   = await api.get(`/sales/${result.sale_id}`);
    showReceipt(sale, result);
    showToast('¡Venta registrada exitosamente!', 'success');

    // Reset cart and form for next sale
    cart = [];
    selectedCustomer = null;
    payMethod = 'cash_usd';
    document.getElementById('order-discount').value = '0';
    document.getElementById('pay-usd').value = '';
    document.getElementById('pay-bs').value  = '';
    document.getElementById('pay-ref').value = '';
    document.getElementById('change-display').style.display = 'none';
    document.querySelectorAll('.pay-method-btn').forEach(b => b.classList.remove('active'));
    document.querySelector('[data-method="cash_usd"]').classList.add('active');
    document.getElementById('bs-pay-row').style.display = 'none';
    document.getElementById('usd-pay-row').style.display = 'flex';
    renderCart();

    // Refresh stock in product list
    allProducts = await api.get('/products?active=1');
    renderProducts(allProducts);
    applyFilters();

    // Reset charge button
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-check-circle"></i> COBRAR';

  } catch (e) {
    showToast(e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-check-circle"></i> COBRAR';
  }
}

/* ── Receipt ────────────────────────────────────────────── */
function showReceipt(sale, result) {
  window.currentSaleData = sale;
  window.currentSaleCustomer = selectedCustomer;
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
    ${sale.change_usd > 0.001 ? `
    <div class="receipt-row" style="color:var(--emerald);font-weight:600;">
      <span>Vuelto:</span>
      <span>${sale.change_bs > 0 ? fmtBS(sale.change_bs) : fmtUSD(sale.change_usd)}</span>
    </div>` : ''}

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
  // Sync IVA with Settings from localStorage
  const ivaEnabled = localStorage.getItem('pos_iva_enabled');
  const ivaPct = localStorage.getItem('pos_iva_pct');
  const isEnabled = ivaEnabled === null ? true : ivaEnabled === 'true';
  const pctValue = ivaPct === null ? 16 : parseFloat(ivaPct);
  document.getElementById('iva-toggle').checked = isEnabled;
  document.getElementById('iva-pct').value = pctValue;
  document.getElementById('iva-pct').disabled = !isEnabled;
  document.getElementById('pay-usd').value = '';
  document.getElementById('pay-bs').value  = '';
  document.getElementById('pay-ref').value = '';
  document.getElementById('change-display').style.display = 'none';
  document.querySelectorAll('.pay-method-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-method="cash_usd"]').classList.add('active');
  document.getElementById('bs-pay-row').style.display = 'none';
  document.getElementById('usd-pay-row').style.display = 'flex';
  clearCustomer();
  renderCart();
  closeModal('receipt-modal');
  if (window.innerWidth <= 1024) switchPosView('products');
}

/* ── WhatsApp Receipt ─────────────────────────────────────── */
function sendReceiptWhatsApp() {
  if (!window.currentSaleData) {
    showToast('No hay datos de venta disponibles.', 'error');
    return;
  }
  const sale = window.currentSaleData;
  const customer = window.currentSaleCustomer;
  
  let phone = customer && customer.phone ? customer.phone : '';
  if (!phone) {
    phone = prompt('Ingrese el número de teléfono (Ej: 04141234567):');
    if (!phone) return; // User cancelled
  }
  
  // Clean phone number (remove spaces, plus, dashes)
  phone = phone.replace(/[^0-9]/g, '');
  // Assume Venezuela code if not provided
  if (phone.length === 11 && phone.startsWith('0')) {
    phone = '58' + phone.substring(1);
  } else if (phone.length === 10 && (phone.startsWith('4') || phone.startsWith('2'))) {
    phone = '58' + phone;
  }
  
  const now = new Date(sale.created_at || Date.now());
  const dateStr = now.toLocaleDateString('es-VE') + ' - ' + now.toLocaleTimeString('es-VE', {hour: '2-digit', minute:'2-digit'});
  const bcv = parseFloat(sale.exchange_rate).toLocaleString('es-VE', {minimumFractionDigits:2});
  const totalUsd = parseFloat(sale.total_usd).toLocaleString('es-VE', {minimumFractionDigits:2});
  const totalBs = parseFloat(sale.total_bs).toLocaleString('es-VE', {minimumFractionDigits:2});
  
  let text = `🏪 *SISTEMA POS VENEZUELA*\n`;
  text += `-----------------------------------\n`;
  text += `🧾 *Recibo:* #${sale.sale_number}\n`;
  text += `📅 *Fecha:* ${dateStr}\n`;
  text += `👤 *Cliente:* ${sale.customer_name || 'Consumidor Final'}\n`;
  text += `-----------------------------------\n`;
  text += `*CANT | DESCRIPCIÓN | TOTAL*\n`;
  
  sale.items.forEach(item => {
    const qty = parseFloat(item.quantity).toString();
    const itemTotal = parseFloat(item.total_usd).toLocaleString('es-VE', {minimumFractionDigits:2});
    text += `${qty}x ${item.product_name} ... $${itemTotal}\n`;
  });
  
  text += `-----------------------------------\n`;
  text += `💰 *TOTAL USD:* $ ${totalUsd}\n`;
  text += `💵 *TOTAL Bs:* ${totalBs}\n`;
  text += `-----------------------------------\n`;
  text += `Tasa BCV del día: ${bcv} Bs/$\n`;
  text += `-----------------------------------\n`;
  text += `🚀 ¡Gracias por su compra!\nConserve este recibo electrónico.`;
  const encoded = encodeURIComponent(text);
  const deepLink = `whatsapp://send?phone=${phone}&text=${encoded}`;
  const fallback = `https://wa.me/${phone}?text=${encoded}`;
  
  // Try native protocol via hidden iframe (bypasses browser interception)
  const iframe = document.createElement('iframe');
  iframe.style.display = 'none';
  iframe.src = deepLink;
  document.body.appendChild(iframe);
  
  // If the protocol didn't trigger after 1.5s, fallback to wa.me
  setTimeout(() => {
    document.body.removeChild(iframe);
    // Only fallback if page is still visible (protocol didn't switch away)
    if (!document.hidden) {
      window.location.href = fallback;
    }
  }, 1500);
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
    addToCart(product.id, 'unidad');
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

/* ═══════════════════════════════════════════════════════
   NAVEGACIÓN POR TECLADO — Estilo GSIN
═══════════════════════════════════════════════════════ */

/** Busca producto por código y lo agrega (wedge scanner + Enter en búsqueda). */
function addProductByCode(code) {
  if (!code || !code.trim()) return false;
  const term = code.trim().toLowerCase();
  const product = allProducts.find(p =>
    (p.code && p.code.toLowerCase() === term) ||
    (p.code && p.code.toLowerCase().startsWith(term))
  );
  if (product) {
    addToCart(product.id, modoVentaGlobal);
    document.getElementById('product-search').value = '';
    applyFilters();
    showToast(`Agregado: ${product.name}`, 'success');
    return true;
  }
  // Si hay un solo resultado por nombre, agregarlo
  const filtered = allProducts.filter(p =>
    p.name.toLowerCase().includes(term) || (p.code && p.code.toLowerCase().includes(term))
  );
  if (filtered.length === 1) {
    addToCart(filtered[0].id, modoVentaGlobal);
    document.getElementById('product-search').value = '';
    applyFilters();
    showToast(`Agregado: ${filtered[0].name}`, 'success');
    return true;
  }
  showToast(`Producto no encontrado: ${code}`, 'warning');
  return false;
}

/** Enter en búsqueda de producto: agregar primer resultado o código exacto. */
document.getElementById('product-search').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  const term = e.target.value.trim();
  if (!term) return;

  // Código exacto
  const exact = allProducts.find(p => p.code && p.code.toLowerCase() === term.toLowerCase());
  if (exact) {
    addToCart(exact.id, modoVentaGlobal);
    e.target.value = '';
    applyFilters();
    showToast(`Agregado: ${exact.name}`, 'success');
    return;
  }

  // Primer resultado del filtro actual
  const filtered = allProducts.filter(p =>
    p.name.toLowerCase().includes(term.toLowerCase()) ||
    (p.code && p.code.toLowerCase().includes(term.toLowerCase()))
  );
  if (filtered.length >= 1) {
    addToCart(filtered[0].id, modoVentaGlobal);
    e.target.value = '';
    applyFilters();
    showToast(`Agregado: ${filtered[0].name}`, 'success');
  } else {
    showToast(`Sin resultados: ${term}`, 'warning');
  }
});

/** Navegación con flechas en el grid de productos. */
document.getElementById('products-grid').addEventListener('keydown', (e) => {
  const cards = Array.from(document.querySelectorAll('#products-grid .product-card'));
  if (!cards.length) return;
  const current = document.activeElement;
  let idx = cards.indexOf(current);

  if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
    e.preventDefault();
    const next = idx === -1 ? 0 : Math.min(idx + 1, cards.length - 1);
    cards[next].focus();
  } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
    e.preventDefault();
    if (idx <= 0) {
      document.getElementById('product-search').focus();
      return;
    }
    cards[idx - 1].focus();
  } else if (e.key === 'Home') {
    e.preventDefault();
    cards[0].focus();
  } else if (e.key === 'End') {
    e.preventDefault();
    cards[cards.length - 1].focus();
  }
  // Enter ya activa el button (addToCart via onclick)
});

/** Selección de línea del carrito con teclado. */
function setKbdCartIndex(idx) {
  kbdCartIndex = idx;
  document.querySelectorAll('.cart-item').forEach((el, i) => {
    el.classList.toggle('kbd-selected', i === idx);
  });
}

function focusCartItem(idx) {
  const items = document.querySelectorAll('#cart-items .cart-item');
  if (!items.length) return;
  const clamped = Math.max(0, Math.min(idx, items.length - 1));
  kbdCartIndex = clamped;
  items[clamped].focus();
  items[clamped].scrollIntoView({ block: 'nearest' });
}

document.getElementById('cart-items').addEventListener('keydown', (e) => {
  const items = Array.from(document.querySelectorAll('#cart-items .cart-item'));
  if (!items.length) return;
  const current = document.activeElement;
  const idx = items.indexOf(current);

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    focusCartItem(idx === -1 ? 0 : idx + 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (idx <= 0) {
      document.getElementById('product-search').focus();
      return;
    }
    focusCartItem(idx - 1);
  } else if (e.key === 'Delete' && idx >= 0) {
    // F8 lo maneja Keyboard global; Delete solo aquí
    e.preventDefault();
    removeFromCart(idx);
  } else if ((e.key === '+' || e.key === '-') && e.target.classList.contains('cart-item')) {
    e.preventDefault();
    changeQty(idx, e.key === '+' ? 1 : -1);
    focusCartItem(idx);
  }
});

/** Resultados de cliente: flechas + Enter. */
document.getElementById('customer-results').addEventListener('keydown', (e) => {
  const items = Array.from(document.querySelectorAll('#customer-results .cust-result-item'));
  if (!items.length) return;
  // Ajustar tabindex roving
  const current = document.activeElement;
  let idx = items.indexOf(current);
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    const next = idx === -1 ? 0 : Math.min(idx + 1, items.length - 1);
    items.forEach(i => i.tabIndex = -1);
    items[next].tabIndex = 0;
    items[next].focus();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (idx <= 0) {
      document.getElementById('cust-search-input').focus();
      return;
    }
    items.forEach(i => i.tabIndex = -1);
    items[idx - 1].tabIndex = 0;
    items[idx - 1].focus();
  }
});

/** Métodos de pago: flechas entre botones. */
document.querySelector('.payment-methods')?.addEventListener('keydown', (e) => {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
  const btns = Array.from(document.querySelectorAll('.pay-method-btn'));
  const idx = btns.indexOf(document.activeElement);
  if (idx === -1) return;
  e.preventDefault();
  let next = idx;
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = Math.min(idx + 1, btns.length - 1);
  else next = Math.max(idx - 1, 0);
  btns[next].focus();
  // Enter ya selecciona (onclick)
});

/** Enfoca el primer input visible de pago. */
function focusPaymentInput() {
  if (payMethod === 'cash_bs' || payMethod === 'pago_movil') {
    document.getElementById('pay-bs').focus();
  } else if (payMethod === 'mixed') {
    document.getElementById('pay-usd').focus();
  } else {
    document.getElementById('pay-usd').focus();
  }
}

/** Enfoca el primer input visible de pago y resalta. */
function focusCharge() {
  if (!cart.length) {
    showToast('Carrito vacío — agrega productos primero (F3)', 'warning');
    document.getElementById('product-search').focus();
    return;
  }
  // Asegurar que el panel de pago sea visible
  const paymentEl = document.getElementById('cart-payment');
  if (paymentEl) paymentEl.style.display = 'block';
  focusPaymentInput();
}

/** Canceal cobro: limpia montos de pago, no borra carrito. */
function cancelCharge() {
  document.getElementById('pay-usd').value = '';
  document.getElementById('pay-bs').value = '';
  document.getElementById('pay-ref').value = '';
  document.getElementById('change-display').style.display = 'none';
  document.getElementById('product-search').focus();
  showToast('Cobro cancelado (carrito conservado)', 'info');
}

/** Refresca productos desde el servidor. */
async function refreshProducts() {
  try {
    allProducts = await api.get('/products?active=1');
    applyFilters();
    showToast('Productos actualizados', 'success');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

/** Elimina la línea seleccionada del carrito con confirmación. */
function deleteSelectedCartLine() {
  if (kbdCartIndex < 0 || kbdCartIndex >= cart.length) {
    showToast('Selecciona una línea del carrito (↑↓)', 'info');
    return;
  }
  const item = cart[kbdCartIndex];
  confirmAction(`¿Eliminar "${item.name}" del carrito?`, () => {
    removeFromCart(kbdCartIndex);
    kbdCartIndex = Math.min(kbdCartIndex, cart.length - 1);
    if (cart.length) focusCartItem(Math.max(0, kbdCartIndex));
  });
}

/* ── Registro de atajos POS ───────────────────────────── */
if (typeof Keyboard !== 'undefined') {
  Keyboard.register('pos', {
    F2: focusCharge,
    F3: () => document.getElementById('product-search').focus(),
    F4: openCustomerSearch,
    F5: refreshProducts,
    F6: () => document.getElementById('order-discount').focus(),
    F8: deleteSelectedCartLine,
    F9: () => document.querySelector('.pay-method-btn.active')?.focus(),
    F10: () => {
      if (cart.length) processSale();
      else showToast('Carrito vacío', 'warning');
    },
    F11: () => {
      // F11 puede ser fullscreen del navegador; intentar expandir panel productos
      const body = document.querySelector('.pos-body');
      if (!body) return;
      const cur = body.style.getPropertyValue('--cart-width');
      if (cur && cur !== '0px') {
        body.dataset.prevCartWidth = cur;
        body.style.setProperty('--cart-width', '0px');
        showToast('Panel productos expandido (F11 para restaurar)', 'info');
      } else {
        const prev = body.dataset.prevCartWidth || '380px';
        body.style.setProperty('--cart-width', prev);
      }
    },
    F12: cancelCharge,
    SLASH: () => document.getElementById('product-search').focus()
  }, [
    { key: 'F1',  desc: 'Ayuda de atajos' },
    { key: 'F2',  desc: 'Cobrar / enfocar pago' },
    { key: 'F3',  desc: 'Buscar producto' },
    { key: 'F4',  desc: 'Buscar cliente' },
    { key: 'F5',  desc: 'Refrescar productos' },
    { key: 'F6',  desc: 'Descuento de orden' },
    { key: 'F8',  desc: 'Eliminar línea del carrito' },
    { key: 'F9',  desc: 'Método de pago' },
    { key: 'F10', desc: 'Procesar venta' },
    { key: 'F11', desc: 'Expandir/colapsar productos' },
    { key: 'F12', desc: 'Cancelar cobro (no borra carrito)' },
    { key: 'Enter', desc: 'Agregar producto buscado / confirmar' },
    { key: '/', desc: 'Buscar producto' },
    { key: '+ / −', desc: 'Cantidad de línea seleccionada' },
    { key: 'Delete', desc: 'Eliminar línea seleccionada' }
  ],
  // F2 y F10 deben funcionar aunque el foco esté en un input de pago
  ['F2', 'F10']);
}

/* ── Autofocus búsqueda al cargar (si hay sesión) ─────── */
if (typeof isLoggedIn === 'function' && isLoggedIn()) {
  // Esperar a que init() cargue productos
  setTimeout(() => {
    const search = document.getElementById('product-search');
    if (search && document.activeElement === document.body) {
      search.focus();
    }
  }, 600);
}

/* ── Tras cobrar: volver a búsqueda ───────────────────── */
// showReceipt / newSale se envuelven para enfocar búsqueda y manejar Enter en recibo
window.addEventListener('load', () => {
  if (typeof showReceipt === 'function') {
    const orig = showReceipt;
    window.showReceipt = function (sale, result) {
      orig(sale, result);
      setTimeout(() => {
        const receiptModal = document.getElementById('receipt-modal');
        if (receiptModal && !receiptModal.classList.contains('hidden')) {
          const newBtn = receiptModal.querySelector('.btn-primary');
          if (newBtn) newBtn.focus();
        }
      }, 150);
    };
  }
  if (typeof newSale === 'function') {
    const origNew = newSale;
    window.newSale = function () {
      origNew();
      document.getElementById('product-search').focus();
    };
  }
});
