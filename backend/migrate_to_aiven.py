import os
from database import init_db
import pymysql
from dotenv import load_dotenv

load_dotenv()

# Source (local) DB connection parameters (hard‑coded as they were before migration)
SRC_HOST = 'localhost'
SRC_USER = 'root'
SRC_PASS = '15508984'
SRC_DB = 'SistemaPOSv1_1'

def get_src_conn():
    return pymysql.connect(
        host=SRC_HOST,
        user=SRC_USER,
        password=SRC_PASS,
        database=SRC_DB,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

# Target (Aiven) DB connection – uses the DATABASE_URL env variable
from urllib.parse import urlparse, unquote

def get_target_conn():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set in environment')
    parsed = urlparse(db_url)
    user = unquote(parsed.username)
    password = unquote(parsed.password)
    host = parsed.hostname
    port = parsed.port or 3306
    db = parsed.path.lstrip('/')
    # SSL mode handling (Aiven requires SSL)
    ssl_params = None
    if 'ssl-mode=REQUIRED' in db_url:
        ssl_params = {
            'ssl': {
                'ca': None,
                'check_hostname': False
            }
        }
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        ssl=ssl_params,
    )

TABLES = [
    'users', 'categories', 'products', 'customers',
    'exchange_rates', 'sales', 'sale_items'
]

def migrate():
    src = get_src_conn()
    tgt = get_target_conn()
    src_cur = src.cursor()
    tgt_cur = tgt.cursor()

    # Disable FK checks on target for clean truncate/insert
    # Ensure tables exist
    init_db()
    tgt_cur.execute('SET FOREIGN_KEY_CHECKS = 0;')
    for tbl in TABLES:
        tgt_cur.execute(f'TRUNCATE TABLE {tbl};')
    tgt_cur.execute('SET FOREIGN_KEY_CHECKS = 1;')
    print('Tablas en Aiven preparadas')

    for tbl in TABLES:
        src_cur.execute(f'SELECT * FROM {tbl};')
        rows = src_cur.fetchall()
        if not rows:
            print(f'Table {tbl} empty - skipping')
            continue
        cols = rows[0].keys()
        cols_str = ', '.join(cols)
        placeholders = ', '.join(['%s'] * len(cols))
        insert_sql = f'INSERT INTO {tbl} ({cols_str}) VALUES ({placeholders});'
        data = [tuple(row[col] for col in cols) for row in rows]
        tgt_cur.executemany(insert_sql, data)
        print(f'Migrated {len(data)} rows into {tbl}')

    src.close()
    tgt.close()
    print('Migration to Aiven completed')

if __name__ == '__main__':
    migrate()
