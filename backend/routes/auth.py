from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity, get_jwt
from werkzeug.security import check_password_hash, generate_password_hash
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"error": "Usuario y contraseña requeridos"}), 400

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username = %s AND active = 1", (username,)
    ).fetchone()
    conn.close()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({"error": "Credenciales incorrectas"}), 401

    token = create_access_token(
        identity=str(user['id']),
        additional_claims={"role": user['role'], "name": user['name']}
    )
    return jsonify({
        "token": token,
        "user": {
            "id": user['id'],
            "username": user['username'],
            "name": user['name'],
            "role": user['role']
        }
    })

@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def me():
    user_id = get_jwt_identity()
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, name, role FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    conn.close()
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404
    return jsonify(dict(user))

@auth_bp.route('/users', methods=['GET'])
@jwt_required()
def list_users():
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    conn = get_db()
    users = conn.execute(
        "SELECT id, username, name, role, active, created_at FROM users ORDER BY name"
    ).fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@auth_bp.route('/users', methods=['POST'])
@jwt_required()
def create_user():
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    name     = data.get('name', '').strip()
    role     = data.get('role', 'cashier')

    if not all([username, password, name]):
        return jsonify({"error": "Campos requeridos faltantes"}), 400
    if role not in ('admin', 'cashier'):
        return jsonify({"error": "Rol inválido"}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, name, role) VALUES (%s,%s,%s,%s)",
            (username, generate_password_hash(password), name, role)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": "El nombre de usuario ya existe"}), 409
    conn.close()
    return jsonify({"message": "Usuario creado exitosamente"}), 201

@auth_bp.route('/users/<int:uid>', methods=['PUT'])
@jwt_required()
def update_user(uid):
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403
    data = request.get_json()
    conn = get_db()
    if data.get('password'):
        conn.execute(
            "UPDATE users SET name=%s, role=%s, active=%s, password_hash=%s WHERE id=%s",
            (data.get('name'), data.get('role'), data.get('active', 1),
             generate_password_hash(data['password']), uid)
        )
    else:
        conn.execute(
            "UPDATE users SET name=%s, role=%s, active=%s WHERE id=%s",
            (data.get('name'), data.get('role'), data.get('active', 1), uid)
        )
    conn.commit()
    conn.close()
    return jsonify({"message": "Usuario actualizado"})
