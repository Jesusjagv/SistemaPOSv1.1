"""Hilo de sincronización offline-first (escritorio SQLite <-> nube).

Ciclo periódico (default 8 s):

  1. push  — filas con sync_dirty=1 + sync_tombstones locales
             -> POST /api/sync/push (idempotente; LWW decide en la nube)
  2. pull  — GET /api/sync/pull?since=...
             -> aplica cambios/tombstones con remapeo de ids (sync_idmap)

Reglas:
  - Los padres viajan antes que los hijos (APPLY_ORDER es topológico).
  - LWW: en pull solo se sobrescribe si el cambio entrante es estrictamente
    más nuevo que la fila local (empate gana la nube, igual que en push).
  - Cambios de hijo con padre aún no llegado se reencolan en memoria
    (_pending); el next_since de la nube conserva su paginación, así que
    el padre se vuelve a entregar y el hijo se reaplica.
  - Cero mensajes en la UI: todo va a stdout, con throttling (solo se
    imprime cuando cambia el estado del último error).

Variables: CLOUD_URL, SYNC_USER, SYNC_PASSWORD, SYNC_INTERVAL, MACHINE_ID.
"""
import os
import random
import threading
import time
from datetime import datetime, timezone

# Orden topológico de aplicación local (padres primero). El mismo orden sirve
# para enviar en push. Debe contener las mismas tablas que SYNCED_TABLES.
APPLY_ORDER = (
    'companies', 'users', 'categories', 'products', 'combo_items',
    'customers', 'exchange_rates', 'cash_sessions', 'sales', 'sale_items',
    'cash_session_denominations', 'cash_session_payments', 'debts',
    'debt_payments', 'inventory_movements', 'security_questions',
)

# {columna_fk_local: tabla_padre} para adjuntar `<padre>_uuid` en push
# y remapear ids cloud->local en pull.
ENGINE_FKS = {
    'users': {'company_id': 'companies'},
    'categories': {'company_id': 'companies'},
    'products': {'company_id': 'companies', 'category_id': 'categories'},
    'customers': {'company_id': 'companies'},
    'sales': {'company_id': 'companies', 'user_id': 'users',
              'customer_id': 'customers', 'session_id': 'cash_sessions'},
    'sale_items': {'sale_id': 'sales', 'product_id': 'products'},
    'cash_sessions': {'company_id': 'companies', 'user_id': 'users',
                      'opened_by_user_id': 'users', 'closed_by_user_id': 'users'},
    'cash_session_denominations': {'session_id': 'cash_sessions'},
    'cash_session_payments': {'session_id': 'cash_sessions'},
    'debts': {'company_id': 'companies', 'customer_id': 'customers',
              'sale_id': 'sales'},
    'debt_payments': {'company_id': 'companies', 'debt_id': 'debts'},
    'combo_items': {'combo_product_id': 'products',
                    'ingredient_product_id': 'products'},
    'inventory_movements': {'product_id': 'products', 'user_id': 'users'},
    'security_questions': {'user_id': 'users'},
}

_MAX_PUSH = 1500
_MAX_PULL = 2000
_MAX_PENDING = 500


class MissingParent(Exception):
    """El padre FK de un cambio aún no existe localmente (reencolar)."""


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace('T', ' ')
        if s.endswith('Z'):
            s = s[:-1]
        dt = None
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(str(value))
            except ValueError:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _dt_str(dt):
    if dt is None:
        return None
    if dt.microsecond:
        return dt.strftime('%Y-%m-%d %H:%M:%S.%f')
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _jsonable(value):
    if isinstance(value, datetime):
        return _dt_str(value)
    if hasattr(value, 'isoformat') and not isinstance(value, (str, bytes)):
        try:
            return value.isoformat()
        except ValueError:
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', 'replace')
    return value


# ── Transporte HTTP (inyectable para tests) ──────────────────────────

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload


class RequestsTransport:
    """Transporte real (requests)."""

    def request(self, method, url, json=None, headers=None, timeout=20):
        import requests
        r = requests.request(method, url, json=json, headers=headers or {},
                             timeout=timeout)
        try:
            payload = r.json()
        except ValueError:
            payload = None
        return _Resp(r.status_code, payload)


class FlaskTestTransport:
    """Transporte para tests: enruta al test_client de una app Flask."""

    def __init__(self, app, base_url=''):
        self.app = app
        self.base_url = base_url or ''

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url
        if self.base_url and path.startswith(self.base_url):
            path = path[len(self.base_url):]
        if not path.startswith('/'):
            path = '/' + path
        resp = self.app.test_client().open(
            path, method=method, json=json, headers=headers or {})
        return _Resp(resp.status_code, resp.get_json(silent=True))


