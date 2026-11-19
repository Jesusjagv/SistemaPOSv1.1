/* reports.js */
requireAuth();
initHeader();

let dailyChart   = null;
let paymentChart = null;
let currentSalesData = [];
let currentSelectedSale = null;

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

// Set default date range (today)
const today = new Date().toISOString().split('T')[0];
document.getElementById('date-from').value = today;
document.getElementById('date-to').value   = today;

/* ── Date presets ───────────────────────────────────────── */
function applyDatePreset(preset) {
  const now = new Date();
  let from, to;

  switch (preset) {
    case 'today':
      from = to = now.toISOString().split('T')[0];
      break;
    case 'yesterday': {
      const y = new Date(now);
      y.setDate(y.getDate() - 1);
      from = to = y.toISOString().split('T')[0];
      break;
    }
    case 'this_week': {
      const d = new Date(now);
      const day = d.getDay();
      const diff = d.getDate() - day + (day === 0 ? -6 : 1);
      from = new Date(d.setDate(diff)).toISOString().split('T')[0];
      to = now.toISOString().split('T')[0];
      break;
    }
    case 'this_month':
      from = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0];
      to = now.toISOString().split('T')[0];
      break;
    case 'last_month': {
      const lm = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      from = lm.toISOString().split('T')[0];
      to = new Date(lm.getFullYear(), lm.getMonth() + 1, 0).toISOString().split('T')[0];
      break;
    }
    case 'last_7': {
      const d7 = new Date(now);
      d7.setDate(d7.getDate() - 6);
      from = d7.toISOString().split('T')[0];
      to = now.toISOString().split('T')[0];
      break;
    }
    case 'last_30': {
      const d30 = new Date(now);
      d30.setDate(d30.getDate() - 29);
      from = d30.toISOString().split('T')[0];
      to = now.toISOString().split('T')[0];
      break;
    }
    default:
      return;
  }

  document.getElementById('date-from').value = from;
  document.getElementById('date-to').value = to;

  document.querySelectorAll('.period-tab').forEach(btn => btn.classList.remove('active'));
  event.currentTarget.classList.add('active');

  loadReports();
}

async function loadReports() {
  const from = document.getElementById('date-from').value;
  const to   = document.getElementById('date-to').value;

  showChartLoading(true);

  try {
    const [summary, topProds, daily, sales] = await Promise.all([
      api.get(`/reports/summary?from=${from}&to=${to}`),
      api.get(`/reports/top-products?from=${from}&to=${to}&limit=8`),
      api.get(`/reports/daily-chart?from=${from}&to=${to}`),
      api.get(`/sales?from=${from}&to=${to}&limit=100`)
    ]);

    renderSummary(summary);
    renderTopProducts(topProds);
    renderLowStock(summary.low_stock_products || []);
    renderDailyChart(daily);
    renderPaymentChart(summary.by_payment_method || []);
    
    currentSalesData = sales;
    renderSalesHistory(sales);
    
    document.getElementById('btn-download-sales').disabled = sales.length === 0;
    document.getElementById('btn-download-detailed').disabled = sales.length === 0;
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    showChartLoading(false);
  }
}

function showChartLoading(loading) {
  const skeleton = document.getElementById('daily-chart-skeleton');
  const canvas = document.getElementById('daily-chart');
  if (skeleton) skeleton.style.display = loading ? 'flex' : 'none';
  if (canvas) canvas.style.display = loading ? 'none' : 'block';
}

loadReports();

/* ── Summary cards ──────────────────────────────────────── */
function renderSummary(data) {
  const t = data.totals || {};
  document.getElementById('m-count').textContent = t.total_sales  || 0;
  document.getElementById('m-usd').textContent   = fmtUSD(t.total_usd || 0);
  document.getElementById('m-bs').textContent    = fmtBS(t.total_bs || 0);
  document.getElementById('m-tax').textContent   = fmtUSD(t.tax_usd || 0);
  document.getElementById('m-avg').textContent   = fmtUSD(t.avg_ticket_usd || 0);
}

