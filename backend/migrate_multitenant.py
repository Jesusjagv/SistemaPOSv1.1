import os, sys, urllib.parse
import pymysql
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
load_dotenv()

def run_migration():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return False

    parsed = urllib.parse.urlparse(db_url)
    user = urllib.parse.unquote(parsed.username)
    password = urllib.parse.unquote(parsed.password)
    host = parsed.hostname
    port = parsed.port or 3306
    dbname = parsed.path.lstrip('/')

    conn = pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=dbname, cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )
    cursor = conn.cursor()

    print("=" * 50)
    print("  MIGRACIÓN MULTI-TENANT")
    print("=" * 50)

    # ── 1. Extender ENUM de users.role ──
    cursor.execute("SHOW COLUMNS FROM users WHERE Field = 'role'")
    row = cursor.fetchone()
    if row and 'superadmin' not in row['Type']:
        cursor.execute("ALTER TABLE users MODIFY COLUMN role ENUM('admin','cashier','superadmin') NOT NULL DEFAULT 'cashier'")
        print("[OK] users.role modificado -> incluye 'superadmin'")
    else:
        print("[ ] users.role ya incluye 'superadmin'")

    # ── 2. Agregar company_id a las tablas ──
    tablas = ['users', 'products', 'customers', 'sales', 'categories']
    for tabla in tablas:
        cursor.execute(f"SHOW COLUMNS FROM {tabla} WHERE Field = 'company_id'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN company_id INT DEFAULT NULL")
            print(f"[OK] Columna company_id agregada a {tabla}")
        else:
            print(f"[ ] company_id ya existe en {tabla}")

    # ── 3. Agregar Foreign Keys ──
    fks = [
        ('users',      'fk_users_company'),
        ('products',   'fk_products_company'),
        ('customers',  'fk_customers_company'),
        ('sales',      'fk_sales_company'),
        ('categories', 'fk_categories_company'),
    ]
    for tabla, fk_name in fks:
        cursor.execute(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = 'company_id' AND REFERENCED_TABLE_NAME IS NOT NULL",
            (tabla,)
        )
        if not cursor.fetchone():
            cursor.execute(
                f"ALTER TABLE {tabla} ADD CONSTRAINT {fk_name} "
                f"FOREIGN KEY (company_id) REFERENCES companies(id)"
            )
            print(f"[OK] FK {fk_name} agregada: {tabla}.company_id -> companies.id")
        else:
            print(f"[ ] FK en {tabla}.company_id ya existe")

    # ── 4. Crear usuario superadmin maestro ──
    cursor.execute("SELECT id FROM users WHERE username = 'master'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, name, role, company_id, active) "
            "VALUES (%s, %s, %s, 'superadmin', NULL, 1)",
            ('master', generate_password_hash('Master123!'), 'Super Administrador')
        )
        print("[OK] Usuario superadmin creado: master / Master123!")
    else:
        print("[ ] El usuario 'master' ya existe")

    # ── 5. Asignar usuarios legacy sin company_id a empresa por defecto ──
    cursor.execute("SELECT id FROM companies WHERE name = 'Mi Empresa'")
    default_company = cursor.fetchone()
    if not default_company:
        cursor.execute("INSERT INTO companies (name, active) VALUES ('Mi Empresa', 1)")
        default_company_id = cursor.lastrowid
        print("[OK] Empresa por defecto 'Mi Empresa' creada (id=%d)" % default_company_id)
    else:
        default_company_id = default_company['id']

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE company_id IS NULL AND role != 'superadmin'")
    legacy_users = cursor.fetchone()['cnt']
    if legacy_users:
        cursor.execute(
            "UPDATE users SET company_id = %s WHERE company_id IS NULL AND role != 'superadmin'",
            (default_company_id,)
        )
        print("[OK] %d usuarios legacy asignados a 'Mi Empresa' (company_id=%d)" % (legacy_users, default_company_id))
    else:
        print("[ ] No hay usuarios legacy sin company_id")

    # ── 6. Asignar datos legacy (productos, clientes, ventas) a empresa por defecto ──
    for tabla in ['products', 'customers', 'sales']:
        cursor.execute("SELECT COUNT(*) as cnt FROM %s WHERE company_id IS NULL" % tabla)
        cnt = cursor.fetchone()['cnt']
        if cnt:
            cursor.execute("UPDATE %s SET company_id = %%s WHERE company_id IS NULL" % tabla, (default_company_id,))
            print("[OK] %d registros de %s asignados a 'Mi Empresa'" % (cnt, tabla))
        else:
            print("[ ] Todos los registros de %s ya tienen company_id" % tabla)

    conn.close()
    print("\n Migracion completada exitosamente.")
    print("   Usuario: master   |   Contraseña: Master123!")
    return True


if __name__ == '__main__':
    run_migration()
