@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 py -3 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import streamlit, litsearch" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -e . -c constraints.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" scripts\launch_gui.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Streamlit 启动失败，请查看上方信息。
pause
exit /b 1
