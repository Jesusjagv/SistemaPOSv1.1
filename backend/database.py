import pymysql
import os
import queue
import re
import sqlite3
import threading
import uuid as uuid_module
from datetime import datetime, date, timezone
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import urllib.parse
load_dotenv()
# Connection parameters will be loaded from DATABASE_URL environment variable

import pymysql.converters

conv = pymysql.converters.conversions.copy()
conv[pymysql.constants.FIELD_TYPE.DECIMAL] = float
conv[pymysql.constants.FIELD_TYPE.NEWDECIMAL] = float

_pool = None
_pool_lock = threading.Lock()


class PoolTimeoutError(Exception):
    """No se pudo obtener una conexión del pool en el tiempo esperado."""
    pass

def _parse_db_url(db_url=None):
    """Parse DATABASE_URL (o una URL dada) y devolver kwargs de conexión."""
    db_url = db_url or resolve_db_url()
    if not db_url:
        raise RuntimeError('DATABASE_URL not set in environment')
    parsed = urllib.parse.urlparse(db_url)
    ssl_params = None
    needs_ssl = (
        'ssl-mode=REQUIRED' in db_url
        or 'ssl=true' in db_url.lower()
        or 'tidbcloud.com' in (parsed.hostname or '')
    )
    if needs_ssl:
        # TiDB Cloud exige TLS. certifi aporta el CA raíz (ISRG Root X1);
        # PyMySQL valida cadena + hostname con ese CA.
        import certifi
        ssl_params = {
            'ca': certifi.where(),
            'check_hostname': True,
        }
    return {
        'host': parsed.hostname,
        'port': parsed.port or 3306,
        'user': urllib.parse.unquote(parsed.username),
        'password': urllib.parse.unquote(parsed.password),
        'database': parsed.path.lstrip('/'),
        'cursorclass': pymysql.cursors.DictCursor,
        'autocommit': True,
        'conv': conv,
        'ssl': ssl_params
    }

def _create_connection():
    kwargs = _parse_db_url()
    return pymysql.connect(**kwargs)


# ── Dialecto SQLite (modo offline-first para el escritorio) ──────────
# El cliente de escritorio corre contra un archivo SQLite local mientras
# un hilo de sincronización replica con la nube. Las rutas existentes
# escriben SQL de MySQL; SQLiteWrapper traduce lo mínimo necesario y
# marca sync_dirty / sync_tombstones en INSERT/UPDATE/DELETE locales.
# El hilo de sync escribe en "engine_mode" (sin reescrituras: él mismo
# gestiona uuid/updated_at/sync_dirty al aplicar lo recibido de la nube).

# Tablas replicables (orden = orden topológico de aplicación en pull).
# Debe mantenerse en sincronía con SYNC_TABLES de routes/sync.py.
SYNCED_TABLES = (
    'companies', 'users', 'categories', 'products', 'combo_items',
    'customers', 'exchange_rates', 'sales', 'sale_items',
    'cash_sessions', 'cash_session_denominations', 'cash_session_payments',
    'debts', 'debt_payments', 'inventory_movements', 'security_questions',
)

# Clave de conflicto para traducir REPLACE INTO (solo exchange_rates usa REPLACE)
_REPLACE_CONFLICT_COL = {'exchange_rates': 'date'}

_RE_FOR_UPDATE = re.compile(r'\s+FOR\s+UPDATE\s*$', re.I)
_RE_START_TX = re.compile(r'^\s*START\s+TRANSACTION\s*;?\s*$', re.I)
_RE_INSERT_IGNORE = re.compile(r'\bINSERT\s+IGNORE\b', re.I)
_RE_INTERVAL = re.compile(
    r'\b(DATE_SUB|DATE_ADD)\s*\(\s*(.+?)\s*,\s*INTERVAL\s+(-?\d+)\s+([A-Za-z]+)\s*\)',
    re.I)
_RE_REPLACE = re.compile(
    r'^\s*REPLACE\s+INTO\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*VALUES\s*\(', re.I)
_RE_UPDATE = re.compile(r'^\s*UPDATE\s+([A-Za-z_]\w*)\s+SET\s+', re.I)
_RE_DELETE = re.compile(r'^\s*DELETE\s+FROM\s+([A-Za-z_]\w*)\b', re.I)
_RE_INSERT = re.compile(
    r'^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*VALUES\s*\(', re.I)

_sqlite_lock = threading.RLock()
_sqlite_state = threading.local()


def _translate_sql(sql):
    """Convierte SQL de MySQL al dialecto equivalente de SQLite."""
    if '%s' in sql:
        sql = sql.replace('%s', '?')
    if '%%' in sql:
        sql = sql.replace('%%', '%')
    if _RE_START_TX.match(sql):
        return 'BEGIN'
    sql = _RE_FOR_UPDATE.sub('', sql)
    sql = _RE_INSERT_IGNORE.sub('INSERT OR IGNORE', sql)

    def _interval(m):
        sign = '-' if m.group(1).lower() == 'date_sub' else '+'
        n = m.group(3).lstrip('-')
        return f"date({m.group(2)}, '{sign}{n} {m.group(4).lower()}')"

    return _RE_INTERVAL.sub(_interval, sql)


# ── Funciones SQL que trae MySQL y SQLite no tiene ──

def _fn_now():
    # UTC: Render/MySQL también usa UTC, el LWW depende de un reloj único
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _fn_lpad(s, n, fill):
    s = '' if s is None else str(s)
    n = int(n)
    fill = ' ' if fill in (None, '') else str(fill)
    if len(s) >= n:
        return s[:n]
    return fill * (n - len(s)) + s


def _dt_part(value, a, b, default=None):
    if value is None:
        return None
    try:
        s = str(value)
        if 'T' in s:
            s = s.replace('T', ' ')
        if len(s) < b:
            return default
        return int(s[a:b])
    except (ValueError, TypeError):
        return default


def _fn_hour(v):
    return _dt_part(v, 11, 13, default=0)


def _fn_minute(v):
    return _dt_part(v, 14, 16, default=0)


def _fn_month(v):
    return _dt_part(v, 5, 7, default=0)


def _fn_day(v):
    return _dt_part(v, 8, 10, default=0)


def _fn_year(v):
    return _dt_part(v, 0, 4, default=0)


def _conv_timestamp(b):
    s = b.decode('utf-8', 'replace') if isinstance(b, (bytes, bytearray)) else str(b)
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _conv_date(b):
    s = b.decode('utf-8', 'replace') if isinstance(b, (bytes, bytearray)) else str(b)
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


