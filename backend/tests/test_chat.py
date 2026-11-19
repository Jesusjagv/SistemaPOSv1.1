"""Tests for /api/reports/chat (AI Business Assistant)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from routes.reports import _detect_intent


class TestIntentDetection:
    """Unit tests for _detect_intent function."""

    def test_help_intent(self):
        assert 'help' in _detect_intent('ayuda')
        assert 'help' in _detect_intent('que puedes hacer')
        assert 'help' in _detect_intent('opciones')

    def test_sales_intent(self):
        assert 'sales' in _detect_intent('como van las ventas')
        assert 'sales' in _detect_intent('que vendi hoy')
        assert 'sales' in _detect_intent('resumen de ventas')
        assert 'sales' in _detect_intent('ingresos')
        assert 'sales' in _detect_intent('cobré')

    def test_products_intent(self):
        assert 'products' in _detect_intent('qué productos tengo')
        assert 'products' in _detect_intent('producto más vendido')
        assert 'products' in _detect_intent('catálogo')

    def test_stock_intent(self):
        assert 'stock' in _detect_intent('qué me falta de stock')
        assert 'stock' in _detect_intent('sin stock')
        assert 'stock' in _detect_intent('reponer')
        assert 'stock' in _detect_intent('agotado')

    def test_customers_intent(self):
        assert 'customers' in _detect_intent('mejores clientes')
        assert 'customers' in _detect_intent('quién compra')
        assert 'customers' in _detect_intent('fiados')
        assert 'customers' in _detect_intent('deudas')

    def test_pricing_intent(self):
        assert 'pricing' in _detect_intent('que producto me da mas ganancia')
        assert 'pricing' in _detect_intent('margenes')
        assert 'pricing' in _detect_intent('subir precio')
        assert 'pricing' in _detect_intent('rentabilidad')

    def test_payments_intent(self):
        assert 'payments' in _detect_intent('como me pagan')
        assert 'payments' in _detect_intent('metodos de pago')
        assert 'payments' in _detect_intent('pago movil')
        assert 'payments' in _detect_intent('efectivo')

    def test_hours_intent(self):
        assert 'hours' in _detect_intent('a qué hora vendo más')
        assert 'hours' in _detect_intent('mejor horario')
        assert 'hours' in _detect_intent('mejor hora')

    def test_combos_intent(self):
        assert 'combos' in _detect_intent('debería hacer combos')
        assert 'combos' in _detect_intent('promociones')
        assert 'combos' in _detect_intent('ofertas')

    def test_discounts_intent(self):
        assert 'discounts' in _detect_intent('debería hacer descuentos')
        assert 'discounts' in _detect_intent('rebajas')

    def test_general_fallback(self):
        result = _detect_intent('xyz random')
        assert 'general' in result

    def test_multiple_intents(self):
        result = _detect_intent('ventas y stock bajo')
        assert 'sales' in result
        assert 'stock' in result


class TestChatEndpoint:
    """Integration tests for POST /api/reports/chat."""

    def test_empty_message(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': ''},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 400

    def test_no_auth(self, client):
        r = client.post('/api/reports/chat', json={'message': 'ventas'})
        assert r.status_code in (401, 422)

    def test_help_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'ayuda'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert 'reply' in data
        reply_text = data['reply'][0]['text']
        assert 'Ventas' in reply_text
        assert 'Productos' in reply_text
        assert 'Stock' in reply_text

    def test_sales_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'resumen de ventas'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'Resumen' in text or 'ventas' in text.lower()

    def test_products_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'que productos tengo'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'productos' in text.lower()

    def test_stock_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'que me falta de stock'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'stock' in text.lower() or 'inventario' in text.lower()

    def test_customers_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'mejores clientes'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'clientes' in text.lower()

    def test_pricing_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'margenes de ganancia'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        all_text = ' '.join(m['text'].lower() for m in data['reply'])
        assert 'margen' in all_text or 'precio' in all_text

    def test_payments_response(self, client, admin_token):
        r = client.post('/api/reports/chat',                         json={'message': 'como me pagan'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'pago' in text.lower()

    def test_hours_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'a que hora vendo mas'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        all_text = ' '.join(m['text'].lower() for m in data['reply'])
        assert 'hora' in all_text

    def test_combos_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'deberia hacer combos'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'combo' in text.lower()

    def test_discounts_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'deberia hacer descuentos'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0
        text = data['reply'][0]['text']
        assert 'descuento' in text.lower() or 'rebaja' in text.lower()

    def test_general_fallback_response(self, client, admin_token):
        r = client.post('/api/reports/chat', json={'message': 'hola'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['reply']) > 0

    def test_reply_format(self, client, admin_token):
        """Each reply message must have role='assistant' and a text field."""
        r = client.post('/api/reports/chat', json={'message': 'ventas'},
                        headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        for msg in r.get_json()['reply']:
            assert msg['role'] == 'assistant'
            assert isinstance(msg['text'], str)
            assert len(msg['text']) > 0

    def test_superadmin_can_use(self, client, superadmin_token):
        r = client.post('/api/reports/chat', json={'message': 'ayuda'},
                        headers={'Authorization': f'Bearer {superadmin_token}'})
        assert r.status_code == 200

    def test_cashier_can_use(self, client, cashier_token):
        r = client.post('/api/reports/chat', json={'message': 'ventas'},
                        headers={'Authorization': f'Bearer {cashier_token}'})
        assert r.status_code == 200


class TestChatHistory:
    """Tests for chat history save/load/clear."""

    def test_history_load_empty(self, client, admin_token):
        r = client.get('/api/reports/chat/history',
                       headers={'Authorization': f'Bearer {admin_token}'})
        assert r.status_code == 200
        data = r.get_json()
        assert 'history' in data
        assert isinstance(data['history'], list)

    def test_history_saved_after_quick_action(self, client, admin_token):
        headers = {'Authorization': f'Bearer {admin_token}'}
        r = client.post('/api/reports/chat', json={'message': 'ayuda'}, headers=headers)
        assert r.status_code == 200

        r = client.get('/api/reports/chat/history', headers=headers)
        data = r.get_json()
        history = data['history']
        assert len(history) >= 2
        assert history[-2]['role'] == 'user'
        assert history[-2]['text'] == 'ayuda'
        assert history[-1]['role'] == 'assistant'

    def test_clear_history(self, client, admin_token):
        headers = {'Authorization': f'Bearer {admin_token}'}
        client.post('/api/reports/chat', json={'message': 'ventas'}, headers=headers)

        r = client.delete('/api/reports/chat/history', headers=headers)
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        r = client.get('/api/reports/chat/history', headers=headers)
        assert len(r.get_json()['history']) == 0

    def test_no_auth_history(self, client):
        r = client.get('/api/reports/chat/history')
        assert r.status_code in (401, 422)

    def test_no_auth_clear(self, client):
        r = client.delete('/api/reports/chat/history')
        assert r.status_code in (401, 422)


class TestGeminiIntegration:
    """Tests for AI integration (OpenRouter)."""

    def test_quick_actions_bypass_ai(self, client, admin_token):
        """Quick actions should NOT trigger AI (direct response)."""
        headers = {'Authorization': f'Bearer {admin_token}'}
        r = client.post('/api/reports/chat', json={'message': 'resumen de ventas'}, headers=headers)
        assert r.status_code == 200
        text = r.get_json()['reply'][0]['text'].lower()
        assert 'resumen' in text or 'ventas' in text

    def test_open_question_without_api_key(self, client, admin_token):
        """Open question without OPENROUTER_API_KEY should show helpful error."""
        import os
        old_key = os.environ.pop('OPENROUTER_API_KEY', None)
        os.environ['OPENROUTER_API_KEY'] = ''
        try:
            headers = {'Authorization': f'Bearer {admin_token}'}
            r = client.post('/api/reports/chat', json={'message': 'que opinas de mi negocio'}, headers=headers)
            assert r.status_code == 200
            text = r.get_json()['reply'][0]['text']
            assert 'OPENROUTER_API_KEY' in text or 'configura' in text.lower() or 'error' in text.lower()
        finally:
            if old_key is not None:
                os.environ['OPENROUTER_API_KEY'] = old_key

    def test_ai_service_no_key(self):
        """AI service raises ValueError when no API key."""
        import os
        old_key = os.environ.pop('OPENROUTER_API_KEY', None)
        os.environ['OPENROUTER_API_KEY'] = ''
        try:
            from services.gemini_service import send_to_ai
            try:
                send_to_ai("system", [], "hello")
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert 'OPENROUTER_API_KEY' in str(e)
        finally:
            if old_key is not None:
                os.environ['OPENROUTER_API_KEY'] = old_key

    def test_ai_service_builds_prompt(self):
        """build_system_prompt produces a valid prompt with context."""
        from services.gemini_service import build_system_prompt
        ctx = {
            'sales_today': {'count': 5, 'total': 120.50, 'avg': 24.10},
            'sales_7d': {'count': 35, 'total': 890.00},
            'sales_30d': {'count': 150, 'total': 3500.00},
            'top_products': [{'name': 'Cafe', 'qty': 50, 'revenue': 150.00}],
            'low_stock': [{'name': 'Azucar', 'stock': 2, 'min': 10}],
            'pending_debts': {'count': 3, 'total': 45.00}
        }
        prompt = build_system_prompt(ctx)
        assert 'POS' in prompt
        assert '120.50' in prompt
        assert 'Cafe' in prompt
        assert 'Azucar' in prompt
        assert '45.00' in prompt
