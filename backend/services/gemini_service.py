import os
import json
import requests
from datetime import date, timedelta
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from routes.reports import company_clause


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"


def _get_api_key():
    return os.getenv('OPENROUTER_API_KEY', '').strip()


def _get_model():
    return os.getenv('OPENROUTER_MODEL', DEFAULT_MODEL).strip()


def get_business_context(conn, claims):
    where, params = company_clause(claims)
    prod_where, prod_params = company_clause(claims, 'p.')
    sales_where, sales_params = company_clause(claims, 's.')

    today = date.today()
    today_str = today.isoformat()
    date_7 = (today - timedelta(days=7)).isoformat()
    date_30 = (today - timedelta(days=30)).isoformat()

    ctx = {}

    row = conn.execute(
        f"SELECT COUNT(*) as cnt, COALESCE(SUM(s.total_usd),0) as total, COALESCE(AVG(s.total_usd),0) as avg_ticket "
        f"FROM sales s WHERE DATE(s.created_at) = %s AND s.status='completed'{sales_where}",
        (today_str, *sales_params)
    ).fetchone()
    ctx['sales_today'] = {'count': row['cnt'], 'total': float(row['total']), 'avg': float(row['avg_ticket'])}

    row = conn.execute(
        f"SELECT COUNT(*) as cnt, COALESCE(SUM(s.total_usd),0) as total "
        f"FROM sales s WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}",
        (date_7, today_str, *sales_params)
    ).fetchone()
    ctx['sales_7d'] = {'count': row['cnt'], 'total': float(row['total'])}

    row = conn.execute(
        f"SELECT COUNT(*) as cnt, COALESCE(SUM(s.total_usd),0) as total "
        f"FROM sales s WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}",
        (date_30, today_str, *sales_params)
    ).fetchone()
    ctx['sales_30d'] = {'count': row['cnt'], 'total': float(row['total'])}

    top_products = conn.execute(f'''
        SELECT si.product_name, SUM(si.quantity) as qty, SUM(si.total_usd) as revenue
        FROM sale_items si JOIN sales s ON si.sale_id = s.id
        WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status='completed'{sales_where}
        GROUP BY si.product_id, si.product_name ORDER BY revenue DESC LIMIT 10
    ''', (date_30, today_str, *sales_params)).fetchall()
    ctx['top_products'] = [{'name': p['product_name'], 'qty': int(p['qty']), 'revenue': float(p['revenue'])} for p in top_products]

    all_products = conn.execute(f'''
        SELECT p.name, p.price_usd, p.stock, c.name as category
        FROM products p LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.active=1{prod_where}
        ORDER BY p.name LIMIT 30
    ''', prod_params).fetchall()
    ctx['products'] = [{'name': p['name'], 'price': float(p['price_usd']), 'stock': int(p['stock']), 'category': p['category'] or 'Sin categoria'} for p in all_products]

    low_stock = conn.execute(
        f"SELECT name, stock, min_stock FROM products p WHERE p.stock <= p.min_stock AND p.active=1{prod_where} ORDER BY p.stock ASC LIMIT 5",
        prod_params
    ).fetchall()
    ctx['low_stock'] = [{'name': p['name'], 'stock': int(p['stock']), 'min': int(p['min_stock'])} for p in low_stock]

    debts = conn.execute(
        f"SELECT COUNT(*) as cnt, COALESCE(SUM(balance_usd),0) as total FROM debts WHERE status='Pendiente'{where}",
        params
    ).fetchone()
    ctx['pending_debts'] = {'count': debts['cnt'], 'total': float(debts['total'])}

    recent_sales = conn.execute(f'''
        SELECT s.sale_number, s.total_usd, s.payment_method, s.created_at
        FROM sales s WHERE s.status='completed'{sales_where}
        ORDER BY s.created_at DESC LIMIT 5
    ''', sales_params).fetchall()
    ctx['recent_sales'] = [{'number': s['sale_number'], 'total': float(s['total_usd']), 'method': s['payment_method'], 'date': s['created_at'].strftime('%d/%m %H:%M')} for s in recent_sales]

    return ctx


