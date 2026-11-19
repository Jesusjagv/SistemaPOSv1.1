# -*- coding: utf-8 -*-
"""Smoke de concurrencia para el POS.

Valida, contra un servidor corriendo (por defecto http://localhost:5000):

1. 160 ventas simultáneas (32 hilos x 5): todas 201, sale_number únicos
   y stock final exacto (sin carreras).
2. Lecturas de tasa BCV concurrentes: todas 200.
3. Venta con stock insuficiente: 400 y rollback (stock no cambia).
4. Rate limiting de login: 5 fallos -> 429 en el 6to; IP distinta no bloqueada.

Requiere: servidor arribado con DATABASE_URL de la BD de tests.
Uso:  .venv\\Scripts\\python.exe scripts\\smoke_concurrency.py
"""
import os
import sys
import threading
import time
import collections

import requests
import pymysql

BASE = os.environ.get('SMOKE_BASE_URL', 'http://localhost:5000')
DB = dict(
    host='localhost', port=3306, user='root', password='15508984',
    database='SistemaPOSv1_1_test', charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor,
)

THREADS = 32
SALES_PER_THREAD = 5
RATE_THREADS = 8
RATE_CALLS_PER_THREAD = 10
INITIAL_STOCK = 100000

failures = []
_lock = threading.Lock()


def fail(msg):
    with _lock:
        failures.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  OK: {msg}")


def db_conn():
    return pymysql.connect(**DB)


