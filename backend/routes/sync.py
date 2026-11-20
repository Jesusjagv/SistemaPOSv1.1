"""API de sincronización offline-first (escritorio <-> nube).

Endpoints (JWT obligatorio):

  POST /api/sync/push   aplica cambios locales del escritorio en el servidor
  GET  /api/sync/pull   devuelve cambios del servidor desde `since`

Modelo:
  - Clave global: columna `uuid` en cada tabla sincronizable (ver
    SYNC_TABLES). El cliente offline genera uuid con crypto.randomUUID();
    las filas creadas online reciben uuid en init_db/pull (backfill).
  - Conflictos: LWW por `updated_at` (gana la hora más nueva; en empate
    gana el servidor). Nunca se mezclan campos entre versiones.
  - Borrados: cambio con `deleted: true` o DELETE de rutas existentes
    -> fila en sync_tombstones, difundida por pull.
  - Stock de productos: en updates se envía `stock_delta` (delta relativo,
    la nube hace stock = stock + delta); sin delta aplica LWW completo.
  - Hijos (sale_items, etc.): las FK se resuelven por `<col>_uuid`
    (padre creado offline, id local no sirve) o por id si el padre ya
    existe en la nube. El padre debe viajar ANTES que el hijo en la lista.
  - Multi-tenant: toda fila se valida contra company_id del JWT
    (superadmin con company_id NULL ve/toca todo).
"""
from datetime import datetime, date, timezone
from decimal import Decimal
import os
import sys

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db  # noqa: E402

sync_bp = Blueprint('sync', __name__)

# scope='self'    companies: solo la empresa del token
# scope='global'  sin filtro de empresa (exchange_rates)
# scope='col'     columna company_id directa en la tabla
# scope='fk'      empresa vía tabla padre ref=(tabla_padre, columna_fk)
SYNC_TABLES = {
    'companies': {'scope': 'self'},
    'users': {'scope': 'col', 'company_col': 'company_id'},
    'categories': {'scope': 'col', 'company_col': 'company_id'},
    'products': {'scope': 'col', 'company_col': 'company_id'},
    'customers': {'scope': 'col', 'company_col': 'company_id'},
    'exchange_rates': {'scope': 'global'},
    'sales': {'scope': 'col', 'company_col': 'company_id'},
    'cash_sessions': {'scope': 'col', 'company_col': 'company_id'},
    'debts': {'scope': 'col', 'company_col': 'company_id'},
    'combo_items': {'scope': 'fk', 'ref': ('products', 'combo_product_id')},
    'sale_items': {'scope': 'fk', 'ref': ('sales', 'sale_id')},
    'cash_session_denominations': {'scope': 'fk', 'ref': ('cash_sessions', 'session_id')},
    'cash_session_payments': {'scope': 'fk', 'ref': ('cash_sessions', 'session_id')},
    'debt_payments': {'scope': 'fk', 'ref': ('debts', 'debt_id')},
    'inventory_movements': {'scope': 'fk', 'ref': ('products', 'product_id')},
    'security_questions': {'scope': 'fk', 'ref': ('users', 'user_id')},
}

# (set(columnas), {columna_fk: tabla_padre}) por tabla — se cachea por proceso
_meta_cache = {}


def _table_meta(conn, table):
    meta = _meta_cache.get(table)
    if meta:
        return meta
    cols_rows = conn.execute(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s",
        (table,)
    ).fetchall()
    cols = {r['COLUMN_NAME'] for r in cols_rows}
    fk_rows = conn.execute(
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND table_name = %s "
        "AND REFERENCED_TABLE_NAME IS NOT NULL",
        (table,)
    ).fetchall()
    fks = {r['COLUMN_NAME']: r['REFERENCED_TABLE_NAME'] for r in fk_rows}
    meta = (cols, fks)
    _meta_cache[table] = meta
    return meta


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


def _dt_iso(dt):
    if dt is None:
        return None
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _jsonable(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, datetime):
            out[k] = _dt_iso(v)
        elif isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, bytes):
            out[k] = v.decode('utf-8', 'replace')
        else:
            out[k] = v
    return out


