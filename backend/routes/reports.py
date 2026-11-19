from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
import sys, os, traceback
import threading
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

reports_bp = Blueprint('reports', __name__)

# Máximo de consultas de IA en paralelo (evita agotar threads/conexiones)
_chat_ai_semaphore = threading.Semaphore(2)

# ── Quick action intents (answered directly without AI) ──
QUICK_INTENTS = {'sales', 'products', 'stock', 'customers', 'pricing', 'payments', 'hours', 'combos', 'discounts', 'help'}


def company_clause(claims, prefix=''):
    cid = claims.get('company_id')
    if cid is not None:
        return f' AND {prefix}company_id = %s', [cid]
    return '', []


@reports_bp.route('/summary', methods=['GET'])
@jwt_required()
def summary():
    claims   = get_jwt()
    date_from = request.args.get('from', date.today().isoformat())
    date_to   = request.args.get('to',   date.today().isoformat())
    where, params = company_clause(claims)
    conn  = get_db()

    totals = conn.execute(
        f'''
        SELECT
            COUNT(*) as total_sales,
            SUM(subtotal_usd) as subtotal_usd,
            SUM(tax_usd) as tax_usd,
            SUM(discount_usd) as discount_usd,
            SUM(total_usd) as total_usd,
            SUM(total_bs) as total_bs,
            AVG(total_usd) as avg_ticket_usd
        FROM sales
        WHERE DATE(created_at) BETWEEN %s AND %s AND status = 'completed'{where}
        ''', (date_from, date_to, *params)
    ).fetchone()

    by_method = conn.execute(
        f'''
        SELECT payment_method, COUNT(*) as count, SUM(total_usd) as sum_usd
        FROM sales
        WHERE DATE(created_at) BETWEEN %s AND %s AND status = 'completed'{where}
        GROUP BY payment_method
        ''', (date_from, date_to, *params)
    ).fetchall()

    prod_where, prod_params = company_clause(claims)
    low_stock = conn.execute(
        f'''
        SELECT name, code, stock, min_stock
        FROM products WHERE stock <= min_stock AND active=1{prod_where}
        ORDER BY stock ASC LIMIT 10
        ''', prod_params
    ).fetchall()

    conn.close()
    return jsonify({
        "period": {"from": date_from, "to": date_to},
        "totals": dict(totals) if totals else {},
        "by_payment_method": [dict(r) for r in by_method],
        "low_stock_products": [dict(r) for r in low_stock]
    })


@reports_bp.route('/top-products', methods=['GET'])
@jwt_required()
def top_products():
    claims   = get_jwt()
    date_from = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    date_to   = request.args.get('to',   date.today().isoformat())
    limit     = int(request.args.get('limit', 10))
    where, params = company_clause(claims)
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT si.product_name, si.product_id,
               SUM(si.quantity) as total_qty,
               SUM(si.total_usd) as total_usd,
               SUM(si.total_bs)  as total_bs,
               COUNT(DISTINCT si.sale_id) as sale_count
        FROM sale_items si
        JOIN sales s ON si.sale_id = s.id
        WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status = 'completed'{where}
        GROUP BY si.product_id, si.product_name
        ORDER BY total_qty DESC
        LIMIT %s
        ''', (date_from, date_to, *params, limit)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@reports_bp.route('/daily-chart', methods=['GET'])
@jwt_required()
def daily_chart():
    from flask import jsonify
    from datetime import date, timedelta, datetime

    claims   = get_jwt()
    date_from = request.args.get('from')
    date_to   = request.args.get('to')
    if date_from is None or date_to is None:
        days = int(request.args.get('days', 7))
        date_from = (date.today() - timedelta(days=days - 1)).isoformat()
        date_to   = date.today().isoformat()
    where, params = company_clause(claims)
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT DATE(created_at) as day,
               COUNT(*) as sales_count,
               COALESCE(SUM(total_usd), 0) as total_usd,
               COALESCE(SUM(total_bs), 0) as total_bs
        FROM sales
        WHERE DATE(created_at) BETWEEN %s AND %s AND status = 'completed'{where}
        GROUP BY day ORDER BY day
        ''', (date_from, date_to, *params)
    ).fetchall()
    conn.close()

    # Build a dict from DB results
    data_map = {}
    for r in rows:
        day_str = r['day'].isoformat() if hasattr(r['day'], 'isoformat') else str(r['day'])
        data_map[day_str] = {
            'day': day_str,
            'sales_count': r['sales_count'],
            'total_usd': float(r['total_usd']),
            'total_bs': float(r['total_bs'])
        }

    # Fill all days in range with zeros for missing days
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    result = []
    current = d_from
    while current <= d_to:
        day_str = current.isoformat()
        if day_str in data_map:
            result.append(data_map[day_str])
        else:
            result.append({
                'day': day_str,
                'sales_count': 0,
                'total_usd': 0.0,
                'total_bs': 0.0
            })
        current += timedelta(days=1)

    return jsonify(result)