def build_system_prompt(context):
    s = context.get('sales_today', {})
    s7 = context.get('sales_7d', {})
    s30 = context.get('sales_30d', {})

    prompt = f"""Sos el asistente personal de un negocio en Venezuela que usa el Sistema POS.
Tu dueno te habla directamente. Sos como un socio experimentado que conoce el negocio.

Hoy es {date.today().strftime('%d de %B de %Y')}.

## Datos del negocio:
### Hoy:
{s.get('count', 0)} vendidos por ${s.get('total', 0):.2f} (ticket promedio: ${s.get('avg', 0):.2f})

### Ultimos 7 dias:
{s7.get('count', 0)} ventas, ${s7.get('total', 0):.2f} en total

### Ultimos 30 dias:
{s30.get('count', 0)} ventas, ${s30.get('total', 0):.2f} en total
"""

    top = context.get('top_products', [])
    if top:
        prompt += "\n### Productos mas vendidos (30 dias):\n"
        for i, p in enumerate(top, 1):
            prompt += f"{i}. {p['name']} - {p['qty']} uds vendidos, ${p['revenue']:.2f} facturado\n"

    products = context.get('products', [])
    if products:
        cats = {}
        for p in products:
            cat = p['category']
            if cat not in cats:
                cats[cat] = []
            cats[cat].append(p)
        prompt += "\n### Catalogo de productos:\n"
        for cat, items in cats.items():
            prompt += f"Categoria {cat}:\n"
            for p in items[:8]:
                prompt += f"  - {p['name']}: ${p['price']:.2f} (stock: {p['stock']})\n"

    low = context.get('low_stock', [])
    if low:
        prompt += "\n### ALERTA - Stock bajo:\n"
        for p in low:
            prompt += f"  - {p['name']}: solo {p['stock']} unidades (minimo {p['min']})\n"

    debts = context.get('pending_debts', {})
    if debts.get('count', 0) > 0:
        prompt += f"\n### Fiados pendientes: {debts['count']} clientes deben ${debts['total']:.2f}\n"

    recent = context.get('recent_sales', [])
    if recent:
        prompt += "\n### Ultimas ventas realizadas:\n"
        for r in recent:
            prompt += f"  - {r['number']}: ${r['total']:.2f} ({r['method']}) - {r['date']}\n"

    prompt += """
## Tu personalidad:
- Sos directo, pratico, como un socio que ya vivio esto
- Hablas en espanol venezolano casual, pero profesional
- Das opiniones reales, no respuestas genericas
- Si algo no te conviene al dueno, lo decis sin rodeos
- Usas datos concretos para respaldar tus recomendaciones

## Como responder:
- Si te preguntan por combos/descuentos: analiza que productos se venden juntos, que margenes tenes, y dale 2-3 ideas concretas con numeros
- Si te preguntan por precios: compara con ticket promedio, sugiere ajustes
- Si te preguntan por stock: alerta sobre lo que falta y que sobra
- Si te preguntan por ventas: compara periodos, identify tendencias
- Si te preguntan algo general: dale una opinion honesta basada en los numeros
- Maximo 4-5 lineas, usa negrita para datos clave
- No inventes datos que no esten en el contexto
"""
    return prompt


