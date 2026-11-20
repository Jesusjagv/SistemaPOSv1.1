"""Tests del dialecto SQLite local (modo offline-first del escritorio)."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import database  # noqa: E402
from database import get_db, init_db, next_counter, _translate_sql  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """BD SQLite temporal (una por test) y CLOUD_URL desactivado."""
    url = 'sqlite:///' + (tmp_path / 'local.db').as_posix()
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.delenv('CLOUD_URL', raising=False)
    init_db()
    conn = get_db()
    yield conn
    conn.close()


class TestInitLocal:
    def test_seeds_and_uuid(self, db):
        assert db.execute("SELECT COUNT(*) c FROM users").fetchone()['c'] == 2
        row = db.execute("SELECT * FROM products LIMIT 1").fetchone()
        assert row['uuid'] and len(row['uuid']) == 36
        assert row['sync_dirty'] == 1
        # created_at se guarda en hora local (fecha de negocio)
        assert hasattr(row['created_at'], 'hour')

    def test_sync_infra_tables(self, db):
        for t in ('sync_tombstones', 'sync_idmap', 'sync_state'):
            db.execute(f"SELECT COUNT(*) c FROM {t}")
        idx = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='uq_products_sync_uuid'"
        ).fetchone()
        assert idx is not None

    def test_cloud_mode_sin_seeds(self, tmp_path, monkeypatch):
        url = 'sqlite:///' + (tmp_path / 'cloud.db').as_posix()
        monkeypatch.setenv('DATABASE_URL', url)
        monkeypatch.setenv('CLOUD_URL', 'https://ejemplo.render')
        init_db()
        conn = get_db()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM users").fetchone()['c'] == 0
            assert conn.execute("SELECT COUNT(*) c FROM products").fetchone()['c'] == 0
            # el esquema sí existe (listo para el pull)
            conn.execute("SELECT COUNT(*) c FROM sync_idmap").fetchone()
        finally:
            conn.close()

    def test_rerun_es_idempotente(self, db):
        init_db()  # segunda corrida: CREATE IF NOT EXISTS, sin error
        assert db.execute("SELECT COUNT(*) c FROM users").fetchone()['c'] == 2


class TestTraduccion:
    @pytest.mark.parametrize('sql,esperado', [
        ('SELECT * FROM sales WHERE id=%s FOR UPDATE',
         'SELECT * FROM sales WHERE id=?'),
        ('START TRANSACTION', 'BEGIN'),
        ('INSERT IGNORE INTO t (a) VALUES (%s)',
         'INSERT OR IGNORE INTO t (a) VALUES (?)'),
        ("SELECT DATE_SUB(%s, INTERVAL 1 DAY)", "SELECT date(?, '-1 day')"),
        ("SELECT DATE_ADD(%s, INTERVAL 2 HOUR)", "SELECT date(?, '+2 hour')"),
        ("SELECT %s, 'a%%b'", "SELECT ?, 'a%b'"),
        ('SELECT * FROM sales WHERE id=%s', 'SELECT * FROM sales WHERE id=?'),
    ])
    def test_translate(self, sql, esperado):
        assert _translate_sql(sql) == esperado

    def test_for_update_select_ejecuta(self, db):
        assert db.execute("SELECT * FROM sales WHERE id=%s FOR UPDATE", (999,)).fetchone() is None

    def test_start_transaction_crudo(self, db):
        db.execute('START TRANSACTION')
        db.execute("UPDATE companies SET name = name WHERE id = %s", (1,))
        db.execute('COMMIT')


class TestFuncionesSQL:
    def test_now_utc(self, db):
        assert len(db.execute("SELECT NOW() t").fetchone()['t']) == 19

    def test_hour_lpad_uuid(self, db):
        assert db.execute("SELECT HOUR('2026-11-19 14:37:00') h").fetchone()['h'] == 14
        assert db.execute("SELECT LPAD(9, 3, '0') l").fetchone()['l'] == '009'
        assert db.execute("SELECT LPAD('abcdef', 3, '0') l").fetchone()['l'] == 'abc'
        assert len(db.execute("SELECT UUID() u").fetchone()['u']) == 36
        assert db.execute("SELECT DATE_SUB('2026-11-19', INTERVAL 1 DAY) d").fetchone()['d'] == '2026-11-18'

    def test_sql_de_reports(self, db):
        db.execute("INSERT INTO products (name, price_usd) VALUES (%s, %s)", ('Prod R', 1.0))
        r = db.execute(
            "SELECT LPAD(HOUR(created_at), 2, '0') h FROM products LIMIT 1"
        ).fetchone()
        assert r['h'] is not None and len(r['h']) == 2


class TestEscrituras:
    def test_insert_inyecta_uuid(self, db):
        db.execute("INSERT INTO customers (name, company_id) VALUES (%s, %s)",
                   ('Cliente X', 1))
        c = db.execute("SELECT * FROM customers WHERE name = 'Cliente X'").fetchone()
        assert c['uuid'] and len(c['uuid']) == 36
        assert c['sync_dirty'] == 1

    def test_update_marca_dirty(self, db):
        db.execute("INSERT INTO customers (name) VALUES (%s)", ('Cliente Y',))
        c = db.execute("SELECT * FROM customers WHERE name = 'Cliente Y'").fetchone()
        db.execute("UPDATE customers SET phone = %s WHERE name = %s",
                   ('0414', 'Cliente Y'))
        c2 = db.execute("SELECT * FROM customers WHERE name = 'Cliente Y'").fetchone()
        assert c2['sync_dirty'] == 1
        assert c2['updated_at'] >= c['updated_at']

    def test_update_con_updated_at_explicito_no_duplica(self, db):
        # products PUT escribe updated_at a mano: el rewrite no lo repite
        db.execute(
            "UPDATE products SET price_usd = 1.99, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s", (1,))

    def test_update_tabla_no_sync_no_se_toca(self, db):
        db.execute("INSERT INTO counters (name, val) VALUES (%s, %s)", ('smoke', 0))
        r = db.execute("UPDATE counters SET val = val + 1 WHERE name = %s", ('smoke',))
        assert r.rowcount == 1
        assert 'sync_dirty' not in db.execute(
            "SELECT * FROM counters WHERE name = 'smoke'").fetchone()

    def test_replace_conserva_uuid(self, db):
        db.execute("REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
                   ('2026-11-19', 36.5, 'BCV'))
        er1 = db.execute("SELECT * FROM exchange_rates WHERE date = %s",
                         ('2026-11-19',)).fetchone()
        db.execute("REPLACE INTO exchange_rates (date, rate, source) VALUES (%s,%s,%s)",
                   ('2026-11-19', 37.1, 'BCV'))
        er2 = db.execute("SELECT * FROM exchange_rates WHERE date = %s",
                         ('2026-11-19',)).fetchone()
        assert er1['uuid'] is not None
        assert er2['uuid'] == er1['uuid']
        assert abs(er2['rate'] - 37.1) < 0.01
        assert er2['sync_dirty'] == 1

    def test_delete_captura_tombstone(self, db):
        db.execute("INSERT INTO customers (name) VALUES (%s)", ('Borrar Z',))
        c = db.execute("SELECT * FROM customers WHERE name = 'Borrar Z'").fetchone()
        db.execute("DELETE FROM customers WHERE name = %s", ('Borrar Z',))
        tb = db.execute(
            "SELECT * FROM sync_tombstones WHERE table_name='customers' AND row_uuid=%s",
            (c['uuid'],)
        ).fetchone()
        assert tb is not None
        # repetir el DELETE no duplica; INSERT IGNORE de rutas tampoco
        db.execute("DELETE FROM customers WHERE name = %s", ('Borrar Z',))
        db.execute(
            "INSERT IGNORE INTO sync_tombstones (company_id, table_name, row_uuid) "
            "VALUES (%s, %s, %s)", (1, 'customers', c['uuid']))
        n = db.execute(
            "SELECT COUNT(*) c FROM sync_tombstones WHERE row_uuid=%s",
            (c['uuid'],)).fetchone()['c']
        assert n == 1

    def test_delete_tabla_no_sync_no_captura(self, db):
        db.execute("DELETE FROM chat_history WHERE 1=0")
        n = db.execute(
            "SELECT COUNT(*) c FROM sync_tombstones WHERE table_name='chat_history'"
        ).fetchone()['c']
        assert n == 0


class TestTransacciones:
    def test_rollback_y_commit(self, db):
        db.begin()
        db.execute("INSERT INTO customers (name) VALUES (%s)", ('Tx A',))
        db.rollback()
        assert db.execute(
            "SELECT COUNT(*) c FROM customers WHERE name='Tx A'").fetchone()['c'] == 0
        db.begin()
        db.execute("INSERT INTO customers (name) VALUES (%s)", ('Tx A',))
        db.commit()
        assert db.execute(
            "SELECT COUNT(*) c FROM customers WHERE name='Tx A'").fetchone()['c'] == 1

    def test_begin_doble_es_noop(self, db):
        db.begin()
        db.begin()  # no debe lanzar "cannot start a transaction within..."
        db.commit()


class TestNextCounter:
    def test_secuencia(self, db):
        seqs = {next_counter(db, 'smoke_seq') for _ in range(5)}
        assert seqs == {1, 2, 3, 4, 5}

    def test_concurrentes_hilos(self, db):
        url = database.resolve_db_url()
        results = []

        def worker():
            conn = database._get_sqlite_wrapper(url)
            for _ in range(10):
                results.append(next_counter(conn, 'thread_seq'))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(results)) == 100


class TestEngineMode:
    def test_sin_reescrituras(self, db):
        url = database.resolve_db_url()
        eng = database._get_sqlite_wrapper(url, engine_mode=True)
        eng.execute("UPDATE products SET sync_dirty = 0 WHERE id = %s", (1,))
        eng.execute("UPDATE products SET min_stock = %s WHERE id = %s", (7, 1))
        assert eng.execute(
            "SELECT sync_dirty d FROM products WHERE id = 1").fetchone()['d'] == 0
        db.execute("UPDATE products SET min_stock = %s WHERE id = %s", (8, 1))
        assert db.execute(
            "SELECT sync_dirty d FROM products WHERE id = 1").fetchone()['d'] == 1
        # un DELETE del engine NO deja tombstone local
        eng.execute("INSERT INTO customers (name, uuid) VALUES (%s, %s)",
                    ('Eng Del', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'))
        eng.execute("DELETE FROM customers WHERE name = %s", ('Eng Del',))
        n = db.execute(
            "SELECT COUNT(*) c FROM sync_tombstones WHERE table_name='customers'"
        ).fetchone()['c']
        assert n == 0
