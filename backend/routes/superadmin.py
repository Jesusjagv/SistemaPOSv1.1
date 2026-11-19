from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from werkzeug.security import generate_password_hash
from functools import wraps
from datetime import datetime, timedelta, timezone
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

superadmin_bp = Blueprint('superadmin', __name__)


def superadmin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        claims = get_jwt()
        if claims.get('role') != 'superadmin':
            return jsonify({"error": "Acceso denegado. Se requiere rol superadmin"}), 403
        return fn(*args, **kwargs)
    return wrapper


@superadmin_bp.route('/', methods=['GET'])
@superadmin_required
def list_companies():
    conn = get_db()
    companies = conn.execute(
        "SELECT id, name, active, status, email, trial_start, trial_end, "
        "(SELECT COUNT(*) FROM users WHERE company_id = companies.id) AS user_count, "
        "(SELECT u.name FROM users u WHERE u.company_id = companies.id AND u.role = 'admin' ORDER BY u.id LIMIT 1) AS admin_name "
        "FROM companies ORDER BY "
        "CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 WHEN 'expired' THEN 2 ELSE 3 END, "
        "name"
    ).fetchall()
    conn.close()

    now = datetime.utcnow()
    result = []
    for c in companies:
        d = dict(c)
        if d.get('trial_end'):
            te = d['trial_end']
            if isinstance(te, str):
                te = datetime.fromisoformat(te)
            d['trial_days_left'] = max((te - now).days, 0)
        else:
            d['trial_days_left'] = None
        result.append(d)

    return jsonify(result)


@superadmin_bp.route('/', methods=['POST'])
@superadmin_required
def register_company():
    data = request.get_json()
    company_name  = data.get('company_name', '').strip()
    admin_username = data.get('admin_username', '').strip()
    admin_password = data.get('admin_password', '')
    admin_name     = data.get('admin_name', '').strip()

    if not all([company_name, admin_username, admin_password, admin_name]):
        return jsonify({"error": "Todos los campos son requeridos: company_name, admin_username, admin_password, admin_name"}), 400

    conn = get_db()
    try:
        conn.execute("START TRANSACTION")

        cur = conn.execute(
            "INSERT INTO companies (name, active, status) VALUES (%s, 1, 'active')",
            (company_name,)
        )
        company_id = cur.lastrowid

        conn.execute(
            "INSERT INTO users (username, password_hash, name, role, company_id, active) "
            "VALUES (%s, %s, %s, 'admin', %s, 1)",
            (admin_username, generate_password_hash(admin_password), admin_name, company_id)
        )

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        return jsonify({"error": f"Error al registrar empresa: {str(e)}"}), 409

    conn.close()
    return jsonify({
        "message": "Empresa registrada exitosamente",
        "company_id": company_id
    }), 201


@superadmin_bp.route('/<int:company_id>', methods=['GET'])
@superadmin_required
def get_company(company_id):
    conn = get_db()
    company = conn.execute(
        "SELECT id, name, active, status, email, trial_start, trial_end FROM companies WHERE id = %s",
        (company_id,)
    ).fetchone()
    if not company:
        conn.close()
        return jsonify({"error": "Empresa no encontrada"}), 404

    users = conn.execute(
        "SELECT id, username, name, role, active, email FROM users WHERE company_id = %s ORDER BY name",
        (company_id,)
    ).fetchall()
    conn.close()

    result = dict(company)
    result['users'] = [dict(u) for u in users]

    if result.get('trial_end'):
        te = result['trial_end']
        if isinstance(te, str):
            te = datetime.fromisoformat(te)
        result['trial_days_left'] = max((te - datetime.utcnow()).days, 0)
    else:
        result['trial_days_left'] = None

    return jsonify(result)


@superadmin_bp.route('/<int:company_id>', methods=['PUT'])
@superadmin_required
def update_company(company_id):
    data = request.get_json()
    conn = get_db()

    if 'active' in data:
        conn.execute(
            "UPDATE companies SET active = %s WHERE id = %s",
            (data['active'], company_id)
        )

    if 'name' in data:
        name = data['name'].strip()
        if name:
            conn.execute(
                "UPDATE companies SET name = %s WHERE id = %s",
                (name, company_id)
            )

    conn.commit()
    conn.close()
    return jsonify({"message": "Empresa actualizada"})


@superadmin_bp.route('/<int:company_id>/approve', methods=['PUT'])
@superadmin_required
def approve_company(company_id):
    data = request.get_json() or {}
    extend_days = data.get('extend_days', 0)

    conn = get_db()
    company = conn.execute(
        "SELECT id, status FROM companies WHERE id = %s", (company_id,)
    ).fetchone()
    if not company:
        conn.close()
        return jsonify({"error": "Empresa no encontrada"}), 404

    if company['status'] == 'active':
        conn.close()
        return jsonify({"error": "La empresa ya esta activa"}), 400

    now = datetime.utcnow()
    if extend_days > 0:
        new_trial_end = now + timedelta(days=extend_days)
        conn.execute(
            "UPDATE companies SET status = 'active', active = 1, trial_start = %s, trial_end = %s WHERE id = %s",
            (now, new_trial_end, company_id)
        )
    else:
        conn.execute(
            "UPDATE companies SET status = 'active', active = 1 WHERE id = %s",
            (company_id,)
        )

    conn.commit()
    conn.close()
    return jsonify({"message": "Empresa aprobada exitosamente"})


@superadmin_bp.route('/<int:company_id>/reject', methods=['PUT'])
@superadmin_required
def reject_company(company_id):
    conn = get_db()
    company = conn.execute(
        "SELECT id, status FROM companies WHERE id = %s", (company_id,)
    ).fetchone()
    if not company:
        conn.close()
        return jsonify({"error": "Empresa no encontrada"}), 404

    conn.execute(
        "UPDATE companies SET status = 'rejected', active = 0 WHERE id = %s",
        (company_id,)
    )
    conn.execute(
        "UPDATE users SET active = 0 WHERE company_id = %s AND role != 'superadmin'",
        (company_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Empresa rechazada"})


@superadmin_bp.route('/<int:company_id>/extend-trial', methods=['PUT'])
@superadmin_required
def extend_trial(company_id):
    data = request.get_json()
    days = data.get('days', 7)

    if not isinstance(days, int) or days < 1 or days > 365:
        return jsonify({"error": "Dias invalidos (1-365)"}), 400

    conn = get_db()
    company = conn.execute(
        "SELECT id, trial_end FROM companies WHERE id = %s", (company_id,)
    ).fetchone()
    if not company:
        conn.close()
        return jsonify({"error": "Empresa no encontrada"}), 404

    now = datetime.utcnow()
    base = now
    if company.get('trial_end'):
        te = company['trial_end']
        if isinstance(te, str):
            te = datetime.fromisoformat(te)
        if te > now:
            base = te

    new_trial_end = base + timedelta(days=days)
    conn.execute(
        "UPDATE companies SET trial_end = %s, status = 'pending', active = 1 WHERE id = %s",
        (new_trial_end, company_id)
    )
    conn.commit()
    conn.close()
    return jsonify({
        "message": f"Demo extendida {days} dias. Nueva fecha: {new_trial_end.strftime('%Y-%m-%d')}",
        "trial_end": new_trial_end.isoformat()
    })
