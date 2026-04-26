from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
import sys, os
from datetime import datetime, date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

sales_bp = Blueprint('sales', __name__)

def generate_sale_number():
    """Genera número de venta único: VEN-YYYYMMDD-XXXX"""
    conn = get_db()
    today = date.today().strftime('%Y%m%d')
    count_dict = conn.execute(
        "SELECT COUNT(*) as cnt FROM sales WHERE sale_number LIKE %s", (f'VEN-{today}-%%',)
    ).fetchone()
    count = count_dict['cnt'] if count_dict else 0
    conn.close()
    return f"VEN-{today}-{(count + 1):04d}"

@sales_bp.route('', methods=['POST'])
@jwt_required()
def create_sale():
    user_id = int(get_jwt_identity())
    data    = request.get_json()
    items   = data.get('items', [])

    if not items:
        return jsonify({"error": "La venta debe tener al menos un producto"}), 400

    exchange_rate   = float(data.get('exchange_rate', 0))
    customer_id     = data.get('customer_id') or None
    customer_name   = data.get('customer_name', 'Consumidor Final')
    payment_method  = data.get('payment_method', 'cash_usd')
    amount_paid_usd = float(data.get('amount_paid_usd', 0))
    amount_paid_bs  = float(data.get('amount_paid_bs', 0))
    discount_pct    = float(data.get('discount_percent', 0))
    tax_percent     = float(data.get('tax_percent', 16.0))
    notes           = data.get('notes', '')

    if exchange_rate <= 0:
        return jsonify({"error": "Tasa de cambio inválida"}), 400

    role = get_jwt().get('role')
    if tax_percent != 16.0 and role != 'admin':
        return jsonify({"error": "Solo un administrador puede modificar o desactivar el IVA"}), 403

    conn = get_db()

    # Verify stock and build item list
    sale_items = []
    subtotal_usd = 0.0

    for item in items:
        pid      = int(item['product_id'])
        qty      = float(item['quantity'])
        disc_pct = float(item.get('discount_percent', 0))

        product = conn.execute("SELECT * FROM products WHERE id=%s AND active=1", (pid,)).fetchone()
        if not product:
            conn.close()
            return jsonify({"error": f"Producto ID {pid} no encontrado"}), 404
        if product['stock'] < qty:
            conn.close()
            return jsonify({"error": f"Stock insuficiente para '{product['name']}'. Disponible: {product['stock']}"}), 400

        price_usd = float(product['price_usd'])
        disc_usd  = price_usd * (disc_pct / 100)
        net_price = price_usd - disc_usd
        total_usd = round(net_price * qty, 4)
        price_bs  = round(price_usd * exchange_rate, 2)
        total_bs  = round(total_usd * exchange_rate, 2)
        subtotal_usd += total_usd

        sale_items.append({
            'product_id':      pid,
            'product_name':    product['name'],
            'product_code':    product['code'],
            'quantity':        qty,
            'price_usd':       round(price_usd, 4),
            'price_bs':        price_bs,
            'discount_percent': disc_pct,
            'total_usd':       total_usd,
            'total_bs':        total_bs
        })

    subtotal_usd   = round(subtotal_usd, 4)
    # Apply order-level discount
    order_disc_usd = round(subtotal_usd * (discount_pct / 100), 4)
    after_disc_usd = round(subtotal_usd - order_disc_usd, 4)
    tax_usd        = round(after_disc_usd * (tax_percent / 100.0), 4)
    total_usd      = round(after_disc_usd + tax_usd, 4)
    total_bs       = round(total_usd * exchange_rate, 2)

    # Calculate change
    total_paid_usd = amount_paid_usd + (amount_paid_bs / exchange_rate if exchange_rate else 0)
    change_usd     = round(max(0, total_paid_usd - total_usd), 4)
    change_bs      = round(change_usd * exchange_rate, 2)

    sale_number = generate_sale_number()

    try:
        cursor = conn.execute('''
            INSERT INTO sales (sale_number, user_id, customer_id, customer_name,
            subtotal_usd, tax_usd, discount_usd, total_usd, exchange_rate, total_bs,
            payment_method, amount_paid_usd, amount_paid_bs, change_usd, change_bs, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ''', (
            sale_number, user_id, customer_id, customer_name,
            subtotal_usd, tax_usd, order_disc_usd, total_usd,
            exchange_rate, total_bs, payment_method,
            amount_paid_usd, amount_paid_bs, change_usd, change_bs, notes
        ))
        sale_id = cursor.lastrowid

        for si in sale_items:
            conn.execute('''
                INSERT INTO sale_items (sale_id, product_id, product_name, product_code,
                quantity, price_usd, price_bs, discount_percent, total_usd, total_bs)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ''', (
                sale_id, si['product_id'], si['product_name'], si['product_code'],
                si['quantity'], si['price_usd'], si['price_bs'],
                si['discount_percent'], si['total_usd'], si['total_bs']
            ))
            # Descont stock
            conn.execute(
                "UPDATE products SET stock = stock - %s WHERE id=%s",
                (si['quantity'], si['product_id'])
            )

        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": f"Error al guardar venta: {str(e)}"}), 500

    conn.close()
    return jsonify({
        "message": "Venta registrada exitosamente",
        "sale_id": sale_id,
        "sale_number": sale_number,
        "total_usd": total_usd,
        "total_bs": total_bs,
        "change_usd": change_usd,
        "change_bs": change_bs
    }), 201

@sales_bp.route('', methods=['GET'])
@jwt_required()
def list_sales():
    date_from = request.args.get('from', date.today().isoformat())
    date_to   = request.args.get('to',   date.today().isoformat())
    limit     = int(request.args.get('limit', 50))
    conn = get_db()
    rows = conn.execute('''
        SELECT s.*, u.name as user_name,
               (SELECT COALESCE(SUM(quantity), 0) FROM sale_items WHERE sale_id = s.id) as total_items
        FROM sales s
        LEFT JOIN users u ON s.user_id = u.id
        WHERE DATE(s.created_at) BETWEEN %s AND %s
        ORDER BY s.created_at DESC
        LIMIT %s
    ''', (date_from, date_to, limit)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@sales_bp.route('/<int:sid>', methods=['GET'])
@jwt_required()
def get_sale(sid):
    conn = get_db()
    sale = conn.execute('''
        SELECT s.*, u.name as user_name
        FROM sales s LEFT JOIN users u ON s.user_id=u.id
        WHERE s.id=%s
    ''', (sid,)).fetchone()
    if not sale:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    items = conn.execute(
        "SELECT * FROM sale_items WHERE sale_id=%s", (sid,)
    ).fetchall()
    conn.close()
    result = dict(sale)
    result['items'] = [dict(i) for i in items]
    return jsonify(result)

@sales_bp.route('/<int:sid>/cancel', methods=['POST'])
@jwt_required()
def cancel_sale(sid):
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Solo el administrador puede anular ventas"}), 403
    conn = get_db()
    sale = conn.execute("SELECT * FROM sales WHERE id=%s", (sid,)).fetchone()
    if not sale:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if sale['status'] == 'cancelled':
        conn.close()
        return jsonify({"error": "La venta ya está anulada"}), 400

    # Revert stock
    items = conn.execute("SELECT * FROM sale_items WHERE sale_id=%s", (sid,)).fetchall()
    for item in items:
        conn.execute(
            "UPDATE products SET stock = stock + %s WHERE id=%s",
            (item['quantity'], item['product_id'])
        )
    conn.execute("UPDATE sales SET status='cancelled' WHERE id=%s", (sid,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Venta anulada y stock revertido"})