def main():
    ts = str(int(time.time()))
    print(f"== Smoke de concurrencia contra {BASE} ==")

    # 1. Login admin
    r = requests.post(f'{BASE}/api/auth/login',
                      json={'username': 'admin', 'password': 'admin123'}, timeout=30)
    if r.status_code != 200:
        print(f"Login admin falló: {r.status_code} {r.text}")
        return 1
    token = r.json()['token']
    auth = {'Authorization': f'Bearer {token}'}
    ok("login admin")

    # 2. Sembrar tasa de hoy (evita llamadas HTTP externas durante el smoke)
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO exchange_rates (date, rate, source) VALUES (CURDATE(), 36.50, 'smoke') "
        "ON DUPLICATE KEY UPDATE rate = rate"
    )
    conn.commit()

    # 3. Crear producto de prueba
    code = f'SMOKE-{ts}'
    r = requests.post(f'{BASE}/api/products', headers=auth, json={
        'name': f'Producto Smoke {ts}',
        'code': code,
        'price_usd': 1.0,
        'stock': INITIAL_STOCK,
        'min_stock': 1,
    }, timeout=30)
    if r.status_code != 201:
        print(f"Creación de producto falló: {r.status_code} {r.text}")
        conn.close()
        return 1
    product_id = r.json()['id']
    ok(f"producto id={product_id} stock={INITIAL_STOCK}")

    # 4. Carga concurrente: ventas + lecturas de tasa
    sale_results = []
    rate_results = []
    start = time.time()

    def sale_worker():
        for _ in range(SALES_PER_THREAD):
            try:
                resp = requests.post(f'{BASE}/api/sales', headers=auth, json={
                    'items': [{'product_id': product_id, 'quantity': 1}],
                    'exchange_rate': 36.50,
                    'payment_method': 'cash_usd',
                    'amount_paid_usd': 10,
                }, timeout=60)
                body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
                with _lock:
                    sale_results.append((resp.status_code, body))
            except Exception as e:
                with _lock:
                    sale_results.append((0, {'error': str(e)}))

    def rate_worker():
        for _ in range(RATE_CALLS_PER_THREAD):
            try:
                resp = requests.get(f'{BASE}/api/exchange/rate', headers=auth, timeout=30)
                body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
                with _lock:
                    rate_results.append((resp.status_code, body))
            except Exception as e:
                with _lock:
                    rate_results.append((0, {'error': str(e)}))

    threads = [threading.Thread(target=sale_worker) for _ in range(THREADS)]
    threads += [threading.Thread(target=rate_worker) for _ in range(RATE_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    # 5. Validar ventas
    expected_sales = THREADS * SALES_PER_THREAD
    ok_statuses = [b for s, b in sale_results if s == 201]
    bad = [(s, b) for s, b in sale_results if s != 201]
    print(f"-- {len(sale_results)} ventas en {elapsed:.1f}s "
          f"({len(sale_results) / elapsed:.1f} ventas/s)")
    if bad:
        fail(f"{len(bad)} ventas con status != 201. Ejemplo: {bad[0]}")
    else:
        ok(f"todas las {expected_sales} ventas respondieron 201")

    numbers = [b.get('sale_number') for b in ok_statuses if b.get('sale_number')]
    dupes = [n for n, c in collections.Counter(numbers).items() if c > 1]
    if dupes:
        fail(f"sale_number duplicados: {dupes[:5]}")
    else:
        ok(f"{len(numbers)} sale_number únicos (VEN-YYYYMMDD-XXXX)")

    # 6. Validar tasa concurrente
    rate_bad = [(s, b) for s, b in rate_results if s != 200 or not b.get('rate')]
    if rate_bad:
        fail(f"{len(rate_bad)}/{len(rate_results)} lecturas de tasa fallidas. Ejemplo: {rate_bad[0]}")
    else:
        ok(f"{len(rate_results)} lecturas de tasa BCV todas 200")

    # 7. Stock final exacto (sin carreras)
    cur.execute("SELECT stock FROM products WHERE id = %s", (product_id,))
    final_stock = float(cur.fetchone()['stock'])
    expected_stock = INITIAL_STOCK - len(ok_statuses)
    if final_stock != expected_stock:
        fail(f"stock final {final_stock} != esperado {expected_stock} "
             f"(ventas OK: {len(ok_statuses)})")
    else:
        ok(f"stock final exacto: {final_stock} = {INITIAL_STOCK} - {len(ok_statuses)}")

    # 8. Venta con stock insuficiente debe dar 400 y no tocar el stock
    r = requests.post(f'{BASE}/api/sales', headers=auth, json={
        'items': [{'product_id': product_id, 'quantity': expected_stock + 1000}],
        'exchange_rate': 36.50,
        'payment_method': 'cash_usd',
        'amount_paid_usd': 10,
    }, timeout=30)
    if r.status_code != 400:
        fail(f"venta con stock insuficiente devolvió {r.status_code}, esperado 400")
    else:
        cur.execute("SELECT stock FROM products WHERE id = %s", (product_id,))
        stock_after = float(cur.fetchone()['stock'])
        if stock_after != expected_stock:
            fail(f"rollback falló: stock {stock_after} != {expected_stock}")
        else:
            ok("stock insuficiente -> 400 y rollback correcto")

    # 9. Rate limiting de login (X-Forwarded-For)
    rl_user = f'smoke_rl_{ts}'
    xff1 = {'X-Forwarded-For': '203.0.113.10'}
    statuses = []
    for _ in range(5):
        rr = requests.post(f'{BASE}/api/auth/login', headers=xff1, json={
            'username': rl_user, 'password': 'bad-pass'}, timeout=30)
        statuses.append(rr.status_code)
    rr = requests.post(f'{BASE}/api/auth/login', headers=xff1, json={
        'username': rl_user, 'password': 'bad-pass'}, timeout=30)
    sixth = rr.status_code
    if statuses == [401] * 5 and sixth == 429:
        ok("rate limit: 5x401 -> 6to 429 (misma IP)")
    else:
        fail(f"rate limit inesperado: {statuses} + 6to={sixth}")

    rr = requests.post(f'{BASE}/api/auth/login',
                       headers={'X-Forwarded-For': '203.0.113.20'}, json={
                           'username': rl_user, 'password': 'bad-pass'}, timeout=30)
    if rr.status_code == 401:
        ok("rate limit: IP distinta (XFF) no está bloqueada")
    else:
        fail(f"IP distinta con XFF debería ser 401, obtuvo {rr.status_code}")

    conn.close()

    print()
    if failures:
        print(f"== SMOKE FALLÓ: {len(failures)} problema(s) ==")
        for f_ in failures:
            print(f"  - {f_}")
        return 1
    print("== SMOKE OK: todas las validaciones pasaron ==")
    return 0


if __name__ == '__main__':
    sys.exit(main())
