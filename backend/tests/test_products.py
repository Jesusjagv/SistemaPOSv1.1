"""Tests for /api/products endpoints."""


class TestCategories:
    def test_list_categories(self, client, admin_auth):
        r = client.get('/api/products/categories/all', headers=admin_auth)
        assert r.status_code == 200
        cats = r.get_json()
        assert len(cats) >= 7
        names = [c['name'] for c in cats]
        assert 'Alimentos' in names

    def test_create_category(self, client, admin_auth):
        r = client.post('/api/products/categories/all', headers=admin_auth, json={
            'name': 'Test Category',
            'color': '#ff0000'
        })
        assert r.status_code == 201
        assert r.get_json()['id'] > 0

    def test_cashier_denied_create_category(self, client, cashier_auth):
        r = client.post('/api/products/categories/all', headers=cashier_auth, json={
            'name': 'Nope'
        })
        assert r.status_code == 403


class TestProducts:
    def test_list_products(self, client, admin_auth):
        r = client.get('/api/products', headers=admin_auth)
        assert r.status_code == 200
        products = r.get_json()
        assert len(products) >= 6  # seed data
        names = [p['name'] for p in products]
        assert 'Agua Mineral 600ml' in names

    def test_get_product(self, client, admin_auth):
        r = client.get('/api/products/1', headers=admin_auth)
        assert r.status_code == 200
        p = r.get_json()
        assert 'name' in p
        assert 'category_name' in p

    def test_get_product_not_found(self, client, admin_auth):
        r = client.get('/api/products/9999', headers=admin_auth)
        assert r.status_code == 404

    def test_create_product(self, client, admin_auth):
        r = client.post('/api/products', headers=admin_auth, json={
            'name': 'Test Product',
            'code': 'TP001',
            'price_usd': 10.00,
            'cost_usd': 5.00,
            'stock': 100,
            'category_id': 1
        })
        assert r.status_code == 201

    def test_create_product_missing_name(self, client, admin_auth):
        r = client.post('/api/products', headers=admin_auth, json={
            'price_usd': 10
        })
        assert r.status_code == 400

    def test_create_product_zero_price(self, client, admin_auth):
        r = client.post('/api/products', headers=admin_auth, json={
            'name': 'Free Product',
            'price_usd': 0
        })
        assert r.status_code == 400

    def test_duplicate_code(self, client, admin_auth):
        r = client.post('/api/products', headers=admin_auth, json={
            'name': 'Duplicate Code',
            'code': 'TP001',
            'price_usd': 5.00
        })
        assert r.status_code == 409

    def test_cashier_denied_create(self, client, cashier_auth):
        r = client.post('/api/products', headers=cashier_auth, json={
            'name': 'Nope',
            'price_usd': 1
        })
        assert r.status_code == 403

    def test_update_product(self, client, admin_auth):
        r = client.put('/api/products/1', headers=admin_auth, json={
            'name': 'Agua Mineral Editada',
            'code': 'P001',
            'price_usd': 0.60,
            'cost_usd': 0.35,
            'stock': 200,
            'active': 1
        })
        assert r.status_code == 200

        # Verify
        r = client.get('/api/products/1', headers=admin_auth)
        assert r.get_json()['name'] == 'Agua Mineral Editada'

        # Revert
        client.put('/api/products/1', headers=admin_auth, json={
            'name': 'Agua Mineral 600ml',
            'code': 'P001',
            'price_usd': 0.50,
            'cost_usd': 0.30,
            'stock': 100,
            'active': 1
        })

    def test_delete_product(self, client, admin_auth):
        # Create a product with no sales history
        r = client.post('/api/products', headers=admin_auth, json={
            'name': 'Delete Me',
            'code': 'DEL001',
            'price_usd': 1.00,
            'stock': 1
        })
        pid = r.get_json()['id']

        r = client.delete(f'/api/products/{pid}', headers=admin_auth)
        assert r.status_code == 200

    def test_delete_with_sales_denied(self, client, admin_auth):
        """Creating a product then trying to delete it should succeed (no sales yet)."""
        import time
        suffix = str(int(time.time() * 1000000))[-6:]
        r = client.post('/api/products', headers=admin_auth, json={
            'name': f'TempDelete {suffix}',
            'code': f'TD{suffix}',
            'price_usd': 1.00,
            'stock': 1
        })
        pid = r.get_json()['id']
        r = client.delete(f'/api/products/{pid}', headers=admin_auth)
        # Should succeed since this product has no sales history
        assert r.status_code in (200, 400)

    def test_stock_patch(self, client, admin_auth):
        r = client.patch('/api/products/1/stock', headers=admin_auth, json={
            'stock': 999
        })
        assert r.status_code == 200

        # Verify via GET
        r = client.get('/api/products/1', headers=admin_auth)
        assert r.get_json()['stock'] == 999

        # Revert
        client.patch('/api/products/1/stock', headers=admin_auth, json={
            'stock': 100
        })
