"""Tests E2E offline-first: escritorio SQLite <-> nube MySQL (Fase 2).

La "nube" es la app Flask de tests (MySQL, conftest). El "escritorio" es una
segunda instancia de backend/app.py sobre un SQLite temporal, sincronizada
con SyncEngine + FlaskTestTransport (sin red).
"""
import importlib.util
import os
import re
import sys
import uuid as uuid_module
from datetime import date
from types import SimpleNamespace

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BACKEND)

import database  # noqa: E402
from database import get_db  # noqa: E402
from sync_engine import CloudClient, FlaskTestTransport, SyncEngine  # noqa: E402


@pytest.fixture
def offline(tmp_path, monkeypatch, app):
    """App local (SQLite temporal) + engine apuntando a la app-nube."""
    db_url = 'sqlite:///' + (tmp_path / 'local.db').as_posix()
    monkeypatch.setenv('DATABASE_URL', db_url)
    monkeypatch.setenv('CLOUD_URL', 'https://nube.test')  # init sin seeds
    spec = importlib.util.spec_from_file_location(
        'offline_app', os.path.join(BACKEND, 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.app.config['DATABASE_URL'] = db_url
    database.init_db()  # sin CLOUD_URL no: con ella, esquema sin seeds
    monkeypatch.delenv('CLOUD_URL')

    client = CloudClient(base_url='', transport=FlaskTestTransport(app),
                         user='admin', password='admin123')
    engine = SyncEngine(
        db_factory=lambda: database._get_sqlite_wrapper(db_url, engine_mode=True),
        client=client,
        interval=0.01,
    )
    yield SimpleNamespace(local_app=mod.app, engine=engine, db_url=db_url)


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _local_login(local_app):
    r = local_app.test_client().post(
        '/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['token']


def _local_conn(ns):
    return database._get_sqlite_wrapper(ns.db_url, engine_mode=True)


def _cloud_sql(app, sql, params=()):
    with app.app_context():
        # los tests escriben placeholders estilo sqlite (?); la nube es MySQL
        return get_db().execute(sql.replace('?', '%s'), params).fetchone()


def _create_product_payload(name, price=2.5, stock=10, **extra):
    payload = {'name': name, 'price_usd': price, 'cost_usd': 1.0,
               'stock': stock, 'min_stock': 1}
    payload.update(extra)
    return payload


class TestBootstrap:
    def test_primer_pull_puebla_local(self, offline):
        offline.engine.pull()
        token = _local_login(offline.local_app)
        r = offline.local_app.test_client().get('/api/products', headers=_h(token))
        assert r.status_code == 200
        products = r.get_json()
        assert len(products) >= 6  # semillas de la nube
        assert all(p['uuid'] for p in products)

        conn = _local_conn(offline)
        dirty = conn.execute(
            'SELECT COUNT(*) c FROM products WHERE sync_dirty = 1').fetchone()['c']
        assert dirty == 0
        users = conn.execute('SELECT COUNT(*) c FROM users').fetchone()['c']
        assert users >= 2  # admin + cajero (el superadmin no tiene empresa)

    def test_pull_es_idempotente(self, offline):
        offline.engine.pull()
        conn = _local_conn(offline)
        before = conn.execute('SELECT COUNT(*) c FROM products').fetchone()['c']
        offline.engine.pull()
        after = conn.execute('SELECT COUNT(*) c FROM products').fetchone()['c']
        assert after == before


class TestPushLocalANube:
    def test_categoria_producto_y_cliente_llegan(self, offline, app):
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()

        r = c.post('/api/products/categories/all',
                   json={'name': 'SyncCat', 'color': '#123456'}, headers=_h(token))
        assert r.status_code == 201
        cat_id = r.get_json()['id']

        r = c.post('/api/products',
                   json=_create_product_payload('Prod Sync', category_id=cat_id),
                   headers=_h(token))
        assert r.status_code == 201
        prod_id = r.get_json()['id']

        r = c.post('/api/customers',
                   json={'name': 'Cliente Sync', 'doc_number': 'V-123'},
                   headers=_h(token))
        assert r.status_code == 201

        conn = _local_conn(ns)
        puuid = conn.execute(
            'SELECT uuid FROM products WHERE id = ?', (prod_id,)).fetchone()['uuid']
        cpuuid = conn.execute(
            "SELECT uuid FROM categories WHERE id = ?", (cat_id,)).fetchone()['uuid']
        cuu = conn.execute(
            "SELECT uuid FROM customers WHERE name = 'Cliente Sync'").fetchone()['uuid']

        res = ns.engine.push()
        assert res['errors'] == 0, res

        cloud_company = _cloud_sql(app, "SELECT id FROM companies WHERE name = 'Mi Empresa'")
        row = _cloud_sql(app, 'SELECT * FROM products WHERE uuid = ?', (puuid,))
        assert row is not None
        assert row['name'] == 'Prod Sync'
        assert row['company_id'] == cloud_company['id']
        assert abs(float(row['price_usd']) - 2.5) < 0.01
        assert row['stock'] == 10
        assert _cloud_sql(app, 'SELECT * FROM categories WHERE uuid = ?', (cpuuid,)) is not None
        assert _cloud_sql(app, 'SELECT * FROM customers WHERE uuid = ?', (cuu,)) is not None

        # idempotencia: segundo push no duplica ni falla
        res2 = ns.engine.push()
        assert res2.get('sent', 0) == 0, res2
        assert _cloud_sql(app,
                          'SELECT COUNT(*) c FROM products WHERE uuid = ?',
                          (puuid,))['c'] == 1

    def test_venta_local_resta_stock_en_la_nube(self, offline, app):
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()

        r = c.post('/api/products',
                   json=_create_product_payload('Prod Venta', stock=10),
                   headers=_h(token))
        assert r.status_code == 201
        pid = r.get_json()['id']
        ns.engine.push()

        r = c.post('/api/sales', json={
            'items': [{'product_id': pid, 'quantity': 3}],
            'exchange_rate': 36.5,
            'payment_method': 'cash_usd',
            'amount_paid_usd': 20.0,
            'amount_paid_bs': 0,
            'customer_name': 'Consumidor Final',
        }, headers=_h(token))
        assert r.status_code == 201, r.get_json()
        sale_number = r.get_json()['sale_number']
        assert re.match(r'^VEN-\d{8}-\d{4}$', sale_number), sale_number

        ns.engine.push()

        conn = _local_conn(ns)
        puuid = conn.execute(
            'SELECT uuid FROM products WHERE id = ?', (pid,)).fetchone()['uuid']
        srow = _cloud_sql(app, 'SELECT * FROM sales WHERE sale_number = %s',
                          (sale_number,))
        assert srow is not None
        assert srow['status'] == 'completed'
        # delta: nube 10 - 3 vendidas = 7
        prow = _cloud_sql(app, 'SELECT stock FROM products WHERE uuid = ?', (puuid,))
        assert prow['stock'] == 7, prow['stock']
        n_items = _cloud_sql(
            app, 'SELECT COUNT(*) c FROM sale_items WHERE sale_id = %s',
            (srow['id'],))
        assert n_items['c'] == 1

    def test_sesion_de_caja_y_machine_id(self, offline, app, monkeypatch):
        monkeypatch.setenv('MACHINE_ID', 'T1')
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()

        # tasa local (evita el fetch externo de BCV en get_today_rate);
        # puede que el pull ya la haya traído desde la nube
        conn = _local_conn(ns)
        today = date.today().isoformat()
        if not conn.execute(
                'SELECT id FROM exchange_rates WHERE date = ?', (today,)).fetchone():
            conn.execute(
                'INSERT INTO exchange_rates (date, rate, source, uuid, updated_at, sync_dirty) '
                'VALUES (?, ?, ?, ?, ?, 1)',
                (today, 36.5, 'test', str(uuid_module.uuid4()), today + ' 00:00:00'))

        r = c.post('/api/cash-sessions/open',
                   json={'opening_amount_usd': 50}, headers=_h(token))
        assert r.status_code == 201, r.get_json()
        session_number = r.get_json()['session_number']
        assert session_number.endswith('-T1'), session_number

        ns.engine.push()
        row = _cloud_sql(app,
                         'SELECT * FROM cash_sessions WHERE session_number = %s',
                         (session_number,))
        assert row is not None
        # la tasa también viaja
        assert _cloud_sql(app,
                          'SELECT COUNT(*) c FROM exchange_rates WHERE date = %s',
                          (today,))['c'] >= 1

    def test_machine_id_en_venta(self, offline, monkeypatch):
        monkeypatch.setenv('MACHINE_ID', 'MID01')
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()
        r = c.post('/api/products',
                   json=_create_product_payload('Prod MID'), headers=_h(token))
        pid = r.get_json()['id']
        r = c.post('/api/sales', json={
            'items': [{'product_id': pid, 'quantity': 1}],
            'exchange_rate': 36.5, 'payment_method': 'cash_usd',
            'amount_paid_usd': 5.0, 'amount_paid_bs': 0,
        }, headers=_h(token))
        assert r.status_code == 201
        assert re.match(r'^VEN-\d{8}-\d{4}-MID01$', r.get_json()['sale_number'])


class TestPullNubeALocal:
    def test_creacion_en_nube_llega_local(self, offline, client, admin_auth):
        offline.engine.pull()
        r = client.post('/api/products',
                        json=_create_product_payload('Prod Nube', price=9.99),
                        headers=admin_auth)
        assert r.status_code == 201

        offline.engine.pull()
        token = _local_login(offline.local_app)
        r = offline.local_app.test_client().get(
            '/api/products?search=Prod Nube', headers=_h(token))
        assert r.status_code == 200
        found = r.get_json()
        assert len(found) == 1
        assert found[0]['name'] == 'Prod Nube'
        assert found[0]['uuid'] is not None
        assert found[0]['sync_dirty'] == 0

    def test_borrado_en_nube_borra_local(self, offline, client, admin_auth):
        offline.engine.pull()
        r = client.post('/api/products',
                        json=_create_product_payload('Prod Borrar Nube'),
                        headers=admin_auth)
        assert r.status_code == 201
        cloud_pid = r.get_json()['id']

        offline.engine.pull()  # local recibe el producto
        conn = _local_conn(offline)
        assert conn.execute(
            "SELECT COUNT(*) c FROM products WHERE name = 'Prod Borrar Nube'"
        ).fetchone()['c'] == 1

        r = client.delete(f'/api/products/{cloud_pid}', headers=admin_auth)
        assert r.status_code == 200

        offline.engine.pull()  # tombstone
        assert conn.execute(
            "SELECT COUNT(*) c FROM products WHERE name = 'Prod Borrar Nube'"
        ).fetchone()['c'] == 0


class TestBorradoLocal:
    def test_borrado_local_se_propaga_a_la_nube(self, offline, app):
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()
        r = c.post('/api/products',
                   json=_create_product_payload('Prod Local Del'), headers=_h(token))
        pid = r.get_json()['id']
        ns.engine.push()

        conn = _local_conn(ns)
        puuid = conn.execute(
            'SELECT uuid FROM products WHERE id = ?', (pid,)).fetchone()['uuid']
        assert _cloud_sql(app, 'SELECT id FROM products WHERE uuid = ?',
                          (puuid,)) is not None

        r = c.delete(f'/api/products/{pid}', headers=_h(token))
        assert r.status_code == 200
        ns.engine.push()

        assert _cloud_sql(app, 'SELECT id FROM products WHERE uuid = ?',
                          (puuid,)) is None
        # la tombstone local ya se limpió tras el push correcto
        assert conn.execute(
            'SELECT COUNT(*) c FROM sync_tombstones').fetchone()['c'] == 0


class TestLWW:
    def _put_cloud_product(self, client, admin_auth, app, uuid_, **changes):
        row = _cloud_sql(app, 'SELECT * FROM products WHERE uuid = ?', (uuid_,))
        payload = {
            'name': row['name'], 'code': row['code'],
            'category_id': row['category_id'],
            'price_usd': float(row['price_usd']),
            'cost_usd': float(row['cost_usd']),
            'unidades_por_paquete': row['unidades_por_paquete'],
            'stock': row['stock'], 'min_stock': row['min_stock'],
            'active': row['active'], 'is_bulk': row['is_bulk'],
        }
        payload.update(changes)
        r = client.put(f"/api/products/{row['id']}", json=payload,
                       headers=admin_auth)
        assert r.status_code == 200, r.get_json()

    def test_version_mas_nueva_de_la_nube_gana(self, offline, app, client, admin_auth):
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()
        r = c.post('/api/products',
                   json=_create_product_payload('Prod LWW'), headers=_h(token))
        pid = r.get_json()['id']
        ns.engine.push()
        conn = _local_conn(ns)
        puuid = conn.execute(
            'SELECT uuid FROM products WHERE id = ?', (pid,)).fetchone()['uuid']

        # la nube edita...
        self._put_cloud_product(client, admin_auth, app, puuid,
                                name='Version Nube')
        # ...y lo local queda con un updated_at MÁS VIEJO (fuerza el conflicto)
        conn.execute(
            "UPDATE products SET name = 'Version Local', updated_at = "
            "'2020-01-01 00:00:00', sync_dirty = 1 WHERE id = ?", (pid,))

        ns.engine.push()   # la nube lo ignora (LWW: 2020 < su versión)
        ns.engine.pull()   # lo local adopta la versión de la nube

        row = conn.execute('SELECT name, sync_dirty FROM products WHERE id = ?',
                           (pid,)).fetchone()
        assert row['name'] == 'Version Nube'
        assert _cloud_sql(app, 'SELECT name FROM products WHERE uuid = ?',
                          (puuid,))['name'] == 'Version Nube'

    def test_version_mas_nueva_local_gana(self, offline, app):
        offline.engine.pull()
        ns = offline
        token = _local_login(ns.local_app)
        c = ns.local_app.test_client()
        r = c.post('/api/products',
                   json=_create_product_payload('Prod LWW2'), headers=_h(token))
        pid = r.get_json()['id']
        ns.engine.push()
        conn = _local_conn(ns)
        puuid = conn.execute(
            'SELECT uuid FROM products WHERE id = ?', (pid,)).fetchone()['uuid']

        conn.execute(
            "UPDATE products SET name = 'Local Futuro', updated_at = "
            "'2030-01-01 00:00:00', sync_dirty = 1 WHERE id = ?", (pid,))
        ns.engine.push()

        assert _cloud_sql(app, 'SELECT name FROM products WHERE uuid = ?',
                          (puuid,))['name'] == 'Local Futuro'