# ── Push ────────────────────────────────────────────────────────────

def _validate_company(row, cfg, claims):
    """Sella/valida company_id según scope. Devuelve (ok, error)."""
    cid = claims.get('company_id')
    scope = cfg['scope']
    if scope in ('global', 'fk', 'self'):
        return True, None
    if cid is None:  # superadmin
        return True, None
    col = cfg['company_col']
    val = row.get(col)
    if val is None:
        row[col] = cid
        return True, None
    if int(val) != int(cid):
        return False, 'company_id no coincide con el token'
    return True, None


def _resolve_fks(conn, table, row, claims):
    """Resuelve las FK a ids de la nube. Devuelve (ok, error).

    Por cada FK (p.ej. sale_id) el cliente envía `sale_uuid` (nombre de la
    tabla padre + _uuid) o `sale_id_uuid`; se aceptan ambas formas. Si solo
    viene el id numérico se asume que es un id de la nube (padre ya
    sincronizado); un id local sin uuid no sirve porque cada tienda tiene
    su propia secuencia.
    """
    cid = claims.get('company_id')
    _, fks = _table_meta(conn, table)
    for col, parent in list(fks.items()):
        ukeys = []
        if col.endswith('_id'):
            ukeys.append(col[:-3] + '_uuid')   # sale_id -> sale_uuid
        ukeys.append(col + '_uuid')            # sale_id -> sale_id_uuid
        ukey = next((k for k in ukeys if row.get(k)), None)
        pcols, _ = _table_meta(conn, parent)
        sel = 'id, company_id' if 'company_id' in pcols else 'id'
        if ukey:
            prow = conn.execute(
                f'SELECT {sel} FROM {parent} WHERE uuid = %s', (row[ukey],)
            ).fetchone()
            if not prow:
                return False, f'padre {parent} aun no existe en el servidor ({ukey})'
        elif col in row and row[col] is not None:
            prow = conn.execute(
                f'SELECT {sel} FROM {parent} WHERE id = %s', (row[col],)
            ).fetchone()
            if not prow:
                return False, f'padre {parent} id={row[col]} no existe (envia {ukeys[0]})'
        else:
            continue  # fk opcional NULL
        if ('company_id' in pcols and cid is not None
                and prow.get('company_id') is not None
                and int(prow['company_id']) != int(cid)):
            return False, f'padre {parent} pertenece a otra empresa'
        row[col] = prow['id']
        if ukey:
            row.pop(ukey, None)
    return True, None


def _push_lookup(conn, table, cfg, claims, row_uuid):
    """Devuelve la fila existente por uuid aplicando el scope (o None)."""
    cid = claims.get('company_id')
    if cfg['scope'] == 'self':
        existing = conn.execute(
            f'SELECT id, updated_at FROM {table} WHERE uuid = %s', (row_uuid,)
        ).fetchone()
        if existing is None:
            return None, 'companies: solo se permite actualizar filas existentes'
        if cid is not None and int(existing['id']) != int(cid):
            return None, 'empresa ajena al token'
        return existing, None
    where, params = '', [row_uuid]
    if cfg['scope'] == 'col' and cid is not None:
        where = f' AND {cfg["company_col"]} = %s'
        params.append(cid)
    elif cfg['scope'] == 'fk' and cid is not None:
        parent, pcol = cfg['ref']
        where = (f' AND {pcol} IN (SELECT id FROM {parent} '
                 f'WHERE company_id = %s)')
        params.append(cid)
    existing = conn.execute(
        f'SELECT id, updated_at FROM {table} WHERE uuid = %s{where}', params
    ).fetchone()
    return existing, None


def _insert_row(conn, table, cols, row):
    insert_cols = [c for c in cols if c in row and c != 'id' and row[c] is not None]
    if not insert_cols:
        raise ValueError('row sin columnas insertables')
    ph = ', '.join(['%s'] * len(insert_cols))
    conn.execute(
        f'INSERT INTO {table} ({", ".join(insert_cols)}) VALUES ({ph})',
        tuple(row[c] for c in insert_cols)
    )