class CloudClient:
    """Cliente de /api/auth + /api/sync con login renovable."""

    def __init__(self, base_url, transport=None, user=None, password=None):
        self.base_url = (base_url or '').rstrip('/')
        self.transport = transport or RequestsTransport()
        self.user = user
        self.password = password
        self._token = None

    def login(self):
        r = self.transport.request(
            'POST', f'{self.base_url}/api/auth/login',
            json={'username': self.user, 'password': self.password})
        if r.status_code != 200 or not (r.payload or {}).get('token'):
            raise RuntimeError(f'login nube falló (HTTP {r.status_code})')
        self._token = r.payload['token']

    def _headers(self, retry=True):
        if not self._token:
            self.login()
        return {'Authorization': f'Bearer {self._token}'}

    def _request(self, method, path, **kw):
        r = self.transport.request(method, f'{self.base_url}{path}',
                                   headers=self._headers(), **kw)
        if r.status_code == 401:
            self.login()
            r = self.transport.request(method, f'{self.base_url}{path}',
                                       headers=self._headers(), **kw)
        if r.status_code == 401:
            raise RuntimeError('nube rechaza credenciales (SYNC_USER/SYNC_PASSWORD)')
        if r.status_code >= 500:
            raise RuntimeError(f'nube: HTTP {r.status_code}')
        return r

    def push(self, changes):
        r = self._request('POST', '/api/sync/push', json={'changes': changes})
        if r.status_code != 200:
            raise RuntimeError(f'push HTTP {r.status_code}: {str(r.payload)[:200]}')
        return r.payload or {}

    def pull(self, since=None, limit=_MAX_PULL):
        qs = f'?limit={limit}'
        if since:
            qs += f'&since={since}'
        r = self._request('GET', f'/api/sync/pull{qs}')
        if r.status_code != 200:
            raise RuntimeError(f'pull HTTP {r.status_code}: {str(r.payload)[:200]}')
        return r.payload or {}


# ── Engine ───────────────────────────────────────────────────────────

