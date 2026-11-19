from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from datetime import date, datetime
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db
from routes.exchange import get_today_rate

debts_bp = Blueprint('debts', __name__)


def company_clause(claims, prefix=''):
    cid = claims.get('company_id')
    if cid is not None:
        return f' AND {prefix}company_id = %s', [cid]
    return '', []


def get_today_rate_simple():
    """Returns today's BCV rate or 0."""
    from routes.exchange import get_today_rate
    try:
        rate, source, cached = get_today_rate()
        return rate
    except Exception:
        return 0


def _calc_balance_bs(balance_usd, rate):
    """Calculate Bs equivalent of a USD balance."""
    if rate > 0:
        return round(float(balance_usd) * float(rate), 2)
    r = get_today_rate_simple()
    if r > 0:
        return round(float(balance_usd) * r, 2)
    return 0


# ── List debts ─────────────────────────────────────────────

@debts_bp.route('', methods=['GET'])
@jwt_required()
def list_debts():
    claims = get_jwt()
    where, params = company_clause(claims, 'd.')
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '')

    query = f'''
        SELECT d.*, c.name as customer_name, c.credit_status
        FROM debts d
        JOIN customers c ON d.customer_id = c.id
        WHERE 1=1{where}
    '''
    if status_filter:
        query += ' AND d.status = %s'
        params.append(status_filter)
    if search:
        query += ' AND (c.name LIKE %s OR d.concept LIKE %s)'
        params += [f'%{search}%', f'%{search}%']
    query += ' ORDER BY d.created_at DESC'

    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    # Una sola tasa por request (no una por fila)
    base_rate = get_today_rate_simple()
    result = []
    for r in rows:
        d = dict(r)
        d['balance_bs'] = _calc_balance_bs(
            d['balance_usd'],
            d.get('exchange_rate_at_creation', 0) or base_rate
        )
        result.append(d)
    return jsonify(result)


# ── Get single debt with payments ─────────────────────────

@debts_bp.route('/<int:did>', methods=['GET'])
@jwt_required()
def get_debt(did):
    claims = get_jwt()
    where, params = company_clause(claims, 'd.')
    conn = get_db()

    debt = conn.execute(
        f'''
        SELECT d.*, c.name as customer_name, c.credit_status,
               c.credit_limit_usd, c.phone, c.doc_number
        FROM debts d
        JOIN customers c ON d.customer_id = c.id
        WHERE d.id=%s{where}
        ''', (did, *params)
    ).fetchone()
    if not debt:
        conn.close()
        return jsonify({"error": "Deuda no encontrada"}), 404

    payments = conn.execute(
        "SELECT * FROM debt_payments WHERE debt_id=%s ORDER BY created_at ASC",
        (did,)
    ).fetchall()

    conn.close()
    result = dict(debt)
    result['balance_bs'] = _calc_balance_bs(result['balance_usd'], result.get('exchange_rate_at_creation', 0))
    result['payments'] = [dict(p) for p in payments]
    return jsonify(result)


# ── Register new debt ─────────────────────────────────────

