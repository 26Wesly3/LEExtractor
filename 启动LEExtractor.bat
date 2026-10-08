@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import streamlit, sklearn, networkx, fastmcp, requests, dotenv" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动失败。请确认已安装 Python 3.10 或以上，并检查上方错误信息。
pause
exit /b 1