def _local_assistant(context, user_message):
    """Asistente local que responde sin necesidad de API externa."""
    s = context.get('sales_today', {})
    s7 = context.get('sales_7d', {})
    s30 = context.get('sales_30d', {})
    top = context.get('top_products', [])
    low = context.get('low_stock', [])
    debts = context.get('pending_debts', {})
    products = context.get('products', [])

    msg = user_message.lower()

    # Detectar intencion del usuario
    if any(w in msg for w in ['venta', 'vendí', 'vendido', 'factur', 'ingreso', 'dinero', 'gananc']):
        if 'semana' in msg or '7' in msg or 'siete' in msg:
            return (f"**Ventas de los ultimos 7 dias:**\n"
                    f"- Total: **${s7.get('total', 0):.2f}**\n"
                    f"- Cantidad: **{s7.get('count', 0)} ventas**\n"
                    f"- Promedio por venta: **${s7.get('total', 0) / max(s7.get('count', 1), 1):.2f}**")
        elif 'mes' in msg or '30' in msg or 'treinta' in msg:
            return (f"**Ventas de los ultimos 30 dias:**\n"
                    f"- Total: **${s30.get('total', 0):.2f}**\n"
                    f"- Cantidad: **{s30.get('count', 0)} ventas**\n"
                    f"- Ticket promedio: **${s30.get('total', 0) / max(s30.get('count', 1), 1):.2f}**")
        else:
            return (f"**Resumen de ventas:**\n"
                    f"- **Hoy:** ${s.get('total', 0):.2f} ({s.get('count', 0)} ventas)\n"
                    f"- **7 dias:** ${s7.get('total', 0):.2f} ({s7.get('count', 0)} ventas)\n"
                    f"- **30 dias:** ${s30.get('total', 0):.2f} ({s30.get('count', 0)} ventas)")

    elif any(w in msg for w in ['producto', 'articulo', 'item', 'que vendo', 'catalogo']):
        if products:
            resp = "**Tu catalogo de productos:**\n"
            for i, p in enumerate(products[:10], 1):
                resp += f"- {p['name']}: **${p['price']:.2f}** (stock: {p['stock']})\n"
            if len(products) > 10:
                resp += f"\n_Tienes {len(products)} productos en total._"
            return resp
        return "No tienes productos registrados aun."

    elif any(w in msg for w in ['stock', 'inventario', 'existencia', 'falta', 'agota']):
        if low:
            resp = "**Alerta de stock bajo:**\n"
            for p in low:
                resp += f"- {p['name']}: solo **{p['stock']}** unidades (minimo: {p['min']})\n"
            resp += "\n_Revisa tu inventario y reabastece lo que mas se vende._"
            return resp
        return "**Stock:** Todos tus productos tienen stock suficiente. No hay alertas por ahora."

    elif any(w in msg for w in ['fiado', 'deuda', 'credito', 'cuenta', 'deben']):
        if debts.get('count', 0) > 0:
            return (f"**Fiados pendientes:**\n"
                    f"- Clientes con deuda: **{debts['count']}**\n"
                    f"- Total por cobrar: **${debts['total']:.2f}**\n"
                    f"\n_Recomiendo hacer un seguimiento semanal para reducir las deudas._")
        return "**Fiados:** No tienes deudas pendientes por cobrar. Excelente!"

    elif any(w in msg for w in ['top', 'mejor', 'mas vendido', 'estrella', 'favorito']):
        if top:
            resp = "**Tus productos mas vendidos (30 dias):**\n"
            for i, p in enumerate(top[:5], 1):
                resp += f"{i}. **{p['name']}**: {p['qty']} uds, ${p['revenue']:.2f}\n"
            resp += "\n_Estos son tu motor de ventas. Mantenlos siempre en stock._"
            return resp
        return "Aun no tienes suficientes ventas para determinar los mas vendidos."

    elif any(w in msg for w in ['precio', 'costo', 'tarifa', 'cobrar']):
        if top:
            avg_ticket = s30.get('total', 0) / max(s30.get('count', 1), 1)
            resp = f"**Analisis de precios:**\n"
            resp += f"- Ticket promedio: **${avg_ticket:.2f}**\n"
            resp += f"- Producto estrella: **{top[0]['name']}** (${top[0]['revenue']:.2f})\n"
            resp += "\n_Si tu ticket promedio es bajo, considera crear combos o paquetes para aumentar la venta promedio._"
            return resp
        return "Necesito mas datos de ventas para analizar tus precios."

    elif any(w in msg for w in ['combo', 'descuento', 'promocion', 'oferta', 'paquete']):
        resp = "**Ideas para aumentar tus ventas:**\n"
        resp += "1. **Combo rapido:** Junta los 2 productos mas vendidos con 10% descuento\n"
        resp += "2. **2x1 en lento mover:** Identifica productos con stock alto y haz promociones\n"
        resp += "3. **Descuento por volumen:** Si compran mas de 3 unidades, 15% off\n"
        resp += "\n_Basado en tus datos, estas estrategias pueden aumentar tu ticket promedio._"
        return resp

    elif any(w in msg for w in ['consejo', 'recomendacion', 'sugerencia', 'ayuda', 'tip']):
        tips = []
        if low:
            tips.append(f"**Reabastece:** {len(low)} productos con stock bajo")
        if debts.get('count', 0) > 0:
            tips.append(f"**Cobra fiados:** ${debts['total']:.2f} pendientes")
        if s.get('count', 0) == 0:
            tips.append("**Hoy no has vendido:** Considera promociones para atraer clientes")
        if s30.get('count', 0) > 0:
            avg = s30.get('total', 0) / s30.get('count', 0)
            tips.append(f"**Ticket promedio:** ${avg:.2f} - Puedes subirlo con combos")

        if tips:
            return "**Recomendaciones del dia:**\n" + "\n".join(f"- {t}" for t in tips)
        return "**Tu negocio va bien!** Sigue asi. Consultame lo que necesites."

    elif any(w in msg for w in ['hola', 'buenos', 'buenas', 'hey', 'que tal']):
        return (f"**Hola!** Soy tu asistente de negocio. \n\n"
                f"**Resumen rapido:**\n"
                f"- Hoy: ${s.get('total', 0):.2f} en {s.get('count', 0)} ventas\n"
                f"- Stock bajo: {len(low)} productos\n"
                f"- Fiados: ${debts.get('total', 0):.2f}\n\n"
                f"_Preguntame sobre ventas, productos, stock, fiados o recomendaciones._")

    elif any(w in msg for w in ['adios', 'chao', 'nos vemos', 'gracias']):
        return "**Hasta luego!** Estoy aqui cuando me necesites. Que tus ventas sigan creciendo!"

    else:
        return (f"**Entendi tu pregunta.** Aqui va un resumen rapido de tu negocio:\n\n"
                f"- **Hoy:** ${s.get('total', 0):.2f} ({s.get('count', 0)} ventas)\n"
                f"- **30 dias:** ${s30.get('total', 0):.2f} ({s30.get('count', 0)} ventas)\n"
                f"- **Stock bajo:** {len(low)} productos\n"
                f"- **Fiados:** ${debts.get('total', 0):.2f}\n\n"
                f"_Puedes preguntarme sobre: ventas, productos, stock, fiados, precios, combos o recomendaciones._")


