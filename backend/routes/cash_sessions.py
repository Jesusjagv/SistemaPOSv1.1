from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
import sys, os
from datetime import datetime, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db, next_counter, machine_suffix
from routes.exchange import get_today_rate

cash_sessions_bp = Blueprint('cash_sessions', __name__)

PAYMENT_METHOD_LABELS = {
    'cash_usd':   'Efectivo USD',
    'cash_bs':    'Efectivo Bs',
    'card':       'Tarjeta Débito/Crédito',
    'pago_movil': 'Pago Móvil',
    'transfer':   'Transferencia',
    'mixed':      'Pago Mixto',
    'credit':     'Crédito (Fiado)',
}


def company_clause(claims, prefix=''):
    cid = claims.get('company_id')
    if cid is not None:
        return f' AND {prefix}company_id = %s', [cid]
    return '', []


def generate_session_number(conn, company_id):
    """Genera número de turno único: CAJA-YYYYMMDD-XXXX.

    Usa la tabla `counters` (incremento atómico) para que dos aperturas
    simultáneas nunca reciban el mismo número.
    """
    today = date.today().strftime('%Y%m%d')

    def _existing_count():
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM cash_sessions WHERE session_number LIKE %s",
            (f'CAJA-{today}-%%',)
        ).fetchone()
        return row['cnt'] if row else 0

    seq = next_counter(conn, f'session_{today}', init_count=_existing_count)
    return f"CAJA-{today}-{seq:04d}{machine_suffix()}"


def build_session_summary(conn, session_id, company_id=None):
    """Calcula el resumen de ventas de una sesión para el Reporte X / Z."""
    company_where = ' AND company_id = %s' if company_id else ''
    company_params = [company_id] if company_id else []

    totals = conn.execute(
        f'''
        SELECT
            COUNT(*) as total_sales,
            COALESCE(SUM(subtotal_usd), 0) as subtotal_usd,
            COALESCE(SUM(tax_usd), 0) as tax_usd,
            COALESCE(SUM(discount_usd), 0) as discount_usd,
            COALESCE(SUM(total_usd), 0) as total_usd,
            COALESCE(SUM(total_bs), 0) as total_bs,
            COALESCE(AVG(total_usd), 0) as avg_ticket_usd
        FROM sales
        WHERE session_id = %s AND status = 'completed'{company_where}
        ''', (session_id, *company_params)
    ).fetchone()

    cancelled = conn.execute(
        f'''
        SELECT COUNT(*) as cnt, COALESCE(SUM(total_usd), 0) as amount
        FROM sales
        WHERE session_id = %s AND status = 'cancelled'{company_where}
        ''', (session_id, *company_params)
    ).fetchone()

    by_method = conn.execute(
        f'''
        SELECT payment_method,
               COUNT(*) as count,
               COALESCE(SUM(total_usd), 0) as total_usd,
               COALESCE(SUM(total_bs), 0) as total_bs,
               COALESCE(SUM(amount_paid_usd), 0) as paid_usd,
               COALESCE(SUM(amount_paid_bs), 0) as paid_bs
        FROM sales
        WHERE session_id = %s AND status = 'completed'{company_where}
        GROUP BY payment_method
        ''', (session_id, *company_params)
    ).fetchall()

    return {
        'totals': dict(totals) if totals else {},
        'cancelled': {'count': cancelled['cnt'] if cancelled else 0,
                      'amount_usd': float(cancelled['amount']) if cancelled else 0},
        'by_payment_method': [dict(r) for r in by_method],
    }


# ──────────────────────────────────────────────────────────────
# POST /api/cash-sessions/open  — Abrir turno de caja
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('/open', methods=['POST'])
@jwt_required()
def open_session():
    claims = get_jwt()
    user_id = int(get_jwt_identity())
    company_id = claims.get('company_id')

    if not company_id:
        return jsonify({'error': 'company_id requerido'}), 400

    data = request.get_json() or {}
    opening_amount_usd = float(data.get('opening_amount_usd', 0))
    opening_amount_bs = float(data.get('opening_amount_bs', 0))
    notes_open = data.get('notes_open', '')

    conn = get_db()

    # Verificar si ya tiene una caja abierta
    existing = conn.execute(
        "SELECT id, session_number FROM cash_sessions WHERE user_id = %s AND company_id = %s AND status = 'open'",
        (user_id, company_id)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({
            'error': f"Ya tienes una caja abierta: {existing['session_number']}. Debes cerrarla antes de abrir una nueva.",
            'session_id': existing['id'],
            'session_number': existing['session_number']
        }), 409

    # Obtener tasa del día (respeta modo manual de la empresa)
    exchange_rate, _, _ = get_today_rate(company_id=company_id)

    session_number = generate_session_number(conn, company_id)

    try:
        cursor = conn.execute(
            '''INSERT INTO cash_sessions
               (company_id, session_number, user_id, opened_by_user_id,
                exchange_rate_open, opening_amount_usd, opening_amount_bs,
                notes_open, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'open')''',
            (company_id, session_number, user_id, user_id,
             exchange_rate, opening_amount_usd, opening_amount_bs, notes_open)
        )
        session_id = cursor.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'error': f'Error al abrir caja: {str(e)}'}), 500

    conn.close()
    return jsonify({
        'message': f'Caja abierta exitosamente',
        'session_id': session_id,
        'session_number': session_number,
        'exchange_rate': exchange_rate,
    }), 201