@debts_bp.route('', methods=['POST'])
@jwt_required()
def create_debt():
    claims = get_jwt()
    company_id = claims.get('company_id')
    data = request.get_json()

    customer_id = data.get('customer_id')
    concept = data.get('concept', '').strip()
    amount_usd = float(data.get('amount_usd', 0))
    amount_bs = float(data.get('amount_bs', 0))
    due_date_str = data.get('due_date')
    sale_id = data.get('sale_id')

    if not customer_id or not due_date_str:
        return jsonify({"error": "customer_id y due_date son requeridos"}), 400

    # Determine USD amount and Bs equivalent
    rate = 0
    if amount_usd > 0 and amount_bs == 0:
        rate = get_today_rate_simple()
        if rate > 0:
            amount_bs = round(amount_usd * rate, 2)
    elif amount_bs > 0 and amount_usd == 0:
        rate = get_today_rate_simple()
        if rate <= 0:
            return jsonify({"error": "No se pudo obtener la tasa de cambio del día"}), 503
        amount_usd = round(amount_bs / rate, 4)
    elif amount_usd > 0 and amount_bs > 0:
        rate = get_today_rate_simple()
        if rate > 0:
            amount_bs = round(amount_usd * rate, 2)

    if amount_usd <= 0:
        return jsonify({"error": "Debe indicar amount_usd (>0) o amount_bs (>0)"}), 400

    conn = get_db()

    # Verify customer exists and belongs to this company
    where_c, params_c = company_clause(claims)
    customer = conn.execute(
        f'SELECT id, name, credit_limit_usd, credit_status FROM customers WHERE id=%s{where_c}',
        (customer_id, *params_c)
    ).fetchone()
    if not customer:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404

    # Check credit limit: current balance + new debt <= credit_limit_usd
    current_balance = conn.execute(
        f'SELECT COALESCE(SUM(balance_usd), 0) as total FROM debts WHERE customer_id=%s AND status!="Pagada"{company_clause(claims)[0].replace("d.","")}',
        (customer_id, *company_clause(claims)[1])
    ).fetchone()['total']

    credit_limit = float(customer['credit_limit_usd'] or 0)
    new_total = current_balance + amount_usd
    if credit_limit > 0 and new_total > credit_limit:
        conn.close()
        return jsonify({
            "error": f"El cliente {customer['name']} tiene un límite de crédito de ${credit_limit:,.2f}. "
                     f"Saldo actual: ${current_balance:,.2f}. Monto solicitado: ${amount_usd:,.2f}. "
                     f"Excede el límite en ${new_total - credit_limit:,.2f}."
        }), 400

    cursor = conn.execute(
        "INSERT INTO debts (company_id, customer_id, sale_id, concept, original_amount_usd, original_amount_bs, exchange_rate_at_creation, balance_usd, due_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (company_id, customer_id, sale_id or None, concept or None, amount_usd, amount_bs, rate, amount_usd, due_date_str)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({"message": "Deuda registrada", "id": new_id}), 201


# ── Make a payment ────────────────────────────────────────

@debts_bp.route('/<int:did>/pay', methods=['POST'])
@jwt_required()
def pay_debt(did):
    claims = get_jwt()
    company_id = claims.get('company_id')
    data = request.get_json()
    payments = data.get('payments', [])

    if not payments:
        return jsonify({"error": "Debe incluir al menos un pago en el array 'payments'"}), 400

    conn = get_db()

    # Get debt
    where, params = company_clause(claims, 'd.')
    debt = conn.execute(
        f'SELECT d.*, c.name as customer_name, c.credit_status, c.credit_limit_usd '
        f'FROM debts d JOIN customers c ON d.customer_id=c.id WHERE d.id=%s{where}',
        (did, *params)
    ).fetchone()
    if not debt:
        conn.close()
        return jsonify({"error": "Deuda no encontrada"}), 404
    if debt['status'] == 'Pagada':
        conn.close()
        return jsonify({"error": "Esta deuda ya está pagada"}), 400

    # Get today's rate (fetches from BCV API if not in DB)
    try:
        today_rate, rate_source, _ = get_today_rate(company_id=company_id)
    except Exception as e:
        print(f"⚠️ Error obteniendo tasa: {e}")
        today_rate = 36.50

    balance = float(debt['balance_usd'])
    payment_records = []
    total_paid_usd = 0

    for pay in payments:
        method = pay.get('payment_method', 'cash')
        amount_bs = float(pay.get('amount_bs', 0))
        amount_usd = float(pay.get('amount_usd', 0))
        ref_number = (pay.get('reference_number') or '').strip()
        bank_origin = (pay.get('bank_origin') or '').strip()
        bank_destination = (pay.get('bank_destination') or '').strip()

        # Validate reference fields for PM/Transfer
        if method in ('pago_movil', 'transferencia'):
            if not ref_number:
                conn.close()
                return jsonify({"error": "Para Pago Móvil o Transferencia, reference_number es obligatorio"}), 400
            if not bank_origin:
                conn.close()
                return jsonify({"error": "Para Pago Móvil o Transferencia, banco origen es obligatorio"}), 400
            if not bank_destination:
                conn.close()
                return jsonify({"error": "Para Pago Móvil o Transferencia, banco destino es obligatorio"}), 400

        if amount_usd > 0:
            # Payment in USD
            usd_amount = amount_usd
            bs_amount = 0
            rate_used = 0
        elif amount_bs > 0:
            # Payment in VES
            if today_rate <= 0:
                conn.close()
                return jsonify({"error": "No se pudo obtener la tasa de cambio del día"}), 503
            usd_amount = round(amount_bs / today_rate, 4)
            bs_amount = amount_bs
            rate_used = today_rate
        else:
            conn.close()
            return jsonify({"error": "Cada pago debe tener amount_usd o amount_bs > 0"}), 400

        total_paid_usd += usd_amount
        payment_records.append((usd_amount, rate_used, bs_amount, method,
                                ref_number, bank_origin, bank_destination))

    # Validate payment does not exceed balance
    if total_paid_usd > balance:
        conn.close()
        return jsonify({
            "error": f"El pago (${total_paid_usd:.2f}) excede el saldo de la deuda (${balance:.2f}). "
                     f"Verifique el monto."
        }), 400

    # Insert all payments (validation passed)
    inserted = []
    for (usd_amount, rate_used, bs_amount, method,
         ref_number, bank_origin, bank_destination) in payment_records:
        cursor = conn.execute(
            "INSERT INTO debt_payments (company_id, debt_id, amount_paid_usd, exchange_rate_used, "
            "amount_paid_bs, payment_method, reference_number, bank_origin, bank_destination) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (company_id, did, usd_amount, rate_used, bs_amount, method,
             ref_number or None, bank_origin or None, bank_destination or None)
        )
        inserted.append(cursor.lastrowid)

    # Reduce balance
    new_balance = round(balance - total_paid_usd, 2)

    if new_balance == 0:
        conn.execute("UPDATE debts SET balance_usd=0, status='Pagada' WHERE id=%s", (did,))
    else:
        conn.execute("UPDATE debts SET balance_usd=%s WHERE id=%s", (new_balance, did))

    # Update customer credit status if in mora
    if debt.get('credit_status') == 'En Mora' and new_balance == 0:
        conn.execute("UPDATE customers SET credit_status='Solvente' WHERE id=%s", (debt['customer_id'],))

    conn.commit()
    conn.close()
    return jsonify({
        "message": "Pago registrado exitosamente",
        "total_paid_usd": round(total_paid_usd, 2),
        "new_balance": new_balance,
        "status": "Pagada" if new_balance == 0 else "Pendiente",
        "payment_ids": inserted
    })