/* ── Top products list ───────────────────────────────────── */
function renderTopProducts(list) {
  const el = document.getElementById('top-products-list');
  if (!list.length) {
    el.innerHTML = `<div class="empty-state"><i class="fa-solid fa-trophy"></i><p>Sin datos en el período</p></div>`;
    return;
  }
  const maxQty = list[0].total_qty || 1;
  el.innerHTML = list.map((p, i) => `
    <div class="report-list-item">
      <div class="report-rank">${i+1}</div>
      <div class="report-item-body">
        <div class="report-item-name">${p.product_name}</div>
        <div class="report-item-bar">
          <div class="report-item-track">
            <div class="report-item-fill" style="width:${(p.total_qty/maxQty)*100}%;"></div>
          </div>
        </div>
      </div>
      <div class="report-item-stats">
        <div class="report-item-qty">${p.total_qty} uds</div>
        <div class="report-item-usd">${fmtUSD(p.total_usd)}</div>
      </div>
    </div>
  `).join('');
}

/* ── Low stock ───────────────────────────────────────────── */
function renderLowStock(list) {
  const el = document.getElementById('low-stock-list');
  if (!list.length) {
    el.innerHTML = `<div class="empty-state"><i class="fa-solid fa-check-circle" style="color:var(--emerald);"></i><p>Todo el stock está bien 👍</p></div>`;
    return;
  }
  el.innerHTML = list.map(p => `
    <div class="stock-item">
      <div class="stock-item-info">
        <div class="stock-item-name">${p.name}</div>
        <div class="stock-item-meta">${p.code || 'Sin código'} &bull; Mínimo: ${p.min_stock}</div>
      </div>
      <span class="badge ${p.stock === 0 ? 'badge-red' : 'badge-amber'}">
        ${p.stock === 0 ? 'Sin stock' : `Stock: ${p.stock}`}
      </span>
    </div>
  `).join('');
}