# Adaptadores: Py3.13 deprecó los default de datetime/date.
# Sin microsegundos si no los trae: el roundtrip debe comparar igual
# (guardas `WHERE updated_at = ?` del engine/limpieza de sync_dirty).
sqlite3.register_adapter(
    datetime,
    lambda d: (d.strftime('%Y-%m-%d %H:%M:%S.%f') if d.microsecond
               else d.strftime('%Y-%m-%d %H:%M:%S'))
)
sqlite3.register_adapter(date, lambda d: d.isoformat())
for _tname in ('timestamp', 'TIMESTAMP', 'datetime', 'DATETIME'):
    sqlite3.register_converter(_tname, _conv_timestamp)
for _tname in ('date', 'DATE'):
    sqlite3.register_converter(_tname, _conv_date)


def _sqlite_path(db_url):
    path = urllib.parse.urlparse(db_url).path
    if path.startswith('/'):
        path = path[1:]  # sqlite:///C:/x.db -> C:/x.db ; sqlite:///./x.db -> ./x.db
    if not path:
        raise RuntimeError(f'DATABASE_URL sqlite sin ruta: {db_url}')
    return path


def _dict_row_factory(cursor, values):
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return dict(zip(cols, values))


def _connect_sqlite(db_url):
    path = _sqlite_path(db_url)
    if path != ':memory:':
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(
        path,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
        isolation_level=None,  # autocommit: las transacciones son explícitas (BEGIN)
    )
    conn.row_factory = _dict_row_factory
    conn.create_function('NOW', 0, _fn_now)
    conn.create_function('UUID', 0, lambda: str(uuid_module.uuid4()))
    conn.create_function('LPAD', 3, _fn_lpad)
    conn.create_function('HOUR', 1, _fn_hour)
    conn.create_function('MINUTE', 1, _fn_minute)
    conn.create_function('MONTH', 1, _fn_month)
    conn.create_function('DAY', 1, _fn_day)
    conn.create_function('YEAR', 1, _fn_year)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA foreign_keys = OFF')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')
    return conn


class SQLiteWrapper:
    """Misma API que DBWrapper (execute/cursor/begin/...) sobre sqlite3."""
    dialect = 'sqlite'
    __slots__ = ('conn', '_in_tx', 'engine_mode')

    def __init__(self, conn, engine_mode=False):
        self.conn = conn
        self._in_tx = False
        self.engine_mode = engine_mode

    # ── ejecución ──

    def execute(self, sql, args=None):
        if self.conn is None:
            raise RuntimeError('Conexión ya cerrada')
        with _sqlite_lock:
            sql = _translate_sql(sql)
            if not self.engine_mode:
                m = _RE_REPLACE.match(sql)
                if m:
                    return self._exec_replace(m, args)
                m = _RE_UPDATE.match(sql)
                if m:
                    if m.group(1) in SYNCED_TABLES:
                        sql = _rewrite_update(sql, m)
                else:
                    m = _RE_DELETE.match(sql)
                    if m and m.group(1) in SYNCED_TABLES:
                        return self._exec_delete(sql, args, m)
                    m = _RE_INSERT.match(sql)
                    if m and m.group(1) in SYNCED_TABLES:
                        sql, args = _inject_uuid(sql, m, args)
            cur = self.conn.cursor()
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            return cur

    def executemany(self, sql, args):
        if self.conn is None:
            raise RuntimeError('Conexión ya cerrada')
        with _sqlite_lock:
            sql = _translate_sql(sql)
            if not self.engine_mode:
                m = _RE_REPLACE.match(sql)
                if m:
                    return [self._exec_replace(m, a) for a in (args or [])]
                m = _RE_UPDATE.match(sql)
                if m:
                    if m.group(1) in SYNCED_TABLES:
                        sql = _rewrite_update(sql, m)
                else:
                    m = _RE_DELETE.match(sql)
                    if m and m.group(1) in SYNCED_TABLES:
                        return [self._exec_delete(sql, a, m) for a in (args or [])]
                    m = _RE_INSERT.match(sql)
                    if m and m.group(1) in SYNCED_TABLES:
                        sql = _sql_inject_uuid(sql, m)
                        args = [tuple(a) + (str(uuid_module.uuid4()),) for a in args]
            cur = self.conn.cursor()
            cur.executemany(sql, args)
            return cur

    def cursor(self):
        return self.conn.cursor()

    def begin(self):
        with _sqlite_lock:
            if self.conn is None:
                raise RuntimeError('Conexión ya cerrada')
            if self._in_tx:
                return
            self.conn.execute('BEGIN')
            self._in_tx = True

    def commit(self):
        if self.conn is None:
            return
        with _sqlite_lock:
            if self._in_tx:
                try:
                    self.conn.execute('COMMIT')
                finally:
                    self._in_tx = False

    def rollback(self):
        if self.conn is None:
            return
        with _sqlite_lock:
            if self._in_tx:
                try:
                    self.conn.execute('ROLLBACK')
                finally:
                    self._in_tx = False

    def close(self):
        """No cierra la conexión: es compartida por thread (thread-local)."""
        if self.conn is None:
            return
        with _sqlite_lock:
            if self._in_tx:
                try:
                    self.conn.execute('ROLLBACK')
                except Exception:
                    pass
                self._in_tx = False

    # ── reescrituras de escritura ──

    def _exec_delete(self, sql, args, m):
        """DELETE en tabla sincronizable: captura uuids en sync_tombstones."""
        table = m.group(1)
        tail = sql[m.end():]
        cur = self.conn.cursor()
        sel = f'SELECT uuid FROM {table}{tail}'
        if args is None:
            cur.execute(sel)
        else:
            cur.execute(sel, args)
        uuids = [r['uuid'] for r in cur.fetchall() if r.get('uuid')]
        if args is None:
            cur.execute(sql)
        else:
            cur.execute(sql, args)
        for u in uuids:
            self.conn.execute(
                'INSERT OR IGNORE INTO sync_tombstones '
                '(company_id, table_name, row_uuid) VALUES (NULL, ?, ?)',
                (table, u)
            )
        return cur

    def _exec_replace(self, m, args):
        """REPLACE INTO -> UPDATE conservando uuid (si no existe, INSERT)."""
        table = m.group(1)
        cols = [c.strip() for c in m.group(2).split(',')]
        conflict = _REPLACE_CONFLICT_COL.get(table)
        if conflict is None or conflict not in cols or args is None:
            sql = f"INSERT OR REPLACE INTO {table} ({m.group(2)}) VALUES ({', '.join('?' * len(cols))})"
            return self.execute(sql, args)
        vals = dict(zip(cols, args))
        sets = [c for c in cols if c != conflict]
        if sets:
            cur = self.execute(
                f"UPDATE {table} SET {', '.join(c + ' = ?' for c in sets)} WHERE {conflict} = ?",
                [vals[c] for c in sets] + [vals[conflict]]
            )
            if cur.rowcount > 0:
                return cur
        return self.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            tuple(vals[c] for c in cols)
        )


