from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db
from datetime import datetime
import pymysql

products_bp = Blueprint('products', __name__)

# ── Categories (must be before /<int:pid> to avoid route conflict) ──
@products_bp.route('/categories/all', methods=['GET'])
@jwt_required()
def list_categories():
    conn = get_db()
    cats = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(c) for c in cats])

@products_bp.route('/categories/all', methods=['POST'])
@jwt_required()
def create_category():
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    data = request.get_json()
    name  = data.get('name', '').strip()
    color = data.get('color', '#7c3aed')
    if not name:
        return jsonify({"error": "Nombre requerido"}), 400
    conn = get_db()
    cursor = conn.execute("INSERT INTO categories (name, color) VALUES (%s,%s)", (name, color))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({"message": "Categoría creada", "id": new_id}), 201

# ── Products ────────────────────────────────────────────────
@products_bp.route('', methods=['GET'])
@jwt_required()
def list_products():
    search    = request.args.get('search', '')
    category  = request.args.get('category', '')
    active    = request.args.get('active', '1')
    conn = get_db()
    query = '''
        SELECT p.*, c.name as category_name, c.color as category_color
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE 1=1
    '''
    params = []
    if search:
        query += " AND (p.name LIKE %s OR p.code LIKE %s)"
        params += [f'%{search}%', f'%{search}%']
    if category:
        query += " AND p.category_id = %s"
        params.append(int(category))
    if active != 'all':
        query += " AND p.active = %s"
        params.append(int(active))
    query += " ORDER BY p.name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@products_bp.route('/<int:pid>', methods=['GET'])
@jwt_required()
def get_product(pid):
    conn = get_db()
    row = conn.execute(
        "SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id=c.id WHERE p.id=%s",
        (pid,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Producto no encontrado"}), 404
    return jsonify(dict(row))

@products_bp.route('', methods=['POST'])
@jwt_required()
def create_product():
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    data = request.get_json()
    name      = data.get('name', '').strip()
    code      = data.get('code', '').strip() or None
    cat_id    = data.get('category_id') or None
    price_usd = float(data.get('price_usd', 0))
    cost_usd  = float(data.get('cost_usd', 0))
    stock     = int(data.get('stock', 0))
    min_stock = int(data.get('min_stock', 5))

    if not name:
        return jsonify({"error": "El nombre del producto es requerido"}), 400
    if price_usd <= 0:
        return jsonify({"error": "El precio debe ser mayor a 0"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO products (name, code, category_id, price_usd, cost_usd, stock, min_stock) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (name, code, cat_id, price_usd, cost_usd, stock, min_stock)
        )
        conn.commit()
        pid = cursor.lastrowid
    except Exception as e:
        conn.close()
        if 'Duplicate entry' in str(e):
            return jsonify({"error": "El código de producto ya existe"}), 409
        return jsonify({"error": str(e)}), 500
    conn.close()
    return jsonify({"message": "Producto creado", "id": pid}), 201

@products_bp.route('/<int:pid>', methods=['PUT'])
@jwt_required()
def update_product(pid):
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    data = request.get_json()
    conn = get_db()
    try:
        conn.execute('''
            UPDATE products SET name=%s, code=%s, category_id=%s, price_usd=%s, cost_usd=%s,
            stock=%s, min_stock=%s, active=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s
        ''', (
            data.get('name'), data.get('code') or None, data.get('category_id') or None,
            float(data.get('price_usd', 0)), float(data.get('cost_usd', 0)),
            int(data.get('stock', 0)), int(data.get('min_stock', 5)),
            int(data.get('active', 1)), pid
        ))
        conn.commit()
    except Exception as e:
        conn.close()
        if 'Duplicate entry' in str(e):
            return jsonify({"error": "El código de producto ya existe"}), 409
        return jsonify({"error": str(e)}), 500
    conn.close()
    return jsonify({"message": "Producto actualizado"})

@products_bp.route('/<int:pid>', methods=['DELETE'])
@jwt_required()
def delete_product(pid):
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    conn = get_db()
    try:
        conn.execute("DELETE FROM products WHERE id=%s", (pid,))
        conn.commit()
    except pymysql.err.IntegrityError:
        conn.close()
        return jsonify({"error": "No se puede eliminar un producto con historial de ventas; use el estado Inactivo."}), 400
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500
    conn.close()
    return jsonify({"message": "Producto eliminado permanentemente"})

@products_bp.route('/<int:pid>/stock', methods=['PATCH'])
@jwt_required()
def update_stock(pid):
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    data = request.get_json()
    new_stock = int(data.get('stock', 0))
    conn = get_db()
    conn.execute("UPDATE products SET stock=%s WHERE id=%s", (new_stock, pid))
    conn.commit()
    conn.close()
    return jsonify({"message": "Stock actualizado"})


