from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from datetime import timedelta
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, close_db, PoolTimeoutError
from routes.auth import auth_bp
from routes.products import products_bp
from routes.customers import customers_bp
from routes.sales import sales_bp
from routes.reports import reports_bp
from routes.exchange import exchange_bp
from routes.superadmin import superadmin_bp
from routes.debts import debts_bp
from routes.cash_sessions import cash_sessions_bp
from routes.settings import settings_bp

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')

app.config['SECRET_KEY'] = 'pos-venezuela-s3cr3t-2025'
app.config['JWT_SECRET_KEY'] = 'pos-jwt-v3n3zu3la-2025'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=12)

CORS(app, resources={r"/api/*": {"origins": "*"}})
jwt = JWTManager(app)

# Registrar el cierre automático de la BD al finalizar la petición
app.teardown_appcontext(close_db)

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

@app.errorhandler(500)
def internal_error(error):
    app.logger.exception('Error interno no controlado')
    return jsonify({"error": "Error interno del servidor"}), 500

@app.errorhandler(PoolTimeoutError)
def pool_timeout(error):
    return jsonify({"error": "Servidor ocupado, intenta de nuevo en unos segundos"}), 503

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Recurso no encontrado"}), 404

# Register blueprints
app.register_blueprint(auth_bp,          url_prefix='/api/auth')
app.register_blueprint(products_bp,      url_prefix='/api/products')
app.register_blueprint(customers_bp,     url_prefix='/api/customers')
app.register_blueprint(sales_bp,         url_prefix='/api/sales')
app.register_blueprint(reports_bp,       url_prefix='/api/reports')
app.register_blueprint(exchange_bp,      url_prefix='/api/exchange')
app.register_blueprint(superadmin_bp,    url_prefix='/api/superadmin')
app.register_blueprint(debts_bp,         url_prefix='/api/debts')
app.register_blueprint(cash_sessions_bp, url_prefix='/api/cash-sessions')
app.register_blueprint(settings_bp,      url_prefix='/api/settings')

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

STATIC_TYPES = ('.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff2', '.woff', '.ttf')

@app.after_request
def add_header(response):
    path = request.path
    # API responses: never cache
    if path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        return response

    # Static assets: always revalidate
    if path.endswith(STATIC_TYPES):
        response.headers['Cache-Control'] = 'no-store'
        return response

    # HTML pages: el browser debe revalidar
    response.headers['Cache-Control'] = 'no-cache'
    return response

if __name__ == '__main__':
    print("=" * 55)
    print("   SISTEMA POS v1.0 - Venezuela")
    print("=" * 55)
    init_db()
    port = int(os.getenv('PORT', '5000'))
    print(f"\n   Abre tu navegador en: http://localhost:{port}\n")
    try:
        from waitress import serve
        threads = int(os.getenv('WAITRESS_THREADS', '16'))
        print(f"   Servidor: Waitress (threads={threads}, port={port})")
        # Confía en X-Forwarded-For solo si el peer es el proxy local
        # (por defecto 127.0.0.1; usar TRUSTED_PROXY='*' u otra IP si el
        # proxy está en otro host). Sin esto waitress borra el header y
        # el rate limit no vería la IP real del cliente.
        serve_kwargs = dict(host='0.0.0.0', port=port, threads=threads)
        trusted_proxy = os.getenv('TRUSTED_PROXY', '127.0.0.1').strip() or None
        if trusted_proxy:
            serve_kwargs['trusted_proxy'] = trusted_proxy
            serve_kwargs['trusted_proxy_headers'] = {'x-forwarded-for'}
        serve(app, **serve_kwargs)
    except ImportError:
        print("   Modo desarrollo: Flask dev server (NO apto para produccion)")
        print("   Instala waitress: pip install waitress")
        app.run(debug=False, host='0.0.0.0', port=port, threaded=True)
