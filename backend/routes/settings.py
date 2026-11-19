from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

settings_bp = Blueprint('settings', __name__)


def company_clause(claims, prefix=''):
    cid = claims.get('company_id')
    if cid is not None:
        return f' AND {prefix}company_id = %s', [cid]
    return '', []


# ── Exchange Rate Settings ──────────────────────────────────

@settings_bp.route('/exchange-rate', methods=['GET'])
@jwt_required()
def get_exchange_rate_settings():
    claims = get_jwt()
    company_id = claims.get('company_id')
    conn = get_db()

    if company_id is not None:
        row = conn.execute(
            "SELECT rate_mode, manual_rate FROM companies WHERE id=%s", (company_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT rate_mode, manual_rate FROM companies WHERE rate_mode IS NOT NULL LIMIT 1"
        ).fetchone()

    conn.close()

    if row:
        return jsonify({
            "mode": row['rate_mode'] or 'auto',
            "manual_rate": float(row['manual_rate']) if row['manual_rate'] else None
        })
    return jsonify({"mode": "auto", "manual_rate": None})


@settings_bp.route('/exchange-rate', methods=['PUT'])
@jwt_required()
def update_exchange_rate_settings():
    claims = get_jwt()
    if claims.get('role') != 'admin':
        return jsonify({"error": "Acceso denegado"}), 403

    company_id = claims.get('company_id')
    if company_id is None:
        return jsonify({"error": "Superadmin no tiene empresa asociada"}), 400

    data = request.get_json()
    mode = data.get('mode', 'auto')
    manual_rate = data.get('manual_rate')

    if mode not in ('auto', 'manual'):
        return jsonify({"error": "Modo inválido. Use 'auto' o 'manual'"}), 400

    if mode == 'manual':
        try:
            manual_rate = float(manual_rate)
            if manual_rate <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "La tasa manual debe ser un número mayor a 0"}), 400
    else:
        manual_rate = None

    conn = get_db()
    conn.execute(
        "UPDATE companies SET rate_mode=%s, manual_rate=%s WHERE id=%s",
        (mode, manual_rate, company_id)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "message": f"Modo de tasa cambiado a {'manual' if mode == 'manual' else 'automático'}",
        "mode": mode,
        "manual_rate": manual_rate
    })


# ── General Settings ────────────────────────────────────────

@settings_bp.route('', methods=['GET'])
@jwt_required()
def get_settings():
    claims = get_jwt()
    company_id = claims.get('company_id')
    conn = get_db()

    if company_id is not None:
        row = conn.execute(
            "SELECT rate_mode, manual_rate FROM companies WHERE id=%s", (company_id,)
        ).fetchone()
    else:
        row = None

    conn.close()

    return jsonify({
        "exchange_rate": {
            "mode": row['rate_mode'] or 'auto' if row else 'auto',
            "manual_rate": float(row['manual_rate']) if row and row['manual_rate'] else None
        }
    })