def _rewrite_update(sql, m):
    """Añade updated_at/sync_dirty a un UPDATE de tabla sincronizable."""
    set_start = m.end()
    mw = re.search(r'\sWHERE\s', sql[set_start:], re.I)
    seg_end = set_start + mw.start() if mw else len(sql)
    set_clause = sql[set_start:seg_end]
    adds = []
    if not re.search(r'\bupdated_at\b', set_clause, re.I):
        # precisión de milisegundos (UTC): con solo segundos, dos cambios en
        # el mismo segundo empatan en LWW y la nube descarta el segundo
        adds.append("updated_at = strftime('%Y-%m-%d %H:%M:%f','now')")
    if not re.search(r'\bsync_dirty\b', set_clause, re.I):
        adds.append('sync_dirty = 1')
    if not adds:
        return sql
    return sql[:seg_end] + ', ' + ', '.join(adds) + sql[seg_end:]


def _sql_inject_uuid(sql, m):
    """Añade la columna uuid (y su ?) a un INSERT de tabla sincronizable."""
    names = [c.strip().lower() for c in m.group(2).split(',')]
    if 'uuid' in names:
        return sql
    sql = sql[:m.end(2)] + ', uuid' + sql[m.end(2):]
    idx = sql.rfind(')')
    return sql[:idx] + ', ?' + sql[idx:]


def _inject_uuid(sql, m, args):
    names = [c.strip().lower() for c in m.group(2).split(',')]
    if 'uuid' in names:
        return sql, args
    new_sql = _sql_inject_uuid(sql, m)
    if args is None:
        args = ()
    args = tuple(args) + (str(uuid_module.uuid4()),)
    return new_sql, args


def _is_sqlite_url(db_url):
    return bool(db_url) and str(db_url).strip().lower().startswith('sqlite:')


def _get_sqlite_wrapper(db_url, engine_mode=False):
    store = getattr(_sqlite_state, 'wrappers', None)
    if store is None:
        store = _sqlite_state.wrappers = {}
    key = (db_url, engine_mode)
    w = store.get(key)
    if w is None or w.conn is None:
        w = SQLiteWrapper(_connect_sqlite(db_url), engine_mode=engine_mode)
        store[key] = w
    return w


# ── Fin dialecto SQLite ──────────────────────────────────────────────


class ConnectionPool:
    __slots__ = ('_pool', '_max_size', '_size', '_lock')

    def __init__(self, min_size=5, max_size=20):
        self._max_size = max_size
        self._pool = queue.Queue(maxsize=max_size)
        self._size = 0
        self._lock = threading.Lock()
        for _ in range(min_size):
            self._pool.put(_create_connection())
            self._size += 1

    def acquire(self):
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._size < self._max_size:
                    conn = _create_connection()
                    self._size += 1
                    return conn
            # Cola vacía y pool al máximo: esperar con timeout en vez de colgarnos
            try:
                return self._pool.get(timeout=10)
            except queue.Empty:
                raise PoolTimeoutError(
                    'Sin conexiones disponibles en el pool de base de datos'
                )

    def release(self, conn):
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            conn.close()
            with self._lock:
                self._size -= 1

def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # El pool debe dar cubierta a todos los threads de Waitress
                threads = int(os.getenv('WAITRESS_THREADS', '16'))
                max_size = int(os.getenv('DB_POOL_MAX', str(threads + 5)))
                _pool = ConnectionPool(min_size=5, max_size=max(5, max_size))
    return _pool

class DBWrapper:
    __slots__ = ('conn', '_in_tx')

    def __init__(self, conn):
        self.conn = conn
        self._in_tx = False

    def execute(self, sql, args=None):
        cursor = self.conn.cursor()
        cursor.execute(sql, args)
        return cursor

    def cursor(self):
        return self.conn.cursor()

    def executemany(self, sql, args=None):
        cursor = self.conn.cursor()
        cursor.executemany(sql, args)
        return cursor

    def begin(self):
        """Inicia una transacción explícita (desactiva autocommit en esta conexión)."""
        if self.conn is None:
            raise RuntimeError('Conexión ya cerrada')
        self.conn.autocommit(False)
        self._in_tx = True

    def commit(self):
        if self.conn is None:
            return
        if self._in_tx:
            try:
                self.conn.commit()
            finally:
                self._in_tx = False
                self.conn.autocommit(True)
        else:
            self.conn.commit()

    def rollback(self):
        """Deshace la transacción en curso (si la hay) y restaura autocommit."""
        if self.conn is None or not self._in_tx:
            return
        try:
            self.conn.rollback()
        finally:
            self._in_tx = False
            self.conn.autocommit(True)

    def close(self):
        if self.conn is None:
            return
        raw = self.conn
        # Si quedó una transacción abierta, deshacerla para no devolver
        # la conexión al pool con locks pendientes
        if self._in_tx:
            try:
                raw.rollback()
            except Exception:
                pass
            try:
                raw.autocommit(True)
            except Exception:
                pass
            self._in_tx = False
        self.conn = None
        p = _pool
        if p is not None:
            p.release(raw)
        else:
            raw.close()

from flask import has_app_context, g, current_app


def resolve_db_url():
    """URL de la BD activa: config de la app (multi-app/tests) > env."""
    if has_app_context():
        try:
            u = current_app.config.get('DATABASE_URL')
            if u:
                return u
        except RuntimeError:
            pass
    return os.getenv('DATABASE_URL')


def get_db():
    url = resolve_db_url()
    if _is_sqlite_url(url):
        # Escritorio offline-first: conexión thread-local sobre el archivo local
        if has_app_context():
            if 'db_conn' not in g or getattr(g.db_conn, 'conn', None) is None:
                g.db_conn = _get_sqlite_wrapper(url)
            return g.db_conn
        return _get_sqlite_wrapper(url)
    if has_app_context():
        # Si estamos dentro de una petición Flask, reusar la conexión si está activa
        if 'db_conn' not in g or getattr(g.db_conn, 'conn', None) is None:
            p = get_pool()
            g.db_conn = DBWrapper(p.acquire())
        return g.db_conn
    else:
        # Modo script (fuera de Flask)
        p = get_pool()
        return DBWrapper(p.acquire())

def close_db(e=None):
    """Cierra la conexión de base de datos asociada a la petición actual si quedó abierta."""
    db = g.pop('db_conn', None)
    if db is not None and getattr(db, 'conn', None) is not None:
        db.close()


