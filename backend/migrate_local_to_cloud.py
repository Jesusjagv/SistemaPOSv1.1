#!/usr/bin/env python
"""Copia los datos de la BD local a la BD en la nube (TiDB Cloud / Render).

Uso (desde la raÃ­z del proyecto):

    set CLOUD_DATABASE_URL=mysql://user:pass@host:4006/db?ssl-mode=REQUIRED
    .venv\\Scripts\\python.exe backend\\migrate_local_to_cloud.py

Opciones:
    --target <url>    URL de destino (alternativa a la env CLOUD_DATABASE_URL)
    --create-schema   crea en destino las tablas que falten (DDL del origen)
    --truncate        limpia cada tabla destino antes de copiar (pide confirmaciÃ³n)
    --tables a,b      solo procesa estas tablas
    --dry-run         solo muestra el plan, no escribe nada
    --yes             no pedir confirmaciÃ³n (para --truncate en automÃ¡tico)

Comportamiento:
    - Fuente: LOCAL_DATABASE_URL si existe, si no DATABASE_URL (.env local)
    - Idempotente: por defecto OMITE tablas destino que ya tienen filas
    - FOREIGN_KEY_CHECKS=0 en destino durante la copia
    - Copia solo las columnas presentes en ambos lados (intersecciÃ³n)
    - Resumen final de filas origen vs destino por tabla

Flujo recomendado con Render:
    1) Deploy del servicio en Render (init_db crea el esquema en TiDB)
    2) Este script copia los datos (--truncate si ya hay datos previos)
    Alternativa: --create-schema ANTES del primer deploy de Render.
"""
import argparse
import os
import sys

import pymysql
from dotenv import load_dotenv

load_dotenv()

from database import _parse_db_url  # noqa: E402  (reutiliza parseo + SSL certifi)

BATCH = 1000


def connect(url):
    return pymysql.connect(**_parse_db_url(url))


def list_tables(conn):
    with conn.cursor(pymysql.cursors.Cursor) as cur:
        cur.execute('SHOW TABLES')
        return [row[0] for row in cur.fetchall()]


def table_columns(conn, table):
    with conn.cursor(pymysql.cursors.Cursor) as cur:
        cur.execute(f'SHOW COLUMNS FROM `{table}`')
        return [row[0] for row in cur.fetchall()]


def count_rows(conn, table):
    with conn.cursor(pymysql.cursors.Cursor) as cur:
        cur.execute(f'SELECT COUNT(*) FROM `{table}`')
        return cur.fetchone()[0]


def copy_table(src, tgt, table, dry_run=False):
    src_cols = table_columns(src, table)
    tgt_cols = table_columns(tgt, table)
    common = [c for c in src_cols if c in tgt_cols]
    only_src = [c for c in src_cols if c not in tgt_cols]
    if not common:
        print(f'  [ERROR] {table}: sin columnas en comÃºn')
        return 0, 'error'
    if only_src:
        print(f'  [AVISO] {table}: columnas ignoradas (no existen en destino): {", ".join(only_src)}')
    n_src = count_rows(src, table)
    if dry_run:
        print(f'  [DRY] {table}: {n_src} filas -> destino, {len(common)} columnas')
        return n_src, 'dry'
    cols_sql = ', '.join(f'`{c}`' for c in common)
    placeholders = ', '.join(['%s'] * len(common))
    insert_sql = f'INSERT INTO `{table}` ({cols_sql}) VALUES ({placeholders})'
    select_sql = f'SELECT {cols_sql} FROM `{table}`'
    copied = 0
    with src.cursor(pymysql.cursors.Cursor) as scur, tgt.cursor(pymysql.cursors.Cursor) as tcur:
        scur.execute(select_sql)
        while True:
            rows = scur.fetchmany(BATCH)
            if not rows:
                break
            tcur.executemany(insert_sql, rows)
            copied += len(rows)
    tgt.commit()
    if copied != n_src:
        print(f'  [ERROR] {table}: copiadas {copied}/{n_src} filas')
        return copied, 'error'
    print(f'  [OK] {table}: {copied} filas')
    return copied, 'ok'