/* ── Daily Chart ──────────────────────────────────────────── */
function renderDailyChart(data) {
  const ctx = document.getElementById('daily-chart').getContext('2d');
  if (dailyChart) dailyChart.destroy();

  const emptyEl = document.getElementById('daily-chart-empty');
  if (!data || !data.length) {
    ctx.clearRect(0, 0, 300, 150);
    if (emptyEl) emptyEl.style.display = 'flex';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  // Labels: solo el número del día
  const labels = data.map(d => new Date(d.day + 'T00:00:00').getDate());

  // Calcular rango de fechas para el título
  const firstDate = new Date(data[0].day + 'T00:00:00');
  const lastDate = new Date(data[data.length - 1].day + 'T00:00:00');
  const monthNames = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
  let periodLabel = '';
  if (firstDate.getMonth() === lastDate.getMonth() && firstDate.getFullYear() === lastDate.getFullYear()) {
    periodLabel = `${monthNames[firstDate.getMonth()]} ${firstDate.getFullYear()}`;
  } else if (firstDate.getFullYear() === lastDate.getFullYear()) {
    periodLabel = `${monthNames[firstDate.getMonth()]} - ${monthNames[lastDate.getMonth()]} ${firstDate.getFullYear()}`;
  } else {
    periodLabel = `${monthNames[firstDate.getMonth()]} ${firstDate.getFullYear()} - ${monthNames[lastDate.getMonth()]} ${lastDate.getFullYear()}`;
  }
  const chartTitleEl = document.getElementById('daily-chart-title');
  if (chartTitleEl) chartTitleEl.textContent = `Ventas por Día — ${periodLabel}`;

  const usdValues = data.map(d => parseFloat(d.total_usd) || 0);
  const bsValues = data.map(d => parseFloat(d.total_bs) || 0);
  const countValues = data.map(d => d.sales_count || 0);

  const violet     = cssVar('--chart-line');
  const violetFill = cssVar('--chart-line-fill');
  const pointColor = cssVar('--chart-line-point');
  const green      = cssVar('--chart-second');
  const greenFill  = cssVar('--chart-second-fill');
  const greenPoint = cssVar('--chart-second-point');
  const gridColor  = cssVar('--chart-grid');
  const labelColor = cssVar('--chart-label');
  const titleColor = cssVar('--chart-title');

  dailyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Ventas ($)',
          data: usdValues,
          borderColor: violet,
          backgroundColor: violetFill,
          borderWidth: 2.5,
          fill: true,
          tension: 0.4,
          pointBackgroundColor: pointColor,
          pointRadius: data.length > 15 ? 4 : 6,
          pointHoverRadius: 9,
          pointBorderWidth: 1,
          yAxisID: 'y',
        },
        {
          label: 'Cant. Ventas',
          data: countValues,
          borderColor: green,
          backgroundColor: greenFill,
          borderWidth: 2,
          fill: false,
          tension: 0.3,
          pointBackgroundColor: greenPoint,
          pointRadius: data.length > 15 ? 3 : 5,
          pointHoverRadius: 8,
          pointBorderWidth: 1,
          borderDash: [5, 3],
          yAxisID: 'y1',
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      onClick: (e, elements) => {
        if (elements.length > 0) {
          const idx = elements[0].index;
          const day = data[idx].day;
          document.getElementById('date-from').value = day;
          document.getElementById('date-to').value = day;
          loadReports();
          const el = document.getElementById('sales-section');
          if (el) el.scrollIntoView({ behavior: 'smooth' });
        }
      },
      plugins: {
        legend: {
          labels: { color: titleColor, font: { size: 13, weight: '600' }, padding: 16 }
        },
        tooltip: {
          titleFont: { size: 13, weight: '700' },
          bodyFont: { size: 12 },
          padding: 12,
          cornerRadius: 8,
          callbacks: {
            title: function(items) {
              if (!items.length) return '';
              const idx = items[0].dataIndex;
              return new Date(data[idx].day + 'T00:00:00').toLocaleDateString('es-VE', {
                day: 'numeric', month: 'long', year: 'numeric'
              });
            },
            label: function(ctx) {
              if (ctx.dataset.label === 'Ventas ($)') {
                return ` Total USD: $${ctx.parsed.y.toFixed(2)}`;
              }
              if (ctx.dataset.label === 'Cant. Ventas') {
                return ` Ventas: ${ctx.parsed.y} transacción${ctx.parsed.y !== 1 ? 'es' : ''}`;
              }
              return '';
            },
            afterBody: function(items) {
              if (!items.length) return '';
              const idx = items[0].dataIndex;
              return ` Total Bs: ${fmtBS(bsValues[idx])}`;
            }
          }
        }
      },
      scales: {
        x: {
          ticks: {
            color: labelColor,
            font: { size: 13, weight: '600' },
            maxRotation: 0,
            autoSkip: true,
            autoSkipPadding: 20,
            maxTicksLimit: 12
          },
          grid: { display: false }
        },
        y: {
          type: 'linear',
          display: true,
          position: 'left',
          beginAtZero: true,
          ticks: { color: labelColor, font: { size: 12 }, callback: v => `$${v}` },
          grid: { color: gridColor },
          title: { display: true, text: 'USD', color: titleColor, font: { size: 12, weight: '600' } }
        },
        y1: {
          type: 'linear',
          display: true,
          position: 'right',
          beginAtZero: true,
          ticks: { color: labelColor, font: { size: 12 }, precision: 0 },
          grid: { drawOnChartArea: false },
          title: { display: true, text: 'Ventas', color: titleColor, font: { size: 12, weight: '600' } }
        }
      }
    }
  });
}

/* ── Payment method chart ─────────────────────────────────── */
function renderPaymentChart(data) {
  const ctx = document.getElementById('payment-chart').getContext('2d');
  const methodLabels = {
    cash_usd: 'Efectivo $',
    cash_bs:  'Efectivo Bs',
    card:     'Tarjeta',
    pago_movil: 'Pago Móvil',
    mixed:    'Mixto'
  };
  const labels  = data.map(d => methodLabels[d.payment_method] || d.payment_method);
  const values  = data.map(d => parseFloat(d.sum_usd) || 0);
  const colors  = [
    cssVar('--chart-doughnut-1'),
    cssVar('--chart-doughnut-2'),
    cssVar('--chart-doughnut-3'),
    cssVar('--chart-doughnut-4')
  ];
  const doughnutBorder = cssVar('--chart-doughnut-bg');
  const titleColor     = cssVar('--chart-title');

  if (paymentChart) paymentChart.destroy();
  if (!data.length) {
    ctx.clearRect(0,0,300,240);
    return;
  }
  paymentChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderColor: doughnutBorder, borderWidth: 2 }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: titleColor, font: { size: 11 }, padding: 12 } },
        tooltip: {
          callbacks: { label: ctx => `${ctx.label}: ${fmtUSD(ctx.parsed)}` }
        }
      }
    }
  });
}

