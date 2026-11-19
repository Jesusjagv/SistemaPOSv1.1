import pymysql
import os
import queue
import threading
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
    db_url = db_url or os.getenv('DATABASE_URL')
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

from flask import has_app_context, g

def get_db():
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

def init_db():
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
