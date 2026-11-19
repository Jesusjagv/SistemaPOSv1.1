"""
Migration script for "Cuentas por Cobrar / Gestión de Fiados".

Adds credit columns to customers, creates debts and debt_payments tables.
Safe to run multiple times (idempotent).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import get_db


def column_exists(cursor, table, column):
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (table, column)
    )
    return cursor.fetchone()['cnt'] > 0


def migrate():
    conn = get_db()
    cur = conn.cursor()
    db_name = conn.conn.db  # current database name

    # 1. Add columns to customers
    if not column_exists(cur, 'customers', 'credit_limit_usd'):
        cur.execute("ALTER TABLE customers ADD COLUMN credit_limit_usd DECIMAL(10,2) DEFAULT 0")
        print("  -> credit_limit_usd added to customers")
    if not column_exists(cur, 'customers', 'credit_status'):
        cur.execute("ALTER TABLE customers ADD COLUMN credit_status ENUM('Solvente','En Mora','Suspendido') DEFAULT 'Solvente'")
        print("  -> credit_status added to customers")

    # 2. Create debts table
    cur.execute("""
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
    """)
    print("  -> debts table ready")

    # 2b. Add columns for existing tables that may lack them
    if not column_exists(cur, 'debts', 'original_amount_bs'):
        cur.execute("ALTER TABLE debts ADD COLUMN original_amount_bs DECIMAL(10,2) NOT NULL DEFAULT 0")
        print("  -> original_amount_bs column added")
    if not column_exists(cur, 'debts', 'exchange_rate_at_creation'):
        cur.execute("ALTER TABLE debts ADD COLUMN exchange_rate_at_creation DECIMAL(10,4) NOT NULL DEFAULT 0")
        print("  -> exchange_rate_at_creation column added")

    # 3. Create debt_payments table
    cur.execute("""
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
    """)
    print("  -> debt_payments table ready")

    conn.commit()
    conn.close()
    print("-> Migracion de deudas completada")


if __name__ == '__main__':
    migrate()