@reports_bp.route('/inventory', methods=['GET'])
@jwt_required()
def inventory():
    claims = get_jwt()
    where, params = company_clause(claims, 'p.')
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT p.id, p.name, p.code, p.stock, p.min_stock, p.price_usd, p.cost_usd,
               c.name as category, p.active,
               (p.stock * p.cost_usd) as inventory_value_usd
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE 1=1{where}
        ORDER BY p.name
        ''', params
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@reports_bp.route('/hourly', methods=['GET'])
@jwt_required()
def hourly():
    claims     = get_jwt()
    query_date = request.args.get('date', date.today().isoformat())
    where, params = company_clause(claims)
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT LPAD(HOUR(created_at), 2, '0') as hour,
               COUNT(*) as count,
               SUM(total_usd) as total_usd
        FROM sales
        WHERE DATE(created_at) = %s AND status = 'completed'{where}
        GROUP BY hour ORDER BY hour
        ''', (query_date, *params)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@reports_bp.route('/insights', methods=['GET'])
@jwt_required()
def get_insights():
    claims = get_jwt()
    where, params = company_clause(claims)
    prod_where, prod_params = company_clause(claims, 'p.')
    sales_where, sales_params = company_clause(claims, 's.')
    insights = []
    conn = None

    try:
        conn = get_db()
        # 1. Sales Trend (Last 7 days vs Previous 7 days)
        today = date.today()
        date_7 = (today - timedelta(days=7)).isoformat()
        date_14 = (today - timedelta(days=14)).isoformat()
        today_str = today.isoformat()

        current_week = conn.execute(f"SELECT COALESCE(SUM(total_usd),0) as total FROM sales WHERE DATE(created_at) BETWEEN %s AND %s AND status='completed'{where}", (date_7, today_str, *params)).fetchone()['total']
        prev_week = conn.execute(f"SELECT COALESCE(SUM(total_usd),0) as total FROM sales WHERE DATE(created_at) BETWEEN %s AND %s AND status='completed'{where}", (date_14, date_7, *params)).fetchone()['total']

        if current_week > prev_week * 1.05:
            inc = ((current_week - prev_week) / prev_week) * 100 if prev_week > 0 else 100
            insights.append({"type": "success", "title": "📈 Crecimiento de Ventas", "message": f"¡Excelente trabajo! Tus ventas de los últimos 7 días subieron un {inc:.1f}% (${current_week:.2f}) en comparación con la semana anterior."})
        elif current_week < prev_week * 0.95:
            dec = ((prev_week - current_week) / prev_week) * 100 if prev_week > 0 else 0
            insights.append({"type": "warning", "title": "📉 Caída de Ventas", "message": f"Tus ventas cayeron un {dec:.1f}% esta semana. Te sugiero lanzar alguna oferta o combo para atraer más clientes estos días."})

        # 2. Dead Stock (High stock, no sales in 30 days)
        date_30 = (today - timedelta(days=30)).isoformat()
        dead_stock = conn.execute(f'''
            SELECT p.name, p.stock, p.cost_usd
            FROM products p
            WHERE p.stock > 10 AND p.active=1 AND p.is_combo=0 {prod_where}
            AND p.id NOT IN (
                SELECT si.product_id FROM sale_items si JOIN sales s ON si.sale_id = s.id 
                WHERE DATE(s.created_at) > %s {sales_where}
            )
            LIMIT 3
        ''', (*prod_params, date_30, *sales_params)).fetchall()

        if dead_stock:
            names = ", ".join([f"**{r['name']}** ({r['stock']} unds)" for r in dead_stock])
            insights.append({"type": "danger", "title": "🧊 Dinero Estancado", "message": f"Tienes productos con buen stock que no se han vendido en los últimos 30 días: {names}. Sugerencia: Aplícales un descuento del 10% para recuperar tu inversión."})

        # 3. Combo Suggestion (High margin, high stock, low sales)
        combo_sugg = conn.execute(f'''
            SELECT p.name, p.stock, p.margen_unidad_porcentaje
            FROM products p
            WHERE p.margen_unidad_porcentaje > 30 AND p.stock > 15 AND p.active=1 AND p.is_combo=0 {prod_where}
            ORDER BY p.stock DESC LIMIT 1
        ''', prod_params).fetchone()

        if combo_sugg:
            insights.append({"type": "info", "title": "🍔 Sugerencia de Combo", "message": f"El producto **{combo_sugg['name']}** tiene un alto margen de ganancia ({combo_sugg['margen_unidad_porcentaje']}%) y tienes {combo_sugg['stock']} unidades. Agrupalo con tu producto más vendido para multiplicar tus ganancias."})

        # 4. Profit Improvement (Top seller with low margin)
        top_low_margin = conn.execute(f'''
            SELECT si.product_name, SUM(si.quantity) as qty, p.margen_unidad_porcentaje
            FROM sale_items si JOIN sales s ON si.sale_id = s.id JOIN products p ON si.product_id = p.id
            WHERE DATE(s.created_at) > %s AND p.margen_unidad_porcentaje > 0 AND p.margen_unidad_porcentaje < 15 AND p.active=1 {sales_where}
            GROUP BY si.product_id, si.product_name, p.margen_unidad_porcentaje
            ORDER BY qty DESC LIMIT 1
        ''', (date_30, *sales_params)).fetchone()

        if top_low_margin:
            insights.append({"type": "warning", "title": "💰 Oportunidad de Rentabilidad", "message": f"Tu producto **{top_low_margin['product_name']}** se vende muchísimo ({int(top_low_margin['qty'])} unds este mes) pero su margen es solo del {top_low_margin['margen_unidad_porcentaje']}%. Si le subes el precio un 5%, ganarás mucho más sin afectar las ventas."})

    except Exception as e:
        traceback.print_exc()
        insights.append({"type": "danger", "title": "⚠️ Error de Análisis", "message": f"No pude completar el análisis: {str(e)}"})
    finally:
        if conn:
            conn.close()
    
    if not insights:
        insights.append({"type": "success", "title": "✅ Todo en orden", "message": "Tu negocio va por buen camino. Sigue así y revisa tus métricas regularmente."})
        
    return jsonify(insights)


