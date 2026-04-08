/* reports.js */
requireAuth();
initHeader();

let dailyChart   = null;
let paymentChart = null;
let currentSalesData = []; // Para almacenar las ventas actuales para exportación

// Set default date range (today)
const today = new Date().toISOString().split('T')[0];
document.getElementById('date-from').value = today;
document.getElementById('date-to').value   = today;

async function loadReports() {
  const from = document.getElementById('date-from').value;
  const to   = document.getElementById('date-to').value;

  try {
    const [summary, topProds, daily, sales] = await Promise.all([
      api.get(`/reports/summary?from=${from}&to=${to}`),
      api.get(`/reports/top-products?from=${from}&to=${to}&limit=8`),
      api.get(`/reports/daily-chart?days=14`),
      api.get(`/sales?from=${from}&to=${to}&limit=100`)
    ]);

    renderSummary(summary);
    renderTopProducts(topProds);
    renderLowStock(summary.low_stock_products || []);
    renderDailyChart(daily);
    renderPaymentChart(summary.by_payment_method || []);
    
    currentSalesData = sales; // Guardar para exportar
    renderSalesHistory(sales);
    
    // Activar/desactivar botón de descarga
    document.getElementById('btn-download-sales').disabled = sales.length === 0;
  } catch (e) {
    showToast(e.message, 'error');
  }
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
    <div style="display:flex;align-items:center;gap:0.75rem;padding:0.5rem 0;border-bottom:1px solid var(--border);">
      <div style="width:22px;height:22px;border-radius:50%;background:var(--violet-glow);color:var(--violet-light);
        font-size:0.72rem;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
        ${i+1}
      </div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:0.82rem;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.product_name}</div>
        <div style="display:flex;align-items:center;gap:0.5rem;margin-top:2px;">
          <div style="flex:1;height:4px;background:var(--border);border-radius:4px;">
            <div style="height:100%;border-radius:4px;background:var(--violet);width:${(p.total_qty/maxQty)*100}%;"></div>
          </div>
        </div>
      </div>
      <div style="text-align:right;flex-shrink:0;">
        <div style="font-size:0.78rem;font-weight:700;color:var(--text-primary);">${p.total_qty} uds</div>
        <div style="font-size:0.7rem;color:var(--usd-color);">${fmtUSD(p.total_usd)}</div>
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
    <div style="display:flex;justify-content:space-between;align-items:center;padding:0.5rem 0;border-bottom:1px solid var(--border);">
      <div>
        <div style="font-size:0.82rem;font-weight:600;">${p.name}</div>
        <div style="font-size:0.7rem;color:var(--text-muted);">${p.code || 'Sin código'} &bull; Mínimo: ${p.min_stock}</div>
      </div>
      <span class="badge ${p.stock === 0 ? 'badge-red' : 'badge-amber'}">
        ${p.stock === 0 ? 'Sin stock' : `Stock: ${p.stock}`}
      </span>
    </div>
  `).join('');
}

/* ── Daily Chart ──────────────────────────────────────────── */
function renderDailyChart(data) {
  const ctx   = document.getElementById('daily-chart').getContext('2d');
  const labels = data.map(d => new Date(d.day + 'T00:00:00').toLocaleDateString('es-VE', { weekday:'short', day:'numeric', month:'short' }));
  const values = data.map(d => parseFloat(d.total_usd) || 0);

  if (dailyChart) dailyChart.destroy();
  dailyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Ventas ($)',
        data: values,
        borderColor: '#7c3aed',
        backgroundColor: 'rgba(124,58,237,0.12)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointBackgroundColor: '#8b5cf6',
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#94a3b8', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => `$ ${ctx.parsed.y.toFixed(2)}`
          }
        }
      },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#2a3352' } },
        y: { ticks: { color: '#64748b', font: { size: 10 }, callback: v => `$${v}` }, grid: { color: '#2a3352' } }
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
    mixed:    'Mixto'
  };
  const labels  = data.map(d => methodLabels[d.payment_method] || d.payment_method);
  const values  = data.map(d => parseFloat(d.sum_usd) || 0);
  const colors  = ['#34d399','#60a5fa','#a78bfa','#fbbf24'];

  if (paymentChart) paymentChart.destroy();
  if (!data.length) {
    ctx.clearRect(0,0,300,240);
    return;
  }
  paymentChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderColor: '#1a1f35', borderWidth: 2 }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#94a3b8', font: { size: 11 }, padding: 12 } },
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
  const methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', mixed:'Mixto' };
  tbody.innerHTML = sales.map(s => `
    <tr>
      <td style="font-family:monospace;font-size:0.78rem;color:var(--violet-light);">${s.sale_number}</td>
      <td style="font-size:0.8rem;">${s.customer_name || 'Consumidor Final'}</td>
      <td style="font-size:0.8rem;color:var(--text-muted);">${s.user_name || '—'}</td>
      <td><span class="badge badge-blue">${methodNames[s.payment_method] || s.payment_method}</span></td>
      <td><span class="price-usd">${parseFloat(s.total_usd).toFixed(2)}</span></td>
      <td><span class="price-bs" style="font-size:0.72rem;">${parseFloat(s.total_bs).toLocaleString('es-VE',{minimumFractionDigits:2})}</span></td>
      <td><span class="badge ${s.status === 'completed' ? 'badge-green' : 'badge-red'}">${s.status === 'completed' ? 'Completada' : 'Anulada'}</span></td>
      <td style="font-size:0.75rem;color:var(--text-muted);">${new Date(s.created_at).toLocaleString('es-VE',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}</td>
    </tr>
  `).join('');
}

/* ── CSV Export ─────────────────────────────────────────── */
function downloadSalesCSV() {
  if (!currentSalesData.length) return;

  const methodNames = { cash_usd:'Efectivo $', cash_bs:'Efectivo Bs', card:'Tarjeta', mixed:'Mixto' };
  
  // Encabezados
  let csvRows = [
    ['Nro Factura', 'Fecha', 'Cliente', 'Cajero', 'Metodo Pago', 'Total USD', 'Total Bs', 'Impuesto (IVA)', 'Estado'].join(';')
  ];

  // Datos
  currentSalesData.forEach(s => {
    csvRows.push([
      s.sale_number,
      new Date(s.created_at).toLocaleString('es-VE'),
      s.customer_name || 'Consumidor Final',
      s.user_name || '—',
      methodNames[s.payment_method] || s.payment_method,
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
