"""Tests for /api/superadmin endpoints."""


class TestListCompanies:
    def test_superadmin_can_list(self, client, superadmin_auth):
        r = client.get('/api/superadmin/', headers=superadmin_auth)
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        names = [c['name'] for c in data]
        assert 'Mi Empresa' in names

    def test_admin_denied(self, client, admin_auth):
        r = client.get('/api/superadmin/', headers=admin_auth)
        assert r.status_code == 403

    def test_unauthenticated(self, client):
        r = client.get('/api/superadmin/')
        assert r.status_code == 401


class TestGetCompany:
    def test_get_existing(self, client, superadmin_auth):
        r = client.get('/api/superadmin/1', headers=superadmin_auth)
        assert r.status_code == 200
        data = r.get_json()
        assert data['name'] == 'Mi Empresa'
        assert 'users' in data
        assert len(data['users']) >= 2

    def test_get_not_found(self, client, superadmin_auth):
        r = client.get('/api/superadmin/9999', headers=superadmin_auth)
        assert r.status_code == 404


class TestRegisterCompany:
    def test_register_success(self, client, superadmin_auth):
        import time
        suffix = str(int(time.time() * 1000000))[-6:]
        r = client.post('/api/superadmin/', headers=superadmin_auth, json={
            'company_name': f'Test Company {suffix}',
            'admin_name': 'Test Admin',
            'admin_username': f'test_admin_{suffix}',
            'admin_password': 'test123'
        })
        assert r.status_code == 201
        data = r.get_json()
        assert 'company_id' in data

    def test_register_duplicate_username(self, client, superadmin_auth):
        import time
        suffix = str(int(time.time() * 1000000))[-6:]
        # First registration
        client.post('/api/superadmin/', headers=superadmin_auth, json={
            'company_name': 'First Co',
            'admin_name': 'Admin',
            'admin_username': f'dup_{suffix}',
            'admin_password': 'test123'
        })
        # Second with same username
        r = client.post('/api/superadmin/', headers=superadmin_auth, json={
            'company_name': 'Another Co',
            'admin_name': 'Admin',
            'admin_username': f'dup_{suffix}',  # already taken
            'admin_password': 'test123'
        })
        assert r.status_code == 409

    def test_register_missing_fields(self, client, superadmin_auth):
        r = client.post('/api/superadmin/', headers=superadmin_auth, json={
            'company_name': 'Incomplete'
        })
        assert r.status_code == 400

    def test_admin_denied(self, client, admin_auth):
        r = client.post('/api/superadmin/', headers=admin_auth, json={
            'company_name': 'X',
            'admin_name': 'X',
            'admin_username': 'x',
            'admin_password': 'x'
        })
        assert r.status_code == 403


class TestUpdateCompany:
    def test_toggle_active(self, client, superadmin_auth):
        # Deactivate
        r = client.put('/api/superadmin/1', headers=superadmin_auth, json={
            'active': 0
        })
        assert r.status_code == 200

        # Verify via GET list
        r = client.get('/api/superadmin/', headers=superadmin_auth)
        company = next(c for c in r.get_json() if c['id'] == 1)
        assert company['active'] == 0

        # Reactivate
        r = client.put('/api/superadmin/1', headers=superadmin_auth, json={
            'active': 1
        })
        assert r.status_code == 200

        r = client.get('/api/superadmin/', headers=superadmin_auth)
        company = next(c for c in r.get_json() if c['id'] == 1)
        assert company['active'] == 1

    def test_rename(self, client, superadmin_auth):
        r = client.put('/api/superadmin/1', headers=superadmin_auth, json={
            'name': 'Mi Empresa Editada'
        })
        assert r.status_code == 200

        r = client.get('/api/superadmin/', headers=superadmin_auth)
        company = next(c for c in r.get_json() if c['id'] == 1)
        assert company['name'] == 'Mi Empresa Editada'

        # Revert
        client.put('/api/superadmin/1', headers=superadmin_auth, json={
            'name': 'Mi Empresa'
        })
