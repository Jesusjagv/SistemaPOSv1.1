from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db
from datetime import datetime
import pymysql

products_bp = Blueprint('products', __name__)


def calculate_unit_fields(cost_usd, unidades_por_paquete, precio_unidad=None, margen_unidad_porcentaje=None):
    ux = max(unidades_por_paquete, 1)
    costo_unidad = round(cost_usd / ux, 2) if cost_usd > 0 else 0

    if precio_unidad is not None and precio_unidad > 0:
        precio_unidad = round(precio_unidad, 2)
        margen_unidad_porcentaje = round(((precio_unidad - costo_unidad) / precio_unidad) * 100, 2)
        if margen_unidad_porcentaje < 0:
            margen_unidad_porcentaje = 0
    elif margen_unidad_porcentaje is not None and margen_unidad_porcentaje > 0:
        margen_unidad_porcentaje = min(margen_unidad_porcentaje, 99.99)
        precio_unidad = round(costo_unidad / (1 - (margen_unidad_porcentaje / 100)), 2)
    else:
        precio_unidad = 0
        margen_unidad_porcentaje = 0

    return costo_unidad, precio_unidad, margen_unidad_porcentaje


def company_clause(claims, prefix=''):
    """Return (where_sql, params_list) for company_id filtering.
       When company_id is NULL (superadmin/legacy), no filter is applied.
       Use prefix when the query uses a table alias (e.g. 'p.')."""
    cid = claims.get('company_id')
    if cid is not None:
        return f' AND {prefix}company_id = %s', [cid]
    return '', []


# ── Categories (must be before /<int:pid> to avoid route conflict) ──

