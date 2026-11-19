"""Tests for exchange rate isolation between companies."""

import time
import pymysql
from werkzeug.security import generate_password_hash


def _create_company_and_admin(client, superadmin_auth, suffix):
    """Helper: create a second company with admin, return (company_id, token)."""
    r = client.post('/api/superadmin/', headers=superadmin_auth, json={
        'company_name': f'Company {suffix}',
        'admin_name': f'Admin {suffix}',
        'admin_username': f'admin_{suffix}',
        'admin_password': 'test123'
    })
    assert r.status_code == 201
    company_id = r.get_json()['company_id']

    r = client.post('/api/auth/login', json={
        'username': f'admin_{suffix}',
        'password': 'test123'
    })
    assert r.status_code == 200
    token = r.get_json()['token']
    return company_id, token


class TestRateIsolation:
    """Verify that manual rate changes in one company don't affect others."""

    def test_manual_rate_does_not_affect_other_company(
        self, client, superadmin_auth, admin_auth
    ):
        """Company A sets manual rate → Company B still uses auto."""
        # 1. Create a second company
        suffix = str(int(time.time() * 1000000))[-6:]
        company_b_id, company_b_token = _create_company_and_admin(
            client, superadmin_auth, suffix
        )
        company_b_auth = {'Authorization': f'Bearer {company_b_token}'}

        # 2. Get initial rates for both companies (both should be auto)
        r_a = client.get('/api/exchange/rate', headers=admin_auth)
        r_b = client.get('/api/exchange/rate', headers=company_b_auth)
        assert r_a.status_code == 200
        assert r_b.status_code == 200
        rate_a_initial = r_a.get_json()['rate']
        rate_b_initial = r_b.get_json()['rate']

        # Both should be using auto mode initially
        r_settings_a = client.get('/api/settings/exchange-rate', headers=admin_auth)
        r_settings_b = client.get('/api/settings/exchange-rate', headers=company_b_auth)
        assert r_settings_a.get_json()['mode'] == 'auto'
        assert r_settings_b.get_json()['mode'] == 'auto'

        # 3. Company A sets manual rate to 100.00
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'manual',
            'manual_rate': 100.00
        })
        assert r.status_code == 200
        assert r.get_json()['mode'] == 'manual'

        # 4. Company A should now return 100.00
        r_a = client.get('/api/exchange/rate', headers=admin_auth)
        assert r_a.status_code == 200
        assert r_a.get_json()['rate'] == 100.00
        assert r_a.get_json()['source'] == 'Manual'

        # 5. Company B should STILL use auto (not 100.00)
        r_b = client.get('/api/exchange/rate', headers=company_b_auth)
        assert r_b.status_code == 200
        assert r_b.get_json()['rate'] != 100.00
        assert r_b.get_json()['source'] != 'Manual'

        # 6. Verify settings: Company A = manual, Company B = auto
        r_settings_a = client.get('/api/settings/exchange-rate', headers=admin_auth)
        r_settings_b = client.get('/api/settings/exchange-rate', headers=company_b_auth)
        assert r_settings_a.get_json()['mode'] == 'manual'
        assert r_settings_a.get_json()['manual_rate'] == 100.00
        assert r_settings_b.get_json()['mode'] == 'auto'
        assert r_settings_b.get_json()['manual_rate'] is None

        # 7. Cleanup: reset Company A back to auto
        client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'auto'
        })

    def test_switch_back_to_auto_restores_api_rate(
        self, client, superadmin_auth, admin_auth
    ):
        """After setting manual, switching back to auto should restore the API rate."""
        # 1. Set manual rate
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'manual',
            'manual_rate': 99.99
        })
        assert r.status_code == 200

        # 2. Verify manual rate is used
        r = client.get('/api/exchange/rate', headers=admin_auth)
        assert r.get_json()['rate'] == 99.99
        assert r.get_json()['source'] == 'Manual'

        # 3. Switch back to auto
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'auto'
        })
        assert r.status_code == 200
        assert r.get_json()['mode'] == 'auto'

        # 4. Verify rate is no longer manual
        r = client.get('/api/exchange/rate', headers=admin_auth)
        assert r.get_json()['source'] != 'Manual'

    def test_manual_rate_validation(self, client, admin_auth):
        """Invalid manual rate values should be rejected."""
        # Zero rate
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'manual',
            'manual_rate': 0
        })
        assert r.status_code == 400

        # Negative rate
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'manual',
            'manual_rate': -5
        })
        assert r.status_code == 400

        # Missing rate when mode is manual
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'manual'
        })
        assert r.status_code == 400

    def test_invalid_mode_rejected(self, client, admin_auth):
        """Invalid mode values should be rejected."""
        r = client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'turbo'
        })
        assert r.status_code == 400

    def test_cashier_cannot_change_rate(self, client, cashier_auth):
        """Cashier role should not be allowed to change rate settings."""
        r = client.put('/api/settings/exchange-rate', headers=cashier_auth, json={
            'mode': 'manual',
            'manual_rate': 50.00
        })
        assert r.status_code == 403

    def test_unauthenticated_cannot_access_settings(self, client):
        """Unauthenticated requests should be rejected."""
        r = client.get('/api/settings/exchange-rate')
        assert r.status_code == 401

        r = client.put('/api/settings/exchange-rate', json={
            'mode': 'manual',
            'manual_rate': 50.00
        })
        assert r.status_code == 401

    def test_debt_payment_uses_company_rate(
        self, client, superadmin_auth, admin_auth
    ):
        """Debt payment should use the company's configured rate, not a global one."""
        from werkzeug.security import generate_password_hash

        # Create second company
        suffix = str(int(time.time() * 1000000))[-6:]
        company_b_id, company_b_token = _create_company_and_admin(
            client, superadmin_auth, suffix
        )
        company_b_auth = {'Authorization': f'Bearer {company_b_token}'}

        # Company A: set manual rate to 200.00
        client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'manual',
            'manual_rate': 200.00
        })

        # Create customer + debt for Company A
        r = client.post('/api/customers', headers=admin_auth, json={
            'name': 'Test Customer A',
            'doc_type': 'V',
            'doc_number': '12345',
            'phone': '0412-0000000'
        })
        customer_a_id = r.get_json()['id']
        r = client.post('/api/debts', headers=admin_auth, json={
            'customer_id': customer_a_id,
            'amount_usd': 100.00,
            'due_date': '2026-12-31',
            'concept': 'Test debt A'
        })
        debt_a_id = r.get_json()['id']

        # Pay in BS (should use manual rate 200.00)
        r = client.post(f'/api/debts/{debt_a_id}/pay', headers=admin_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_bs': 2000.00}]
        })
        assert r.status_code == 200
        # 2000 BS / 200 rate = 10 USD
        assert r.get_json()['new_balance'] == 90.00

        # Create customer + debt for Company B
        r = client.post('/api/customers', headers=company_b_auth, json={
            'name': 'Test Customer B',
            'doc_type': 'V',
            'doc_number': '67890',
            'phone': '0412-1111111'
        })
        customer_b_id = r.get_json()['id']
        r = client.post('/api/debts', headers=company_b_auth, json={
            'customer_id': customer_b_id,
            'amount_usd': 100.00,
            'due_date': '2026-12-31',
            'concept': 'Test debt B'
        })
        debt_b_id = r.get_json()['id']

        # Company B pays 2000 BS (should NOT use rate 200.00, but auto rate)
        r = client.post(f'/api/debts/{debt_b_id}/pay', headers=company_b_auth, json={
            'payments': [{'payment_method': 'cash', 'amount_bs': 2000.00}]
        })
        assert r.status_code == 200
        new_balance_b = r.get_json()['new_balance']
        # With auto rate (not 200), the balance should be different from 90.00
        assert new_balance_b != 90.00, (
            f"Company B should NOT use Company A's manual rate. "
            f"Balance is {new_balance_b}, expected != 90.00"
        )

        # Cleanup: reset Company A back to auto
        client.put('/api/settings/exchange-rate', headers=admin_auth, json={
            'mode': 'auto'
        })