/* ── Sales history table ─────────────────────────────────── */
function renderSalesHistory(sales) {
  const tbody = document.getElementById('sales-tbody');
  if (!sales.length) {
    tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state"><i class="fa-solid fa-receipt"></i><p>Sin ventas en el período</p></div></td></tr>`;
    return;
  }
  const methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', pago_movil:'Pago Móvil', mixed:'Mixto' };
  tbody.innerHTML = sales.map(s => `
    <tr class="sale-row" onclick="openSaleDetail(${s.id})">
      <td class="sale-number">${s.sale_number}</td>
      <td class="text-sm">${s.customer_name || 'Consumidor Final'}</td>
      <td class="text-sm text-muted">${s.user_name || '—'}</td>
      <td><span class="badge badge-blue">${methodNames[s.payment_method] || s.payment_method}</span></td>
      <td class="text-center font-bold">${parseFloat(s.total_items || 0)}</td>
      <td><span class="price-usd">${parseFloat(s.total_usd).toFixed(2)}</span></td>
      <td><span class="price-bs" style="font-size:0.72rem;">${parseFloat(s.total_bs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span></td>
      <td><span class="badge ${s.status === 'completed' ? 'badge-green' : 'badge-red'}">${s.status === 'completed' ? 'Completada' : 'Anulada'}</span></td>
      <td class="sale-date">${new Date(s.created_at).toLocaleString('es-VE',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}</td>
      <td class="text-center">
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation(); downloadSaleExcel(${s.id})" title="Descargar Excel" style="color:var(--violet-light);padding:0.25rem 0.5rem;">
          <i class="fa-solid fa-file-excel"></i>
        </button>
      </td>
    </tr>
  `).join('');
}

