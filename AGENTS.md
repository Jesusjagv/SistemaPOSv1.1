# AGENTS.md

## Qué es

POS multi-tenant para Venezuela (precios en USD + tasa BCV en Bs). Flask + MySQL + HTML/JS vanilla. Opción escritorio vía `pywebview`.

## Entrypoints reales

- **Backend real:** `backend/app.py` (incluye blueprints `cash_sessions` y `settings`).
- **NO usar la raíz `app.py`:** es una copia vieja sin esos blueprints; `start.bat` lanza `python backend\app.py`.
- Frontend estático en `frontend/` servido por el mismo Flask (static_folder). Sin build step ni framework JS.
- Escritorio: `desktop.py` sirve el frontend en un HTTP local y abre `pywebview` apuntando a `http://localhost:5000`. Un solo lock de instancia (puerto 49152).

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

:: Empaquetado Windows
build_desktop.bat
```

No hay lint, typecheck ni CI configurados. Orden de verificación: solo pytest.

`setup.bat` está roto: instala `backend\requirements.txt` (borrado). Usar `pip install -r requirements.txt` en la raíz.

## Base de datos

- **MySQL obligatorio** (PyMySQL). `DATABASE_URL` en `.env` (ver `.env.example`). No hay SQLite.
- Al arrancar, `init_db()` crea esquema + aplica migraciones incrementales de columnas (p.ej. conversiones de paquete en `products`). No hay Alembic ni framework de migraciones; scripts one-shot en `backend/migrate_*.py` (dejar de usar `migrate_to_aiven.py` y credenciales hardcodeadas).
- Pool de conexiones propio en `database.py`. Rutas usan `get_db()` + `close_db` teardown.

### Tests

`backend/tests/conftest.py` **dropea y recrea** la BD `SistemaPOSv1_1_test` por sesión, con credenciales hardcodeadas `root:15508984@localhost`. Sobreescribir con `TEST_DATABASE_URL` si cambian. Los tests NO usan la BD de producción.

Semilla de roles: `admin/admin123`, `cajero/cajero123`, superadmin `master/Master123!` (este último solo se inserta en el fixture de tests, no en `init_db`).

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
- `backend/pos.db` es residuo; el sistema no usa SQLite.
- Python 3.13 en `.venv`. Dependencias pinneadas en `requirements.txt` (Flask 3, waitress, pywebview, pyinstaller, pytest).
