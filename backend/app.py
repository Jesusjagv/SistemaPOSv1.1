from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from datetime import timedelta
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db
from routes.auth import auth_bp
from routes.products import products_bp
from routes.customers import customers_bp
from routes.sales import sales_bp
from routes.reports import reports_bp
from routes.exchange import exchange_bp

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')

app.config['SECRET_KEY'] = 'pos-venezuela-s3cr3t-2025'
app.config['JWT_SECRET_KEY'] = 'pos-jwt-v3n3zu3la-2025'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=12)

CORS(app, resources={r"/api/*": {"origins": "*"}})
jwt = JWTManager(app)

# JWT error handlers
@jwt.unauthorized_loader
def unauthorized_callback(reason):
    return jsonify({"error": "Token requerido", "msg": reason}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({"error": "Sesión expirada, inicia sesión nuevamente"}), 401

@jwt.invalid_token_loader
def invalid_token_callback(reason):
    return jsonify({"error": "Token inválido"}), 401

# Register blueprints
app.register_blueprint(auth_bp,     url_prefix='/api/auth')
app.register_blueprint(products_bp, url_prefix='/api/products')
app.register_blueprint(customers_bp,url_prefix='/api/customers')
app.register_blueprint(sales_bp,    url_prefix='/api/sales')
app.register_blueprint(reports_bp,  url_prefix='/api/reports')
app.register_blueprint(exchange_bp, url_prefix='/api/exchange')

# Serve frontend pages
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    full_path = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, 'index.html')

if __name__ == '__main__':
    print("=" * 55)
    print("   🏪  SISTEMA POS v1.0 — Venezuela")
    print("=" * 55)
    init_db()
    print(f"\n🌐 Abre tu navegador en: http://localhost:5000\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