# ──────────────────────────────────────────────────────────────
# GET /api/cash-sessions/current  — Turno abierto del usuario
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('/current', methods=['GET'])
@jwt_required()
def get_current_session():
    claims = get_jwt()
    user_id = int(get_jwt_identity())
    company_id = claims.get('company_id')
    role = claims.get('role')

    conn = get_db()

    # Admin puede ver la caja de cualquier cajero (query param user_id)
    target_user_id = request.args.get('user_id')
    if target_user_id and role == 'admin':
        target_user_id = int(target_user_id)
    else:
        target_user_id = user_id

    where = ' AND cs.company_id = %s' if company_id else ''
    params = [company_id] if company_id else []

    session = conn.execute(
        f'''SELECT cs.*, u.name as user_name, ou.name as opened_by_name
            FROM cash_sessions cs
            JOIN users u ON cs.user_id = u.id
            JOIN users ou ON cs.opened_by_user_id = ou.id
            WHERE cs.user_id = %s AND cs.status = 'open'{where}
            ORDER BY cs.open_datetime DESC LIMIT 1''',
        (target_user_id, *params)
    ).fetchone()

    conn.close()

    if not session:
        return jsonify({'session': None, 'is_open': False})

    return jsonify({'session': dict(session), 'is_open': True})


# ──────────────────────────────────────────────────────────────
# GET /api/cash-sessions/current/summary  — Reporte X
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('/current/summary', methods=['GET'])
@jwt_required()
def get_current_summary():
    claims = get_jwt()
    user_id = int(get_jwt_identity())
    company_id = claims.get('company_id')

    conn = get_db()

    where = ' AND company_id = %s' if company_id else ''
    params = [company_id] if company_id else []

    session = conn.execute(
        f"SELECT * FROM cash_sessions WHERE user_id = %s AND status = 'open'{where} ORDER BY open_datetime DESC LIMIT 1",
        (user_id, *params)
    ).fetchone()

    if not session:
        conn.close()
        return jsonify({'error': 'No tienes una caja abierta'}), 404

    summary = build_session_summary(conn, session['id'], company_id)
    conn.close()

    return jsonify({
        'session': dict(session),
        'summary': summary,
        'report_type': 'X',
        'generated_at': datetime.now().isoformat(),
    })