# ═══════════════════════════════════════════════
# CHAT ASSISTANT — Intent Detection + DB Queries
# ═══════════════════════════════════════════════

def _detect_intent(text):
    t = text.lower().strip()
    intents = []

    if any(w in t for w in ['venta', 'vend', 'factur', 'ingreso', 'cobr', 'dinero', 'gananci', 'resumen', 'como van', 'qué vend', 'que vend', 'total']):
        intents.append('sales')
    if any(w in t for w in ['producto', 'articulo', 'qué tengo', 'que tengo', 'inventario', 'catálogo', 'catalogo']):
        intents.append('products')
    if any(w in t for w in ['stock', 'existencia', 'falta', 'agotado', 'sin stock', 'pocas unidades', 'reponer', 'desabastecido']):
        intents.append('stock')
    if any(w in t for w in ['cliente', 'quien compra', 'quién compra', 'mejor cliente', 'top cliente', 'fiado', 'deuda', 'deben']):
        intents.append('customers')
    if any(w in t for w in ['precio', 'margen', 'margenes', 'rentabilidad', 'utilidad', 'subir precio', 'bajar precio', 'ganancia']):
        intents.append('pricing')
    if any(w in t for w in ['pago', 'pagan', 'metodo', 'método', 'efectivo', 'tarjeta', 'pago movil', 'pago móvil', 'como pagan', 'cómo pagan']):
        intents.append('payments')
    if any(w in t for w in ['hora', 'horario', 'horas', 'cuando vendo', 'cuándo vendo', 'mejor hora', 'turno', 'momento']):
        intents.append('hours')
    if any(w in t for w in ['combo', 'promocion', 'promoción', 'oferta', 'agrupar', 'empaquetar']):
        intents.append('combos')
    if any(w in t for w in ['descuento', 'rebaja', 'rebajas', 'bajar']):
        intents.append('discounts')
    if any(w in t for w in ['ayuda', 'help', 'que puedes', 'qué puedes', 'opciones', 'comandos', 'menu', 'emenú', 'que sabes', 'qué sabes']):
        intents.append('help')

    return intents if intents else ['general']