class SyncEngine:
    def __init__(self, db_factory, client, interval=None, log=print):
        self._db_factory = db_factory
        self.client = client
        self.interval = float(interval if interval is not None
                              else os.getenv('SYNC_INTERVAL', '8'))
        self.log = log
        self._stop = threading.Event()
        self._pending = []
        self._last_msg = None

    # ── ciclo ──

    def run_forever(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.cycle()
                self._msg(None)
            except Exception as exc:  # noqa: BLE001 — el hilo no debe morir
                self._msg(f'sync: {exc}')
            elapsed = time.monotonic() - started
            wait = max(0.5, self.interval - elapsed) + random.uniform(0, 1.5)
            self._stop.wait(wait)

    def stop(self):
        self._stop.set()

    def cycle(self):
        self.push()
        self.pull()

    def _msg(self, msg):
        """Log con throttling: solo imprime cuando cambia el estado."""
        if msg != self._last_msg:
            self.log(f'[sync] {msg}' if msg else '[sync] sincronizado')
            self._last_msg = msg

    # ── push ──

    def push(self):
        conn = self._db_factory()
        changes, sent = [], []
        for table in APPLY_ORDER:
            if len(changes) >= _MAX_PUSH:
                break
            rows = conn.execute(
                f'SELECT * FROM {table} WHERE sync_dirty = 1 LIMIT ?',
                (_MAX_PUSH - len(changes),)
            ).fetchall()
            for row in rows:
                ch = self._row_change(conn, table, row)
                if ch is None:
                    continue
                changes.append(ch)
                sent.append((table, row.get('uuid'), row.get('updated_at')))

        tomb_rows = conn.execute(
            'SELECT table_name, row_uuid FROM sync_tombstones LIMIT ?',
            (_MAX_PUSH // 3,)
        ).fetchall()
        tombstones = [(t['table_name'], t['row_uuid']) for t in tomb_rows]
        for t, u in tombstones:
            changes.append({'table': t, 'uuid': u, 'deleted': True})

        if not changes:
            return {'sent': 0}

        res = self.client.push(changes)
        errors = {(e.get('table'), e.get('uuid'))
                  for e in res.get('errors', []) if isinstance(e, dict)}

        for table, u, ua in sent:
            if (table, u) in errors:
                continue
            if ua is None:
                conn.execute(f'UPDATE {table} SET sync_dirty = 0 WHERE uuid = ?', (u,))
            else:
                conn.execute(
                    f'UPDATE {table} SET sync_dirty = 0 '
                    f'WHERE uuid = ? AND updated_at = ?', (u, ua))
            if table == 'products':
                # base del delta de stock: solo si la fila no se re-dirtyó
                conn.execute(
                    'UPDATE products SET sync_base_stock = stock '
                    'WHERE uuid = ? AND sync_dirty = 0', (u,))

        for t, u in tombstones:
            if (t, u) in errors:
                continue
            conn.execute(
                'DELETE FROM sync_tombstones WHERE table_name = ? AND row_uuid = ?',
                (t, u))
        return {'sent': len(changes), 'errors': len(errors)}

    def _row_change(self, conn, table, row):
        uuid = row.get('uuid')
        if not uuid:
            self._msg(f'sync: fila sin uuid en {table} (id={row.get("id")})')
            return None
        payload = {k: _jsonable(v) for k, v in row.items()
                   if k not in ('id', 'sync_dirty', 'sync_base_stock')}

        stock_delta = None
        if table == 'products':
            base = row.get('sync_base_stock')
            stock = row.get('stock')
            if base is not None and stock is not None and float(base) != float(stock):
                stock_delta = float(stock) - float(base)
                payload['stock'] = float(stock)

        # Adjuntar uuid de padres y QUITAR company_id: la nube lo sella con
        # el token (o lo remapea con company_uuid) — el id local no le sirve.
        for col, parent in ENGINE_FKS.get(table, {}).items():
            val = row.get(col)
            if val is None:
                if col == 'company_id':
                    payload.pop('company_id', None)
                continue
            prow = conn.execute(
                f'SELECT uuid FROM {parent} WHERE id = ?', (val,)).fetchone()
            if not (prow and prow.get('uuid')):
                if col == 'company_id':
                    # sin uuid: que la nube lo selle con el token
                    payload.pop('company_id', None)
                    continue
                self._msg(f'sync: {table}.{col}={val} sin uuid de {parent}')
                return None
            key = (col[:-3] if col.endswith('_id') else col) + '_uuid'
            payload[key] = prow['uuid']
            if col == 'company_id':
                # validate company compara este valor ANTES de resolve_fks
                payload.pop('company_id', None)

        ua = _dt_str(row.get('updated_at'))
        if ua is None:
            ua = _dt_str(datetime.now(timezone.utc).replace(tzinfo=None))
        ch = {'table': table, 'uuid': uuid, 'updated_at': ua, 'row': payload}
        if stock_delta is not None:
            ch['stock_delta'] = stock_delta
        return ch

    # ── pull ──

    def pull(self):
        conn = self._db_factory()
        since = self._state(conn, 'since')
        while True:
            res = self.client.pull(since=since)
            changes = res.get('changes') or []
            tombs = res.get('tombstones') or []
            self._apply_batch(conn, changes, tombs)
            since = res.get('next_since') or since
            self._set_state(conn, 'since', since)
            if not res.get('has_more'):
                break
            if not changes and not tombs:
                break  # seguridad contra respuestas vacías infinitas

        # cambios reencolados en ciclos anteriores (padre acababa de llegar)
        if self._pending:
            pending, self._pending = self._pending, []
            self._apply_batch(conn, pending, [])

    def _apply_batch(self, conn, changes, tombstones):
        by_table = {}
        for ch in changes:
            if isinstance(ch, dict) and ch.get('table') in APPLY_ORDER:
                by_table.setdefault(ch['table'], []).append(ch)

        errors = 0
        for table in APPLY_ORDER:
            for ch in by_table.get(table, ()):
                try:
                    self._apply_one(conn, table, ch)
                except MissingParent:
                    if len(self._pending) < _MAX_PENDING:
                        self._pending.append(ch)
                except Exception as exc:  # noqa: BLE001 — fila tóxica: log y sigue
                    errors += 1
                    self._msg(f'sync pull {table}#{ch.get("uuid")}: {exc}')

        for t in tombstones:
            tu = (t or {}).get('table')
            uu = (t or {}).get('uuid')
            if not tu or not uu or tu not in APPLY_ORDER:
                continue
            conn.execute(f'DELETE FROM {tu} WHERE uuid = ?', (uu,))

        if errors:
            self._msg(f'sync pull: {errors} cambios con error (se descartan)')
        return errors

    def _apply_one(self, conn, table, ch):
        uuid = str(ch.get('uuid') or '')
        if not uuid or len(uuid) > 36:
            raise ValueError('uuid invalido')
        row = dict(ch.get('row') or {})
        cloud_id = row.pop('id', None)
        row.pop('sync_dirty', None)
        row.pop('sync_base_stock', None)

        # Remapear FK de ids de la nube a ids locales via idmap
        for col, parent in ENGINE_FKS.get(table, {}).items():
            if col not in row or row[col] is None:
                continue
            m = conn.execute(
                'SELECT local_id FROM sync_idmap WHERE table_name = ? AND cloud_id = ?',
                (parent, row[col])
            ).fetchone()
            if m is None:
                raise MissingParent(f'{table}.{col} -> {parent}#{row[col]}')
            row[col] = m['local_id']

        ua = _parse_dt(ch.get('updated_at') or row.get('updated_at'))
        if ua is None:
            raise ValueError('updated_at requerido')
        ua_str = _dt_str(ua)
        row['uuid'] = uuid
        row['updated_at'] = ua_str

        existing = conn.execute(
            f'SELECT id, updated_at FROM {table} WHERE uuid = ?', (uuid,)
        ).fetchone()

        if existing is not None:
            if cloud_id is not None:
                self._idmap_upsert(conn, table, cloud_id, existing['id'])
            prev = _parse_dt(existing.get('updated_at'))
            if prev is not None and ua < prev:
                return  # LWW: lo local es más nuevo (gana; push lo subirá)
            sets = [f'{c} = ?' for c in row if c != 'id']
            vals = [row[c] for c in row if c != 'id']
            conn.execute(
                f"UPDATE {table} SET {', '.join(sets)}, sync_dirty = 0 WHERE id = ?",
                (*vals, existing['id'])
            )
            if table == 'products' and row.get('stock') is not None:
                conn.execute(
                    'UPDATE products SET sync_base_stock = ? WHERE id = ?',
                    (float(row['stock']), existing['id']))
            return

        cols = [c for c in row if c != 'id']
        if not cols:
            raise ValueError('row vacia')
        # sync_dirty=0 explícito: la fila viene limpia de la nube
        cols.append('sync_dirty')
        ph = ', '.join('?' * len(cols))
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})",
            tuple(row[c] for c in row if c != 'id') + (0,)
        )
        local_id = cur.lastrowid
        if cloud_id is not None:
            self._idmap_upsert(conn, table, cloud_id, local_id)
        if table == 'products' and row.get('stock') is not None:
            conn.execute(
                'UPDATE products SET sync_base_stock = ? WHERE id = ?',
                (float(row['stock']), local_id))

    @staticmethod
    def _idmap_upsert(conn, table, cloud_id, local_id):
        conn.execute(
            'INSERT INTO sync_idmap (table_name, cloud_id, local_id) '
            'VALUES (?, ?, ?) '
            'ON CONFLICT(table_name, cloud_id) DO UPDATE SET local_id = excluded.local_id',
            (table, cloud_id, local_id)
        )

    def _state(self, conn, name):
        row = conn.execute(
            'SELECT value FROM sync_state WHERE name = ?', (name,)).fetchone()
        return row['value'] if row else None

    def _set_state(self, conn, name, value):
        conn.execute(
            'INSERT INTO sync_state (name, value) VALUES (?, ?) '
            'ON CONFLICT(name) DO UPDATE SET value = excluded.value',
            (name, value)
        )


