@echo off
setlocal
cd /d "%~dp0"
title Publicar Panorama Cleanup Windows no GitHub
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\publish_windows.py
) else (
    python scripts\publish_windows.py
)
if errorlevel 1 (
    echo.
    echo A publicacao nao foi concluida. Confira a mensagem acima.
) else (
    echo.
    echo Executaveis publicados e conferidos no GitHub.
)
pause
