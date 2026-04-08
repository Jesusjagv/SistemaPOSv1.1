from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
import requests
import sys, os
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

exchange_bp = Blueprint('exchange', __name__)

FALLBACK_RATE = 36.50  # Tasa de respaldo en caso de fallo total

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

def get_today_rate():
    """Retorna la tasa del día. Prioriza el caché de BD; solo va a internet si no hay tasa hoy."""
    today_str = date.today().isoformat()
    conn = get_db()

    # 1. Si ya tenemos tasa para HOY → devolver inmediatamente sin tocar internet
    row = conn.execute(
        "SELECT rate, source FROM exchange_rates WHERE date = %s", (today_str,)
    ).fetchone()
    if row:
        conn.close()
        return row['rate'], row['source'], True

    # 2. Si no hay tasa de hoy, usar la última conocida mientras buscamos en internet
    last = conn.execute(
        "SELECT rate, source, date FROM exchange_rates ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()

    # Si tenemos una tasa anterior, devolvemos esa AHORA y actualizamos en background
    if last:
        # Intentar actualizar en background (puede fallar sin problema)
        try:
            rate, source = fetch_bcv_rate_from_api()
            if rate and rate > 0:
                conn2 = get_db()
                conn2.execute(
                    "REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
                    (today_str, rate, source)
                )
                conn2.commit()
                conn2.close()
                return rate, source, False
        except Exception:
            pass
        return last['rate'], f"BCV ({last['date']})", True

    # 3. Primera vez sin ninguna tasa: intentar obtener de internet
    rate, source = fetch_bcv_rate_from_api()
    if rate and rate > 0:
        conn3 = get_db()
        conn3.execute(
            "REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
            (today_str, rate, source)
        )
        conn3.commit()
        conn3.close()
        return rate, source, False

    # Último recurso: tasa de respaldo
    return FALLBACK_RATE, 'Respaldo manual', True

@exchange_bp.route('/rate', methods=['GET'])
@jwt_required()
def get_rate():
    rate, source, cached = get_today_rate()
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
    rate, source = fetch_bcv_rate_from_api()
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
