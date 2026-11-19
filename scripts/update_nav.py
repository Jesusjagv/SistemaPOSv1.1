"""
update_nav.py — Actualiza navegación en todos los archivos HTML
- Cambia "Productos" → "Inventario" con icono fa-boxes-stacked
- Asegura iconos consistentes en la barra de navegación
"""

import re
import os
import glob

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend')

# Mapeo de iconos consistentes para la navegación
NAV_ICONS = {
    'POS':          'fa-cash-register',
    'Inventario':   'fa-boxes-stacked',
    'Productos':    'fa-boxes-stacked',  # alias
    'Clientes':     'fa-users',
    'Reportes':     'fa-chart-bar',
    'Fiados':       'fa-hand-holding-dollar',
    'Caja':         'fa-cash-register',
    'Configuración':'fa-gear',
}

# Texto de navegación consistente
NAV_TEXT = {
    'fa-cash-register': 'POS',
    'fa-boxes-stacked': 'Inventario',
    'fa-users': 'Clientes',
    'fa-chart-bar': 'Reportes',
    'fa-hand-holding-dollar': 'Fiados',
    'fa-gear': 'Configuración',
}


def update_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content

    # 1. Reemplazar "Productos" → "Inventario" con icono fa-boxes-stacked
    # Patrón para header-nav-desktop (con inline styles)
    content = re.sub(
        r'<i class="fa-solid fa-box"></i>\s*Productos',
        '<i class="fa-solid fa-boxes-stacked"></i> Inventario',
        content
    )

    # 2. Reemplazar en sidebar nav items también
    content = re.sub(
        r'<i class="fa-solid fa-box"></i>\s*Productos',
        '<i class="fa-solid fa-boxes-stacked"></i> Inventario',
        content
    )

    # 3. Reemplazar "Punto de Venta" → "POS" en sidebar (si existe)
    content = re.sub(
        r'<i class="fa-solid fa-cash-register"></i>\s*Punto de Venta',
        '<i class="fa-solid fa-cash-register"></i> POS',
        content
    )

    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False


def main():
    html_files = glob.glob(os.path.join(FRONTEND_DIR, '*.html'))
    updated = 0

    for filepath in sorted(html_files):
        name = os.path.basename(filepath)
        if update_file(filepath):
            print(f'  [OK] {name}')
            updated += 1
        else:
            print(f'  [--] {name} (sin cambios)')

    print(f'\nTotal: {updated} archivos actualizados de {len(html_files)}')


if __name__ == '__main__':
    main()
