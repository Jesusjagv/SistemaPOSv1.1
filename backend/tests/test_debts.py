"""Tests for /api/debts endpoints."""

import time


def create_customer(client, auth):
    """Helper: create a test customer and return its ID."""
    suffix = str(int(time.time() * 1000000))[-6:]
    r = client.post('/api/customers', headers=auth, json={
        'name': f'Test Customer {suffix}',
        'doc_type': 'V',
        'doc_number': suffix,
        'phone': '0412-0000000'
    })
    return r.get_json()['id']


class TestDebts:
    def test_list_empty(self, client, admin_auth):
        r = client.get('/api/debts', headers=admin_auth)
        assert r.status_code == 200
        assert r.get_json() == []

    def test_overdue_count_zero(self, client, admin_auth):
        r = client.get('/api/debts/overdue/count', headers=admin_auth)
        assert r.status_code == 200
        assert r.get_json()['count'] == 0

    def test_create_debt_success(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 50.00,
            'due_date': '2026-07-01',
            'concept': 'Test debt'
        })
        assert r.status_code == 201
        data = r.get_json()
        assert 'id' in data

    def test_list_after_create(self, client, admin_auth):
        # This test assumes test_create_debt_success ran first (pytest runs sequentially per class)
        r = client.get('/api/debts', headers=admin_auth)
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) >= 1
        assert data[0]['balance_usd'] == 50.00

    def test_create_debt_no_customer(self, client, admin_auth):
        r = client.post('/api/debts', headers=admin_auth, json={
            'amount_usd': 10,
            'due_date': '2026-07-01'
        })
        assert r.status_code == 400

    def test_create_debt_zero_amount(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 0,
            'due_date': '2026-07-01'
        })
        assert r.status_code == 400

    def test_get_debt_detail(self, client, admin_auth):
        # Create customer + debt fresh
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 50.00,
            'due_date': '2026-07-01',
            'concept': 'Detail test'
        })
        did = r.get_json()['id']

        r = client.get(f'/api/debts/{did}', headers=admin_auth)
        assert r.status_code == 200
        data = r.get_json()
        assert data['balance_usd'] == 50.00
        assert 'payments' in data

    def test_pay_debt_full_usd(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 50.00,
            'due_date': '2026-07-01'
        })
        did = r.get_json()['id']

        r = client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_usd': 50.00}]
        })
        assert r.status_code == 200
        data = r.get_json()
        assert data['new_balance'] == 0
        assert data['status'] == 'Pagada'

    def test_debt_marked_paid(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 30.00,
            'due_date': '2026-07-01'
        })
        did = r.get_json()['id']
        # Pay full
        client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_usd': 30.00}]
        })
        r = client.get(f'/api/debts/{did}', headers=admin_auth)
        assert r.get_json()['status'] == 'Pagada'

    def test_pay_already_paid(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 10.00,
            'due_date': '2026-07-01'
        })
        did = r.get_json()['id']
        # Pay full
        client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_usd': 10.00}]
        })
        # Try paying again
        r = client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_usd': 5}]
        })
        assert r.status_code == 400

    def test_pay_exceeds_balance(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 10.00,
            'due_date': '2026-07-01'
        })
        did = r.get_json()['id']
        r = client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_usd': 15.00}]
        })
        assert r.status_code == 400
        assert 'excede' in r.get_json()['error'].lower()

    def test_pay_requires_reference_for_pm(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 30.00,
            'due_date': '2026-08-01'
        })
        did = r.get_json()['id']

        r = client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{
                'payment_method': 'pago_movil',
                'amount_usd': 30.00,
                'reference_number': '',
                'bank_origin': '',
                'bank_destination': ''
            }]
        })
        assert r.status_code == 400

    def test_pay_with_reference_success(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 30.00,
            'due_date': '2026-08-01'
        })
        did = r.get_json()['id']

        r = client.post(f'/api/debts/{did}/pay', headers=admin_auth, json={
            'payments': [{
                'payment_method': 'transferencia',
                'amount_usd': 30.00,
                'reference_number': 'REF-12345',
                'bank_origin': 'Banco Mercantil',
                'bank_destination': 'Banco de Venezuela'
            }]
        })
        assert r.status_code == 200
        assert r.get_json()['new_balance'] == 0

    def test_get_reminder(self, client, admin_auth):
        cid = create_customer(client, admin_auth)
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': cid,
            'amount_usd': 15.00,
            'due_date': '2026-09-01',
            'concept': 'Reminder test'
        })
        did = r.get_json()['id']

        r = client.get(f'/api/debts/{did}/reminder', headers=admin_auth)
        assert r.status_code == 200
        data = r.get_json()
        assert 'message' in data
        assert '15.00' in data['message']
