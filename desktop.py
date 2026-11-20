"""
desktop.py — Lanzador de escritorio para Sistema POS (offline-first).

1. Carga `.env` de la carpeta de la app y fija defaults: sqlite local en
   `data/pos.db` y puerto 5000 (si DATABASE_URL/PORT no están definidos).
2. Arranca el backend Flask (waitress) en un hilo daemon, solo en
   127.0.0.1:PORT — el backend también sirve el frontend (same-origin,
   config.js queda vacío).
3. Abre la ventana pywebview apuntando al backend.

En modo empaquetado (PyInstaller onedir) todo viene incluido: el cliente
solo necesita abrir SistemaPOS.exe; sus datos viven en `data/` junto al
ejecutable (sincronización opcional vía `.env`: CLOUD_URL, SYNC_USER,
SYNC_PASSWORD, MACHINE_ID).
"""
import os
import sys
import threading
import socket
import atexit
import time
import traceback

# ── RUTAS ─────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)  # dist/SistemaPOS/
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, 'desktop_error.log')
LOCK_PORT = 49152  # Puerto fijo para evitar múltiples instancias

# ── ENTORNO (antes de importar el backend) ────────────────
from dotenv import load_dotenv

load_dotenv(os.path.join(APP_DIR, '.env'))  # no pisa variables ya definidas
PORT = int(os.getenv('PORT', '5000'))
if not os.getenv('DATABASE_URL'):
    # Offline-first: base sqlite local junto al programa
    _db = os.path.join(APP_DIR, 'data', 'pos.db').replace('\\', '/')
    os.environ['DATABASE_URL'] = f'sqlite:///{_db}'

# Importar el backend REAL (backend/app.py, no la copia vieja de la raíz).
# El orden importa: primero APP_DIR (paquete `backend`), luego backend/
# (módulos top-level: database, routes, ...). En el exe congelado estos
# módulos viven en el PYZ; las rutas de arriba solo aplican en desarrollo.
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(APP_DIR, 'backend'))
from backend.app import app as flask_app  # noqa: E402
from database import init_db  # noqa: E402
from sync_engine import start_sync_engine  # noqa: E402


def log(msg):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f'{msg}\n')


# ── Bloqueo de instancia única ────────────────────────────
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
        except Exception:
            pass


# ── Backend en hilo daemon ────────────────────────────────
_backend_errors = []


def _run_backend():
    try:
        init_db()            # esquema sqlite (o MySQL si DATABASE_URL lo es)
        start_sync_engine()  # no-op sin CLOUD_URL
        from waitress import serve
        threads = int(os.getenv('WAITRESS_THREADS', '16'))
        log(f'Backend: waitress 127.0.0.1:{PORT} threads={threads} '
            f'db={os.getenv("DATABASE_URL")}')
        # 127.0.0.1 a propósito: el escritorio no expone el puerto en la LAN.
        serve(flask_app, host='127.0.0.1', port=PORT, threads=threads,
              trusted_proxy='127.0.0.1',
              trusted_proxy_headers={'x-forwarded-for'})
    except Exception as e:
        _backend_errors.append(e)
        log(f'ERROR backend: {e}')
        log(traceback.format_exc())


def _wait_backend(timeout=20.0):
    """Espera a que el backend acepte conexiones. Devuelve None o el error."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _backend_errors:
            return _backend_errors[0]
        try:
            s = socket.create_connection(('127.0.0.1', PORT), 0.5)
            s.close()
            return None
        except OSError:
            time.sleep(0.2)
    return TimeoutError(f'El backend no respondió en {int(timeout)}s (puerto {PORT})')


def _is_pos_backend():
    """¿Ya hay un backend Sistema POS respondiendo en PORT? (flujo dev)"""
    try:
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/', timeout=2) as r:
            body = r.read(4096).decode('utf-8', 'replace')
        return 'KalyPOS' in body
    except Exception:
        return False


# ── Ventana de escritorio ─────────────────────────────────
def _dialog(title, msg, flags=0x10):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40 | 0x1000 | flags)
    except Exception:
        pass


def main():
    atexit.register(unlock)

    if not try_lock():
        log('Ya hay una instancia abierta.')
        _dialog('Sistema POS',
                'La aplicación ya está en ejecución.\n'
                'Cierra la ventana abierta e inténtalo de nuevo.',
                0x40)
        sys.exit(0)

    try:
        log('=== Iniciando Sistema POS Desktop ===')
        log(f'APP_DIR: {APP_DIR}')

        if _is_pos_backend():
            # Ya hay un backend POS en el puerto (dev con start.bat, u otra
            # instancia): no arrancar otro, solo abrir la ventana.
            log(f'Backend ya corriendo en {PORT}: se reutiliza.')
        else:
            threading.Thread(target=_run_backend, daemon=True,
                             name='pos-backend').start()
            err = _wait_backend()
            if err:
                raise RuntimeError(
                    f'No se pudo iniciar el servidor local (puerto {PORT}).\n{err}')

        url = f'http://127.0.0.1:{PORT}/'
        log(f'URL: {url}')

        import webview
        window = webview.create_window(
            title='Sistema POS v1.1 — Escritorio',
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
        _dialog('Error - Sistema POS',
                f'Error al iniciar Sistema POS:\n\n{e}\n\n'
                'Revisa desktop_error.log')


if __name__ == '__main__':
    main()