def _update_row(conn, table, cols, row, updated_at, stock_delta=None):
    sets, values = [], []
    for c in cols:
        if c in ('id', 'uuid', 'updated_at', 'created_at'):
            continue
        if c not in row or row[c] is None:
            continue
        if table == 'products' and c == 'stock' and stock_delta is not None:
            continue  # stock se aplica como delta (abajo)
        sets.append(f'{c} = %s')
        values.append(row[c])
    if table == 'products' and stock_delta is not None:
        sets.append('stock = COALESCE(stock, 0) + %s')
        values.append(float(stock_delta))
    sets.append('updated_at = %s')
    values.append(updated_at)
    conn.execute(
        f'UPDATE {table} SET {", ".join(sets)} WHERE uuid = %s',
        tuple(values + [row['uuid']])
    )


def _apply_delete(conn, table, cfg, claims, row_uuid):
    cid = claims.get('company_id')
    where, params = 'uuid = %s', [row_uuid]
    if cid is not None:
        if cfg['scope'] == 'col':
            where += f' AND {cfg["company_col"]} = %s'
            params.append(cid)
        elif cfg['scope'] == 'self':
            where += ' AND id = %s'
            params.append(cid)
        elif cfg['scope'] == 'fk':
            parent, pcol = cfg['ref']
            where += f' AND {pcol} IN (SELECT id FROM {parent} WHERE company_id = %s)'
            params.append(cid)
    cur = conn.execute(f'DELETE FROM {table} WHERE {where}', tuple(params))
    conn.execute(
        'INSERT IGNORE INTO sync_tombstones (company_id, table_name, row_uuid) '
        'VALUES (%s, %s, %s)',
        (cid, table, row_uuid)
    )
    return cur.rowcount > 0


def _apply_change(conn, claims, ch, res):
    table = ch.get('table')
    cfg = SYNC_TABLES.get(table)
    if not cfg:
        raise ValueError('tabla no sincronizable')
    row_uuid = ch.get('uuid')
    if not row_uuid or len(str(row_uuid)) > 36:
        raise ValueError('uuid requerido (max 36 caracteres)')
    row_uuid = str(row_uuid)

    if ch.get('deleted'):
        if _apply_delete(conn, table, cfg, claims, row_uuid):
            res['deleted'] += 1
        else:
            res['skipped'] += 1
        return

    row = ch.get('row')
    if not isinstance(row, dict):
        raise ValueError('row (objeto) requerido')
    updated_at = _parse_dt(ch.get('updated_at') or row.get('updated_at'))
    if updated_at is None:
        raise ValueError('updated_at requerido (ISO 8601)')

    cols, _ = _table_meta(conn, table)
    ok, err = _validate_company(row, cfg, claims)
    if not ok:
        raise ValueError(err)
    ok, err = _resolve_fks(conn, table, row, claims)
    if not ok:
        raise ValueError(err)
    row['uuid'] = row_uuid
    row['updated_at'] = updated_at

    existing, err = _push_lookup(conn, table, cfg, claims, row_uuid)
    if err:
        raise ValueError(err)
    if existing is None:
        _insert_row(conn, table, cols, row)
        res['applied'] += 1
        return
    prev = existing.get('updated_at')
    if prev is not None and updated_at <= prev:
        res['skipped'] += 1  # LWW: el servidor tiene una versión igual/más nueva
        return
    _update_row(conn, table, cols, row, updated_at,
                stock_delta=ch.get('stock_delta'))
    res['applied'] += 1


@sync_bp.route('/push', methods=['POST'])
@jwt_required()
def push():
    claims = get_jwt()
    data = request.get_json(silent=True) or {}
    changes = data.get('changes')
    if not isinstance(changes, list):
        return jsonify({'error': 'Se requiere changes (lista)'}), 400
    if len(changes) > 2000:
        return jsonify({'error': 'Maximo 2000 cambios por peticion'}), 400
    conn = get_db()
    res = {'applied': 0, 'skipped': 0, 'deleted': 0, 'errors': []}
    for ch in changes:
        if not isinstance(ch, dict):
            res['errors'].append({'reason': 'cambio invalido (no es objeto)'})
            continue
        try:
            _apply_change(conn, claims, ch, res)
        except Exception as exc:  # noqa: BLE001 — error por cambio, no fatal
            res['errors'].append({
                'table': ch.get('table'),
                'uuid': ch.get('uuid'),
                'reason': str(exc)[:300],
            })
    conn.close()
    return jsonify(res)


