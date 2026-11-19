/* ======================================================
   keyboard.js — Navegación por teclado estilo GSIN
   Pila de modales, trap de foco, roving tabindex, F-keys
   ====================================================== */
(function () {
  'use strict';

  /* ── Pila de modales + focus restore ──────────────── */
  const modalStack = []; // [{ id, previousFocus }]

  function isModalOpen(id) {
    return modalStack.some(m => m.id === id);
  }

  function openModalKeyboard(id) {
    if (isModalOpen(id)) return;
    const el = document.getElementById(id);
    if (!el) return;
    const previous = document.activeElement;
    modalStack.push({ id, previousFocus: previous });

    // Foco inicial: input con autofocus, o primer focable, o el modal
    requestAnimationFrame(() => {
      const initial = el.querySelector('[data-autofocus], input:not([type=hidden]):not([disabled]), select, textarea, button:not(.btn-close), [tabindex="0"]');
      if (initial) initial.focus();
      else {
        const dialog = el.querySelector('.modal');
        if (dialog) {
          dialog.setAttribute('tabindex', '-1');
          dialog.focus();
        }
      }
      trapFocus(el);
    });
  }

  function closeModalKeyboard(id) {
    const idx = modalStack.map(m => m.id).lastIndexOf(id);
    if (idx === -1) return;
    const entry = modalStack.splice(idx, 1)[0];
    const el = document.getElementById(id);
    if (el) releaseTrap(el);
    if (entry.previousFocus && document.contains(entry.previousFocus)) {
      entry.previousFocus.focus();
    }
  }

  /** Cierra el modal más reciente. Devuelve true si cerró algo. */
  function closeTopModal() {
    if (!modalStack.length) return false;
    const top = modalStack[modalStack.length - 1];
    if (typeof window.closeModal === 'function') {
      window.closeModal(top.id); // api.js
    } else {
      const el = document.getElementById(top.id);
      if (el) {
        el.classList.add('hidden');
        el.style.removeProperty('display');
      }
    }
    return true;
  }

  /* ── Focus trap ────────────────────────────────────── */
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  const trapHandlers = new WeakMap(); // modalEl -> { keydown, focusin }

  function trapFocus(modalEl) {
    if (trapHandlers.has(modalEl)) return;
    const onKeyDown = (e) => {
      if (e.key !== 'Tab') return;
      const nodes = Array.from(modalEl.querySelectorAll(FOCUSABLE))
        .filter(n => n.offsetParent !== null || n === document.activeElement);
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    const onFocusIn = (e) => {
      if (!modalEl.contains(e.target)) {
        const nodes = modalEl.querySelectorAll(FOCUSABLE);
        if (nodes.length) nodes[0].focus();
      }
    };
    modalEl.addEventListener('keydown', onKeyDown);
    document.addEventListener('focusin', onFocusIn);
    trapHandlers.set(modalEl, { keydown: onKeyDown, focusin: onFocusIn });
  }

  function releaseTrap(modalEl) {
    const h = trapHandlers.get(modalEl);
    if (!h) return;
    modalEl.removeEventListener('keydown', h.keydown);
    document.removeEventListener('focusin', h.focusin);
    trapHandlers.delete(modalEl);
  }

  /* ── Roving tabindex (grids / listas) ──────────────── */
  /**
   * Navega con flechas entre elementos focables de un contenedor.
   * @param {HTMLElement} container
   * @param {KeyboardEvent} e
   * @param {string} orientation 'horizontal' | 'vertical' | 'grid'
   * @returns {HTMLElement|null} el nuevo foco o null
   */
  function moveRoving(container, e, orientation = 'vertical') {
    if (!container) return null;
    const items = Array.from(container.querySelectorAll('[data-roving], ' + FOCUSABLE))
      .filter(n => n.offsetParent !== null && !n.disabled);
    if (!items.length) return null;
    const current = document.activeElement;
    let idx = items.indexOf(current);
    if (idx === -1) {
      // si el foco no está en items, ir al primero
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight' || e.key === 'Enter') {
        items[0].focus();
        e.preventDefault();
        return items[0];
      }
      return null;
    }

    const cols = parseInt(container.dataset.rovingCols || '0', 10) || 0;
    let next = idx;

    switch (e.key) {
      case 'ArrowDown':
        next = cols > 0 ? idx + cols : idx + 1;
        if (orientation === 'horizontal') return null;
        break;
      case 'ArrowUp':
        next = cols > 0 ? idx - cols : idx - 1;
        if (orientation === 'horizontal') return null;
        break;
      case 'ArrowRight':
        if (orientation === 'vertical' && !cols) {
          // en vertical sin cols, ArrowRight no navega (deja pasar a inputs)
          return null;
        }
        next = cols > 0 ? idx + 1 : idx + 1;
        break;
      case 'ArrowLeft':
        if (orientation === 'vertical' && !cols) return null;
        next = idx - 1;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = items.length - 1;
        break;
      default:
        return null;
    }

    if (next < 0 || next >= items.length) return null;
    // No robar flechas si el foco está en un input numérico y la orientación lo permite
    e.preventDefault();
    items[next].focus();
    return items[next];
  }

  /* ── Registro de atajos F-keys por página ──────────── */
  const pageShortcuts = {}; // pageKey -> { F2: fn, ... }
  let activePage = null;

  /**
   * Registra atajos para la página actual.
   * @param {string} pageKey identificador libre ('pos', 'products', ...)
   * @param {Object<string, Function>} map  ej: { F2: cobrar, F3: focusSearch }
   * @param {Array<{key:string,desc:string}>} [helpRows] filas para el modal F1
   * @param {string[]} [allowInInput] teclas que deben dispararse aunque el foco esté en un input
   */
  function register(pageKey, map, helpRows, allowInInput) {
    pageShortcuts[pageKey] = {
      map: map || {},
      help: helpRows || [],
      allowInInput: allowInInput || []
    };
    activePage = pageKey;
    document.documentElement.setAttribute('data-kbd-page', pageKey);
  }

  function isEditableTarget(t) {
    if (!t) return false;
    const tag = (t.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
  }

  /* ── Modal de ayuda F1 ────────────────────────────── */
  function ensureHelpModal() {
    if (document.getElementById('kbd-help-modal')) return;
    const wrap = document.createElement('div');
    wrap.className = 'modal-overlay hidden';
    wrap.id = 'kbd-help-modal';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-labelledby', 'kbd-help-title');
    wrap.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeHelp();
    });
    wrap.innerHTML = `
      <div class="modal" style="width:min(95vw,520px);">
        <div class="modal-header">
          <div class="modal-title" id="kbd-help-title"><i class="fa-solid fa-keyboard" style="color:var(--violet);"></i> Atajos de Teclado</div>
          <button class="btn-close" id="kbd-help-close" aria-label="Cerrar"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="modal-body">
          <p style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.75rem;">Atajos disponibles en esta página (estilo GSIN). Escape cierra ventanas.</p>
          <table style="width:100%;font-size:0.85rem;border-collapse:collapse;">
            <thead>
              <tr style="border-bottom:1px solid var(--border);text-align:left;">
                <th style="padding:0.4rem 0.5rem;color:var(--text-muted);font-weight:600;">Tecla</th>
                <th style="padding:0.4rem 0.5rem;color:var(--text-muted);font-weight:600;">Acción</th>
              </tr>
            </thead>
            <tbody id="kbd-help-rows"></tbody>
          </table>
        </div>
        <div class="modal-footer">
          <button class="btn btn-primary" id="kbd-help-ok">Entendido</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    wrap.querySelector('#kbd-help-close').addEventListener('click', closeHelp);
    wrap.querySelector('#kbd-help-ok').addEventListener('click', closeHelp);
  }

  function openHelp() {
    ensureHelpModal();
    const rows = (activePage && pageShortcuts[activePage]) ? pageShortcuts[activePage].help : [];
    const tbody = document.getElementById('kbd-help-rows');
    const defaults = [
      { key: 'F1', desc: 'Esta ayuda' },
      { key: 'Escape', desc: 'Cerrar ventana / menú' },
      { key: 'Tab / Shift+Tab', desc: 'Moverse entre controles' },
      { key: '↑ ↓ ← →', desc: 'Navegar listas y grillas' },
      { key: 'Enter', desc: 'Confirmar / agregar' }
    ];
    const seen = new Set();
    const all = [...defaults, ...rows].filter(r => {
      if (seen.has(r.key)) return false;
      seen.add(r.key);
      return true;
    });
    tbody.innerHTML = all.map(r =>
      `<tr style="border-bottom:1px solid var(--border);">
        <td style="padding:0.45rem 0.5rem;"><kbd style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:4px;padding:0.15rem 0.45rem;font-size:0.78rem;font-family:monospace;">${r.key}</kbd></td>
        <td style="padding:0.45rem 0.5rem;">${r.desc}</td>
      </tr>`).join('');
    openModalKeyboard('kbd-help-modal');
    document.getElementById('kbd-help-modal').classList.remove('hidden');
  }

  function closeHelp() {
    const m = document.getElementById('kbd-help-modal');
    if (m) {
      m.classList.add('hidden');
      m.style.removeProperty('display');
    }
    closeModalKeyboard('kbd-help-modal');
  }

  /* ── Escape global ─────────────────────────────────── */
  function handleEscape() {
    // 1. Cerrar modal topmost
    if (closeTopModal()) return true;

    // 2. Cerrar sidebar móvil
    const nav = document.querySelector('.app-nav.active');
    if (nav && typeof window.closeSidebar === 'function') {
      window.closeSidebar();
      return true;
    }

    // 3. Cerrar dropdown usuario
    const dd = document.getElementById('user-dropdown');
    if (dd && !dd.classList.contains('hidden')) {
      dd.classList.add('hidden');
      return true;
    }

    // 4. Hook de página (POS limpia búsqueda, etc.)
    if (activePage && typeof pageShortcuts[activePage].onEscape === 'function') {
      pageShortcuts[activePage].onEscape();
      return true;
    }
    return false;
  }

  /* ── Dispatcher global keydown ─────────────────────── */
  document.addEventListener('keydown', (e) => {
    // F1 siempre (aunque esté en input)
    if (e.key === 'F1') {
      e.preventDefault();
      openHelp();
      return;
    }

    // Escape: también funciona en inputs
    if (e.key === 'Escape') {
      // No interferir si el navegador maneja something (fullscreen etc.)
      if (handleEscape()) {
        e.preventDefault();
      }
      return;
    }

    // F-keys de página: no robar si estás en un input (excepto F-keys de acción global)
    const page = activePage && pageShortcuts[activePage];
    if (page && page.map[e.key]) {
      // Permitir F2/F10 incluso desde input (cobro), bloquear solo si es un shortcut "navegación"
      const allowInInput = page.allowInInput && page.allowInInput.includes(e.key);
      if (!isEditableTarget(e.target) || allowInInput) {
        e.preventDefault();
        page.map[e.key](e);
        return;
      }
    }

    // "/" enfoca búsqueda (fuera de inputs)
    if (e.key === '/' && !isEditableTarget(e.target)) {
      const page2 = activePage && pageShortcuts[activePage];
      if (page2 && page2.map.SLASH) {
        e.preventDefault();
        page2.map.SLASH(e);
      }
    }
  });

  /* ── Observador: openModal/closeModal de api.js ────── */
  // Si api.js define openModal antes, lo envolvemos una vez cargado el DOM.
  function hookApiModals() {
    if (window.__kbdHooked) return;
    window.__kbdHooked = true;

    if (typeof window.openModal === 'function') {
      const origOpen = window.openModal;
      window.openModal = function (id) {
        origOpen(id);
        openModalKeyboard(id);
      };
    }
    if (typeof window.closeModal === 'function') {
      const origClose = window.closeModal;
      window.closeModal = function (id) {
        origClose(id);
        closeModalKeyboard(id);
      };
    }
  }

  // api.js carga antes que keyboard.js en las páginas; esperar microtask
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(hookApiModals, 0));
  } else {
    setTimeout(hookApiModals, 0);
  }

  /* ── API pública ───────────────────────────────────── */
  window.Keyboard = {
    register,
    openModal: openModalKeyboard,
    closeModal: closeModalKeyboard,
    closeTopModal,
    trapFocus,
    releaseTrap,
    moveRoving,
    openHelp,
    isEditableTarget,
    FOCUSABLE
  };
})();
