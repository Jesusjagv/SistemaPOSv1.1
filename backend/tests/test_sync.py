"""Tests de la API de sincronización offline (push/pull, LWW, tombstones)."""
from datetime import datetime, timedelta

import pymysql


class _Conn:
    """Mini-wrapper: misma API .execute() que DBWrapper (la BD de prueba)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, args=None):
        cur = self._conn.cursor()
        cur.execute(sql, args)
        return cur

    def close(self):
        self._conn.close()


def _db():
    return _Conn(pymysql.connect(
        host='localhost', port=3306, user='root', password='15508984',
        database='SistemaPOSv1_1_test',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    ))


def _company_id(name='Mi Empresa'):
    conn = _db()
    row = conn.execute("SELECT id FROM companies WHERE name=%s", (name,)).fetchone()
    conn.close()
    return row['id']


def _now(offset=0):
    return (datetime.now() + timedelta(seconds=offset)).strftime('%Y-%m-%d %H:%M:%S')


def _product_change(uuid='p-sync-001', offset=0, **row_fields):
    row = {'name': 'Producto Sync', 'code': 'SYNC001', 'price_usd': 1.5,
           'stock': 10, 'min_stock': 2}
    row.update(row_fields)
    return {'table': 'products', 'uuid': uuid, 'updated_at': _now(offset),
            'row': row}


class TestSyncAuth:

    def test_pull_requires_token(self, client):
        assert client.get('/api/sync/pull').status_code == 401

    def test_push_requires_token(self, client):
        assert client.post('/api/sync/push', json={'changes': []}).status_code == 401


class TestSyncPush:

    def test_push_insert_and_company_stamp(self, client, admin_auth):
        r = client.post('/api/sync/push', json={'changes': [
            _product_change('p-insert-01', code='SYNCI01')
        ]}, headers=admin_auth)
        assert r.status_code == 200
        body = r.get_json()
        assert body['applied'] == 1 and body['errors'] == []
        conn = _db()
        row = conn.execute(
            "SELECT * FROM products WHERE uuid=%s", ('p-insert-01',)
        ).fetchone()
        conn.close()
        assert row is not None
        assert int(row['company_id']) == _company_id()
        assert row['updated_at'] is not None

    def test_push_is_idempotent(self, client, admin_auth):
        change = _product_change('p-idem-01', code='SYNCI02')
        r1 = client.post('/api/sync/push', json={'changes': [change]},
                         headers=admin_auth)
        r2 = client.post('/api/sync/push', json={'changes': [change]},
                         headers=admin_auth)
        assert r1.get_json()['applied'] == 1
        assert r2.get_json()['skipped'] == 1
        conn = _db()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM products WHERE uuid=%s", ('p-idem-01',)
        ).fetchone()['c']
        conn.close()
        assert n == 1

    def test_push_lww_newest_wins(self, client, admin_auth):
        v1 = _product_change('p-lww-01', offset=0, name='version 1', code='SYNCL01')
        v2 = _product_change('p-lww-01', offset=5, name='version 2')
        v3 = _product_change('p-lww-01', offset=2, name='version vieja')

        assert client.post('/api/sync/push', json={'changes': [v1]},
                           headers=admin_auth).get_json()['applied'] == 1
        assert client.post('/api/sync/push', json={'changes': [v2]},
                           headers=admin_auth).get_json()['applied'] == 1
        r3 = client.post('/api/sync/push', json={'changes': [v3]},
                         headers=admin_auth).get_json()
        assert r3['skipped'] == 1 and r3['applied'] == 0

        conn = _db()
        name = conn.execute(
            "SELECT name FROM products WHERE uuid=%s", ('p-lww-01',)
        ).fetchone()['name']
        conn.close()
        assert name == 'version 2'

    def test_push_stock_delta(self, client, admin_auth):
        ins = _product_change('p-stock-01', code='SYNCS01', stock=10)
        assert client.post('/api/sync/push', json={'changes': [ins]},
                           headers=admin_auth).get_json()['applied'] == 1
        upd = {
            'table': 'products', 'uuid': 'p-stock-01',
            'updated_at': _now(5), 'stock_delta': -3,
            'row': {'name': 'Producto Sync Update', 'code': 'SYNCS01',
                    'price_usd': 2.0, 'stock': 999},
        }
        r = client.post('/api/sync/push', json={'changes': [upd]},
                        headers=admin_auth)
        assert r.get_json()['applied'] == 1
        conn = _db()
        row = conn.execute(
            "SELECT name, stock FROM products WHERE uuid=%s", ('p-stock-01',)
        ).fetchone()
        conn.close()
        assert float(row['stock']) == 7.0  # 10 - 3 (delta, ignora row.stock=999)
        assert row['name'] == 'Producto Sync Update'

    def test_push_delete_creates_tombstone(self, client, admin_auth):
        assert client.post(
            '/api/sync/push', json={'changes': [_product_change('p-del-01', code='SYNCD01')]},
            headers=admin_auth).get_json()['applied'] == 1
        r = client.post('/api/sync/push', json={'changes': [
            {'table': 'products', 'uuid': 'p-del-01', 'deleted': True}
        ]}, headers=admin_auth)
        assert r.get_json()['deleted'] == 1
        conn = _db()
        row = conn.execute("SELECT id FROM products WHERE uuid=%s", ('p-del-01',)).fetchone()
        tomb = conn.execute(
            "SELECT * FROM sync_tombstones WHERE table_name='products' AND row_uuid=%s",
            ('p-del-01',)
        ).fetchone()
        conn.close()
        assert row is None
        assert tomb is not None and int(tomb['company_id']) == _company_id()

        # reintentar el mismo delete es idempotente
        r2 = client.post('/api/sync/push', json={'changes': [
            {'table': 'products', 'uuid': 'p-del-01', 'deleted': True}
        ]}, headers=admin_auth).get_json()
        assert r2['skipped'] == 1 and r2['deleted'] == 0

    def test_push_unknown_table_reports_error(self, client, admin_auth):
        r = client.post('/api/sync/push', json={'changes': [
            {'table': 'counters', 'uuid': 'x', 'updated_at': _now(), 'row': {}}
        ]}, headers=admin_auth)
        body = r.get_json()
        assert body['applied'] == 0
        assert len(body['errors']) == 1
        assert 'no sincronizable' in body['errors'][0]['reason']

    def test_push_rejects_foreign_company(self, client, admin_auth):
        conn = _db()
        conn.execute(
            "INSERT INTO companies (name, active, status) VALUES ('Empresa Ajena', 1, 'active')"
        )
        other_id = conn.execute("SELECT LAST_INSERT_ID() AS n").fetchone()['n']
        conn.close()
        change = _product_change('p-foreign-01', code='SYNCF01',
                                 company_id=int(other_id))
        body = client.post('/api/sync/push', json={'changes': [change]},
                           headers=admin_auth).get_json()
        assert body['applied'] == 0
        assert len(body['errors']) == 1
        assert 'otra empresa' in body['errors'][0]['reason'] or \
               'company_id' in body['errors'][0]['reason']

    def test_push_child_with_parent_refs(self, client, admin_auth):
        conn = _db()
        admin_uuid = conn.execute(
            "SELECT uuid FROM users WHERE username='admin'"
        ).fetchone()['uuid']
        conn.close()

        # producto (padre de sale_items) y venta (padre de sale_items)
        product_uuid, sale_uuid = 'p-ref-01', 's-ref-01'
        sale_number = 'VEN-20261119-REF01'
        changes = [
            _product_change(product_uuid, code='SYNCR01', stock=5),
            {'table': 'sales', 'uuid': sale_uuid, 'updated_at': _now(1),
             'row': {
                 'sale_number': sale_number, 'user_uuid': admin_uuid,
                 'customer_name': 'Cliente Sync', 'subtotal_usd': 10.0,
                 'tax_usd': 0.0, 'discount_usd': 0.0, 'total_usd': 10.0,
                 'exchange_rate': 36.5, 'total_bs': 365.0,
                 'payment_method': 'cash', 'amount_paid_usd': 10.0,
                 'amount_paid_bs': 365.0, 'status': 'completed',
             }},
            {'table': 'sale_items', 'uuid': 'si-ref-01', 'updated_at': _now(2),
             'row': {
                 'sale_uuid': sale_uuid, 'product_uuid': product_uuid,
                 'product_name': 'Producto Sync', 'product_code': 'SYNCR01',
                 'quantity': 1.0, 'price_usd': 10.0, 'price_bs': 365.0,
                 'total_usd': 10.0, 'total_bs': 365.0,
             }},
        ]
        body = client.post('/api/sync/push', json={'changes': changes},
                           headers=admin_auth).get_json()
        assert body['errors'] == [], body['errors']
        assert body['applied'] == 3

        conn = _db()
        sale = conn.execute("SELECT id FROM sales WHERE uuid=%s", (sale_uuid,)).fetchone()
        product = conn.execute(
            "SELECT id FROM products WHERE uuid=%s", (product_uuid,)
        ).fetchone()
        item = conn.execute(
            "SELECT * FROM sale_items WHERE uuid=%s", ('si-ref-01',)
        ).fetchone()
        conn.close()
        assert item['sale_id'] == sale['id']          # resuelto por uuid
        assert item['product_id'] == product['id']

    def test_push_child_missing_parent_reports_error(self, client, admin_auth):
        changes = [
            _product_change('p-missing-01', code='SYNCM01'),
            {'table': 'sale_items', 'uuid': 'si-missing-01', 'updated_at': _now(1),
             'row': {'sale_uuid': 'uuid-que-no-existe', 'product_name': 'x',
                     'quantity': 1.0, 'price_usd': 1.0, 'price_bs': 1.0,
                     'total_usd': 1.0, 'total_bs': 1.0}},
        ]
        body = client.post('/api/sync/push', json={'changes': changes},
                           headers=admin_auth).get_json()
        assert body['applied'] == 1  # el producto sí entró
        assert len(body['errors']) == 1
        assert 'padre' in body['errors'][0]['reason']
        conn = _db()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM sale_items WHERE uuid=%s", ('si-missing-01',)
        ).fetchone()['c']
        conn.close()
        assert n == 0


class TestSyncPull:

    def test_pull_returns_pushed_row(self, client, admin_auth):
        client.post('/api/sync/push', json={'changes': [
            _product_change('p-pull-01', code='SYNCP01')
        ]}, headers=admin_auth)
        body = client.get('/api/sync/pull', headers=admin_auth).get_json()
        match = [c for c in body['changes'] if c['uuid'] == 'p-pull-01']
        assert len(match) == 1
        assert match[0]['table'] == 'products'
        assert match[0]['row']['company_id'] == _company_id()
        assert body['has_more'] is False
        assert body['next_since']

    def test_pull_since_filters_old_rows(self, client, admin_auth):
        r1 = client.get('/api/sync/pull', headers=admin_auth).get_json()
        since = r1['next_since']
        client.post('/api/sync/push', json={'changes': [
            _product_change('p-since-01', code='SYNCT01')
        ]}, headers=admin_auth)
        body = client.get(f'/api/sync/pull?since={since}',
                          headers=admin_auth).get_json()
        uuids = {c['uuid'] for c in body['changes']}
        assert 'p-since-01' in uuids

    def test_pull_is_company_isolated(self, client, admin_auth, superadmin_auth):
        conn = _db()
        conn.execute(
            "INSERT INTO companies (name, active, status) "
            "VALUES ('Empresa B', 1, 'active')"
        )
        other_id = int(conn.execute("SELECT LAST_INSERT_ID() AS n").fetchone()['n'])
        conn.execute(
            "INSERT INTO products (name, code, price_usd, stock, company_id, uuid) "
            "VALUES ('Prod Empresa B', 'PB001', 9.9, 3, %s, 'p-company-b')",
            (other_id,)
        )
        conn.close()

        body = client.get('/api/sync/pull', headers=admin_auth).get_json()
        uuids = {c['uuid'] for c in body['changes']}
        assert 'p-company-b' not in uuids  # admin de Empresa A no la ve

        body_sa = client.get('/api/sync/pull', headers=superadmin_auth).get_json()
        uuids_sa = {c['uuid'] for c in body_sa['changes']}
        assert 'p-company-b' in uuids_sa  # superadmin ve todo

    def test_pull_includes_tombstones(self, client, admin_auth):
        client.post('/api/sync/push', json={'changes': [
            _product_change('p-tomb-01', code='SYNCB01'),
            {'table': 'products', 'uuid': 'p-tomb-01', 'deleted': True},
        ]}, headers=admin_auth)
        body = client.get('/api/sync/pull', headers=admin_auth).get_json()
        tombs = [t for t in body['tombstones'] if t['uuid'] == 'p-tomb-01']
        assert len(tombs) == 1
        assert tombs[0]['table'] == 'products'

    def test_pull_invalid_since_is_400(self, client, admin_auth):
        r = client.get('/api/sync/pull?since=not-a-date', headers=admin_auth)
        assert r.status_code == 400
