@echo off
chcp 65001 >nul
title Sistema POS v1.0 — Venezuela

echo.
echo =====================================================
echo   🏪  SISTEMA POS v1.0 — Venezuela
echo =====================================================
echo.
echo   Iniciando servidor...
echo   Abre tu navegador en: http://localhost:5000
echo.
echo   Usuarios:
echo   - Admin:  usuario=admin   contrasena=admin123
echo   - Cajero: usuario=cajero  contrasena=cajero123
echo.
echo   Presiona Ctrl+C para detener el servidor
echo =====================================================
echo.

cd /d "%~dp0"
python backend\app.py

pause