def main():
    ap = argparse.ArgumentParser(description='Copia datos local -> nube')
    ap.add_argument('--target', help='URL de la BD destino (nube)')
    ap.add_argument('--create-schema', action='store_true',
                    help='Crear en destino las tablas que falten (DDL del origen)')
    ap.add_argument('--truncate', action='store_true',
                    help='Vaciar cada tabla destino antes de copiar')
    ap.add_argument('--tables', help='Lista separada por comas de tablas a copiar')
    ap.add_argument('--dry-run', action='store_true', help='No escribe nada')
    ap.add_argument('--yes', action='store_true', help='No pedir confirmaciÃ³n')
    args = ap.parse_args()

    src_url = os.getenv('LOCAL_DATABASE_URL') or os.getenv('DATABASE_URL')
    tgt_url = args.target or os.getenv('CLOUD_DATABASE_URL')
    if not src_url:
        sys.exit('[ERROR] Falta DATABASE_URL (fuente local) en .env')
    if not tgt_url:
        sys.exit('[ERROR] Falta CLOUD_DATABASE_URL o --target <url> (destino nube)')

    src = connect(src_url)
    tgt = connect(tgt_url)

    tables = list_tables(src)
    if args.tables:
        wanted = [t.strip() for t in args.tables.split(',') if t.strip()]
        unknown = [t for t in wanted if t not in tables]
        if unknown:
            sys.exit(f'[ERROR] Tablas inexistentes en origen: {", ".join(unknown)}')
        tables = wanted

    tgt_tables = set(list_tables(tgt))
    missing = [t for t in tables if t not in tgt_tables]

    print(f'Origen : {src_url.split("@")[-1]}')
    print(f'Destino: {tgt_url.split("@")[-1]}')
    print(f'Tablas : {len(tables)} a procesar, {len(missing)} sin crear en destino')
    if missing:
        print(f'  Faltan: {", ".join(missing)}')
        if not args.create_schema:
            print('  [AVISO] No se copiarÃ¡n. Crea el esquema primero (deploy en '
                  'Render ejecuta init_db) o vuelve a lanzar con --create-schema.')

    with tgt.cursor(pymysql.cursors.Cursor) as tcur:
        if args.truncate and not args.dry_run:
            if not args.yes:
                total = sum(count_rows(tgt, t) for t in tables if t in tgt_tables)
                resp = input(f'Â¿Vaciar {len([t for t in tables if t in tgt_tables])} '
                             f'tablas destino con {total} filas? [s/N] ').strip().lower()
                if resp not in ('s', 'si', 'sÃ­', 'y', 'yes'):
                    print('Cancelado.')
                    return 1
            tcur.execute('SET FOREIGN_KEY_CHECKS=0')
            for t in tables:
                if t in tgt_tables:
                    tcur.execute(f'TRUNCATE `{t}`')
            print('Tablas destino vaciadas.')
        elif not args.dry_run:
            tcur.execute('SET FOREIGN_KEY_CHECKS=0')

        if args.create_schema and missing and not args.dry_run:
            for t in missing:
                with src.cursor(pymysql.cursors.Cursor) as scur:
                    scur.execute(f'SHOW CREATE TABLE `{t}`')
                    ddl = scur.fetchone()[1]
                try:
                    tcur.execute(ddl)
                    print(f'  [DDL] {t}: creada en destino')
                except Exception as exc:
                    print(f'  [ERROR] {t}: no se pudo crear -> {exc}')
            tgt.commit()
            tgt_tables = set(list_tables(tgt))

    resumen = {'ok': 0, 'skip': 0, 'error': 0}
    for t in tables:
        if t not in tgt_tables:
            resumen['skip'] += 1
            continue
        tgt_n = count_rows(tgt, t)
        if tgt_n > 0 and not args.truncate:
            if args.dry_run:
                print(f'  [DRY-SKIP] {t}: destino ya tiene {tgt_n} filas')
            else:
                print(f'  [SKIP] {t}: destino ya tiene {tgt_n} filas (usa --truncate para recopiar)')
            resumen['skip'] += 1
            continue
        try:
            _, estado = copy_table(src, tgt, t, dry_run=args.dry_run)
        except Exception as exc:
            print(f'  [ERROR] {t}: {exc}')
            estado = 'error'
        resumen[estado] = resumen.get(estado, 0) + 1

    src.close()
    tgt.close()
    print(f"\nResumen: {resumen.get('ok', 0)} copiadas, {resumen.get('dry', 0)} en dry-run, "
          f"{resumen.get('skip', 0)} omitidas, {resumen.get('error', 0)} con error"
          + (' (dry-run, sin escrituras)' if args.dry_run else ''))
    return 1 if resumen.get('error', 0) else 0


if __name__ == '__main__':
    sys.exit(main())