# ── Overdue debts ─────────────────────────────────────────

@debts_bp.route('/overdue', methods=['GET'])
@jwt_required()
def overdue_debts():
    claims = get_jwt()
    where, params = company_clause(claims, 'd.')
    today_str = date.today().isoformat()

    conn = get_db()

    # Update status for debts past due
    conn.execute(
        f'UPDATE debts SET status="Vencida" WHERE due_date < %s AND balance_usd > 0 AND status="Pendiente"{where}',
        (today_str, *params)
    )

    # Update customer credit_status for overdue debts
    overdue_customers = conn.execute(
        f'''
        SELECT DISTINCT d.customer_id FROM debts d
        WHERE d.status="Vencida" AND d.balance_usd > 0{where}
        ''',
        params
    ).fetchall()
    for row in overdue_customers:
        conn.execute(
            "UPDATE customers SET credit_status='En Mora' WHERE id=%s AND credit_status!='Suspendido'",
            (row['customer_id'],)
        )

    # Return overdue list
    rows = conn.execute(
        f'''
        SELECT d.*, c.name as customer_name, c.phone, c.credit_status
        FROM debts d
        JOIN customers c ON d.customer_id = c.id
        WHERE d.status="Vencida" AND d.balance_usd > 0{where}
        ORDER BY d.due_date ASC
        ''', params
    ).fetchall()

    conn.commit()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Overdue count (lightweight, for dashboard alert) ──────

@debts_bp.route('/overdue/count', methods=['GET'])
@jwt_required()
def overdue_count():
    claims = get_jwt()
    where, params = company_clause(claims, 'd.')

    conn = get_db()
    row = conn.execute(
        f'SELECT COUNT(*) as cnt FROM debts d WHERE d.status="Vencida" AND d.balance_usd > 0{where}',
        params
    ).fetchone()
    conn.close()
    return jsonify({"count": row['cnt'] if row else 0})


# ── Generate WhatsApp reminder ────────────────────────────

@debts_bp.route('/<int:did>/reminder', methods=['GET'])
@jwt_required()
def reminder(did):
    claims = get_jwt()
    where, params = company_clause(claims, 'd.')
    conn = get_db()

    debt = conn.execute(
        f'''
        SELECT d.*, c.name as customer_name, c.phone
        FROM debts d JOIN customers c ON d.customer_id=c.id
        WHERE d.id=%s{where}
        ''', (did, *params)
    ).fetchone()
    if not debt:
        conn.close()
        return jsonify({"error": "Deuda no encontrada"}), 404

    due = debt['due_date'].isoformat() if hasattr(debt['due_date'], 'isoformat') else str(debt['due_date'])
    message = (
        f"🟡 *RECORDATORIO DE DEUDA*\n\n"
        f"Hola {debt['customer_name']},\n\n"
        f"Te recordamos que tienes un saldo pendiente de *${float(debt['balance_usd']):,.2f} USD* "
        f"con vencimiento el *{due}*.\n\n"
        f"Por favor, acércate a nuestro local para regularizar tu situación.\n\n"
        f"¡Gracias por tu preferencia! 🙌"
    )

    conn.close()
    return jsonify({
        "message": message,
        "phone": debt.get('phone') or None
    })
