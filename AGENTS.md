# AGENTS.md

## Qué es

POS multi-tenant para Venezuela (precios en USD + tasa BCV en Bs). Flask + MySQL + HTML/JS vanilla. Opción escritorio vía `pywebview`.

## Entrypoints reales

- **Backend real:** `backend/app.py` (incluye blueprints `cash_sessions` y `settings`).
- **NO usar la raíz `app.py`:** es una copia vieja sin esos blueprints; `start.bat` lanza `python backend\app.py`.
- Frontend estático en `frontend/` servido por el mismo Flask (static_folder). Sin build step ni framework JS.
- Escritorio: `desktop.py` arranca el backend Flask en proceso (waitress, solo `127.0.0.1:PORT`, default 5000) y abre `pywebview` contra él — frontend y API same-origin. Si ya hay un backend POS en el puerto (dev con `start.bat`), lo reutiliza. Defaults de env antes de importar el backend: `DATABASE_URL=sqlite:///.../data/pos.db` si falta; carga `.env` de la carpeta de la app. Lock de instancia única (puerto 49152).

## Comandos

```bat
:: Servidor dev (requiere .env con DATABASE_URL)
.venv\Scripts\python.exe backend\app.py
:: o start.bat

:: Escritorio
Sistema POS.bat

:: Tests (requiere MySQL local — ver abajo)
.venv\Scripts\python.exe -m pytest backend\tests\ -v
:: Un archivo / test:
.venv\Scripts\python.exe -m pytest backend\tests\test_auth.py::TestLogin::test_success_admin -v

:: Empaquetado Windows (exe portable + ZIP para clientes)
build_desktop.bat
::  -> dist\SistemaPOS\SistemaPOS.exe  y  SistemaPOS-v1.1.zip
```

No hay lint, typecheck ni CI configurados. Orden de verificación: solo pytest.

`setup.bat` está roto: instala `backend\requirements.txt` (borrado). Usar `pip install -r requirements.txt` en la raíz.

## Base de datos

- **MySQL en nube/dev** (PyMySQL): `DATABASE_URL` en `.env` (ver `.env.example`, formato TiDB `mysql://...?ssl-mode=REQUIRED`).
- **SQLite en escritorio offline**: si `DATABASE_URL` empieza con `sqlite://`, `database.py` crea un `SQLiteWrapper` con el mismo API que traduce el dialecto MySQL→SQLite (`%s`→`?`, `FOR UPDATE` eliminado, `INSERT IGNORE`→`INSERT OR IGNORE`, `REPLACE INTO`→UPDATE-then-INSERT, `DATE_SUB/ADD`→`date()`, shims SQL `NOW()/UUID()/LPAD/HOUR/...`). El rewrite de INSERT inyecta `uuid`; el de UPDATE añade `updated_at` (ms UTC) + `sync_dirty=1`; el de DELETE registra `sync_tombstones`. En `engine_mode=True` (hilo sync) NO hay rewrite: el engine gestiona uuid/updated_at/dirty.
- Al arrancar, `init_db()` crea esquema MySQL (o `init_local_db()` el sqlite, con seeds solo si no hay `CLOUD_URL`) + aplica migraciones incrementales de columnas. No hay Alembic; scripts one-shot en `backend/migrate_*.py` (dejar de usar `migrate_to_aiven.py` y credenciales hardcodeadas).
- Pool de conexiones propio en `database.py`. Rutas usan `get_db()` + `close_db` teardown. En sqlite hay lock global `_sqlite_lock` (RLock reentrante); wrappers son thread-local por `(url, engine_mode)`.

## Sincronización offline (escritorio ↔ nube)

- **API nube:** `routes/sync.py` (`POST /api/sync/push`, `GET /api/sync/pull`). LWW por `updated_at` (empate gana servidor), `stock_delta` en products, FKs por `<col>_uuid`, company sellada con el token, DELETEs → `sync_tombstones`.
- **Engine escritorio:** `backend/sync_engine.py`. `start_sync_engine()` (hook en `__main__` de `backend/app.py`) lanza un hilo daemon que hace push→pull cada `SYNC_INTERVAL` (default 8s). **Cero mensajes en pantalla**: log con throttling (solo imprime al cambiar el estado). Env: `CLOUD_URL`, `SYNC_USER`, `SYNC_PASSWORD`; sin ellas no hay sync. Tablas: `SYNCED_TABLES` en `database.py` (16); orden de push/pull: `APPLY_ORDER` en `sync_engine.py` (¡cash_sessions ANTES que sales por `session_id`!); `ENGINE_FKS` define qué uuid de padres adjuntar en push.
- **`MACHINE_ID`** (≤6 chars): sufijo de `VEN-YYYYMMDD-XXXX{mid}` y `CAJA-...` para que dos máquinas no choquen el UNIQUE de `counters` locales.
- `CLOUD_URL` presente en init → el sqlite local arranca VACÍO (sin seeds) y se puebla con el primer pull.

