@echo off
REM =====================================================
REM  build_desktop.bat — Compila desktop.py a .exe
REM  Requiere: pip install pyinstaller
REM =====================================================
title Compilando Sistema POS...
cd /d "%~dp0"

echo ========================================
echo  Compilando Sistema POS v1.0
echo  Modo: Directorio portable (recomendado)
echo ========================================
echo.

REM Activar entorno virtual si existe
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo [1/3] Instalando/actualizando pyinstaller...
pip install pyinstaller --quiet

echo [2/3] Compilando...
pyinstaller --windowed --onedir ^
  --name "SistemaPOS" ^
  --add-data "frontend;frontend" ^
  --collect-all pywebview ^
  --collect-all pythonnet ^
  --hidden-import webview ^
  --hidden-import bottle ^
  --hidden-import proxy_tools ^
  desktop.py

echo.
echo [3/3] Compilacion completada.
echo El ejecutable se encuentra en: dist\SistemaPOS\SistemaPOS.exe
echo.
echo Para distribuir, comprime toda la carpeta: dist\SistemaPOS\
echo.

if errorlevel 1 (
    echo ERROR: La compilacion fallo. Revisa los mensajes arriba.
    pause
    exit /b 1
)

pause
