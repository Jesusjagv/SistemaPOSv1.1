from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

customers_bp = Blueprint('customers', __name__)


def company_clause(claims):
    cid = claims.get('company_id')
    if cid is not None:
        return ' AND company_id = %s', [cid]
    return '', []


@customers_bp.route('', methods=['GET'])
@jwt_required()
def list_customers():
    claims = get_jwt()
    search = request.args.get('search', '')
    where, params = company_clause(claims)
    conn = get_db()
    if search:
        rows = conn.execute(
            f'SELECT * FROM customers WHERE (name LIKE %s OR doc_number LIKE %s OR phone LIKE %s){where} ORDER BY name',
            (f'%{search}%', f'%{search}%', f'%{search}%', *params)
        ).fetchall()
    else:
        rows = conn.execute(
            f'SELECT * FROM customers WHERE 1=1{where} ORDER BY name', params
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@customers_bp.route('/<int:cid>', methods=['GET'])
@jwt_required()
def get_customer(cid):
    claims = get_jwt()
    where, params = company_clause(claims)
    conn = get_db()
    cust = conn.execute(
        f'SELECT * FROM customers WHERE id=%s{where}', (cid, *params)
    ).fetchone()
    if not cust:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404
    sales = conn.execute(
        "SELECT id, sale_number, total_usd, total_bs, payment_method, created_at "
        "FROM sales WHERE customer_id=%s ORDER BY created_at DESC LIMIT 10",
        (cid,)
    ).fetchall()
    conn.close()
    result = dict(cust)
    result['recent_sales'] = [dict(s) for s in sales]
    return jsonify(result)


@customers_bp.route('', methods=['POST'])
@jwt_required()
def create_customer():
    try:
        claims = get_jwt()
        data = request.get_json()
        if not data:
            return jsonify({"error": "Datos no válidos"}), 400

        name       = (data.get('name') or '').strip()
        doc_type   = data.get('doc_type', 'V')
        doc_number = (data.get('doc_number') or '').strip() or None
        phone      = (data.get('phone') or '').strip() or None
        address    = (data.get('address') or '').strip() or None

        if not name:
            return jsonify({"error": "El nombre del cliente es requerido"}), 400

        if phone and not re.match(r'^[0-9\-\+\s]{7,15}$', phone):
            return jsonify({"error": "Formato de teléfono inválido"}), 400

        conn = get_db()
        cursor = conn.execute(
            "INSERT INTO customers (name, doc_type, doc_number, phone, address, company_id) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (name, doc_type, doc_number, phone, address, claims.get('company_id'))
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return jsonify({"message": "Cliente creado", "id": new_id}), 201
    except Exception as e:
        return jsonify({"error": f"Error al crear cliente: {str(e)}"}), 500


@customers_bp.route('/<int:cid>', methods=['PUT'])
@jwt_required()
def update_customer(cid):
    claims = get_jwt()
    where, params = company_clause(claims)
    data = request.get_json()
    conn = get_db()

    phone = data.get('phone') or None
    if phone and not re.match(r'^[0-9\-\+\s]{7,15}$', phone):
        return jsonify({"error": "Formato de teléfono inválido"}), 400

    conn.execute(
        f"UPDATE customers SET name=%s, doc_type=%s, doc_number=%s, phone=%s, address=%s "
        f"WHERE id=%s{where}",
        (data.get('name'), data.get('doc_type', 'V'),
         data.get('doc_number') or None, data.get('phone') or None,
         data.get('address') or None, cid, *params)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Cliente actualizado"})


@customers_bp.route('/<int:cid>', methods=['DELETE'])
@jwt_required()
def delete_customer(cid):
    claims = get_jwt()
    where, params = company_clause(claims)
    conn = get_db()
    row = conn.execute(
        f"SELECT uuid FROM customers WHERE id=%s{where}", (cid, *params)
    ).fetchone()
    conn.execute(
        f"DELETE FROM customers WHERE id=%s{where}", (cid, *params)
    )
    if row and row.get('uuid'):
        conn.execute(
            'INSERT IGNORE INTO sync_tombstones (company_id, table_name, row_uuid) VALUES (%s, %s, %s)',
            (claims.get('company_id'), 'customers', row['uuid'])
        )
    conn.commit()
    conn.close()
    return jsonify({"message": "Cliente eliminado"})
