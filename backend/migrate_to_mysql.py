import sqlite3
import pymysql
import os

MYSQL_HOST = 'localhost'
MYSQL_USER = 'root'
MYSQL_PASS = '15508984'
MYSQL_DB = 'SistemaPOSv1_1'

SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pos.db')

def get_sqlite_conn():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_mysql_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def main():
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"❌ No se encontró pos.db en la ruta {SQLITE_DB_PATH}")
        return

    print("🔌 Conectando a las bases de datos...")
    sqlite_conn = get_sqlite_conn()
    sqlite_cursor = sqlite_conn.cursor()

    try:
        mysql_conn = get_mysql_conn()
        mysql_cursor = mysql_conn.cursor()
    except Exception as e:
        print(f"❌ Error al conectar a MySQL: {e}")
        return

    from database import init_db
    print("🛠️ Inicializando esquemas de tablas en MySQL si no existen...")
    init_db()

    tables = [
        'users', 'categories', 'products', 'customers',
        'exchange_rates', 'sales', 'sale_items'
    ]

    print("🧹 Vaciando tablas en MySQL para la migración...")
    mysql_cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
    for table in tables:
        mysql_cursor.execute(f"TRUNCATE TABLE {table};")
    mysql_cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

    for table in tables:
        sqlite_cursor.execute(f"SELECT * FROM {table}")
        rows = sqlite_cursor.fetchall()
        if not rows:
            print(f"⏭️  Tabla {table} está vacía en SQLite. Omitiendo.")
            continue

        columns = rows[0].keys()
        cols_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))

        insert_query = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})"
        
        data_to_insert = []
        for row in rows:
            data_to_insert.append(tuple(row[col] for col in columns))

        print(f"⏳ Migrando {len(data_to_insert)} registros a -> {table}...")
        mysql_cursor.executemany(insert_query, data_to_insert)
        print(f"✅ Migrada tabla {table}")

    sqlite_conn.close()
    mysql_conn.close()
    print("🎉 Migración completada exitosamente!")

if __name__ == "__main__":
    main()
