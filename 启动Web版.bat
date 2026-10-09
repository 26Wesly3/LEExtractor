@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, litsearch; from importlib.metadata import version; assert version('litsearch') == litsearch.__version__" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -e ".[web]" -c constraints.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" scripts\launch_web.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动失败，请检查上方信息。需要 Python 3.10 或以上，首次安装需要网络。
pause
exit /b 1