def _build_response(conn, claims, intents, user_text):
    where, params = company_clause(claims)
    prod_where, prod_params = company_clause(claims, 'p.')
    sales_where, sales_params = company_clause(claims, 's.')
    today = date.today()
    today_str = today.isoformat()
    date_7 = (today - timedelta(days=7)).isoformat()
    date_30 = (today - timedelta(days=30)).isoformat()
    date_14 = (today - timedelta(days=14)).isoformat()
    messages = []

    if 'help' in intents:
        messages.append({
            "role": "assistant",
            "text": "Soy tu asistente de negocios. Puedes preguntarme sobre:\n\n"
                    "- **Ventas**: \"¿Cómo van las ventas?\", \"¿Qué vendí hoy?\"\n"
                    "- **Productos**: \"¿Qué productos tengo?\", \"¿Cuál es el más vendido?\"\n"
                    "- **Stock**: \"¿Qué me falta?\", \"¿Cuál está por agotarse?\"\n"
                    "- **Clientes**: \"¿Quiénes son mis mejores clientes?\", \"¿Quién debe?\"\n"
                    "- **Precios**: \"¿Qué producto me da más ganancia?\", \"¿Debería subir precios?\"\n"
                    "- **Pagos**: \"¿Cómo me pagan?\", \"¿Cuánto por pago móvil?\"\n"
                    "- **Horarios**: \"¿A qué hora vendo más?\"\n"
                    "- **Combos**: \"¿Debería hacer combos?\"\n"
                    "- **Descuentos**: \"¿Debería hacer descuentos?\"\n\n"
                    "Escribe tu pregunta y te respondo con datos reales de tu negocio."
        })
        return messages

    # ── SALES ──
    if 'sales' in intents:
        today_sales = conn.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(total_usd),0) as total_usd, COALESCE(SUM(total_bs),0) as total_bs FROM sales WHERE DATE(created_at) = %s AND status='completed'{where}", (today_str, *params)).fetchone()
        week_sales = conn.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(total_usd),0) as total_usd FROM sales WHERE DATE(created_at) BETWEEN %s AND %s AND status='completed'{where}", (date_7, today_str, *params)).fetchone()
        month_sales = conn.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(total_usd),0) as total_usd FROM sales WHERE DATE(created_at) BETWEEN %s AND %s AND status='completed'{where}", (date_30, today_str, *params)).fetchone()
        avg_ticket = conn.execute(f"SELECT COALESCE(AVG(total_usd),0) as avg FROM sales WHERE DATE(created_at) BETWEEN %s AND %s AND status='completed'{where}", (date_30, today_str, *params)).fetchone()

        text = f"**Resumen de Ventas**\n\n"
        text += f"Hoy: **{today_sales['cnt']}** ventas por **${today_sales['total_usd']:.2f}** ({today_sales['total_bs']:.2f} Bs)\n"
        text += f"Últimos 7 días: **{week_sales['cnt']}** ventas por **${week_sales['total_usd']:.2f}**\n"
        text += f"Últimos 30 días: **{month_sales['cnt']}** ventas por **${month_sales['total_usd']:.2f}**\n"
        text += f"Ticket promedio: **${avg_ticket['avg']:.2f}**"

        if today_sales['cnt'] > 0:
            prev_day = conn.execute(f"SELECT COALESCE(SUM(total_usd),0) as total FROM sales WHERE DATE(created_at) = DATE_SUB(%s, INTERVAL 1 DAY) AND status='completed'{where}", (today_str, *params)).fetchone()
            if prev_day and prev_day['total'] > 0:
                pct = ((today_sales['total_usd'] - prev_day['total']) / prev_day['total']) * 100
                if pct > 5:
                    text += f"\n\n📈 ¡Vas {pct:.0f}% mejor que ayer! Sigue así."
                elif pct < -5:
                    text += f"\n\n📉 Estás {abs(pct):.0f}% por debajo de ayer. Considera una promoción."

        messages.append({"role": "assistant", "text": text})

    # ── PRODUCTS ──
    if 'products' in intents:
        total_prods = conn.execute(f"SELECT COUNT(*) as cnt FROM products p WHERE p.active=1{prod_where}", prod_params).fetchone()
        top = conn.execute(f'''
            SELECT si.product_name, SUM(si.quantity) as qty
            FROM sale_items si JOIN sales s ON si.sale_id = s.id
            WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}
            GROUP BY si.product_id, si.product_name ORDER BY qty DESC LIMIT 5
        ''', (date_30, today_str, *sales_params)).fetchall()

        text = f"**Tienes {total_prods['cnt']} productos activos.**\n\n"
        if top:
            text += "Top 5 más vendidos (30 días):\n"
            for i, p in enumerate(top, 1):
                text += f"{i}. **{p['product_name']}** — {int(p['qty'])} unidades\n"
        else:
            text += "Aún no tienes ventas registradas este mes."
        messages.append({"role": "assistant", "text": text})

    # ── STOCK ──
    if 'stock' in intents:
        low = conn.execute(f"SELECT p.name, p.stock, p.min_stock FROM products p WHERE p.stock <= p.min_stock AND p.active=1{prod_where} ORDER BY p.stock ASC LIMIT 5", prod_params).fetchall()
        zero = conn.execute(f"SELECT COUNT(*) as cnt FROM products p WHERE p.stock = 0 AND p.active=1{prod_where}", prod_params).fetchone()
        dead = conn.execute(f'''
            SELECT p.name, p.stock FROM products p
            WHERE p.stock > 10 AND p.active=1 AND p.is_combo=0{prod_where}
            AND p.id NOT IN (SELECT si.product_id FROM sale_items si JOIN sales s ON si.sale_id = s.id WHERE DATE(s.created_at) > %s{sales_where})
            LIMIT 3
        ''', (*prod_params, date_30, *sales_params)).fetchall()

        text = "**Análisis de Inventario**\n\n"
        if zero['cnt'] > 0:
            text += f"⚠️ **{zero['cnt']} productos sin stock.**\n"
        if low:
            text += "Stock bajo:\n"
            for p in low:
                text += f"- **{p['name']}**: {p['stock']} unds (mínimo: {p['min_stock']})\n"
        if dead:
            text += "\n🧊 Productos sin movimiento (30 días):\n"
            for p in dead:
                text += f"- **{p['name']}**: {p['stock']} unds estancadas\n"
            text += "Sugerencia: Ofréceles descuento para recuperar la inversión."
        if not low and not dead and zero['cnt'] == 0:
            text += "✅ Todo el stock está bien."
        messages.append({"role": "assistant", "text": text})

    # ── CUSTOMERS ──
    if 'customers' in intents:
        top_cust = conn.execute(f'''
            SELECT s.customer_name, SUM(s.total_usd) as total, COUNT(*) as visits
            FROM sales s WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}
            GROUP BY s.customer_name ORDER BY total DESC LIMIT 5
        ''', (date_30, today_str, *sales_params)).fetchall()

        debts = conn.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(balance_usd),0) as total FROM debts WHERE status='Pendiente'{where}", params).fetchone()

        text = "**Análisis de Clientes**\n\n"
        if top_cust:
            text += "Top 5 clientes (30 días):\n"
            for i, c in enumerate(top_cust, 1):
                text += f"{i}. **{c['customer_name']}** — ${c['total']:.2f} ({c['visits']} compras)\n"
        else:
            text += "No hay datos de clientes con nombre en este período.\n"

        if debts and debts['cnt'] > 0:
            text += f"\n💳 Tienes **{debts['cnt']}** fiados pendientes por **${debts['total']:.2f}**. Considera enviar recordatorios."
        messages.append({"role": "assistant", "text": text})

    # ── PRICING ──
    if 'pricing' in intents:
        best_margin = conn.execute(f'''
            SELECT p.name, p.margen_unidad_porcentaje, p.price_usd, p.cost_usd, p.stock
            FROM products p WHERE p.margen_unidad_porcentaje > 0 AND p.active=1{prod_where}
            ORDER BY p.margen_unidad_porcentaje DESC LIMIT 3
        ''', prod_params).fetchall()

        low_margin = conn.execute(f'''
            SELECT si.product_name, SUM(si.quantity) as qty, p.margen_unidad_porcentaje, p.price_usd
            FROM sale_items si JOIN sales s ON si.sale_id = s.id JOIN products p ON si.product_id = p.id
            WHERE DATE(s.created_at) > %s AND p.margen_unidad_porcentaje > 0 AND p.margen_unidad_porcentaje < 15 AND p.active=1{sales_where}
            GROUP BY si.product_id, si.product_name, p.margen_unidad_porcentaje, p.price_usd
            ORDER BY qty DESC LIMIT 3
        ''', (date_30, *sales_params)).fetchall()

        text = "**Análisis de Precios y Márgenes**\n\n"
        if best_margin:
            text += "Mejores márgenes de ganancia:\n"
            for p in best_margin:
                text += f"- **{p['name']}**: {p['margen_unidad_porcentaje']}% (precio: ${p['price_usd']:.2f})\n"
        if low_margin:
            text += "\n⚠️ Productos vendidos con margen bajo (<15%):\n"
            for p in low_margin:
                suggested = round(float(p['price_usd']) * 1.05, 2)
                text += f"- **{p['product_name']}**: margen {p['margen_unidad_porcentaje']}%, {int(p['qty'])} unds/mes. Si lo subes a ${suggested:.2f}, ganarías más sin perder ventas.\n"
        if not best_margin and not low_margin:
            text += "No hay datos de márgenes configurados en tus productos."
        messages.append({"role": "assistant", "text": text})

    # ── PAYMENTS ──
    if 'payments' in intents:
        methods = conn.execute(f'''
            SELECT payment_method, COUNT(*) as cnt, SUM(total_usd) as total
            FROM sales s WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}
            GROUP BY payment_method ORDER BY total DESC
        ''', (date_30, today_str, *sales_params)).fetchall()

        labels = {'cash_usd': 'Efectivo $', 'cash_bs': 'Efectivo Bs', 'card': 'Tarjeta', 'pago_movil': 'Pago Móvil', 'mixed': 'Mixto'}
        text = "**Métodos de Pago (30 días)**\n\n"
        if methods:
            for m in methods:
                pct = (m['total'] / sum(x['total'] for x in methods)) * 100 if sum(x['total'] for x in methods) > 0 else 0
                text += f"- **{labels.get(m['payment_method'], m['payment_method'])}**: ${m['total']:.2f} ({m['cnt']} ventas, {pct:.0f}%)\n"
        else:
            text += "No hay ventas registradas este mes."
        messages.append({"role": "assistant", "text": text})

    # ── HOURS ──
    if 'hours' in intents:
        hours = conn.execute(f'''
            SELECT LPAD(HOUR(s.created_at),2,'0') as hour, COUNT(*) as cnt, SUM(s.total_usd) as total
            FROM sales s WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}
            GROUP BY hour ORDER BY total DESC LIMIT 3
        ''', (date_30, today_str, *sales_params)).fetchall()

        quiet = conn.execute(f'''
            SELECT LPAD(HOUR(s.created_at),2,'0') as hour, COUNT(*) as cnt
            FROM sales s WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}
            GROUP BY hour ORDER BY cnt ASC LIMIT 3
        ''', (date_30, today_str, *sales_params)).fetchall()

        text = "**Análisis de Horarios**\n\n"
        if hours:
            text += "Tus mejores horas (más ventas):\n"
            for h in hours:
                text += f"- **{h['hour']}:00** — {h['cnt']} ventas, ${h['total']:.2f}\n"
        if quiet:
            text += "\nHoras más tranquilas:\n"
            for h in quiet:
                text += f"- **{h['hour']}:00** — {h['cnt']} ventas\n"
            text += "\nSugerencia: Ofrece promociones especiales en esas horas para atraer clientes."
        if not hours:
            text += "No hay suficientes datos de horarios aún."
        messages.append({"role": "assistant", "text": text})

    # ── COMBOS ──
    if 'combos' in intents:
        combos = conn.execute(f"SELECT COUNT(*) as cnt FROM products p WHERE p.is_combo=1 AND p.active=1{prod_where}", prod_params).fetchone()
        high_margin = conn.execute(f'''
            SELECT p.name, p.margen_unidad_porcentaje, p.stock
            FROM products p WHERE p.margen_unidad_porcentaje > 30 AND p.stock > 10 AND p.active=1 AND p.is_combo=0{prod_where}
            ORDER BY p.margen_unidad_porcentaje DESC LIMIT 3
        ''', prod_params).fetchall()

        text = "**Sugerencia de Combos**\n\n"
        if combos and combos['cnt'] > 0:
            text += f"Ya tienes **{combos['cnt']}** combos creados.\n\n"
        if high_margin:
            text += "Productos con buen margen y stock para armar combos:\n"
            for p in high_margin:
                text += f"- **{p['name']}**: margen {p['margen_unidad_porcentaje']}%, {p['stock']} unds\n"
            text += "\nCombina uno de estos con un producto popular para crear un combo atractivo."
        else:
            text += "No hay productos que cumplan los criterios ideales para combos (margen >30%, stock >10)."
        messages.append({"role": "assistant", "text": text})

    # ── DISCOUNTS ──
    if 'discounts' in intents:
        dead = conn.execute(f'''
            SELECT p.name, p.stock, p.price_usd, p.cost_usd
            FROM products p WHERE p.stock > 15 AND p.active=1 AND p.is_combo=0{prod_where}
            AND p.id NOT IN (SELECT si.product_id FROM sale_items si JOIN sales s ON si.sale_id = s.id WHERE DATE(s.created_at) > %s{sales_where})
            LIMIT 5
        ''', (*prod_params, date_30, *sales_params)).fetchall()

        text = "**Análisis de Descuentos**\n\n"
        if dead:
            text += "Productos que podrías poner en descuento:\n"
            for p in dead:
                loss = p['stock'] * float(p.get('cost_usd', 0) or 0)
                text += f"- **{p['name']}**: {p['stock']} unds sin vender. Valor invertido: ~${loss:.2f}\n"
            text += "\nUn descuento del 10-15% podría ayudarte a recuperar parte de esa inversión."
        else:
            text += "No tienes productos estancados que necesiten descuento. ¡Todo bien!"
        messages.append({"role": "assistant", "text": text})

    # ── GENERAL (fallback) ──
    if 'general' in intents and not messages:
        sales_today = conn.execute(f"SELECT COUNT(*) as cnt, COALESCE(SUM(s.total_usd),0) as total FROM sales s WHERE DATE(s.created_at) = %s AND s.status='completed'{where}", (today_str, *params)).fetchone()
        low_stock = conn.execute(f"SELECT COUNT(*) as cnt FROM products p WHERE p.stock <= p.min_stock AND p.active=1{prod_where}", prod_params).fetchone()
        pending_debts = conn.execute(f"SELECT COUNT(*) as cnt FROM debts d WHERE d.status='Pendiente'{where}", params).fetchone()

        text = f"**Resumen Rápido de Hoy**\n\n"
        text += f"- Ventas hoy: **{sales_today['cnt']}** por **${sales_today['total']:.2f}**\n"
        text += f"- Productos con stock bajo: **{low_stock['cnt']}**\n"
        text += f"- Fiados pendientes: **{pending_debts['cnt']}**\n\n"
        text += "Pregúntame sobre ventas, productos, stock, clientes, precios, pagos, horarios, combos o descuentos."
        messages.append({"role": "assistant", "text": text})

    return messages