def send_to_ai(system_prompt, history, user_message):
    api_key = _get_api_key()

    # Si no hay API key, usar asistente local
    if not api_key:
        # Extraer contexto del system_prompt para el asistente local
        # Buscamos los datos en el contexto
        import re
        context = {}

        # Extraer ventas de hoy
        match = re.search(r'Hoy:\n(\d+) vendidos por \$([\d.]+)', system_prompt)
        if match:
            context['sales_today'] = {'count': int(match.group(1)), 'total': float(match.group(2))}

        # Extraer ventas 7 dias
        match = re.search(r'Ultimos 7 dias:\n(\d+) ventas, \$([\d.]+)', system_prompt)
        if match:
            context['sales_7d'] = {'count': int(match.group(1)), 'total': float(match.group(2))}

        # Extraer ventas 30 dias
        match = re.search(r'Ultimos 30 dias:\n(\d+) ventas, \$([\d.]+)', system_prompt)
        if match:
            context['sales_30d'] = {'count': int(match.group(1)), 'total': float(match.group(2))}

        # Extraer top productos
        top_products = []
        for match in re.finditer(r'\d+\. (.+?) - (\d+) uds vendidos, \$([\d.]+)', system_prompt):
            top_products.append({
                'name': match.group(1),
                'qty': int(match.group(2)),
                'revenue': float(match.group(3))
            })
        context['top_products'] = top_products

        # Extraer stock bajo
        low_stock = []
        for match in re.finditer(r'- (.+?): solo (\d+) unidades \(minimo (\d+)\)', system_prompt):
            low_stock.append({
                'name': match.group(1),
                'stock': int(match.group(2)),
                'min': int(match.group(3))
            })
        context['low_stock'] = low_stock

        # Extraer fiados
        match = re.search(r'Fiados pendientes: (\d+) clientes deben \$([\d.]+)', system_prompt)
        if match:
            context['pending_debts'] = {'count': int(match.group(1)), 'total': float(match.group(2))}
        else:
            context['pending_debts'] = {'count': 0, 'total': 0}

        return _local_assistant(context, user_message)

    # Si hay API key, usar OpenRouter
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        role = "user" if msg['role'] == 'user' else "assistant"
        messages.append({"role": role, "content": msg['text']})
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": _get_model(),
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 512
    }

    resp = requests.post(
        OPENROUTER_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://sistema-pos-venezuela.com",
            "X-Title": "POS Venezuela"
        },
        data=json.dumps(payload),
        timeout=10
    )

    if resp.status_code == 429:
        raise ValueError("Demasiadas solicitudes. Espera un momento e intentalo de nuevo.")

    if resp.status_code != 200:
        error_msg = resp.json().get('error', {}).get('message', resp.text[:200])
        raise ValueError(f"Error de IA: {error_msg}")

    data = resp.json()
    choices = data.get('choices', [])
    if not choices:
        raise ValueError("No se pudo generar una respuesta.")

    text = choices[0].get('message', {}).get('content', '')
    if not text:
        raise ValueError("Respuesta vacia del servicio de IA.")

    return text