# ── Pull ────────────────────────────────────────────────────────────

def _pull_scope(cfg, claims):
    cid = claims.get('company_id')
    if cid is None or cfg['scope'] == 'global':
        return '', []
    if cfg['scope'] == 'self':
        return ' AND id = %s', [cid]
    if cfg['scope'] == 'col':
        return f' AND {cfg["company_col"]} = %s', [cid]
    parent, pcol = cfg['ref']
    return f' AND {pcol} IN (SELECT id FROM {parent} WHERE company_id = %s)', [cid]


@sync_bp.route('/pull', methods=['GET'])
@jwt_required()
def pull():
    claims = get_jwt()
    since_raw = request.args.get('since')
    since = None
    if since_raw:
        since = _parse_dt(since_raw)
        if since is None:
            return jsonify({'error': 'since invalido (ISO 8601)'}), 400
    try:
        limit = int(request.args.get('limit', 2000))
    except ValueError:
        limit = 2000
    limit = max(1, min(limit, 5000))

    conn = get_db()
    # Hora del servidor como tope de esta corrida (reloj único LWW)
    server_ts = conn.execute('SELECT NOW() AS t').fetchone()['t']
    # Backfill: filas creadas online sin uuid reciben uno aquí
    for table in SYNC_TABLES:
        conn.execute(f'UPDATE {table} SET uuid = UUID() WHERE uuid IS NULL')

    since_sql = since.strftime('%Y-%m-%d %H:%M:%S') if since else None
    changes, tombstones = [], []
    budget = limit
    has_more = False

    for table, cfg in SYNC_TABLES.items():
        if budget <= 0:
            has_more = True
            break
        where, params = _pull_scope(cfg, claims)
        tf, tparams = ('', [])
        if since_sql:
            tf = ' AND updated_at >= %s'
            tparams = [since_sql]
        rows = conn.execute(
            f'SELECT * FROM {table} WHERE 1=1{where}{tf} '
            f'ORDER BY updated_at ASC LIMIT %s',
            (*params, *tparams, budget + 1)
        ).fetchall()
        if len(rows) > budget:
            has_more = True
            rows = rows[:budget]
        for r in rows:
            d = dict(r)
            changes.append({
                'table': table,
                'uuid': d.get('uuid'),
                'updated_at': _dt_iso(d.get('updated_at')),
                'row': _jsonable(d),
            })
        budget -= len(rows)

    if budget > 0:
        cid = claims.get('company_id')
        tw, tp = ('', [])
        if cid is not None:
            tw, tp = ' AND company_id = %s', [cid]
        ttf, ttp = ('', [])
        if since_sql:
            ttf = ' AND created_at >= %s'
            ttp = [since_sql]
        trows = conn.execute(
            f'SELECT * FROM sync_tombstones WHERE 1=1{tw}{ttf} '
            f'ORDER BY created_at ASC LIMIT %s',
            (*tp, *ttp, budget + 1)
        ).fetchall()
        if len(trows) > budget:
            has_more = True
            trows = trows[:budget]
        for r in trows:
            d = dict(r)
            tombstones.append({
                'table': d['table_name'],
                'uuid': d['row_uuid'],
                'deleted_at': _dt_iso(d['created_at']),
            })
        budget -= len(trows)

    conn.close()

    if has_more:
        # No avanzar el reloj: reenviar desde el último timestamp enviado
        # (aplicar es idempotente por uuid/LWW)
        stamps = [c['updated_at'] for c in changes if c['updated_at']]
        stamps += [t['deleted_at'] for t in tombstones if t['deleted_at']]
        next_since = max(stamps) if stamps else (_dt_iso(since) if since else _dt_iso(server_ts))
    else:
        next_since = _dt_iso(server_ts)

    return jsonify({
        'changes': changes,
        'tombstones': tombstones,
        'next_since': next_since,
        'has_more': has_more,
    })