# ── Arranque desde variables de entorno ──────────────────────────────

_engine = None


def start_sync_engine():
    """Arranca el hilo si hay CLOUD_URL + creds + BD sqlite. No-op si no."""
    global _engine
    from database import resolve_db_url, _is_sqlite_url, _get_sqlite_wrapper

    cloud_url = os.getenv('CLOUD_URL', '').strip()
    if not cloud_url:
        return None
    db_url = resolve_db_url()
    if not _is_sqlite_url(db_url):
        print('[sync] CLOUD_URL definido pero la BD no es sqlite '
              '(la sincronización solo aplica al escritorio)')
        return None
    user = os.getenv('SYNC_USER', '').strip()
    password = os.getenv('SYNC_PASSWORD', '')
    if not user or not password:
        print('[sync] faltan SYNC_USER/SYNC_PASSWORD; sincronización desactivada')
        return None

    client = CloudClient(cloud_url, user=user, password=password)
    _engine = SyncEngine(
        db_factory=lambda: _get_sqlite_wrapper(db_url, engine_mode=True),
        client=client,
    )
    t = threading.Thread(target=_engine.run_forever, daemon=True, name='pos-sync')
    t.start()
    print(f'[sync] sincronización activa contra {cloud_url}')
    return _engine


def get_sync_engine():
    return _engine
