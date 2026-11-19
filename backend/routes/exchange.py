from flask import Blueprint, jsonify, has_app_context
from flask_jwt_extended import jwt_required, get_jwt
import requests
import sys, os
import threading
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

exchange_bp = Blueprint('exchange', __name__)

FALLBACK_RATE = 36.50  # Tasa de respaldo en caso de fallo total

# Un solo fetch de tasa en background a la vez (evita el "thundering herd"
# del primer request de cada día, que podía bloquear los threads del servidor)
_rate_refresh_lock = threading.Lock()

def fetch_bcv_rate_from_api():
    """Intenta obtener la tasa BCV de múltiples fuentes."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json'
    }

    # Fuente 1: ve.dolarapi.com
    try:
        resp = requests.get(
            'https://ve.dolarapi.com/v1/dolares/oficial',
            headers=headers, timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = float(data.get('promedio') or data.get('precio') or 0)
            if rate > 0:
                print(f"✅ Tasa BCV obtenida (ve.dolarapi.com): {rate} Bs/$")
                return rate, 've.dolarapi.com'
    except Exception as e:
        print(f"⚠️  ve.dolarapi.com falló: {e}")

    # Fuente 2: pydolarve.org
    try:
        resp = requests.get(
            'https://pydolarve.org/api/v1/dollar?page=bcv',
            headers=headers, timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            monitors = data.get('monitors', {})
            usd = monitors.get('usd', {})
            rate = float(usd.get('price') or 0)
            if rate > 0:
                print(f"✅ Tasa BCV obtenida (pydolarve.org): {rate} Bs/$")
                return rate, 'pydolarve.org'
    except Exception as e:
        print(f"⚠️  pydolarve.org falló: {e}")

    # Fuente 3: Scraping directo de BCV
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            'https://www.bcv.org.ve/',
            headers={**headers, 'Accept': 'text/html'},
            timeout=10
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'lxml')
            usd_div = soup.find('div', {'id': 'dolar'})
            if usd_div:
                strong = usd_div.find('strong')
                if strong:
                    rate_text = strong.text.strip().replace(',', '.')
                    rate = float(rate_text)
                    if rate > 0:
                        print(f"✅ Tasa BCV obtenida (BCV scraping): {rate} Bs/$")
                        return rate, 'BCV scraping'
    except Exception as e:
        print(f"⚠️  BCV scraping falló: {e}")

    return None, None


def _spawn_rate_refresh(today_str):
    """Lanza la descarga de la tasa en un hilo daemon (no bloquea la request).

    Solo puede haber una actualización en curso; si ya hay una, no hace nada.
    """
    if not _rate_refresh_lock.acquire(blocking=False):
        return  # ya hay una actualización en curso

    def _worker():
        try:
            rate, source = fetch_bcv_rate_from_api()
            if rate and rate > 0:
                conn = get_db()  # sin contexto de request: hay que cerrar a mano
                try:
                    conn.execute(
                        "REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
                        (today_str, rate, source)
                    )
                    conn.commit()
                finally:
                    conn.close()
                print(f"✅ Tasa BCV actualizada en background: {rate} Bs/$ ({source})")
        except Exception as e:
            print(f"⚠️  Actualización de tasa en background falló: {e}")
        finally:
            _rate_refresh_lock.release()

    threading.Thread(target=_worker, daemon=True, name='bcv-rate-refresh').start()

def get_today_rate(company_id=None):
    """Retorna la tasa del día. Si la empresa tiene modo manual, retorna esa tasa."""
    today_str = date.today().isoformat()
    conn = get_db()
    owns_conn = not has_app_context()

    # Check if company has manual rate mode
    if company_id is not None:
        company = conn.execute(
            "SELECT rate_mode, manual_rate FROM companies WHERE id=%s", (company_id,)
        ).fetchone()
        if company and company['rate_mode'] == 'manual' and company['manual_rate']:
            if owns_conn:
                conn.close()
            return float(company['manual_rate']), 'Manual', True

    # 1. Si ya tenemos tasa para HOY → devolver inmediatamente sin tocar internet
    row = conn.execute(
        "SELECT rate, source FROM exchange_rates WHERE date = %s", (today_str,)
    ).fetchone()
    if row:
        if owns_conn:
            conn.close()
        return row['rate'], row['source'], True

    # 2. Si no hay tasa de hoy, usar la última conocida mientras buscamos en internet
    last = conn.execute(
        "SELECT rate, source, date FROM exchange_rates ORDER BY date DESC LIMIT 1"
    ).fetchone()

    # Si tenemos una tasa anterior, devolvemos esa YA y actualizamos en
    # background (nunca bloquear la request con HTTP externo)
    if last:
        _spawn_rate_refresh(today_str)
        if owns_conn:
            conn.close()
        return last['rate'], f"BCV ({last['date']})", True

    # 3. Primera vez sin ninguna tasa: intentar obtener de internet
    rate, source = fetch_bcv_rate_from_api()
    if rate and rate > 0:
        conn.execute(
            "REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
            (today_str, rate, source)
        )
        conn.commit()
        if owns_conn:
            conn.close()
        return rate, source, False

    if owns_conn:
        conn.close()
    # Último recurso: tasa de respaldo
    return FALLBACK_RATE, 'Respaldo manual', True

@exchange_bp.route('/rate', methods=['GET'])
@jwt_required()
def get_rate():
    claims = get_jwt()
    company_id = claims.get('company_id')
    rate, source, cached = get_today_rate(company_id=company_id)
    return jsonify({
        "rate": rate,
        "source": source,
        "date": date.today().isoformat(),
        "cached": cached,
        "formatted": f"1 USD = {rate:,.2f} Bs"
    })

@exchange_bp.route('/rate/force', methods=['POST'])
@jwt_required()
def force_update():
    """Fuerza la actualización de la tasa ignorando el caché."""
    if not _rate_refresh_lock.acquire(blocking=False):
        return jsonify({"error": "Ya hay una actualización de tasa en curso"}), 429
    try:
        rate, source = fetch_bcv_rate_from_api()
    finally:
        _rate_refresh_lock.release()
    if not rate:
        return jsonify({"error": "No se pudo obtener la tasa BCV"}), 503

    today_str = date.today().isoformat()
    conn = get_db()
    conn.execute(
        "REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
        (today_str, rate, source)
    )
    conn.commit()
    conn.close()
    return jsonify({
        "rate": rate,
        "source": source,
        "date": today_str,
        "message": f"Tasa actualizada: 1 USD = {rate:,.2f} Bs"
    })

@exchange_bp.route('/history', methods=['GET'])
@jwt_required()
def history():
    conn = get_db()
    rows = conn.execute(
        "SELECT date, rate, source FROM exchange_rates ORDER BY date DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