@products_bp.route('/categories/all', methods=['GET'])
@jwt_required()
def list_categories():
    claims = get_jwt()
    where, params = company_clause(claims)
    conn = get_db()
    cats = conn.execute(
        f'SELECT * FROM categories WHERE 1=1{where} ORDER BY name', params
    ).fetchall()
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
    cursor = conn.execute(
        'INSERT INTO categories (name, color, company_id) VALUES (%s,%s,%s)',
        (name, color, claims.get('company_id'))
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({"message": "Categoría creada", "id": new_id}), 201


@products_bp.route('/categories/<int:cid>', methods=['DELETE'])
@jwt_required()
def delete_category(cid):
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
        
    conn = get_db()
    where, params = company_clause(claims)
    
    cursor = conn.execute('SELECT id, uuid FROM categories WHERE id = %s' + where, [cid] + params)
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Categoría no encontrada"}), 404
        
    conn.execute('UPDATE products SET category_id = NULL WHERE category_id = %s' + where, [cid] + params)
    conn.execute('DELETE FROM categories WHERE id = %s' + where, [cid] + params)
    if row.get('uuid'):
        conn.execute(
            'INSERT IGNORE INTO sync_tombstones (company_id, table_name, row_uuid) VALUES (%s, %s, %s)',
            (claims.get('company_id'), 'categories', row['uuid'])
        )
    conn.commit()
    conn.close()
    return jsonify({"message": "Categoría eliminada"})


# ── Products ────────────────────────────────────────────────

@products_bp.route('', methods=['GET'])
@jwt_required()
def list_products():
    claims   = get_jwt()
    search   = request.args.get('search', '')
    category = request.args.get('category', '')
    active   = request.args.get('active', '1')
    where, params = company_clause(claims, 'p.')
    query = '''
        SELECT p.*, c.name as category_name, c.color as category_color,
               (CASE WHEN p.is_combo = 1 THEN
                   (SELECT IFNULL(MIN(FLOOR(ing.stock / ci.quantity)), 0)
                    FROM combo_items ci
                    JOIN products ing ON ci.ingredient_product_id = ing.id
                    WHERE ci.combo_product_id = p.id)
                ELSE p.stock END) as computed_stock
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE 1=1
    ''' + where
    if search:
        query += ' AND (p.name LIKE %s OR p.code LIKE %s)'
        params += [f'%{search}%', f'%{search}%']
    if category:
        query += ' AND p.category_id = %s'
        params.append(int(category))
    if active != 'all':
        query += ' AND p.active = %s'
        params.append(int(active))
    query += ' ORDER BY p.name'
    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    result = []
    for r in rows:
        d = dict(r)
        if d.get('is_combo') == 1:
            d['stock'] = d.get('computed_stock', 0)
        result.append(d)
        
    return jsonify(result)


@products_bp.route('/<int:pid>', methods=['GET'])
@jwt_required()
def get_product(pid):
    claims = get_jwt()
    where, params = company_clause(claims, 'p.')
    conn = get_db()
    row = conn.execute(
        'SELECT p.*, c.name as category_name FROM products p '
        'LEFT JOIN categories c ON p.category_id=c.id '
        f'WHERE p.id=%s{where}',
        (pid, *params)
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
    code      = (data.get('code') or '').strip() or None
    cat_id    = data.get('category_id') or None
    price_usd = float(data.get('price_usd', 0))
    cost_usd  = float(data.get('cost_usd', 0))
    stock     = int(data.get('stock', 0))
    min_stock = int(data.get('min_stock', 5))
    unidades  = int(data.get('unidades_por_paquete', 1))

    if not name:
        return jsonify({"error": "El nombre del producto es requerido"}), 400
    if price_usd <= 0:
        return jsonify({"error": "El precio debe ser mayor a 0"}), 400

    costo_unidad, precio_unidad, margen = calculate_unit_fields(
        cost_usd, unidades,
        data.get('precio_unidad'),
        data.get('margen_unidad_porcentaje')
    )

    precio_paquete = data.get('precio_paquete')
    if precio_paquete is not None:
        precio_paquete = round(float(precio_paquete), 2)
    else:
        precio_paquete = price_usd

    conn = get_db()
    try:
        is_combo = int(data.get('is_combo', 0))
        is_bulk = int(data.get('is_bulk', 0))
        cursor = conn.execute(
            'INSERT INTO products (name, code, category_id, price_usd, cost_usd, '
            'unidades_por_paquete, costo_unidad, precio_unidad, precio_paquete, margen_unidad_porcentaje, '
            'stock, min_stock, company_id, is_combo, is_bulk) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (name, code, cat_id, price_usd, cost_usd,
             unidades, costo_unidad, precio_unidad, precio_paquete, margen,
             stock, min_stock, claims.get('company_id'), is_combo, is_bulk)
        )
        pid = cursor.lastrowid
        
        if is_combo and data.get('ingredients'):
            for ing in data['ingredients']:
                conn.execute(
                    'INSERT INTO combo_items (combo_product_id, ingredient_product_id, quantity) VALUES (%s, %s, %s)',
                    (pid, ing['product_id'], ing['quantity'])
                )
        conn.commit()
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
    where, params = company_clause(claims)

    cost_usd  = float(data.get('cost_usd', 0))
    unidades  = int(data.get('unidades_por_paquete', 1))
    costo_unidad, precio_unidad, margen = calculate_unit_fields(
        cost_usd, unidades,
        data.get('precio_unidad'),
        data.get('margen_unidad_porcentaje')
    )

    precio_paquete = data.get('precio_paquete')
    if precio_paquete is not None:
        precio_paquete = round(float(precio_paquete), 2)
    else:
        precio_paquete = float(data.get('price_usd', 0))

    conn = get_db()
    try:
        is_bulk = int(data.get('is_bulk', 0))
        conn.execute(
            'UPDATE products SET name=%s, code=%s, category_id=%s, price_usd=%s, cost_usd=%s, '
            'unidades_por_paquete=%s, costo_unidad=%s, precio_unidad=%s, precio_paquete=%s, margen_unidad_porcentaje=%s, '
            'stock=%s, min_stock=%s, active=%s, is_bulk=%s, updated_at=CURRENT_TIMESTAMP '
            f'WHERE id=%s{where}',
            (data.get('name'), data.get('code') or None, data.get('category_id') or None,
             float(data.get('price_usd', 0)), cost_usd,
             unidades, costo_unidad, precio_unidad, precio_paquete, margen,
             int(data.get('stock', 0)), int(data.get('min_stock', 5)),
             int(data.get('active', 1)), is_bulk, pid, *params)
        )
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
    where, params = company_clause(claims)
    conn = get_db()
    row = conn.execute(
        f'SELECT uuid FROM products WHERE id=%s{where}', (pid, *params)
    ).fetchone()
    try:
        conn.execute(
            f'DELETE FROM products WHERE id=%s{where}', (pid, *params)
        )
        if row and row.get('uuid'):
            conn.execute(
                'INSERT IGNORE INTO sync_tombstones (company_id, table_name, row_uuid) VALUES (%s, %s, %s)',
                (claims.get('company_id'), 'products', row['uuid'])
            )
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
    where, params = company_clause(claims)
    conn = get_db()

    mode = data.get('mode', 'add')

    product = conn.execute(
        f'SELECT stock, unidades_por_paquete FROM products WHERE id=%s{where}',
        (pid, *params)
    ).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "Producto no encontrado"}), 404

    previous_stock = float(product['stock'])

    if mode == 'audit':
        real_stock = float(data.get('real_stock', 0))
        difference = real_stock - previous_stock
        new_stock = real_stock
        movement_type = 'audit_merma'
        reason = data.get('reason', f'Auditoría: stock real {real_stock} (anterior: {previous_stock})')
    else:
        packages = data.get('packages')
        if packages is not None:
            ux = max(int(product['unidades_por_paquete']), 1)
            new_stock = int(packages) * ux
        else:
            new_stock = int(data.get('stock', 0))
        difference = new_stock - previous_stock
        if difference > 0:
            movement_type = 'add'
        elif difference < 0:
            movement_type = 'remove'
        else:
            movement_type = 'add'
        reason = data.get('reason', None)

    update_fields = ['stock=%s']
    update_values = [new_stock]
    
    # Costo de reposición (optional)
    if 'cost_usd' in data and 'price_usd' in data:
        cost_usd = float(data['cost_usd'])
        price_usd = float(data['price_usd'])
        unidades = int(product['unidades_por_paquete'])
        unit_price = price_usd / max(unidades, 1) if price_usd > 0 else None
        costo_unidad, precio_unidad, margen = calculate_unit_fields(cost_usd, unidades, unit_price, None)
        precio_paquete = price_usd

        update_fields.extend([
            'cost_usd=%s', 'price_usd=%s', 'precio_paquete=%s',
            'costo_unidad=%s', 'precio_unidad=%s', 'margen_unidad_porcentaje=%s'
        ])
        update_values.extend([cost_usd, price_usd, precio_paquete, costo_unidad, precio_unidad, margen])
        
        # Modify reason to reflect price change
        if reason is None:
            reason = f"Ingreso de mercancía con nuevo costo: ${cost_usd}"

    update_values.extend([pid, *params])
    set_clause = ', '.join(update_fields)

    conn.execute(
        f'UPDATE products SET {set_clause} WHERE id=%s{where}',
        tuple(update_values)
    )

    conn.execute(
        'INSERT INTO inventory_movements (product_id, user_id, type, quantity_changed, previous_stock, new_stock, reason) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s)',
        (pid, claims.get('sub'), movement_type, difference, previous_stock, new_stock, reason)
    )

    conn.commit()
    conn.close()
    return jsonify({"message": "Stock actualizado", "stock": new_stock, "difference": difference})