# ── Chat history helpers ──────────────────────────────────────

def _save_chat(conn, claims, user_text, messages, intents=None):
    """Save user message and assistant replies to chat_history."""
    cid = claims.get('company_id')
    uid = claims.get('user_id', claims.get('sub'))
    if not uid:
        return
    try:
        conn.execute(
            "INSERT INTO chat_history (user_id, company_id, role, message) VALUES (%s, %s, 'user', %s)",
            (uid, cid, user_text)
        )
        for msg in messages:
            conn.execute(
                "INSERT INTO chat_history (user_id, company_id, role, message) VALUES (%s, %s, 'assistant', %s)",
                (uid, cid, msg['text'])
            )
    except Exception:
        traceback.print_exc()


def _load_chat_history(conn, claims, limit=20):
    """Load last N messages for the user (ordered oldest first for Gemini context)."""
    cid = claims.get('company_id')
    uid = claims.get('user_id', claims.get('sub'))
    if not uid:
        return []
    try:
        rows = conn.execute(
            "SELECT role, message as text FROM chat_history "
            "WHERE user_id = %s AND (company_id = %s OR (company_id IS NULL AND %s IS NULL)) "
            "ORDER BY id DESC LIMIT %s",
            (uid, cid, cid, limit)
        ).fetchall()
        return list(reversed([dict(r) for r in rows]))
    except Exception:
        return []


