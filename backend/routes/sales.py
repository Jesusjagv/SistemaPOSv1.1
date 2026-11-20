from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
import sys, os
from datetime import datetime, date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db, next_counter, machine_suffix

sales_bp = Blueprint('sales', __name__)


class _SaleError(Exception):
    """Error controlado durante la creación/anulación de una venta."""
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


def company_clause(claims, prefix=''):
    cid = claims.get('company_id')
    if cid is not None:
        return f' AND {prefix}company_id = %s', [cid]
    return '', []


def generate_sale_number():
    """Genera número de venta único: VEN-YYYYMMDD-XXXX.

    Usa la tabla `counters` (incremento atómico) en lugar de COUNT(*) para
    que dos cajeros simultáneos nunca reciban el mismo número.
    """
    conn = get_db()
    today = date.today().strftime('%Y%m%d')

    def _existing_count():
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sales WHERE sale_number LIKE %s",
            (f'VEN-{today}-%%',)
        ).fetchone()
        return row['cnt'] if row else 0

    seq = next_counter(conn, f'sale_{today}', init_count=_existing_count)
    return f"VEN-{today}-{seq:04d}{machine_suffix()}"


@sales_bp.route('', methods=['POST'])
@jwt_required()
def create_sale():
    claims    = get_jwt()
    user_id   = int(get_jwt_identity())
    company_id = claims.get('company_id')
    data      = request.get_json()
    items     = data.get('items', [])

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

    role = claims.get('role')
    if tax_percent != 16.0 and role != 'admin':
        return jsonify({"error": "Solo un administrador puede modificar o desactivar el IVA"}), 403

    conn = get_db()

    # Número de venta y sesión actual ANTES de la transacción: el contador
    # se incrementa en autocommit y no retiene locks durante la venta
    sale_number = generate_sale_number()
    session_row = conn.execute(
        "SELECT id FROM cash_sessions WHERE user_id = %s AND status = 'open' ORDER BY open_datetime DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    session_id = session_row['id'] if session_row else None

    # Transacción: leer productos con lock, validar stock e insertar todo de
    # forma atómica (ventas simultáneas no pueden dejar stock negativo)
    product_ids = [int(item['product_id']) for item in items]
    placeholders = ','.join(['%s'] * len(product_ids))
    product_where = ' AND company_id = %s' if company_id else ''
    product_params = [company_id] if company_id else []

    conn.begin()
    try:
        product_rows = conn.execute(
            f'''SELECT *, unidades_por_paquete, costo_unidad, precio_unidad, precio_paquete
                FROM products
                WHERE id IN ({placeholders}) AND active=1{product_where}
                FOR UPDATE''',
            (*product_ids, *product_params)
        ).fetchall()
        products_by_id = {p['id']: p for p in product_rows}

        sale_items = []
        subtotal_usd = 0.0

        for item in items:
            pid      = int(item['product_id'])
            qty      = float(item['quantity'])
            disc_pct = float(item.get('discount_percent', 0))
            tipo_venta = item.get('tipo_venta', 'unidad')

            product = products_by_id.get(pid)
            if not product:
                raise _SaleError(f"Producto ID {pid} no encontrado", 404)

            # Calculate stock to deduct based on sale type
            ux = max(int(product['unidades_por_paquete']), 1)
            stock_to_deduct = qty * (ux if tipo_venta == 'paquete' else 1)

            if product['stock'] < stock_to_deduct:
                raise _SaleError(
                    f"Stock insuficiente para '{product['name']}'. Disponible: "
                    f"{product['stock']} unidades sueltas, necesitas: {stock_to_deduct}"
                )

            # Use the correct price based on sale type
            if tipo_venta == 'paquete' and float(product.get('precio_paquete', 0)) > 0:
                unit_price = float(product['precio_paquete'])
            elif tipo_venta == 'unidad':
                if float(product.get('precio_unidad', 0)) > 0:
                    unit_price = float(product['precio_unidad'])
                elif ux > 1:
                    # Derivar precio unitario desde el precio del paquete
                    paq_price = float(product.get('precio_paquete', 0))
                    if paq_price <= 0:
                        paq_price = float(product['price_usd'])
                    unit_price = round(paq_price / ux, 4)
                else:
                    unit_price = float(product['price_usd'])
            else:
                unit_price = float(product['price_usd'])

            disc_usd  = unit_price * (disc_pct / 100)
            net_price = unit_price - disc_usd
            total_usd = round(net_price * qty, 4)
            price_bs  = round(unit_price * exchange_rate, 2)
            total_bs  = round(total_usd * exchange_rate, 2)
            subtotal_usd += total_usd

            sale_items.append({
                'product_id':      pid,
                'product_name':    product['name'],
                'product_code':    product['code'],
                'quantity':        qty,
                'tipo_venta':      tipo_venta,
                'stock_deducted':  stock_to_deduct,
                'price_usd':       round(unit_price, 4),
                'price_bs':        price_bs,
                'discount_percent': disc_pct,
                'total_usd':       total_usd,
                'total_bs':        total_bs
            })

        subtotal_usd   = round(subtotal_usd, 4)
        order_disc_usd = round(subtotal_usd * (discount_pct / 100), 4)
        after_disc_usd = round(subtotal_usd - order_disc_usd, 4)
        tax_usd        = round(after_disc_usd * (tax_percent / 100.0), 4)
        total_usd      = round(after_disc_usd + tax_usd, 4)
        total_bs       = round(total_usd * exchange_rate, 2)

        total_paid_usd = amount_paid_usd + (amount_paid_bs / exchange_rate if exchange_rate else 0)
        change_usd     = round(max(0, total_paid_usd - total_usd), 4)
        change_bs      = round(change_usd * exchange_rate, 2)

        cursor = conn.execute('''
            INSERT INTO sales (sale_number, user_id, customer_id, customer_name,
            subtotal_usd, tax_usd, discount_usd, total_usd, exchange_rate, total_bs,
            payment_method, amount_paid_usd, amount_paid_bs, change_usd, change_bs,
            notes, company_id, session_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ''', (
            sale_number, user_id, customer_id, customer_name,
            subtotal_usd, tax_usd, order_disc_usd, total_usd,
            exchange_rate, total_bs, payment_method,
            amount_paid_usd, amount_paid_bs, change_usd, change_bs,
            notes, company_id, session_id
        ))
        sale_id = cursor.lastrowid

        for si in sale_items:
            conn.execute('''
                INSERT INTO sale_items (sale_id, product_id, product_name, product_code,
                quantity, price_usd, price_bs, discount_percent, total_usd, total_bs,
                tipo_venta, stock_deducted)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ''', (
                sale_id, si['product_id'], si['product_name'], si['product_code'],
                si['quantity'], si['price_usd'], si['price_bs'],
                si['discount_percent'], si['total_usd'], si['total_bs'],
                si['tipo_venta'], si['stock_deducted']
            ))

        # Ingredientes de combos (una sola lectura para todos los ítems)
        combo_map = {}
        combo_ids = sorted({si['product_id'] for si in sale_items
                            if products_by_id[si['product_id']].get('is_combo') == 1})
        if combo_ids:
            combo_ph = ','.join(['%s'] * len(combo_ids))
            for row in conn.execute(
                f'''SELECT combo_product_id, ingredient_product_id, quantity
                    FROM combo_items WHERE combo_product_id IN ({combo_ph})''',
                combo_ids
            ).fetchall():
                combo_map.setdefault(row['combo_product_id'], []).append(row)

        # Descontar stock de forma atómica: solo si realmente hay stock
        for si in sale_items:
            if products_by_id[si['product_id']].get('is_combo') == 1:
                for ci in combo_map.get(si['product_id'], []):
                    deduct = float(ci['quantity']) * float(si['stock_deducted'])
                    conn.execute(
                        "UPDATE products SET stock = stock - %s WHERE id=%s",
                        (deduct, ci['ingredient_product_id'])
                    )
            else:
                cur = conn.execute(
                    "UPDATE products SET stock = stock - %s WHERE id=%s AND stock >= %s",
                    (si['stock_deducted'], si['product_id'], si['stock_deducted'])
                )
                if cur.rowcount == 0:
                    raise _SaleError(
                        f"Stock insuficiente para '{si['product_name']}' "
                        f"durante el registro de la venta"
                    )

        conn.commit()
    except _SaleError as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": e.message}), e.code
    except Exception as e:
        conn.rollback()
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
    claims   = get_jwt()
    date_from = request.args.get('from', date.today().isoformat())
    date_to   = request.args.get('to',   date.today().isoformat())
    limit     = int(request.args.get('limit', 50))
    where, params = company_clause(claims, 's.')
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT s.*, u.name as user_name,
               (SELECT COALESCE(SUM(quantity), 0) FROM sale_items WHERE sale_id = s.id) as total_items
        FROM sales s
        LEFT JOIN users u ON s.user_id = u.id
        WHERE DATE(s.created_at) BETWEEN %s AND %s{where}
        ORDER BY s.created_at DESC
        LIMIT %s
        ''', (date_from, date_to, *params, limit)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@sales_bp.route('/<int:sid>', methods=['GET'])
@jwt_required()
def get_sale(sid):
    claims = get_jwt()
    where, params = company_clause(claims, 's.')
    conn = get_db()
    sale = conn.execute(
        f'''
        SELECT s.*, u.name as user_name
        FROM sales s LEFT JOIN users u ON s.user_id=u.id
        WHERE s.id=%s{where}
        ''', (sid, *params)
    ).fetchone()
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
    where, params = company_clause(claims)
    conn = get_db()

    conn.begin()
    try:
        # FOR UPDATE: dos administradores anulando a la vez no pueden
        # revertir el stock dos veces
        sale = conn.execute(
            f'SELECT * FROM sales WHERE id=%s{where} FOR UPDATE', (sid, *params)
        ).fetchone()
        if not sale:
            raise _SaleError("Venta no encontrada", 404)
        if sale['status'] == 'cancelled':
            raise _SaleError("La venta ya está anulada", 400)

        items = conn.execute("SELECT * FROM sale_items WHERE sale_id=%s", (sid,)).fetchall()
        for item in items:
            stock_to_restore = float(item.get('stock_deducted', 0))
            if stock_to_restore <= 0:
                stock_to_restore = float(item['quantity'])
            conn.execute(
                "UPDATE products SET stock = stock + %s WHERE id=%s",
                (stock_to_restore, item['product_id'])
            )
        conn.execute("UPDATE sales SET status='cancelled' WHERE id=%s", (sid,))
        conn.commit()
    except _SaleError as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": e.message}), e.code
    except Exception:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Error al anular la venta"}), 500

    conn.close()
    return jsonify({"message": "Venta anulada y stock revertido"})
