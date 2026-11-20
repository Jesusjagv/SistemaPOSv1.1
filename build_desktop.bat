@echo off
REM =====================================================
REM  build_desktop.bat — Compila desktop.py + backend a
REM  dist\SistemaPOS\ (portable) y genera el ZIP para
REM  distribuir a clientes.
REM  Requiere: pip install pyinstaller (venv)
REM =====================================================
title Compilando Sistema POS...
cd /d "%~dp0"

echo ========================================
echo  Compilando Sistema POS v1.1
echo  Modo: Directorio portable + backend
echo ========================================
echo.

REM Activar entorno virtual si existe
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo [1/4] Instalando/actualizando pyinstaller...
pip install pyinstaller --quiet

echo [2/4] Compilando (frontend + backend incluidos)...
pyinstaller --noconfirm --windowed --onedir ^
  --name "SistemaPOS" ^
  --paths "backend" ^
  --add-data "frontend;frontend" ^
  --collect-all pywebview ^
  --collect-all pythonnet ^
  --hidden-import webview ^
  --hidden-import bottle ^
  --hidden-import proxy_tools ^
  desktop.py

if errorlevel 1 (
    echo ERROR: La compilacion fallo. Revisa los mensajes arriba.
    pause
    exit /b 1
)

echo [3/4] Copiando artefactos para el cliente...
if exist "LEEME.txt" (
    copy /y "LEEME.txt" "dist\SistemaPOS\LEEME.txt" >nul
)
if exist ".env.example" (
    copy /y ".env.example" "dist\SistemaPOS\config.env.example" >nul
)

echo [4/4] Creando ZIP de distribucion...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\SistemaPOS' -DestinationPath 'SistemaPOS-v1.1.zip' -Force"
if errorlevel 1 (
    echo ERROR: No se pudo crear el ZIP.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Listo.
echo   Ejecutable: dist\SistemaPOS\SistemaPOS.exe
echo   ZIP:        SistemaPOS-v1.1.zip
echo ========================================
echo.
echo Para probar: ejecuta dist\SistemaPOS\SistemaPOS.exe
echo.

pause