def _clear_chat_history(conn, claims):
    """Clear all chat history for the user."""
    cid = claims.get('company_id')
    uid = claims.get('user_id', claims.get('sub'))
    if not uid:
        return
    try:
        conn.execute(
            "DELETE FROM chat_history WHERE user_id = %s AND (company_id = %s OR (company_id IS NULL AND %s IS NULL))",
            (uid, cid, cid)
        )
    except Exception:
        traceback.print_exc()


# ── Chat endpoints ────────────────────────────────────────────

@reports_bp.route('/chat', methods=['POST'])
@jwt_required()
def chat():
    claims = get_jwt()
    data = request.get_json()
    user_text = data.get('message', '').strip()

    if not user_text:
        return jsonify({"error": "Escribe un mensaje"}), 400

    intents = _detect_intent(user_text)
    conn = get_db()
    try:
        # Quick actions → direct response (no Gemini)
        if intents and intents != ['general']:
            messages = _build_response(conn, claims, intents, user_text)
            _save_chat(conn, claims, user_text, messages, intents)
            return jsonify({"reply": messages})

        # Open-ended questions -> AI (OpenRouter)
        from services.gemini_service import get_business_context, build_system_prompt, send_to_ai

        # Máximo 2 consultas de IA en paralelo: evita agotar threads/conexiones
        if not _chat_ai_semaphore.acquire(blocking=False):
            messages = [{"role": "assistant",
                         "text": "Ya estoy respondiendo otra pregunta. Intenta de nuevo en unos segundos."}]
            _save_chat(conn, claims, user_text, messages)
            return jsonify({"reply": messages, "busy": True}), 429
        try:
            history = _load_chat_history(conn, claims, limit=20)
            context = get_business_context(conn, claims)
            system_prompt = build_system_prompt(context)
            # Liberar la conexión del pool antes de la llamada lenta a la IA
            conn.close()
            conn = None
            reply_text = send_to_ai(system_prompt, history, user_text)
            messages = [{"role": "assistant", "text": reply_text}]
            conn = get_db()
            _save_chat(conn, claims, user_text, messages, ['gemini'])
            return jsonify({"reply": messages})
        finally:
            _chat_ai_semaphore.release()

    except ValueError as e:
        if conn is None:
            conn = get_db()
        messages = [{"role": "assistant", "text": str(e)}]
        _save_chat(conn, claims, user_text, messages)
        return jsonify({"reply": messages})
    except Exception as e:
        traceback.print_exc()
        if conn is None:
            conn = get_db()
        messages = [{"role": "assistant", "text": f"Ocurrió un error al procesar tu pregunta: {str(e)}"}]
        _save_chat(conn, claims, user_text, messages)
        return jsonify({"reply": messages})
    finally:
        if conn is not None:
            conn.close()


@reports_bp.route('/chat/history', methods=['GET'])
@jwt_required()
def chat_history():
    """Return last 50 messages for the current user."""
    claims = get_jwt()
    conn = get_db()
    try:
        history = _load_chat_history(conn, claims, limit=50)
        return jsonify({"history": history})
    finally:
        conn.close()


@reports_bp.route('/chat/history', methods=['DELETE'])
@jwt_required()
def clear_chat_history():
    """Clear all chat history for the current user."""
    claims = get_jwt()
    conn = get_db()
    try:
        _clear_chat_history(conn, claims)
        return jsonify({"ok": True, "message": "Historial limpiado"})
    finally:
        conn.close()
