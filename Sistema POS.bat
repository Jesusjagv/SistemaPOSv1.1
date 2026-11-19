@echo off
title Sistema POS v1.0 — Escritorio
cd /d "%~dp0"

echo Iniciando Sistema POS...
echo Si la ventana no aparece en 10 segundos, revisa el archivo desktop_error.log
echo.

start /b "" ".venv\Scripts\pythonw.exe" "desktop.py"

timeout /t 3 >nul

:: Verificar que el proceso arrancó
tasklist /fi "IMAGENAME eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
if %errorlevel%==0 (
    echo Proceso iniciado correctamente.
) else (
    echo ERROR: No se pudo iniciar el proceso.
    echo Ejecutando en modo consola para ver errores...
    ".venv\Scripts\python.exe" desktop.py
    pause
)
