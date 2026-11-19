"""Tests for /api/auth endpoints."""


class TestLogin:
    def test_success_admin(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'admin123'
        })
        assert r.status_code == 200
        data = r.get_json()
        assert 'token' in data
        assert data['user']['role'] == 'admin'
        assert data['user']['company_id'] is not None

    def test_success_cashier(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'cajero', 'password': 'cajero123'
        })
        assert r.status_code == 200
        assert r.get_json()['user']['role'] == 'cashier'

    def test_success_superadmin(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'master', 'password': 'Master123!'
        })
        assert r.status_code == 200
        assert r.get_json()['user']['role'] == 'superadmin'
        assert r.get_json()['user']['company_id'] is None

    def test_wrong_password(self, client):
        r = client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'wrong'
        })
        assert r.status_code == 401
        assert 'error' in r.get_json()

    def test_missing_fields(self, client):
        r = client.post('/api/auth/login', json={'username': 'admin'})
        assert r.status_code == 400

    def test_inactive_user(self, client, admin_token):
        """Create temp user, deactivate, verify login blocked."""
        # Use timestamp for unique username to avoid test DB collisions
        import time
        suffix = str(int(time.time() * 1000000))[-6:]
        username = f'temp_inactive_{suffix}'

        r = client.post('/api/auth/users', headers={
            'Authorization': f'Bearer {admin_token}'
        }, json={
            'username': username,
            'password': 'temp123',
            'name': 'Temp Inactive',
            'role': 'cashier'
        })
        assert r.status_code == 201

        # Get the user's real id from list
        r = client.get('/api/auth/users', headers={
            'Authorization': f'Bearer {admin_token}'
        })
        users = r.get_json()
        uid = next(u['id'] for u in users if u['username'] == username)

        # Deactivate (backend requires name + role)
        client.put(f'/api/auth/users/{uid}', headers={
            'Authorization': f'Bearer {admin_token}'
        }, json={'name': 'Temp Inactive', 'role': 'cashier', 'active': 0})

        # Login should fail
        r = client.post('/api/auth/login', json={
            'username': username, 'password': 'temp123'
        })
        assert r.status_code == 401

        # Reactivate
        client.put(f'/api/auth/users/{uid}', headers={
            'Authorization': f'Bearer {admin_token}'
        }, json={'name': 'Temp Inactive', 'role': 'cashier', 'active': 1})

        r = client.post('/api/auth/login', json={
            'username': username, 'password': 'temp123'
        })
        assert r.status_code == 200


class TestMe:
    def test_authenticated(self, client, admin_auth):
        r = client.get('/api/auth/me', headers=admin_auth)
        assert r.status_code == 200
        assert r.get_json()['username'] == 'admin'

    def test_unauthenticated(self, client):
        r = client.get('/api/auth/me')
        assert r.status_code == 401


class TestUserCRUD:
    def test_list_users(self, client, admin_auth):
        r = client.get('/api/auth/users', headers=admin_auth)
        assert r.status_code == 200
        users = r.get_json()
        assert len(users) >= 2
        usernames = [u['username'] for u in users]
        assert 'admin' in usernames
        assert 'cajero' in usernames

    def test_create_user(self, client, admin_auth):
        import time
        suffix = str(int(time.time() * 1000000))[-6:]
        username = f'testuser_{suffix}'
        r = client.post('/api/auth/users', headers=admin_auth, json={
            'username': username,
            'password': 'test123',
            'name': 'Test User',
            'role': 'cashier'
        })
        assert r.status_code == 201

        # Verify it appears in list
        r = client.get('/api/auth/users', headers=admin_auth)
        usernames = [u['username'] for u in r.get_json()]
        assert username in usernames

    def test_cashier_denied(self, client, cashier_auth):
        r = client.get('/api/auth/users', headers=cashier_auth)
        assert r.status_code == 403

    def test_duplicate_username(self, client, admin_auth):
        r = client.post('/api/auth/users', headers=admin_auth, json={
            'username': 'admin',
            'password': 'x',
            'name': 'Duplicate',
            'role': 'cashier'
        })
        assert r.status_code == 409
