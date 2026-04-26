/* products.js */
requireAuth();

// Inicializar header SIN llamar a loadBCVRate (lo haremos nosotros después)
const user = getUser();
const isAdmin = user.role === 'admin';

// Rellenar datos del header manualmente
(function() {
  const nameEl   = document.getElementById('user-name');
  const roleEl   = document.getElementById('user-role');
  const avatarEl = document.getElementById('user-avatar');
  if (nameEl)   nameEl.textContent   = user.name || 'Usuario';
  if (roleEl)   roleEl.textContent   = user.role === 'admin' ? 'Admin' : 'Cajero';
  if (avatarEl) avatarEl.textContent = (user.name || 'U')[0].toUpperCase();
})();

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
      `<tr><td colspan="9" style="text-align:center;padding:2rem;">
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
  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="9"><div class="empty-state"><i class="fa-solid fa-box-open"></i><h3>Sin productos</h3><p>No se encontraron productos</p></div></td></tr>`;
    return;
  }
  tbody.innerHTML = list.map(p => {
    const cat      = categories.find(c => c.id === p.category_id);
    const priceBs  = (p.price_usd * (window.currentRate || 0)).toFixed(2);
    const stockCls = p.stock === 0 ? 'badge-red' : p.stock <= p.min_stock ? 'badge-amber' : 'badge-green';

    // ── Permisos de acciones ─────────────────────────────────────
    // Botón EDITAR: visible para todos los usuarios autenticados
    const editBtn = `<button class="btn btn-outline btn-sm" onclick="openProductModal(${p.id})" title="Editar producto">
          <i class="fa-solid fa-pen"></i>
        </button>`;

    // Botón ELIMINAR: exclusivo del administrador
    const deleteBtn = isAdmin
      ? `<button class="btn btn-danger btn-sm" onclick="deleteProduct(${p.id}, '${p.name.replace(/'/g, "\\'")}')" title="Eliminar (solo admin)">
          <i class="fa-solid fa-trash"></i>
        </button>`
      : '';

    const actions = `<div class="action-group">${editBtn}${deleteBtn}</div>`;
    const rowCls = p.active ? '' : 'row-inactive';

    return `<tr class="${rowCls}">
      <td>
        <div style="font-weight:600;">${p.name}</div>
      </td>
      <td><code style="font-size:0.75rem;color:var(--text-muted);">${p.code || '—'}</code></td>
      <td>${cat ? `<span class="badge" style="background:${cat.color}22;color:${cat.color};">${cat.name}</span>` : '—'}</td>
      <td><span class="price-usd">${p.price_usd.toFixed(2)}</span></td>
      <td><span class="price-bs">${parseFloat(priceBs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span></td>
      <td style="color:var(--text-muted);font-size:0.8rem;">${fmtUSD(p.cost_usd)}</td>
      <td><span class="badge ${stockCls}">${p.stock}</span></td>
      <td>${p.active ? '<span class="badge badge-green">Activo</span>' : '<span class="badge badge-gray">Inactivo</span>'}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('');
}

/* ── Modal: Product ─────────────────────────────────────── */
function openProductModal(productId = null) {
  document.getElementById('prod-id').value = '';
  document.getElementById('prod-name').value = '';
  document.getElementById('prod-code').value = '';
  document.getElementById('prod-price').value = '';
  document.getElementById('prod-cost').value = '';
  document.getElementById('prod-stock').value = '';
  document.getElementById('prod-minstock').value = '5';
  document.getElementById('prod-active').value = '1';
  document.getElementById('prod-active-row').style.display = 'none';
  document.getElementById('bs-preview').textContent = '';

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
    updatePriceBsPreview();
  } else {
    document.getElementById('modal-prod-title').innerHTML = '<i class="fa-solid fa-plus" style="color:var(--violet-light);"></i> Nuevo Producto';
    document.getElementById('prod-cat').value = '';
  }
  openModal('product-modal');
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

  if (!name)       { showToast('El nombre es requerido', 'warning'); return; }
  if (priceUsd<=0) { showToast('El precio debe ser mayor a 0', 'warning'); return; }

  const body = { name, code: code || null, category_id: catId || null, price_usd: priceUsd, cost_usd: costUsd, stock, min_stock: minStock, active };

  try {
    if (id) {
      await api.put(`/products/${id}`, body);
      showToast('Producto actualizado', 'success');
    } else {
      await api.post('/products', body);
      showToast('Producto creado', 'success');
    }
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
      showToast('Producto eliminado permanentemente', 'success');
      products = await api.get('/products?active=all');
      renderProducts(products);
      updateStats(products);
    } catch (e) { showToast(e.message, 'error'); }
  });
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
    </div>`).join('');
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