# ──────────────────────────────────────────────────────────────
# POST /api/cash-sessions/<id>/close  — Cerrar turno (Reporte Z)
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('/<int:sid>/close', methods=['POST'])
@jwt_required()
def close_session(sid):
    claims = get_jwt()
    user_id = int(get_jwt_identity())
    company_id = claims.get('company_id')
    role = claims.get('role')

    conn = get_db()

    where = ' AND company_id = %s' if company_id else ''
    params = [company_id] if company_id else []

    session = conn.execute(
        f"SELECT * FROM cash_sessions WHERE id = %s{where}",
        (sid, *params)
    ).fetchone()

    if not session:
        conn.close()
        return jsonify({'error': 'Sesión no encontrada'}), 404

    if session['status'] == 'closed':
        conn.close()
        return jsonify({'error': 'Esta caja ya está cerrada'}), 400

    # Solo el cajero dueño o un admin puede cerrar
    if session['user_id'] != user_id and role != 'admin':
        conn.close()
        return jsonify({'error': 'No tienes permiso para cerrar esta caja'}), 403

    data = request.get_json() or {}
    closing_amount_usd = float(data.get('closing_amount_usd', 0))
    closing_amount_bs = float(data.get('closing_amount_bs', 0))
    notes_close = data.get('notes_close', '')
    denominations = data.get('denominations', [])  # [{currency, denomination, quantity}]

    # Tasa actual (respeta modo manual de la empresa)
    exchange_rate_close, _, _ = get_today_rate(company_id=company_id)

    # Calcular resumen de ventas del turno
    summary = build_session_summary(conn, sid, company_id)
    totals = summary['totals']

    # Calcular efectivo esperado
    cash_methods = {'cash_usd', 'cash_bs', 'mixed'}
    expected_cash_usd = float(session['opening_amount_usd'])
    expected_cash_bs = float(session['opening_amount_bs'])

    for pm in summary['by_payment_method']:
        if pm['payment_method'] in cash_methods:
            expected_cash_usd += float(pm.get('paid_usd', 0))
            expected_cash_bs += float(pm.get('paid_bs', 0))

    difference_usd = round(closing_amount_usd - expected_cash_usd, 4)
    difference_bs = round(closing_amount_bs - expected_cash_bs, 4)

    total_sales_count = int(totals.get('total_sales', 0))
    total_revenue_usd = float(totals.get('total_usd', 0))
    total_revenue_bs = float(totals.get('total_bs', 0))
    total_tax_usd = float(totals.get('tax_usd', 0))
    total_discount_usd = float(totals.get('discount_usd', 0))
    cancelled_count = summary['cancelled']['count']
    cancelled_amount = summary['cancelled']['amount_usd']

    try:
        # Actualizar la sesión como cerrada
        conn.execute(
            '''UPDATE cash_sessions SET
                status = 'closed',
                close_datetime = %s,
                closed_by_user_id = %s,
                exchange_rate_close = %s,
                closing_amount_usd = %s,
                closing_amount_bs = %s,
                expected_cash_usd = %s,
                expected_cash_bs = %s,
                difference_usd = %s,
                difference_bs = %s,
                total_sales = %s,
                total_revenue_usd = %s,
                total_revenue_bs = %s,
                total_tax_usd = %s,
                total_discount_usd = %s,
                cancelled_sales = %s,
                cancelled_amount_usd = %s,
                notes_close = %s
               WHERE id = %s''',
            (datetime.now(), user_id, exchange_rate_close,
             closing_amount_usd, closing_amount_bs,
             expected_cash_usd, expected_cash_bs,
             difference_usd, difference_bs,
             total_sales_count, total_revenue_usd, total_revenue_bs,
             total_tax_usd, total_discount_usd,
             cancelled_count, cancelled_amount,
             notes_close, sid)
        )

        # Guardar desglose por método de pago
        conn.execute("DELETE FROM cash_session_payments WHERE session_id = %s", (sid,))
        for pm in summary['by_payment_method']:
            conn.execute(
                '''INSERT INTO cash_session_payments
                   (session_id, payment_method, count, total_usd, total_bs)
                   VALUES (%s,%s,%s,%s,%s)''',
                (sid, pm['payment_method'], pm['count'], pm['total_usd'], pm['total_bs'])
            )

        # Guardar denominaciones si se proveen
        if denominations:
            conn.execute("DELETE FROM cash_session_denominations WHERE session_id = %s", (sid,))
            for d in denominations:
                qty = int(d.get('quantity', 0))
                denom = float(d.get('denomination', 0))
                subtotal = round(qty * denom, 2)
                conn.execute(
                    '''INSERT INTO cash_session_denominations
                       (session_id, currency, denomination, quantity, subtotal)
                       VALUES (%s,%s,%s,%s,%s)''',
                    (sid, d.get('currency', 'USD'), denom, qty, subtotal)
                )

        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'error': f'Error al cerrar caja: {str(e)}'}), 500

    # Recargar la sesión actualizada para el Reporte Z
    session_updated = conn.execute(
        '''SELECT cs.*, u.name as user_name, ou.name as opened_by_name,
                  cu.name as closed_by_name
           FROM cash_sessions cs
           JOIN users u ON cs.user_id = u.id
           JOIN users ou ON cs.opened_by_user_id = ou.id
           LEFT JOIN users cu ON cs.closed_by_user_id = cu.id
           WHERE cs.id = %s''', (sid,)
    ).fetchone()

    denoms = conn.execute(
        "SELECT * FROM cash_session_denominations WHERE session_id = %s ORDER BY currency, denomination DESC",
        (sid,)
    ).fetchall()

    payments = conn.execute(
        "SELECT * FROM cash_session_payments WHERE session_id = %s",
        (sid,)
    ).fetchall()

    conn.close()

    return jsonify({
        'message': 'Caja cerrada exitosamente',
        'session': dict(session_updated),
        'summary': summary,
        'denominations': [dict(d) for d in denoms],
        'payments': [dict(p) for p in payments],
        'report_type': 'Z',
        'generated_at': datetime.now().isoformat(),
        'difference_alert': abs(difference_usd) > 5.0,
    })


