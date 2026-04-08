from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
import sys, os
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import get_db

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/summary', methods=['GET'])
@jwt_required()
def summary():
    date_from = request.args.get('from', date.today().isoformat())
    date_to   = request.args.get('to',   date.today().isoformat())
    conn  = get_db()

    totals = conn.execute('''
        SELECT
            COUNT(*) as total_sales,
            SUM(subtotal_usd) as subtotal_usd,
            SUM(tax_usd) as tax_usd,
            SUM(discount_usd) as discount_usd,
            SUM(total_usd) as total_usd,
            SUM(total_bs) as total_bs,
            AVG(total_usd) as avg_ticket_usd
        FROM sales
        WHERE DATE(created_at) BETWEEN %s AND %s AND status = 'completed'
    ''', (date_from, date_to)).fetchone()

    by_method = conn.execute('''
        SELECT payment_method, COUNT(*) as count, SUM(total_usd) as sum_usd
        FROM sales
        WHERE DATE(created_at) BETWEEN %s AND %s AND status = 'completed'
        GROUP BY payment_method
    ''', (date_from, date_to)).fetchall()

    # Low stock
    low_stock = conn.execute('''
        SELECT name, code, stock, min_stock
        FROM products WHERE stock <= min_stock AND active=1 ORDER BY stock ASC LIMIT 10
    ''').fetchall()

    conn.close()
    return jsonify({
        "period": {"from": date_from, "to": date_to},
        "totals": dict(totals) if totals else {},
        "by_payment_method": [dict(r) for r in by_method],
        "low_stock_products": [dict(r) for r in low_stock]
    })

@reports_bp.route('/top-products', methods=['GET'])
@jwt_required()
def top_products():
    date_from = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    date_to   = request.args.get('to',   date.today().isoformat())
    limit     = int(request.args.get('limit', 10))
    conn = get_db()
    rows = conn.execute('''
        SELECT si.product_name, si.product_id,
               SUM(si.quantity) as total_qty,
               SUM(si.total_usd) as total_usd,
               SUM(si.total_bs)  as total_bs,
               COUNT(DISTINCT si.sale_id) as sale_count
        FROM sale_items si
        JOIN sales s ON si.sale_id = s.id
        WHERE DATE(s.created_at) BETWEEN %s AND %s AND s.status = 'completed'
        GROUP BY si.product_id, si.product_name
        ORDER BY total_qty DESC
        LIMIT %s
    ''', (date_from, date_to, limit)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@reports_bp.route('/daily-chart', methods=['GET'])
@jwt_required()
def daily_chart():
    days = int(request.args.get('days', 7))
    date_from = (date.today() - timedelta(days=days - 1)).isoformat()
    date_to   = date.today().isoformat()
    conn = get_db()
    rows = conn.execute('''
        SELECT DATE(created_at) as day,
               COUNT(*) as sales_count,
               SUM(total_usd) as total_usd,
               SUM(total_bs) as total_bs
        FROM sales
        WHERE DATE(created_at) BETWEEN %s AND %s AND status = 'completed'
        GROUP BY day ORDER BY day
    ''', (date_from, date_to)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@reports_bp.route('/inventory', methods=['GET'])
@jwt_required()
def inventory():
    conn = get_db()
    rows = conn.execute('''
        SELECT p.id, p.name, p.code, p.stock, p.min_stock, p.price_usd, p.cost_usd,
               c.name as category, p.active,
               (p.stock * p.cost_usd) as inventory_value_usd
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        ORDER BY p.name
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@reports_bp.route('/hourly', methods=['GET'])
@jwt_required()
def hourly():
    query_date = request.args.get('date', date.today().isoformat())
    conn = get_db()
    rows = conn.execute('''
        SELECT LPAD(HOUR(created_at), 2, '0') as hour,
               COUNT(*) as count,
               SUM(total_usd) as total_usd
        FROM sales
        WHERE DATE(created_at) = %s AND status = 'completed'
        GROUP BY hour ORDER BY hour
    ''', (query_date,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
