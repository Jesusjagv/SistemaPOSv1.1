"""
desktop.py — Lanzador de escritorio nativo para Sistema POS v1.0
Usa pywebview para crear una ventana nativa que carga el frontend local
y apunta a un servidor backend en la nube.
"""
import os
import sys
import threading
import webview
import traceback
import socket
import atexit
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# ── CONFIGURACIÓN ────────────────────────────────────────
API_BASE_URL = 'http://localhost:5000'
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, 'desktop_error.log')
LOCK_PORT = 49152  # Puerto fijo para evitar múltiples instancias

def log(msg):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f'{msg}\n')

def get_frontend_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'frontend')
    return os.path.join(APP_DIR, 'frontend')

FRONTEND_PATH = get_frontend_path()

# ── Bloqueo de instancia única ───────────────────────────
lock = None

def try_lock():
    global lock
    try:
        lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Sin SO_REUSEADDR: en Windows permite bindear un puerto ya en LISTEN
        # y el lock de instancia única dejaba de funcionar (dos ventanas).
        lock.bind(('127.0.0.1', LOCK_PORT))
        lock.listen(1)
        return True
    except OSError:
        return False

def unlock():
    global lock
    if lock:
        try:
            lock.close()
        except:
            pass

# ── Servidor HTTP local con CORS ─────────────────────────
class PosHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_PATH, **kwargs)

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()

    def do_GET(self):
        # Comparar sin query string: la página pide 'js/config.js?v=1.0'
        path = self.path.split('?', 1)[0]
        if path == '/js/config.js':
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript')
            self.end_headers()
            self.wfile.write(f'window.API_BASE_URL = "{API_BASE_URL}";'.encode())
            return
        return super().do_GET()

def start_server():
    server = ThreadingHTTPServer(('localhost', 0), PosHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port

# ── Ventana de escritorio ────────────────────────────────
def main():
    atexit.register(unlock)

    if not try_lock():
        log('Ya hay una instancia abierta.')
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, 'La aplicación ya está en ejecución.\nRevisa la bandeja del sistema.',
            'Sistema POS', 0x40 | 0x1000
        )
        sys.exit(0)

    try:
        log('=== Iniciando Sistema POS Desktop ===')
        log(f'API: {API_BASE_URL}')

        server, port = start_server()
        url = f'http://localhost:{port}/index.html'
        log(f'URL: {url}')

        window = webview.create_window(
            title='Sistema POS v1.0 — Escritorio',
            url=url,
            width=1280,
            height=800,
            min_size=(1024, 768),
            resizable=True,
            text_select=True,
        )

        webview.start(
            private_mode=False,
            storage_path=os.path.join(APP_DIR, '.webview2_data'),
            debug=False,
        )

        log('=== Cerrado normal ===')
    except Exception as e:
        log(f'ERROR: {e}')
        log(traceback.format_exc())
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f'Error al iniciar Sistema POS:\n\n{e}\n\nRevisa desktop_error.log',
            'Error - Sistema POS',
            0x10 | 0x1000
        )

if __name__ == '__main__':
    main()