# ──────────────────────────────────────────────────────────────
# GET /api/cash-sessions  — Historial de turnos (Admin)
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('', methods=['GET'])
@jwt_required()
def list_sessions():
    claims = get_jwt()
    role = claims.get('role')
    company_id = claims.get('company_id')
    user_id = int(get_jwt_identity())

    date_from = request.args.get('from', date.today().isoformat())
    date_to = request.args.get('to', date.today().isoformat())
    status_filter = request.args.get('status', '')

    conn = get_db()

    where_parts = ['DATE(cs.open_datetime) BETWEEN %s AND %s']
    params = [date_from, date_to]

    if company_id:
        where_parts.append('cs.company_id = %s')
        params.append(company_id)

    # Cajeros solo ven sus propias cajas
    if role == 'cashier':
        where_parts.append('cs.user_id = %s')
        params.append(user_id)

    if status_filter:
        where_parts.append('cs.status = %s')
        params.append(status_filter)

    where_sql = ' AND '.join(where_parts)

    rows = conn.execute(
        f'''SELECT cs.*,
                   u.name as user_name,
                   ou.name as opened_by_name,
                   cu.name as closed_by_name
            FROM cash_sessions cs
            JOIN users u ON cs.user_id = u.id
            JOIN users ou ON cs.opened_by_user_id = ou.id
            LEFT JOIN users cu ON cs.closed_by_user_id = cu.id
            WHERE {where_sql}
            ORDER BY cs.open_datetime DESC
            LIMIT 100''',
        params
    ).fetchall()

    conn.close()
    return jsonify([dict(r) for r in rows])


# ──────────────────────────────────────────────────────────────
# GET /api/cash-sessions/<id>  — Detalle completo de un turno
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('/<int:sid>', methods=['GET'])
@jwt_required()
def get_session(sid):
    claims = get_jwt()
    company_id = claims.get('company_id')
    role = claims.get('role')
    user_id = int(get_jwt_identity())

    conn = get_db()

    where = ' AND cs.company_id = %s' if company_id else ''
    params = [company_id] if company_id else []

    session = conn.execute(
        f'''SELECT cs.*,
                   u.name as user_name,
                   ou.name as opened_by_name,
                   cu.name as closed_by_name
            FROM cash_sessions cs
            JOIN users u ON cs.user_id = u.id
            JOIN users ou ON cs.opened_by_user_id = ou.id
            LEFT JOIN users cu ON cs.closed_by_user_id = cu.id
            WHERE cs.id = %s{where}''',
        (sid, *params)
    ).fetchone()

    if not session:
        conn.close()
        return jsonify({'error': 'Sesión no encontrada'}), 404

    # Cajeros solo pueden ver sus propias cajas
    if role == 'cashier' and session['user_id'] != user_id:
        conn.close()
        return jsonify({'error': 'Acceso denegado'}), 403

    summary = build_session_summary(conn, sid, company_id)

    denoms = conn.execute(
        "SELECT * FROM cash_session_denominations WHERE session_id = %s ORDER BY currency, denomination DESC",
        (sid,)
    ).fetchall()

    payments = conn.execute(
        "SELECT * FROM cash_session_payments WHERE session_id = %s",
        (sid,)
    ).fetchall()

    conn.close()

    return jsonify({
        'session': dict(session),
        'summary': summary,
        'denominations': [dict(d) for d in denoms],
        'payments': [dict(p) for p in payments],
    })


# ──────────────────────────────────────────────────────────────
# POST /api/cash-sessions/<id>/reopen  — Reabrir (solo admin)
# ──────────────────────────────────────────────────────────────
@cash_sessions_bp.route('/<int:sid>/reopen', methods=['POST'])
@jwt_required()
def reopen_session(sid):
    claims = get_jwt()
    role = claims.get('role')

    if role != 'admin':
        return jsonify({'error': 'Solo el administrador puede reabrir una caja'}), 403

    company_id = claims.get('company_id')
    where = ' AND company_id = %s' if company_id else ''
    params = [company_id] if company_id else []

    conn = get_db()
    session = conn.execute(
        f"SELECT * FROM cash_sessions WHERE id = %s{where}",
        (sid, *params)
    ).fetchone()

    if not session:
        conn.close()
        return jsonify({'error': 'Sesión no encontrada'}), 404

    if session['status'] == 'open':
        conn.close()
        return jsonify({'error': 'La sesión ya está abierta'}), 400

    conn.execute(
        "UPDATE cash_sessions SET status='open', close_datetime=NULL, closed_by_user_id=NULL WHERE id=%s",
        (sid,)
    )
    conn.commit()
    conn.close()

    return jsonify({'message': 'Caja reabierta exitosamente'})
