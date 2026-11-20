import os
import sys
import pytest

# Point to test database
TEST_DB_URL = os.getenv(
    'TEST_DATABASE_URL',
    'mysql://root:15508984@localhost:3306/SistemaPOSv1_1_test'
)

# Make backend/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
# Make app.py (root) importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


@pytest.fixture(scope='session')
def app():
    """Flask app instance using the test database."""
    os.environ['DATABASE_URL'] = TEST_DB_URL

    # Recreate test DB from scratch each session
    import pymysql
    _c = pymysql.connect(host='localhost', port=3306, user='root', password='15508984')
    _cur = _c.cursor()
    _cur.execute('DROP DATABASE IF EXISTS SistemaPOSv1_1_test')
    _cur.execute('CREATE DATABASE SistemaPOSv1_1_test')
    _c.close()

    # backend/app.py EXPLÍCITO: la raíz tiene una copia vieja sin los
    # blueprints de settings/cash_sessions/sync (el orden de sys.path
    # no basta para distinguirlas).
    from backend.app import app as _app
    from database import init_db, get_db
    from werkzeug.security import generate_password_hash
    init_db()  # creates schema + seeds default data

    # Seed superadmin user (not created by init_db)
    conn = get_db()
    conn.execute(
        "INSERT IGNORE INTO users (username, password_hash, name, role, company_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        ('master', generate_password_hash('Master123!'), 'Super Admin', 'superadmin', None)
    )

    # Assign seed categories/products to default company (init_db creates them with NULL company_id)
    conn.execute("UPDATE categories SET company_id = (SELECT id FROM companies WHERE name = 'Mi Empresa') WHERE company_id IS NULL")
    conn.execute("UPDATE products SET company_id = (SELECT id FROM companies WHERE name = 'Mi Empresa') WHERE company_id IS NULL")
    conn.commit()
    conn.close()

    return _app


@pytest.fixture
def client(app):
    """Flask test client (isolated per test)."""
    return app.test_client()


# ── Authenticated tokens ──────────────────────────────────────

@pytest.fixture
def superadmin_token(client):
    """Token for superadmin user 'master'."""
    r = client.post('/api/auth/login', json={
        'username': 'master',
        'password': 'Master123!'
    })
    assert r.status_code == 200
    return r.get_json()['token']


@pytest.fixture
def admin_token(client):
    """Token for admin user of default company."""
    r = client.post('/api/auth/login', json={
        'username': 'admin',
        'password': 'admin123'
    })
    assert r.status_code == 200
    return r.get_json()['token']


@pytest.fixture
def cashier_token(client):
    """Token for cashier user of default company."""
    r = client.post('/api/auth/login', json={
        'username': 'cajero',
        'password': 'cajero123'
    })
    assert r.status_code == 200
    return r.get_json()['token']


@pytest.fixture
def auth_header():
    """Helper: returns a dict {'Authorization': 'Bearer <token>'}."""
    def _make(token):
        return {'Authorization': f'Bearer {token}'}
    return _make


@pytest.fixture
def admin_auth(admin_token, auth_header):
    return auth_header(admin_token)


@pytest.fixture
def superadmin_auth(superadmin_token, auth_header):
    return auth_header(superadmin_token)


@pytest.fixture
def cashier_auth(cashier_token, auth_header):
    return auth_header(cashier_token)