/* ── Sale details modal ─────────────────────────────────── */
async function openSaleDetail(sid) {
  try {
    const sale = await api.get(`/sales/${sid}`);
    currentSelectedSale = sale; // Guardar para el botón de descarga del modal
    const methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', pago_movil:'Pago Móvil', mixed:'Mixto' };
    
    // Configurar boton de descarga del modal
    const modalBtn = document.getElementById('btn-modal-download-excel');
    modalBtn.onclick = () => downloadSaleExcel(sid, sale);
    
    document.getElementById('sd-number').textContent = sale.sale_number;
    document.getElementById('sd-customer').textContent = sale.customer_name || 'Consumidor Final';
    document.getElementById('sd-user').textContent = sale.user_name || '—';
    document.getElementById('sd-date').textContent = new Date(sale.created_at).toLocaleString('es-VE',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'});
    document.getElementById('sd-method').textContent = methodNames[sale.payment_method] || sale.payment_method;
    
    const statusEl = document.getElementById('sd-status');
    if (sale.status === 'completed') {
      statusEl.className = 'badge badge-green'; statusEl.textContent = 'Completada';
    } else {
      statusEl.className = 'badge badge-red'; statusEl.textContent = 'Anulada';
    }
    
    const tbody = document.getElementById('sd-items');
    tbody.innerHTML = sale.items.map(i => `
      <tr>
        <td class="text-sm font-bold">
          ${i.product_name}
          <div class="text-sm text-muted" style="font-family:monospace;">${i.product_code || ''}</div>
        </td>
        <td class="text-center font-bold">${i.quantity}</td>
        <td class="text-center text-muted">${i.discount_percent > 0 ? i.discount_percent + '%' : '—'}</td>
        <td style="font-family:monospace;color:var(--text-muted);">${fmtUSD(i.price_usd)}</td>
        <td style="font-family:monospace;font-weight:600;">${fmtUSD(i.total_usd)}</td>
        <td style="font-family:monospace;font-weight:600;color:var(--bs-color);font-size:0.85rem;">${fmtBS(i.total_bs)}</td>
      </tr>
    `).join('');
    
    document.getElementById('sd-subtotal').textContent = fmtUSD(sale.subtotal_usd);
    document.getElementById('sd-discount').textContent = sale.discount_usd > 0 ? '-' + fmtUSD(sale.discount_usd) : '$ 0.00';
    document.getElementById('sd-tax').textContent = fmtUSD(sale.tax_usd);
    document.getElementById('sd-total').textContent = fmtUSD(sale.total_usd);
    document.getElementById('sd-total-bs').innerHTML = `Tasa BCV: ${sale.exchange_rate} Bs/$ &mdash; Total Bs: <strong style="color:var(--bs-color);font-size:0.85rem;">${fmtBS(sale.total_bs)}</strong>`;
    
    openModal('sale-detail-modal');
  } catch (e) {
    showToast('Error cargando detalles de venta: ' + e.message, 'error');
  }
}

/* ── CSV Export ─────────────────────────────────────────── */
function downloadSalesCSV() {
  if (!currentSalesData.length) return;

  const methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', pago_movil:'Pago Móvil', mixed:'Mixto' };
  
  // Encabezados
  let csvRows = [
    ['Nro Factura', 'Fecha', 'Cliente', 'Cajero', 'Metodo Pago', 'Productos', 'Total USD', 'Total Bs', 'Impuesto (IVA)', 'Estado'].join(';')
  ];

  // Datos
  currentSalesData.forEach(s => {
    csvRows.push([
      s.sale_number,
      new Date(s.created_at).toLocaleString('es-VE'),
      s.customer_name || 'Consumidor Final',
      s.user_name || '—',
      methodNames[s.payment_method] || s.payment_method,
      parseFloat(s.total_items || 0),
      s.total_usd.toFixed(2),
      s.total_bs.toFixed(2),
      s.tax_usd.toFixed(2),
      s.status === 'completed' ? 'Completada' : 'Anulada'
    ].join(';'));
  });

  // Generar el archivo
  const csvString = csvRows.join('\n');
  const blob = new Blob([csvString], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  
  // Crear link temporal y disparar descarga
  const link = document.createElement('a');
  const from = document.getElementById('date-from').value;
  const to = document.getElementById('date-to').value;
  
  link.href = url;
  link.setAttribute('download', `ventas_${from}_a_${to}.csv`);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/* ── Excel Export (Individual Sale) ───────────────────────── */
async function downloadSaleExcel(sid, saleData) {
  try {
    var sale = saleData || await api.get('/sales/' + sid);
    var methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', pago_movil:'Pago Movil', mixed:'Mixto' };
    
    // Build data rows
    var data = [
      ['SISTEMA POS v1.1 - Recibo de Venta'],
      [],
      ['Factura No', sale.sale_number],
      ['Fecha', new Date(sale.created_at).toLocaleString('es-VE')],
      ['Cajero', sale.user_name || '-'],
      ['Cliente', sale.customer_name || 'Consumidor Final'],
      ['Metodo de Pago', methodNames[sale.payment_method] || sale.payment_method],
      ['Estado', sale.status === 'completed' ? 'Completada' : 'Anulada'],
      ['Tasa BCV', '1$ = ' + parseFloat(sale.exchange_rate).toFixed(2) + ' Bs'],
      [],
      ['--- PRODUCTOS ---'],
      ['Producto', 'Codigo', 'Cantidad', 'Precio Unit $', 'Descuento %', 'Total USD', 'Total Bs']
    ];
    
    sale.items.forEach(function(item) {
      data.push([
        item.product_name,
        item.product_code || '',
        item.quantity,
        parseFloat(item.price_usd).toFixed(2),
        (item.discount_percent || 0) + '%',
        parseFloat(item.total_usd).toFixed(2),
        parseFloat(item.total_bs).toFixed(2)
      ]);
    });
    
    data.push([]);
    data.push(['', '', '', '', 'Subtotal:', parseFloat(sale.subtotal_usd).toFixed(2)]);
    if (parseFloat(sale.discount_usd) > 0) {
      data.push(['', '', '', '', 'Descuento:', '-' + parseFloat(sale.discount_usd).toFixed(2)]);
    }
    data.push(['', '', '', '', 'IVA:', parseFloat(sale.tax_usd).toFixed(2)]);
    data.push(['', '', '', '', 'TOTAL USD:', parseFloat(sale.total_usd).toFixed(2)]);
    data.push(['', '', '', '', 'TOTAL BS:', parseFloat(sale.total_bs).toFixed(2)]);
    
    var wb = XLSX.utils.book_new();
    var ws = XLSX.utils.aoa_to_sheet(data);
    ws['!cols'] = [
      { wch: 25 }, { wch: 15 }, { wch: 10 }, { wch: 14 },
      { wch: 12 }, { wch: 14 }, { wch: 14 }
    ];
    XLSX.utils.book_append_sheet(wb, ws, 'Recibo');
    XLSX.writeFile(wb, 'Recibo_' + sale.sale_number + '.xlsx');
    
  } catch (e) {
    showToast('Error generando Excel: ' + e.message, 'error');
    console.error('Excel Error:', e);
  }
}

/* ── Excel Export (Detailed Sales Report) ────────────────── */
async function downloadDetailedSalesExcel() {
  if (!currentSalesData.length) return;
  
  try {
    showToast('Generando reporte detallado...', 'info', 2000);
    var methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', pago_movil:'Pago Movil', mixed:'Mixto' };
    
    var rows = [
      ['Nro Factura', 'Fecha', 'Cliente', 'Cajero', 'Metodo Pago', 'Producto', 'Codigo', 'Cantidad', 'Precio Unit USD', 'Desc %', 'Total USD', 'Total Bs', 'Estado']
    ];
    
    for (var i = 0; i < currentSalesData.length; i++) {
      var s = currentSalesData[i];
      var fullSale = await api.get('/sales/' + s.id);
      
      fullSale.items.forEach(function(item) {
        rows.push([
          s.sale_number,
          new Date(s.created_at).toLocaleString('es-VE'),
          s.customer_name || 'Consumidor Final',
          s.user_name || '-',
          methodNames[s.payment_method] || s.payment_method,
          item.product_name,
          item.product_code || '',
          item.quantity,
          parseFloat(item.price_usd).toFixed(2),
          (item.discount_percent || 0) + '%',
          parseFloat(item.total_usd).toFixed(2),
          parseFloat(item.total_bs).toFixed(2),
          s.status === 'completed' ? 'Completada' : 'Anulada'
        ]);
      });
    }
    
    var wb = XLSX.utils.book_new();
    var ws = XLSX.utils.aoa_to_sheet(rows);
    ws['!cols'] = [
      { wch: 20 }, { wch: 20 }, { wch: 20 }, { wch: 15 }, { wch: 14 },
      { wch: 25 }, { wch: 12 }, { wch: 10 }, { wch: 14 }, { wch: 10 },
      { wch: 12 }, { wch: 14 }, { wch: 12 }
    ];
    XLSX.utils.book_append_sheet(wb, ws, 'Detalle Ventas');
    
    var from = document.getElementById('date-from').value;
    var to = document.getElementById('date-to').value;
    XLSX.writeFile(wb, 'Reporte_Detallado_' + from + '_a_' + to + '.xlsx');
    showToast('Reporte descargado exitosamente', 'success');
    
  } catch (e) {
    showToast('Error al generar reporte: ' + e.message, 'error');
    console.error('Excel Error:', e);
  }
}

/* ── AI Assistant (Chat) ────────────────────────────────────── */
async function openAssistantModal() {
  document.getElementById('assistant-modal').classList.remove('hidden');
  const chatBox = document.getElementById('ai-chat-messages');
  chatBox.innerHTML = '';

  // Try to load chat history
  let hasHistory = false;
  try {
    const resp = await api.get('/reports/chat/history');
    if (resp.history && resp.history.length > 0) {
      hasHistory = true;
      for (const msg of resp.history) {
        addChatMessage(msg.role, msg.text);
      }
    }
  } catch (e) {
    // Ignore errors loading history
  }

  if (!hasHistory) {
    // Welcome message only if no history
    addChatMessage('assistant', '¡Hola! Soy tu asistente de negocios. Puedo analizar tus ventas, productos, stock, clientes, precios y más. Escribe tu pregunta o elige una de estas opciones:');

    // Quick action buttons
    const quickActions = [
      { label: '📊 Resumen de ventas', msg: 'Resumen de ventas' },
      { label: '📦 Productos más vendidos', msg: 'Qué productos tengo' },
      { label: '⚠️ Stock bajo', msg: 'Qué me falta de stock' },
      { label: '💰 Márgenes de ganancia', msg: 'Qué producto me da más ganancia' },
      { label: '💳 Métodos de pago', msg: 'Cómo me pagan' },
      { label: '🕐 Mejores horarios', msg: 'A qué hora vendo más' }
    ];

    const actionsDiv = document.createElement('div');
    actionsDiv.style = 'display:flex;flex-wrap:wrap;gap:0.35rem;margin-top:0.25rem;';
    quickActions.forEach(a => {
      const btn = document.createElement('button');
      btn.className = 'combo-chip';
      btn.textContent = a.label;
      btn.onclick = () => { document.getElementById('ai-chat-input').value = a.msg; sendChatMessage(); };
      actionsDiv.appendChild(btn);
    });
    chatBox.appendChild(actionsDiv);
  }

  document.getElementById('ai-chat-input').focus();
}

async function clearChatHistory() {
  try {
    await api.delete('/reports/chat/history');
    openAssistantModal();
  } catch (e) {
    showToast('Error al limpiar historial: ' + e.message, 'error');
  }
}

function addChatMessage(role, text) {
  const chatBox = document.getElementById('ai-chat-messages');
  const div = document.createElement('div');

  if (role === 'user') {
    div.style = 'align-self:flex-end;background:var(--violet);color:white;padding:0.6rem 1rem;border-radius:12px 12px 2px 12px;max-width:80%;font-size:0.88rem;line-height:1.45;';
    div.textContent = text;
  } else {
    div.style = 'align-self:flex-start;background:var(--bg-secondary);border:1px solid var(--border);padding:0.75rem 1rem;border-radius:12px 12px 12px 2px;max-width:88%;font-size:0.88rem;line-height:1.55;color:var(--text-primary);';
    // Format markdown-like bold
    div.innerHTML = text
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');
  }

  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
  return div;
}

async function sendChatMessage() {
  const input = document.getElementById('ai-chat-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  addChatMessage('user', text);

  // Remove quick actions after first use
  const actions = document.querySelectorAll('#ai-chat-messages .combo-chip');
  actions.forEach(a => a.parentElement.remove());

  // Typing indicator
  const typing = addChatMessage('assistant', '<i class="fa-solid fa-circle-notch fa-spin" style="color:#3b82f6;"></i> Analizando...');

  try {
    const resp = await api.post('/reports/chat', { message: text });
    typing.remove();
    if (resp.reply) {
      for (const msg of resp.reply) {
        await addChatMessageAnimated(msg.role, msg.text);
      }
    }
  } catch (e) {
    typing.remove();
    addChatMessage('assistant', 'Error: ' + e.message);
  }
}

async function addChatMessageAnimated(role, text) {
  const chatBox = document.getElementById('ai-chat-messages');
  const div = document.createElement('div');

  if (role === 'user') {
    div.style = 'align-self:flex-end;background:var(--violet);color:white;padding:0.6rem 1rem;border-radius:12px 12px 2px 12px;max-width:80%;font-size:0.88rem;line-height:1.45;';
    div.textContent = text;
    chatBox.appendChild(div);
    return;
  }

  div.style = 'align-self:flex-start;background:var(--bg-secondary);border:1px solid var(--border);padding:0.75rem 1rem;border-radius:12px 12px 12px 2px;max-width:88%;font-size:0.88rem;line-height:1.55;color:var(--text-primary);';
  chatBox.appendChild(div);

  // Typewriter effect for markdown
  const formatted = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  await typeWriterHTML(div, formatted, 8);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function typeWriter(element, text, speed = 15) {
  return new Promise(resolve => {
    let i = 0;
    element.innerHTML = '';
    function type() {
      if (i < text.length) {
        element.innerHTML += text.charAt(i);
        i++;
        setTimeout(type, speed);
      } else {
        resolve();
      }
    }
    type();
  });
}

function typeWriterHTML(element, htmlString, speed = 10) {
  return new Promise(resolve => {
    element.innerHTML = '';
    let i = 0;
    // We append character by character, but if it's an HTML tag, we append the whole tag at once
    function type() {
      if (i < htmlString.length) {
        if (htmlString.charAt(i) === '<') {
          let tag = '';
          while (i < htmlString.length && htmlString.charAt(i) !== '>') {
            tag += htmlString.charAt(i);
            i++;
          }
          tag += '>';
          element.innerHTML += tag;
          i++; // move past '>'
        } else {
          element.innerHTML += htmlString.charAt(i);
          i++;
        }
        setTimeout(type, speed);
      } else {
        resolve();
      }
    }
    type();
  });
}
