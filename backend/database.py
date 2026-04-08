import pymysql
import os
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import urllib.parse
load_dotenv()
# Connection parameters will be loaded from DATABASE_URL environment variable

class DBWrapper:
    def __init__(self, conn):
        self.conn = conn
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
    def commit(self):
        self.conn.commit()
    def close(self):
        self.conn.close()

import pymysql.converters

conv = pymysql.converters.conversions.copy()
conv[pymysql.constants.FIELD_TYPE.DECIMAL] = float
conv[pymysql.constants.FIELD_TYPE.NEWDECIMAL] = float

def get_db():
    # Parse DATABASE_URL from environment (format: mysql://user:pass@host:port/dbname?ssl-mode=REQUIRED)
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set in environment')
    parsed = urllib.parse.urlparse(db_url)
    # Extract components
    db_user = urllib.parse.unquote(parsed.username)
    db_pass = urllib.parse.unquote(parsed.password)
    db_host = parsed.hostname
    db_port = parsed.port or 3306
    db_name = parsed.path.lstrip('/')
    # SSL mode handling (Aiven requires SSL)
    ssl_params = {'ssl': {'ca': None}} if 'ssl-mode=REQUIRED' in db_url else None
    conn = pymysql.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_pass,
        database=db_name,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        conv=conv,
        ssl=ssl_params
    )
    return DBWrapper(conn)

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            role ENUM('admin', 'cashier') NOT NULL,
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Categories table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            color VARCHAR(50) DEFAULT '#7c3aed'
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
            stock INT DEFAULT 0,
            min_stock INT DEFAULT 5,
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            notes TEXT,
            status VARCHAR(50) DEFAULT 'completed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    ''')

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
    
    # Default admin user
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, name, role) VALUES (%s, %s, %s, %s)",
            ('admin', generate_password_hash('admin123'), 'Administrador', 'admin')
        )

    # Default cashier user
    cursor.execute("SELECT id FROM users WHERE username = 'cajero'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, name, role) VALUES (%s, %s, %s, %s)",
            ('cajero', generate_password_hash('cajero123'), 'Cajero Principal', 'cashier')
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
    print("✅ Base de datos inicializada correctamente")
    print("   👤 Admin:  usuario='admin'  | contraseña='admin123'")
    print("   👤 Cajero: usuario='cajero' | contraseña='cajero123'")
