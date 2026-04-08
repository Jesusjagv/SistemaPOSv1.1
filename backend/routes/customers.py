from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

customers_bp = Blueprint('customers', __name__)

@customers_bp.route('', methods=['GET'])
@jwt_required()
def list_customers():
    search = request.args.get('search', '')
    conn = get_db()
    if search:
        rows = conn.execute(
            "SELECT * FROM customers WHERE name LIKE %s OR doc_number LIKE %s OR phone LIKE %s ORDER BY name",
            (f'%{search}%', f'%{search}%', f'%{search}%')
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@customers_bp.route('/<int:cid>', methods=['GET'])
@jwt_required()
def get_customer(cid):
    conn = get_db()
    cust = conn.execute("SELECT * FROM customers WHERE id=%s", (cid,)).fetchone()
    if not cust:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404
    # Sales history
    sales = conn.execute(
        "SELECT id, sale_number, total_usd, total_bs, payment_method, created_at FROM sales WHERE customer_id=%s ORDER BY created_at DESC LIMIT 10",
        (cid,)
    ).fetchall()
    conn.close()
    result = dict(cust)
    result['recent_sales'] = [dict(s) for s in sales]
    return jsonify(result)

@customers_bp.route('', methods=['POST'])
@jwt_required()
def create_customer():
    data = request.get_json()
    name       = data.get('name', '').strip()
    doc_type   = data.get('doc_type', 'V')
    doc_number = data.get('doc_number', '').strip() or None
    phone      = data.get('phone', '').strip() or None
    email      = data.get('email', '').strip() or None
    address    = data.get('address', '').strip() or None

    if not name:
        return jsonify({"error": "El nombre del cliente es requerido"}), 400

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO customers (name, doc_type, doc_number, phone, email, address) VALUES (%s,%s,%s,%s,%s,%s)",
        (name, doc_type, doc_number, phone, email, address)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({"message": "Cliente creado", "id": new_id}), 201

@customers_bp.route('/<int:cid>', methods=['PUT'])
@jwt_required()
def update_customer(cid):
    data = request.get_json()
    conn = get_db()
    conn.execute(
        "UPDATE customers SET name=%s, doc_type=%s, doc_number=%s, phone=%s, email=%s, address=%s WHERE id=%s",
        (
            data.get('name'), data.get('doc_type', 'V'),
            data.get('doc_number') or None, data.get('phone') or None,
            data.get('email') or None, data.get('address') or None, cid
        )
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Cliente actualizado"})

@customers_bp.route('/<int:cid>', methods=['DELETE'])
@jwt_required()
def delete_customer(cid):
    conn = get_db()
    conn.execute("DELETE FROM customers WHERE id=%s", (cid,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Cliente eliminado"})
