/**
 * theme.js — Gestión de temas Claro/Oscuro
 */

(function() {
    const theme = localStorage.getItem('app-theme') || 'dark';
    if (theme === 'light') {
        document.documentElement.classList.add('light-mode');
    }
})();

function toggleTheme() {
    const isLight = document.documentElement.classList.toggle('light-mode');
    const newTheme = isLight ? 'light' : 'dark';
    localStorage.setItem('app-theme', newTheme);
    updateThemeIcon(isLight);
}

function updateThemeIcon(isLight) {
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.className = isLight ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
    }
}

// Inicializar icono al cargar
document.addEventListener('DOMContentLoaded', () => {
    const isLight = document.documentElement.classList.contains('light-mode');
    updateThemeIcon(isLight);

    // Registrar Service Worker para soporte PWA
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .then(reg => console.log('PWA: Service Worker registrado.', reg.scope))
            .catch(err => console.log('PWA: Error de registro de Service Worker:', err));
    }
});