### Tests

`backend/tests/conftest.py` **dropea y recrea** la BD `SistemaPOSv1_1_test` por sesión, con credenciales hardcodeadas `root:15508984@localhost`. Sobreescribir con `TEST_DATABASE_URL` si cambian. Los tests NO usan la BD de producción.

Semilla de roles: `admin/admin123`, `cajero/cajero123`, superadmin `master/Master123!` (este último solo se inserta en el fixture de tests, no en `init_db`).

Resultado esperado de la suite completa: **149 passed / 2 failed** (los 2 de `test_chat.py::TestGeminiIntegration` son preexistentes: requieren `OPENROUTER_API_KEY`). `test_sqlite_dialect.py` (28, dialecto sqlite) y `test_offline.py` (11, E2E offline) NO requieren la app cloud excepto el fixture `app` compartido en `test_offline`.

## Multi-tenant (crítico)

Casi toda query va filtrada por empresa desde el JWT:

```python
from routes.products import company_clause  # o copiar el patrón local
where, params = company_clause(get_jwt(), prefix='p.')
# superadmin: company_id NULL en JWT → sin filtro (ve todo)
```

Tablas con `company_id`: users, products, customers, sales, categories, cash_sessions, settings, companies. Al añadir endpoints o tablas nuevas, replicar el filtro; superadmin no debe filtrar.

## Dinero y tasa BCV

- Precios/costos se guardan en **USD** (`price_usd`, `cost_usd`). La conversión a Bs usa la tasa del día.
- `routes/exchange.py:get_today_rate(company_id)` intenta APIs externas (ve.dolarapi.com, pydolarve.org, scraping BCV) y cachea en `companies` (modo auto o manual con `rate_mode`/`manual_rate`). Fallback 36.50.
- Cajas (`cash_sessions`) y deudas (`debts`) fijan la tasa al abrir/cerrar sesión.

## Frontend

- `frontend/js/config.js`: `window.API_BASE_URL` (vacío = same origin). El modo escritorio lo sirve desde `/js/config.js` con URL inyectada por `desktop.py`.
- Cliente HTTP compartido: `frontend/js/api.js` (`apiRequest`, tokens en `localStorage`).
- Páginas multi-HTML (una por módulo) + service worker PWA (`sw.js`). Sin React/Vue ni empaquetado.
- UI y comentarios en **español**; mantener ese idioma en strings visibles.

### Navegación por teclado (estilo GSIN)

- Módulo central: `frontend/js/keyboard.js` (cargado en **todas** las páginas después de `api.js`).
- Convención POS (F-keys): F1 ayuda, F2 cobrar, F3 buscar, F4 cliente, F5 refrescar, F6 descuento, F8 borrar línea, F9 método pago, F10 procesar, F11 expandir productos, F12 cancelar cobro. Escape cierra modales/menús. `/` enfoca búsqueda.
- Escáner de barras: **teclado-wedge** (escribe código + Enter en `#product-search`) y cámara (`onScanSuccess`). Ambos comparten la lógica de agregar al carrito.
- `Keyboard.register('pos', mapaFkeys, filasAyuda)` registra atajos por página. `openModal`/`closeModal` de `api.js` están enganchados para trampa de foco y foco inicial.
- Al añadir un módulo nuevo, registrar sus F-keys y añadir `keyboard.js` al HTML.

## Otros gotchas

- Secretos Flask/JWT hardcodeados en `backend/app.py`; no copiar a producción real sin cambiar.
- `.env` y `.webview2_data/` no deben commitearse (webview2 generó mucho ruido en el staging de git).
- `backend/pos.db` es residuo de una versión vieja; el modo offline usa la ruta que diga `DATABASE_URL` (`sqlite:///...`).
- `backend/__init__.py` existe para que PyInstaller empaquete `backend.app`. Con él, pytest importa los tests como `backend.tests.*` y `conftest.py` DEBE importar la app con `from backend.app import app` explícito (el orden de sys.path no distingue la copia vieja de la raíz).
- Python 3.13 en `.venv`. Dependencias pinneadas en `requirements.txt` (Flask 3, waitress, pywebview, pyinstaller, pytest).
