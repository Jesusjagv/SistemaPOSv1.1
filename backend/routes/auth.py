from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity, get_jwt
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
import sys, os
import time
import threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

auth_bp = Blueprint('auth', __name__)

TRIAL_DAYS = 7

# ── Rate limiting de login (en memoria, ventana deslizante) ──
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutos
_login_attempts = {}
_login_attempts_lock = threading.Lock()


def _client_ip():
    """IP real del cliente: X-Forwarded-For (primera IP) o remote_addr."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        ip = xff.split(',')[0].strip()
        if ip:
            return ip
    return request.remote_addr or 'unknown'


def _login_key(ip, username):
    return f"{ip}|{username.lower()}"


def _prune_attempts(now):
    """Elimina entradas expiradas para que el diccionario no crezca sin límite."""
    expired = [
        k for k, stamps in _login_attempts.items()
        if not any(now - t < LOGIN_WINDOW_SECONDS for t in stamps)
    ]
    for k in expired:
        del _login_attempts[k]


def check_login_rate(ip, username):
    """Devuelve (permitido, segundos_restantes) según la ventana deslizante."""
    key = _login_key(ip, username)
    now = time.time()
    with _login_attempts_lock:
        stamps = [t for t in _login_attempts.get(key, [])
                  if now - t < LOGIN_WINDOW_SECONDS]
        if len(stamps) >= LOGIN_MAX_ATTEMPTS:
            _login_attempts[key] = stamps
            retry_in = int(LOGIN_WINDOW_SECONDS - (now - stamps[0])) + 1
            return False, retry_in
        _login_attempts[key] = stamps
        return True, 0


def register_login_fail(ip, username):
    key = _login_key(ip, username)
    now = time.time()
    with _login_attempts_lock:
        stamps = [t for t in _login_attempts.get(key, [])
                  if now - t < LOGIN_WINDOW_SECONDS]
        stamps.append(now)
        _login_attempts[key] = stamps
        _prune_attempts(now)


def clear_login_attempts(ip, username):
    with _login_attempts_lock:
        _login_attempts.pop(_login_key(ip, username), None)

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    company_name   = data.get('company_name', '').strip()
    admin_name     = data.get('admin_name', '').strip()
    admin_email    = data.get('admin_email', '').strip()
    admin_username = data.get('admin_username', '').strip()
    admin_password = data.get('admin_password', '')

    if not all([company_name, admin_name, admin_username, admin_password]):
        return jsonify({"error": "Todos los campos son requeridos"}), 400

    if len(admin_password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 400

    now = datetime.utcnow()
    trial_end = now + timedelta(days=TRIAL_DAYS)

    conn = get_db()
    try:
        conn.execute("START TRANSACTION")

        cur = conn.execute(
            "INSERT INTO companies (name, active, status, email, trial_start, trial_end) "
            "VALUES (%s, 1, 'pending', %s, %s, %s)",
            (company_name, admin_email or None, now, trial_end)
        )
        company_id = cur.lastrowid

        conn.execute(
            "INSERT INTO users (username, password_hash, name, email, role, company_id, active) "
            "VALUES (%s, %s, %s, %s, 'admin', %s, 1)",
            (admin_username, generate_password_hash(admin_password), admin_name,
             admin_email or None, company_id)
        )

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        if 'Duplicate' in str(e) or 'duplicate' in str(e):
            return jsonify({"error": "El nombre de usuario o email ya existe"}), 409
        return jsonify({"error": f"Error al registrar: {str(e)}"}), 500

    conn.close()
    return jsonify({
        "message": f"Registro exitoso. Tienes {TRIAL_DAYS} dias de demo gratis.",
        "trial_days": TRIAL_DAYS
    }), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"error": "Usuario y contraseña requeridos"}), 400

    # Rate limiting: 5 intentos fallidos cada 5 minutos por IP + usuario
    client_ip = _client_ip()
    allowed, retry_in = check_login_rate(client_ip, username)
    if not allowed:
        return jsonify({
            "error": f"Demasiados intentos de acceso. Intenta de nuevo en {retry_in} segundos."
        }), 429

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username = %s AND active = 1", (username,)
    ).fetchone()

    if not user or not check_password_hash(user['password_hash'], password):
        register_login_fail(client_ip, username)
        conn.close()
        return jsonify({"error": "Credenciales incorrectas"}), 401

    clear_login_attempts(client_ip, username)

    # Check company trial status (skip for superadmin)
    company_status = None
    trial_end = None
    trial_days_left = None
    if user['role'] != 'superadmin' and user.get('company_id'):
        company = conn.execute(
            "SELECT status, trial_end FROM companies WHERE id = %s",
            (user['company_id'],)
        ).fetchone()
        if company:
            company_status = company['status']
            trial_end = company['trial_end']

            if company_status == 'rejected':
                conn.close()
                return jsonify({"error": "Tu cuenta ha sido rechazada. Contacta al administrador."}), 403

            if company_status == 'expired':
                conn.close()
                return jsonify({"error": "Tu cuenta ha expirada. Contacta al administrador."}), 403

            if company_status == 'pending' and trial_end:
                from datetime import datetime as _dt
                if isinstance(trial_end, str):
                    trial_end_dt = _dt.fromisoformat(trial_end)
                else:
                    trial_end_dt = trial_end
                remaining = (trial_end_dt - _dt.utcnow()).days
                trial_days_left = max(remaining, 0)
                if remaining < 0:
                    # Trial expired, auto-expire the company
                    conn.execute("UPDATE companies SET status = 'expired' WHERE id = %s",
                                 (user['company_id'],))
                    conn.commit()
                    conn.close()
                    return jsonify({"error": "Tu periodo de demo ha expirada. Contacta al administrador."}), 403

    conn.close()

    token = create_access_token(
        identity=str(user['id']),
        additional_claims={
            "role": user['role'],
            "name": user['name'],
            "company_id": user.get('company_id')
        }
    )
    return jsonify({
        "token": token,
        "user": {
            "id": user['id'],
            "username": user['username'],
            "name": user['name'],
            "role": user['role'],
            "company_id": user.get('company_id')
        },
        "company_status": company_status,
        "trial_days_left": trial_days_left
    })

@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def me():
    user_id = get_jwt_identity()
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, name, role, company_id FROM users WHERE id = %s", (user_id,)
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
    company_id = claims.get('company_id')
    if company_id:
        users = conn.execute(
            "SELECT id, username, name, role, active, created_at FROM users WHERE company_id = %s ORDER BY name",
            (company_id,)
        ).fetchall()
    else:
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
    company_id = claims.get('company_id')
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, name, role, company_id) VALUES (%s,%s,%s,%s,%s)",
            (username, generate_password_hash(password), name, role, company_id)
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
    company_id = claims.get('company_id')
    params_base = []
    where_extra = ''
    if company_id:
        where_extra = ' AND company_id = %s'
        params_base.append(company_id)
    if data.get('password'):
        conn.execute(
            'UPDATE users SET name=%s, role=%s, active=%s, password_hash=%s WHERE id=%s' + where_extra,
            (data.get('name'), data.get('role'), data.get('active', 1),
             generate_password_hash(data['password']), uid, *params_base)
        )
    else:
        conn.execute(
            'UPDATE users SET name=%s, role=%s, active=%s WHERE id=%s' + where_extra,
            (data.get('name'), data.get('role'), data.get('active', 1), uid, *params_base)
        )
    conn.commit()
    conn.close()
    return jsonify({"message": "Usuario actualizado"})

@auth_bp.route('/me/password', methods=['PUT'])
@jwt_required()
def change_own_password():
    """Permite a un usuario autenticado cambiar su propia contraseña.
    Se verifica la contraseña actual y se actualiza con un hash nuevo.
    """
    user_id = get_jwt_identity()
    data = request.get_json()
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    if not old_password or not new_password:
        return jsonify({"error": "Contraseñas requeridas"}), 400
    conn = get_db()
    user = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,)).fetchone()
    if not user or not check_password_hash(user['password_hash'], old_password):
        conn.close()
        return jsonify({"error": "Contraseña actual incorrecta"}), 400
    conn.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new_password), user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Contraseña actualizada"}), 200


# ══════════════════════════════════════════════════════════════════
# RECUPERACIÓN POR PREGUNTAS DE SEGURIDAD
# ══════════════════════════════════════════════════════════════════

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """Paso 1: El usuario ingresa su usuario.
    Devuelve las preguntas de seguridad configuradas para ese usuario.
    """
    data = request.get_json()
    username = data.get('username', '').strip()

    if not username:
        return jsonify({"error": "El usuario es requerido"}), 400

    conn = get_db()
    user = conn.execute(
        "SELECT id, username, name FROM users WHERE username = %s AND active = 1",
        (username,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"error": "Usuario no encontrado"}), 404

    # Obtener preguntas de seguridad del usuario
    questions = conn.execute(
        "SELECT question, answer FROM security_questions WHERE user_id = %s",
        (user['id'],)
    ).fetchall()
    conn.close()

    if not questions:
        return jsonify({
            "error": "Este usuario no tiene preguntas de seguridad configuradas. Contacta al administrador."
        }), 400

    # Devolver solo las preguntas (sin respuestas)
    return jsonify({
        "username": user['username'],
        "name": user['name'],
        "questions": [q['question'] for q in questions]
    }), 200


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """Paso 2: El usuario responde las preguntas y crea una nueva contraseña.
    """
    data = request.get_json()
    username = data.get('username', '').strip()
    answers = data.get('answers', [])
    new_password = data.get('new_password', '')

    if not all([username, answers, new_password]):
        return jsonify({"error": "Todos los campos son requeridos"}), 400

    if len(new_password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 400

    conn = get_db()
    user = conn.execute(
        "SELECT id FROM users WHERE username = %s AND active = 1",
        (username,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({"error": "Usuario no encontrado"}), 404

    # Obtener preguntas y respuestas guardadas
    questions = conn.execute(
        "SELECT question, answer FROM security_questions WHERE user_id = %s ORDER BY id",
        (user['id'],)
    ).fetchall()

    if not questions:
        conn.close()
        return jsonify({"error": "No hay preguntas de seguridad configuradas"}), 400

    # Verificar que todas las respuestas coincidan (case-insensitive, trim)
    for i, q in enumerate(questions):
        user_answer = answers[i].strip().lower() if i < len(answers) else ''
        saved_answer = q['answer'].strip().lower()
        if user_answer != saved_answer:
            conn.close()
            return jsonify({"error": "Una o más respuestas son incorrectas"}), 400

    # Actualizar contraseña
    conn.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new_password), user['id'])
    )
    conn.commit()
    conn.close()

    return jsonify({"message": "Contraseña actualizada exitosamente. Ya puedes iniciar sesión."}), 200


@auth_bp.route('/security-questions', methods=['GET'])
@jwt_required()
def get_security_questions():
    """Obtiene las preguntas de seguridad de un usuario (para admin ver o usuario ver las suyas).
    Si se pasa ?user_id=, ese admin puede ver las de otro usuario.
    """
    claims = get_jwt()
    target_user_id = request.args.get('user_id')

    # Si es admin y pide otro usuario, puede verlo
    if claims.get('role') == 'admin' and target_user_id:
        user_id = target_user_id
    else:
        user_id = get_jwt_identity()

    conn = get_db()
    questions = conn.execute(
        "SELECT id, question, answer FROM security_questions WHERE user_id = %s ORDER BY id",
        (user_id,)
    ).fetchall()
    conn.close()

    return jsonify([dict(q) for q in questions])


@auth_bp.route('/security-questions', methods=['PUT'])
@jwt_required()
def update_security_questions():
    """Actualiza las preguntas de seguridad de un usuario.
    Body: { questions: [{ question: "...", answer: "..." }, ...] }
    """
    user_id = get_jwt_identity()
    data = request.get_json()
    questions = data.get('questions', [])

    if not questions:
        return jsonify({"error": "Debe configurar al menos una pregunta"}), 400

    # Validar que tengan pregunta y respuesta
    for q in questions:
        if not q.get('question', '').strip() or not q.get('answer', '').strip():
            return jsonify({"error": "Todas las preguntas deben tener respuesta"}), 400

    conn = get_db()

    # Eliminar preguntas anteriores
    conn.execute("DELETE FROM security_questions WHERE user_id = %s", (user_id,))

    # Insertar nuevas
    for q in questions:
        conn.execute(
            "INSERT INTO security_questions (user_id, question, answer) VALUES (%s, %s, %s)",
            (user_id, q['question'].strip(), q['answer'].strip())
        )

    conn.commit()
    conn.close()

    return jsonify({"message": "Preguntas de seguridad actualizadas"}), 200


@auth_bp.route('/security-questions/all', methods=['GET'])
@jwt_required()
def list_all_questions():
    """Lista todas las preguntas predefinidas disponibles."""
    predefined = [
        "¿Cuál es el nombre de tu mascota?",
        "¿En qué ciudad naciste?",
        "¿Cuál es tu color favorito?",
        "¿Cuál es el nombre de tu mejor amigo de la infancia?",
        "¿Cuál es tu comida favorita?",
        "¿Cuál es tu hobby favorito?",
        "¿En qué escuela primaria estudiaste?",
        "¿Cuál es tu canción favorita?",
        "¿Cuál es tu deporte favorito?",
        "¿Cuál es tu película favorita?"
    ]
    return jsonify({"questions": predefined})


@auth_bp.route('/has-security-questions', methods=['GET'])
@jwt_required()
def has_security_questions():
    """Verifica si el usuario autenticado tiene preguntas de seguridad configuradas."""
    user_id = get_jwt_identity()
    conn = get_db()
    result = conn.execute(
        "SELECT COUNT(*) as count FROM security_questions WHERE user_id = %s",
        (user_id,)
    ).fetchone()
    conn.close()
    return jsonify({"has_questions": result['count'] > 0})