def next_counter(conn, name, init_count=None):
    """Siguiente valor único de un contador nombrado (atómico a nivel de fila).

    Usa la tabla `counters` con LAST_INSERT_ID(val + 1) para que dos hilos
    nunca obtengan el mismo número, sin depender de COUNT(*) + inserción.
    `init_count` (callable) se usa solo al crear la fila, para partir del
    conteo de registros existentes con el formato antiguo.
    """
    if getattr(conn, 'dialect', 'mysql') == 'sqlite':
        # Secuencia completa bajo el lock global: sqlite no tiene
        # LAST_INSERT_ID por sesión, así que la lectura final debe quedar
        # atómica con el incremento.
        with _sqlite_lock:
            row = conn.execute(
                "SELECT val FROM counters WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                base = int(init_count()) if init_count else 0
                conn.execute(
                    "INSERT OR IGNORE INTO counters (name, val) VALUES (?, ?)",
                    (name, base)
                )
            conn.execute(
                "UPDATE counters SET val = val + 1 WHERE name = ?", (name,)
            )
            result = conn.execute(
                "SELECT val FROM counters WHERE name = ?", (name,)
            ).fetchone()
            return int(result['val'])
    row = conn.execute(
        "SELECT val FROM counters WHERE name = %s", (name,)
    ).fetchone()
    if row is None:
        base = int(init_count()) if init_count else 0
        conn.execute(
            "INSERT INTO counters (name, val) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE val = val",
            (name, base)
        )
    conn.execute(
        "UPDATE counters SET val = LAST_INSERT_ID(val + 1) WHERE name = %s",
        (name,)
    )
    result = conn.execute("SELECT LAST_INSERT_ID() AS n").fetchone()
    return int(result['n'])

def machine_suffix():
    """Sufijo corto de máquina para números únicos multi-caja (MACHINE_ID).

    VEN-20261119-0001-01 / CAJA-20261119-0001-T1: dos escritorios con la
    misma BD nube no pueden chocar en los UNIQUE de sale_number /
    session_number. Vacío si no hay MACHINE_ID (servidor único, como antes).
    """
    mid = re.sub(r'[^A-Za-z0-9]', '', os.getenv('MACHINE_ID', ''))[:6]
    return f'-{mid}' if mid else ''


def init_db():
    if _is_sqlite_url(resolve_db_url()):
        return init_local_db()
    conn = get_db()
    cursor = conn.cursor()

    # Companies table (multi-tenant)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS companies (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name TEXT NOT NULL,
            active INT DEFAULT 1
        )
    ''')

    # Contadores secuenciales (números de venta/turno únicos bajo concurrencia)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS counters (
            name VARCHAR(100) PRIMARY KEY,
            val BIGINT NOT NULL DEFAULT 0
        )
    ''')

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            role ENUM('admin', 'cashier', 'superadmin') NOT NULL,
            active BOOLEAN DEFAULT 1,
            company_id INT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    ''')

    # Categories table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            color VARCHAR(50) DEFAULT '#7c3aed',
            company_id INT DEFAULT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    ''')

    # Products table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            code VARCHAR(100) UNIQUE,
            category_id INT,
            price_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            cost_usd DECIMAL(10,2) DEFAULT 0,
            unidades_por_paquete INT DEFAULT 1,
            costo_unidad DECIMAL(10,2) DEFAULT 0,
            precio_unidad DECIMAL(10,2) DEFAULT 0,
            precio_paquete DECIMAL(10,2) DEFAULT 0,
            margen_unidad_porcentaje DECIMAL(5,2) DEFAULT 0,
            stock INT DEFAULT 0,
            min_stock INT DEFAULT 5,
            active BOOLEAN DEFAULT 1,
            company_id INT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id),
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    ''')
    
    # Migrate existing products table with unit-conversion columns
    for col, ddl in [
        ('unidades_por_paquete', 'ALTER TABLE products ADD COLUMN unidades_por_paquete INT DEFAULT 1'),
        ('costo_unidad', 'ALTER TABLE products ADD COLUMN costo_unidad DECIMAL(10,2) DEFAULT 0'),
        ('precio_unidad', 'ALTER TABLE products ADD COLUMN precio_unidad DECIMAL(10,2) DEFAULT 0'),
        ('precio_paquete', 'ALTER TABLE products ADD COLUMN precio_paquete DECIMAL(10,2) DEFAULT 0'),
        ('margen_unidad_porcentaje', 'ALTER TABLE products ADD COLUMN margen_unidad_porcentaje DECIMAL(5,2) DEFAULT 0'),
        ('is_combo', 'ALTER TABLE products ADD COLUMN is_combo BOOLEAN DEFAULT 0'),
        ('is_bulk', 'ALTER TABLE products ADD COLUMN is_bulk BOOLEAN DEFAULT 0'),
    ]:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = %s",
            (col,)
        )
        if cursor.fetchone()['COUNT(*)'] == 0:
            cursor.execute(ddl)

    # Combo Items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS combo_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            combo_product_id INT NOT NULL,
            ingredient_product_id INT NOT NULL,
            quantity DECIMAL(10,2) NOT NULL DEFAULT 1,
            FOREIGN KEY (combo_product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (ingredient_product_id) REFERENCES products(id) ON DELETE RESTRICT
        )
    ''')

    # Migrate companies table with exchange rate mode columns
    for col, ddl in [
        ('rate_mode', "ALTER TABLE companies ADD COLUMN rate_mode ENUM('auto','manual') DEFAULT 'auto'"),
        ('manual_rate', 'ALTER TABLE companies ADD COLUMN manual_rate DECIMAL(10,4) DEFAULT NULL'),
    ]:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'companies' AND column_name = %s",
            (col,)
        )
        if cursor.fetchone()['COUNT(*)'] == 0:
            cursor.execute(ddl)

    # Customers table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            doc_type VARCHAR(10) DEFAULT 'V',
            doc_number VARCHAR(50),
            phone VARCHAR(50),
            email VARCHAR(255),
            address TEXT,
            credit_limit_usd DECIMAL(10,2) DEFAULT 0,
            credit_status ENUM('Solvente','En Mora','Suspendido') DEFAULT 'Solvente',
            company_id INT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    ''')

    # Exchange rates table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exchange_rates (
            id INT AUTO_INCREMENT PRIMARY KEY,
            date DATE NOT NULL UNIQUE,
            rate DECIMAL(10,4) NOT NULL,
            source VARCHAR(50) DEFAULT 'BCV',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Cash Sessions table (Turnos de Caja)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cash_sessions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            company_id INT NOT NULL,
            session_number VARCHAR(50) UNIQUE NOT NULL,
            user_id INT NOT NULL,
            opened_by_user_id INT NOT NULL,
            closed_by_user_id INT DEFAULT NULL,
            open_datetime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            close_datetime DATETIME DEFAULT NULL,
            exchange_rate_open DECIMAL(10,4) NOT NULL DEFAULT 0,
            exchange_rate_close DECIMAL(10,4) DEFAULT NULL,
            opening_amount_usd DECIMAL(10,2) DEFAULT 0,
            opening_amount_bs DECIMAL(10,2) DEFAULT 0,
            expected_cash_usd DECIMAL(10,2) DEFAULT 0,
            expected_cash_bs DECIMAL(10,2) DEFAULT 0,
            closing_amount_usd DECIMAL(10,2) DEFAULT 0,
            closing_amount_bs DECIMAL(10,2) DEFAULT 0,
            difference_usd DECIMAL(10,2) DEFAULT 0,
            difference_bs DECIMAL(10,2) DEFAULT 0,
            total_sales INT DEFAULT 0,
            total_revenue_usd DECIMAL(10,2) DEFAULT 0,
            total_revenue_bs DECIMAL(10,2) DEFAULT 0,
            total_tax_usd DECIMAL(10,2) DEFAULT 0,
            total_discount_usd DECIMAL(10,2) DEFAULT 0,
            cancelled_sales INT DEFAULT 0,
            cancelled_amount_usd DECIMAL(10,2) DEFAULT 0,
            notes_open TEXT,
            notes_close TEXT,
            status ENUM('open','closed','pending_review') DEFAULT 'open',
            FOREIGN KEY (company_id) REFERENCES companies(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (opened_by_user_id) REFERENCES users(id),
            FOREIGN KEY (closed_by_user_id) REFERENCES users(id)
        )
    ''')

    # Cash Session Denominations (Arqueo por denominación)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cash_session_denominations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            session_id INT NOT NULL,
            currency ENUM('USD','BS') NOT NULL,
            denomination DECIMAL(10,2) NOT NULL,
            quantity INT NOT NULL DEFAULT 0,
            subtotal DECIMAL(10,2) NOT NULL DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES cash_sessions(id) ON DELETE CASCADE
        )
    ''')

    # Cash Session Payments breakdown (Desglose por método de pago al cierre)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cash_session_payments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            session_id INT NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            count INT DEFAULT 0,
            total_usd DECIMAL(10,2) DEFAULT 0,
            total_bs DECIMAL(10,2) DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES cash_sessions(id) ON DELETE CASCADE
        )
    ''')

    # Sales table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sale_number VARCHAR(100) UNIQUE NOT NULL,
            user_id INT,
            customer_id INT,
            customer_name VARCHAR(255),
            subtotal_usd DECIMAL(10,2) NOT NULL,
            tax_usd DECIMAL(10,2) NOT NULL,
            discount_usd DECIMAL(10,2) DEFAULT 0,
            total_usd DECIMAL(10,2) NOT NULL,
            exchange_rate DECIMAL(10,4) NOT NULL,
            total_bs DECIMAL(10,2) NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            amount_paid_usd DECIMAL(10,2) DEFAULT 0,
            amount_paid_bs DECIMAL(10,2) DEFAULT 0,
            change_usd DECIMAL(10,2) DEFAULT 0,
            change_bs DECIMAL(10,2) DEFAULT 0,
            company_id INT DEFAULT NULL,
            session_id INT DEFAULT NULL,
            notes TEXT,
            status VARCHAR(50) DEFAULT 'completed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (company_id) REFERENCES companies(id),
            FOREIGN KEY (session_id) REFERENCES cash_sessions(id)
        )
    ''')

    # Migrate: add session_id to sales if not exists (for existing DBs)
    try:
        cursor.execute('''
            ALTER TABLE sales ADD COLUMN session_id INT DEFAULT NULL,
            ADD FOREIGN KEY fk_sales_session (session_id) REFERENCES cash_sessions(id)
        ''')
    except Exception:
        pass  # Column already exists

    # Sale items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sale_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sale_id INT,
            product_id INT,
            product_name VARCHAR(255) NOT NULL,
            product_code VARCHAR(100),
            quantity DECIMAL(10,2) NOT NULL,
            price_usd DECIMAL(10,2) NOT NULL,
            price_bs DECIMAL(10,2) NOT NULL,
            discount_percent DECIMAL(5,2) DEFAULT 0,
            total_usd DECIMAL(10,2) NOT NULL,
            total_bs DECIMAL(10,2) NOT NULL,
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')
    
    # Migrate: add tipo_venta and stock_deducted to sale_items
    for col, ddl in [
        ('tipo_venta', 'ALTER TABLE sale_items ADD COLUMN tipo_venta VARCHAR(10) DEFAULT "unidad"'),
        ('stock_deducted', 'ALTER TABLE sale_items ADD COLUMN stock_deducted DECIMAL(10,2) NOT NULL DEFAULT 0'),
    ]:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'sale_items' AND column_name = %s",
            (col,)
        )
        if cursor.fetchone()['COUNT(*)'] == 0:
            cursor.execute(ddl)

    # Debts table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS debts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            company_id INT DEFAULT NULL,
            customer_id INT NOT NULL,
            sale_id INT DEFAULT NULL,
            concept TEXT,
            original_amount_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            original_amount_bs DECIMAL(10,2) NOT NULL DEFAULT 0,
            exchange_rate_at_creation DECIMAL(10,4) NOT NULL DEFAULT 0,
            balance_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            due_date DATE,
            status ENUM('Pendiente','Pagada','Vencida') DEFAULT 'Pendiente',
            FOREIGN KEY (company_id) REFERENCES companies(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (sale_id) REFERENCES sales(id)
        )
    ''')

    # Add columns for existing debts tables (safe to run if already present)
    for col, ddl in [
        ('original_amount_bs', 'ALTER TABLE debts ADD COLUMN original_amount_bs DECIMAL(10,2) NOT NULL DEFAULT 0'),
        ('exchange_rate_at_creation', 'ALTER TABLE debts ADD COLUMN exchange_rate_at_creation DECIMAL(10,4) NOT NULL DEFAULT 0'),
    ]:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'debts' AND column_name = %s",
            (col,)
        )
        if cursor.fetchone()['COUNT(*)'] == 0:
            cursor.execute(ddl)

    # Debt payments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS debt_payments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            company_id INT DEFAULT NULL,
            debt_id INT NOT NULL,
            amount_paid_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            exchange_rate_used DECIMAL(10,4) NOT NULL DEFAULT 0,
            amount_paid_bs DECIMAL(10,2) NOT NULL DEFAULT 0,
            payment_method VARCHAR(50) NOT NULL DEFAULT 'cash',
            reference_number VARCHAR(255) DEFAULT NULL,
            bank_origin VARCHAR(255) DEFAULT NULL,
            bank_destination VARCHAR(255) DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id),
            FOREIGN KEY (debt_id) REFERENCES debts(id) ON DELETE CASCADE
        )
    ''')

    # Chat history table (AI assistant conversation memory)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            company_id INT DEFAULT NULL,
            role ENUM('user','assistant') NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (company_id) REFERENCES companies(id),
            INDEX idx_chat_user_company (user_id, company_id, created_at)
        )
    ''')
    
    # Security Questions (Preguntas de seguridad para recuperación de contraseña)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS security_questions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            question VARCHAR(255) NOT NULL,
            answer VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            INDEX idx_sq_user (user_id)
        )
    ''')

    # Inventory Movements (Auditoría de stock / Mermas)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_movements (
            id INT AUTO_INCREMENT PRIMARY KEY,
            product_id INT NOT NULL,
            user_id INT DEFAULT NULL,
            type ENUM('add', 'remove', 'audit_merma') NOT NULL,
            quantity_changed DECIMAL(10,2) DEFAULT 0,
            previous_stock DECIMAL(10,2) DEFAULT 0,
            new_stock DECIMAL(10,2) DEFAULT 0,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            INDEX idx_inv_mov_product (product_id, created_at)
        )
    ''')

    # Composite indexes for performance with 100+ concurrent users
    indexes = [
        'CREATE INDEX idx_debts_company_status ON debts(company_id, status)',
        'CREATE INDEX idx_debts_due_date ON debts(due_date)',
        'CREATE INDEX idx_sales_company_created ON sales(company_id, created_at)',
        'CREATE INDEX idx_products_company_active ON products(company_id, active)',
        'CREATE INDEX idx_cash_sessions_company_status ON cash_sessions(company_id, status)',
    ]
    for idx in indexes:
        try:
            cursor.execute(idx)
        except Exception:
            pass  # Already exists

    # ── Migrate: columnas de sincronización offline (uuid/updated_at) ──
    # Todas las tablas sincronizables llevan uuid (clave global) y
    # updated_at (LWW). El uuid se rellena con UUID() para filas creadas
    # online; el cliente offline genera el suyo.
    sync_tables = [
        'companies', 'users', 'categories', 'products', 'combo_items',
        'customers', 'exchange_rates', 'sales', 'sale_items',
        'cash_sessions', 'cash_session_denominations', 'cash_session_payments',
        'debts', 'debt_payments', 'inventory_movements', 'security_questions',
    ]
    for table in sync_tables:
        for col, ddl in [
            ('uuid', f'ALTER TABLE {table} ADD COLUMN uuid VARCHAR(36) DEFAULT NULL'),
            ('updated_at', f'ALTER TABLE {table} ADD COLUMN updated_at TIMESTAMP NULL '
                           f'DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'),
        ]:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                (table, col)
            )
            if cursor.fetchone()['COUNT(*)'] == 0:
                cursor.execute(ddl)
        for idx_sql in (
            f'CREATE UNIQUE INDEX uq_{table}_sync_uuid ON {table}(uuid)',
            f'CREATE INDEX idx_{table}_sync_updated ON {table}(updated_at)',
        ):
            try:
                cursor.execute(idx_sql)
            except Exception:
                pass  # Ya existe
        cursor.execute(f'UPDATE {table} SET uuid = UUID() WHERE uuid IS NULL')

    # ── Tombstones: borrados propagados por pull (broadcast) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_tombstones (
            id INT AUTO_INCREMENT PRIMARY KEY,
            company_id INT DEFAULT NULL,
            table_name VARCHAR(64) NOT NULL,
            row_uuid VARCHAR(36) NOT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_tomb (table_name, row_uuid),
            INDEX idx_tomb_company (company_id, created_at)
        )
    ''')

    # ── Migrate: trial/demo columns for companies ──
    for col, ddl in [
        ("status", "ALTER TABLE companies ADD COLUMN status ENUM('pending','active','rejected','expired') DEFAULT 'pending'"),
        ("trial_start", "ALTER TABLE companies ADD COLUMN trial_start DATETIME DEFAULT NULL"),
        ("trial_end", "ALTER TABLE companies ADD COLUMN trial_end DATETIME DEFAULT NULL"),
        ("email", "ALTER TABLE companies ADD COLUMN email VARCHAR(255) DEFAULT NULL"),
    ]:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'companies' AND column_name = %s",
            (col,)
        )
        if cursor.fetchone()['COUNT(*)'] == 0:
            cursor.execute(ddl)

    # Set existing companies to active status (legacy data)
    cursor.execute("UPDATE companies SET status = 'active' WHERE status IS NULL OR status = ''")

    # ── Migrate: email column for users ──
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'email'"
    )
    if cursor.fetchone()['COUNT(*)'] == 0:
        cursor.execute("ALTER TABLE users ADD COLUMN email VARCHAR(255) DEFAULT NULL")

    # Default company (for fresh single-tenant installs)
    cursor.execute("SELECT id FROM companies WHERE name = 'Mi Empresa'")
    default_company = cursor.fetchone()
    if not default_company:
        cursor.execute(
            "INSERT INTO companies (name, active, status) VALUES (%s, 1, 'active')",
            ('Mi Empresa',)
        )
        default_company_id = cursor.lastrowid
    else:
        default_company_id = default_company['id']

    # Default admin user
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, name, role, company_id) VALUES (%s, %s, %s, %s, %s)",
            ('admin', generate_password_hash('admin123'), 'Administrador', 'admin', default_company_id)
        )

    # Default cashier user
    cursor.execute("SELECT id FROM users WHERE username = 'cajero'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, name, role, company_id) VALUES (%s, %s, %s, %s, %s)",
            ('cajero', generate_password_hash('cajero123'), 'Cajero Principal', 'cashier', default_company_id)
        )

    # Default categories
    cursor.execute("SELECT COUNT(*) as count FROM categories")
    if cursor.fetchone()['count'] == 0:
        categories = [
            ('Alimentos', '#f59e0b'),
            ('Bebidas', '#3b82f6'),
            ('Limpieza', '#10b981'),
            ('Cuidado Personal', '#ec4899'),
            ('Electrónica', '#6366f1'),
            ('Papelería', '#f97316'),
            ('Otros', '#6b7280'),
        ]
        cursor.executemany("INSERT INTO categories (name, color) VALUES (%s, %s)", categories)

    # Sample products
    cursor.execute("SELECT COUNT(*) as count FROM products")
    if cursor.fetchone()['count'] == 0:
        sample_products = [
            ('Agua Mineral 600ml',  'P001', 1, 0.50, 0.30, 100, 10),
            ('Refresco 350ml',      'P002', 2, 0.75, 0.45, 80,  10),
            ('Pan de Sandwich',     'P003', 1, 1.20, 0.80, 50,  5),
            ('Detergente 1kg',      'P004', 3, 2.50, 1.80, 30,  5),
            ('Shampoo 400ml',       'P005', 4, 3.00, 2.00, 25,  5),
            ('Cuaderno 100 hojas',  'P006', 6, 1.50, 0.90, 40,  5),
        ]
        cursor.executemany(
            "INSERT INTO products (name, code, category_id, price_usd, cost_usd, stock, min_stock) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            sample_products
        )

    conn.close()
    print("Base de datos inicializada correctamente")
    print("   User Admin:  usuario='admin'  | contrasena='admin123'")
    print("   User Cajero: usuario='cajero' | contrasena='cajero123'")


def init_local_db():
    """Esquema SQLite local para el cliente de escritorio offline-first.

    - uuid/updated_at/sync_dirty en las 16 tablas replicables.
    - sync_idmap (id nube -> id local), sync_state (cursor `since`),
      sync_tombstones (borrados locales pendientes de subir).
    - Con CLOUD_URL definido NO se siembran datos: el primer pull trae
      empresa, usuarios, categorías y productos desde la nube.
    - Sin CLOUD_URL (modo standalone) se siembra igual que MySQL.
    """
    cloud_mode = bool(os.getenv('CLOUD_URL'))
    conn = get_db()

    conn.execute('''
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            rate_mode TEXT DEFAULT 'auto',
            manual_rate REAL,
            status TEXT DEFAULT 'pending',
            trial_start TIMESTAMP,
            trial_end TIMESTAMP,
            email TEXT,
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS counters (
            name VARCHAR(100) PRIMARY KEY,
            val INTEGER NOT NULL DEFAULT 0
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            role TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            company_id INTEGER,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            email VARCHAR(255),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            color VARCHAR(50) DEFAULT '#7c3aed',
            company_id INTEGER,
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            code VARCHAR(100) UNIQUE,
            category_id INTEGER,
            price_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            cost_usd DECIMAL(10,2) DEFAULT 0,
            unidades_por_paquete INTEGER DEFAULT 1,
            costo_unidad DECIMAL(10,2) DEFAULT 0,
            precio_unidad DECIMAL(10,2) DEFAULT 0,
            precio_paquete DECIMAL(10,2) DEFAULT 0,
            margen_unidad_porcentaje DECIMAL(5,2) DEFAULT 0,
            is_combo BOOLEAN DEFAULT 0,
            is_bulk BOOLEAN DEFAULT 0,
            stock INTEGER DEFAULT 0,
            min_stock INTEGER DEFAULT 5,
            active BOOLEAN DEFAULT 1,
            company_id INTEGER,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1,
            sync_base_stock REAL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS combo_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            combo_product_id INTEGER NOT NULL,
            ingredient_product_id INTEGER NOT NULL,
            quantity DECIMAL(10,2) NOT NULL DEFAULT 1,
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            doc_type VARCHAR(10) DEFAULT 'V',
            doc_number VARCHAR(50),
            phone VARCHAR(50),
            email VARCHAR(255),
            address TEXT,
            credit_limit_usd DECIMAL(10,2) DEFAULT 0,
            credit_status TEXT DEFAULT 'Solvente',
            company_id INTEGER,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS exchange_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL UNIQUE,
            rate DECIMAL(10,4) NOT NULL,
            source VARCHAR(50) DEFAULT 'BCV',
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS cash_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            session_number VARCHAR(50) UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            opened_by_user_id INTEGER NOT NULL,
            closed_by_user_id INTEGER,
            open_datetime DATETIME NOT NULL DEFAULT (datetime('now', 'localtime')),
            close_datetime DATETIME,
            exchange_rate_open DECIMAL(10,4) NOT NULL DEFAULT 0,
            exchange_rate_close DECIMAL(10,4),
            opening_amount_usd DECIMAL(10,2) DEFAULT 0,
            opening_amount_bs DECIMAL(10,2) DEFAULT 0,
            expected_cash_usd DECIMAL(10,2) DEFAULT 0,
            expected_cash_bs DECIMAL(10,2) DEFAULT 0,
            closing_amount_usd DECIMAL(10,2) DEFAULT 0,
            closing_amount_bs DECIMAL(10,2) DEFAULT 0,
            difference_usd DECIMAL(10,2) DEFAULT 0,
            difference_bs DECIMAL(10,2) DEFAULT 0,
            total_sales INTEGER DEFAULT 0,
            total_revenue_usd DECIMAL(10,2) DEFAULT 0,
            total_revenue_bs DECIMAL(10,2) DEFAULT 0,
            total_tax_usd DECIMAL(10,2) DEFAULT 0,
            total_discount_usd DECIMAL(10,2) DEFAULT 0,
            cancelled_sales INTEGER DEFAULT 0,
            cancelled_amount_usd DECIMAL(10,2) DEFAULT 0,
            notes_open TEXT,
            notes_close TEXT,
            status TEXT DEFAULT 'open',
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS cash_session_denominations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            denomination DECIMAL(10,2) NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            subtotal DECIMAL(10,2) NOT NULL DEFAULT 0,
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS cash_session_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            count INTEGER DEFAULT 0,
            total_usd DECIMAL(10,2) DEFAULT 0,
            total_bs DECIMAL(10,2) DEFAULT 0,
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_number VARCHAR(100) UNIQUE NOT NULL,
            user_id INTEGER,
            customer_id INTEGER,
            customer_name VARCHAR(255),
            subtotal_usd DECIMAL(10,2) NOT NULL,
            tax_usd DECIMAL(10,2) NOT NULL,
            discount_usd DECIMAL(10,2) DEFAULT 0,
            total_usd DECIMAL(10,2) NOT NULL,
            exchange_rate DECIMAL(10,4) NOT NULL,
            total_bs DECIMAL(10,2) NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            amount_paid_usd DECIMAL(10,2) DEFAULT 0,
            amount_paid_bs DECIMAL(10,2) DEFAULT 0,
            change_usd DECIMAL(10,2) DEFAULT 0,
            change_bs DECIMAL(10,2) DEFAULT 0,
            company_id INTEGER,
            session_id INTEGER,
            notes TEXT,
            status VARCHAR(50) DEFAULT 'completed',
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER,
            product_id INTEGER,
            product_name VARCHAR(255) NOT NULL,
            product_code VARCHAR(100),
            quantity DECIMAL(10,2) NOT NULL,
            price_usd DECIMAL(10,2) NOT NULL,
            price_bs DECIMAL(10,2) NOT NULL,
            discount_percent DECIMAL(5,2) DEFAULT 0,
            total_usd DECIMAL(10,2) NOT NULL,
            total_bs DECIMAL(10,2) NOT NULL,
            tipo_venta VARCHAR(10) DEFAULT 'unidad',
            stock_deducted DECIMAL(10,2) NOT NULL DEFAULT 0,
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            customer_id INTEGER NOT NULL,
            sale_id INTEGER,
            concept TEXT,
            original_amount_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            original_amount_bs DECIMAL(10,2) NOT NULL DEFAULT 0,
            exchange_rate_at_creation DECIMAL(10,4) NOT NULL DEFAULT 0,
            balance_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            due_date DATE,
            status TEXT DEFAULT 'Pendiente',
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS debt_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            debt_id INTEGER NOT NULL,
            amount_paid_usd DECIMAL(10,2) NOT NULL DEFAULT 0,
            exchange_rate_used DECIMAL(10,4) NOT NULL DEFAULT 0,
            amount_paid_bs DECIMAL(10,2) DEFAULT 0,
            payment_method VARCHAR(50) DEFAULT 'cash',
            reference_number VARCHAR(255),
            bank_origin VARCHAR(255),
            bank_destination VARCHAR(255),
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            company_id INTEGER,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS security_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question VARCHAR(255) NOT NULL,
            answer VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS inventory_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            user_id INTEGER,
            type TEXT NOT NULL,
            quantity_changed DECIMAL(10,2) DEFAULT 0,
            previous_stock DECIMAL(10,2) DEFAULT 0,
            new_stock DECIMAL(10,2) DEFAULT 0,
            reason TEXT,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            uuid VARCHAR(36),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sync_dirty INTEGER NOT NULL DEFAULT 1
        )
    ''')

    # Migración para archivos locales creados antes de sync_base_stock
    try:
        conn.execute('ALTER TABLE products ADD COLUMN sync_base_stock REAL')
    except Exception:
        pass  # ya existe

    # ── Infraestructura de sincronización ──
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_tombstones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            table_name VARCHAR(64) NOT NULL,
            row_uuid VARCHAR(36) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (table_name, row_uuid)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_idmap (
            table_name VARCHAR(64) NOT NULL,
            cloud_id INTEGER NOT NULL,
            local_id INTEGER NOT NULL,
            PRIMARY KEY (table_name, cloud_id)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_state (
            name VARCHAR(64) PRIMARY KEY,
            value TEXT
        )
    ''')

    # Índices (uuid único por tabla; NULL repetido permitido)
    for table in SYNCED_TABLES:
        conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_sync_uuid '
            f'ON {table}(uuid)'
        )
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{table}_sync_updated '
            f'ON {table}(updated_at)'
        )
    for idx in (
        'CREATE INDEX IF NOT EXISTS idx_debts_company_status ON debts(company_id, status)',
        'CREATE INDEX IF NOT EXISTS idx_debts_due_date ON debts(due_date)',
        'CREATE INDEX IF NOT EXISTS idx_sales_company_created ON sales(company_id, created_at)',
        'CREATE INDEX IF NOT EXISTS idx_products_company_active ON products(company_id, active)',
        'CREATE INDEX IF NOT EXISTS idx_cash_sessions_company_status ON cash_sessions(company_id, status)',
        'CREATE INDEX IF NOT EXISTS idx_chat_user_company ON chat_history(user_id, company_id, created_at)',
        'CREATE INDEX IF NOT EXISTS idx_sq_user ON security_questions(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_inv_mov_product ON inventory_movements(product_id, created_at)',
    ):
        conn.execute(idx)

    if not cloud_mode:
        # Semillas equivalentes a init_db() MySQL (solo sin nube)
        row = conn.execute("SELECT id FROM companies WHERE name = 'Mi Empresa'").fetchone()
        if not row:
            cur = conn.execute(
                "INSERT INTO companies (name, active, status) VALUES (%s, %s, %s)",
                ('Mi Empresa', 1, 'active')
            )
            default_company_id = cur.lastrowid
        else:
            default_company_id = row['id']

        if not conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone():
            conn.execute(
                "INSERT INTO users (username, password_hash, name, role, company_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                ('admin', generate_password_hash('admin123'), 'Administrador',
                 'admin', default_company_id)
            )
        if not conn.execute("SELECT id FROM users WHERE username = 'cajero'").fetchone():
            conn.execute(
                "INSERT INTO users (username, password_hash, name, role, company_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                ('cajero', generate_password_hash('cajero123'), 'Cajero Principal',
                 'cashier', default_company_id)
            )
        if conn.execute("SELECT COUNT(*) as count FROM categories").fetchone()['count'] == 0:
            conn.executemany(
                "INSERT INTO categories (name, color) VALUES (%s, %s)",
                [
                    ('Alimentos', '#f59e0b'), ('Bebidas', '#3b82f6'),
                    ('Limpieza', '#10b981'), ('Cuidado Personal', '#ec4899'),
                    ('Electrónica', '#6366f1'), ('Papelería', '#f97316'),
                    ('Otros', '#6b7280'),
                ]
            )
        if conn.execute("SELECT COUNT(*) as count FROM products").fetchone()['count'] == 0:
            conn.executemany(
                "INSERT INTO products (name, code, category_id, price_usd, cost_usd, "
                "stock, min_stock) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [
                    ('Agua Mineral 600ml', 'P001', 1, 0.50, 0.30, 100, 10),
                    ('Refresco 350ml', 'P002', 2, 0.75, 0.45, 80, 10),
                    ('Pan de Sandwich', 'P003', 1, 1.20, 0.80, 50, 5),
                    ('Detergente 1kg', 'P004', 3, 2.50, 1.80, 30, 5),
                    ('Shampoo 400ml', 'P005', 4, 3.00, 2.00, 25, 5),
                    ('Cuaderno 100 hojas', 'P006', 6, 1.50, 0.90, 40, 5),
                ]
            )
        print("Base de datos local inicializada correctamente")
        print("   User Admin:  usuario='admin'  | contrasena='admin123'")
        print("   User Cajero: usuario='cajero' | contrasena='cajero123'")
    else:
        print("Base de datos local inicializada (modo offline, datos llegan por pull)")
