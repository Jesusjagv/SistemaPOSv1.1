/**
 * theme.js — Gestión de temas Claro/Oscuro
 * Default: Light mode (clean UI)
 */

(function() {
    const theme = localStorage.getItem('app-theme') || 'light';
    if (theme === 'dark') {
        document.documentElement.classList.add('dark-mode');
    }
})();

function toggleTheme() {
    const isDark = document.documentElement.classList.toggle('dark-mode');
    const newTheme = isDark ? 'dark' : 'light';
    localStorage.setItem('app-theme', newTheme);
    updateThemeIcon(isDark);
}

function updateThemeIcon(isDark) {
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.className = isDark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
    }
}

// Inicializar icono al cargar
document.addEventListener('DOMContentLoaded', () => {
    const isDark = document.documentElement.classList.contains('dark-mode');
    updateThemeIcon(isDark);

    // Registrar Service Worker para soporte PWA
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .then(reg => console.log('PWA: Service Worker registrado.', reg.scope))
            .catch(err => console.log('PWA: Error de registro de Service Worker:', err));
    }
});
