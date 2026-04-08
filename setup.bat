@echo off
chcp 65001 >nul
echo.
echo =====================================================
echo   SISTEMA POS v1.0 — Instalacion de dependencias
echo =====================================================
echo.

cd /d "%~dp0"

echo [1/2] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado.
    echo Descargalo en: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version

echo.
echo [2/2] Instalando dependencias...
pip install -r backend\requirements.txt

echo.
echo =====================================================
echo   Instalacion completada!
echo   Ahora ejecuta: start.bat
echo =====================================================
pause
