@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title LEExtractor

rem ==========================================================================
rem Self-contained launcher. Only this file is needed.
rem   - prepares an interpreter and installs dependencies if required
rem   - starts app.py next to this file
rem   - waits until the server really answers, then opens the browser once
rem   - this window stays open and shows the server log; close it to stop
rem ==========================================================================

echo 应用文件：%~dp0app.py
echo 访问地址：http://127.0.0.1:8501
echo.

rem ---- 1. Pick an interpreter: repo venv first, then system Python ----------
if exist ".venv\Scripts\python.exe" goto checkvenv

python -c "import streamlit, sklearn, networkx, fastmcp, requests, dotenv" >nul 2>&1
if not errorlevel 1 goto systempy

echo [1/3] 首次启动：创建独立虚拟环境（不会改动系统 Python）
python -m venv .venv
if errorlevel 1 (
    py -3 -m venv .venv
    if errorlevel 1 goto failed
)
goto installdeps

:checkvenv
".venv\Scripts\python.exe" -c "import streamlit, sklearn, networkx, fastmcp, requests, dotenv" >nul 2>&1
if not errorlevel 1 goto ready
echo [1/3] 虚拟环境依赖不完整，重新安装

:installdeps
echo [2/3] 安装依赖（首次需要几分钟）
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
goto ready

:systempy
set "PYEXE=python"
goto launch

:ready
set "PYEXE=.venv\Scripts\python.exe"
goto launch

rem ---- 2. Write a one-off helper, then start through it --------------------
:launch
set "HELPER=%TEMP%\leextractor_open_browser.py"
del "%HELPER%" 2>nul
rem Indentation is kept in variables because echo eats the first space after it.
set "S1=    "
set "S2=        "
set "S3=            "

>> "%HELPER%" echo import subprocess, sys, time, urllib.request, webbrowser
>> "%HELPER%" echo app = sys.argv[1]
>> "%HELPER%" echo port = sys.argv[2]
>> "%HELPER%" echo url = "http://127.0.0.1:" + port
>> "%HELPER%" echo health = url + "/_stcore/health"
>> "%HELPER%" echo def alive(target):
>> "%HELPER%" echo %S1%try:
>> "%HELPER%" echo %S2%with urllib.request.urlopen(target, timeout=1.0) as response:
>> "%HELPER%" echo %S3%return response.status == 200
>> "%HELPER%" echo %S1%except Exception:
>> "%HELPER%" echo %S2%return False
>> "%HELPER%" echo if alive(health):
>> "%HELPER%" echo %S1%print("LEExtractor 已经在运行：" + url)
>> "%HELPER%" echo %S1%webbrowser.open(url + "/?_=" + str(int(time.time())))
>> "%HELPER%" echo %S1%sys.exit(0)
>> "%HELPER%" echo print("正在启动 LEExtractor，浏览器会自动打开")
>> "%HELPER%" echo server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", app, "--server.address", "127.0.0.1", "--server.port", port, "--server.headless", "true", "--browser.gatherUsageStats", "false"])
>> "%HELPER%" echo ready = False
>> "%HELPER%" echo for _ in range(240):
>> "%HELPER%" echo %S1%if server.poll() is not None:
>> "%HELPER%" echo %S2%print("Streamlit 启动失败，请看上方错误信息")
>> "%HELPER%" echo %S2%sys.exit(1)
>> "%HELPER%" echo %S1%if alive(health):
>> "%HELPER%" echo %S2%ready = True
>> "%HELPER%" echo %S2%break
>> "%HELPER%" echo %S1%time.sleep(0.5)
>> "%HELPER%" echo if not ready:
>> "%HELPER%" echo %S1%print("等待服务响应超时")
>> "%HELPER%" echo %S1%sys.exit(1)
>> "%HELPER%" echo print("服务已就绪，正在打开浏览器：" + url)
>> "%HELPER%" echo webbrowser.open(url + "/?_=" + str(int(time.time())))
>> "%HELPER%" echo print("本窗口保持打开即为运行中，关闭窗口或按 Ctrl+C 停止")
>> "%HELPER%" echo sys.exit(server.wait())

echo [3/3] 启动中，浏览器会自动打开
"%PYEXE%" "%HELPER%" "%~dp0app.py" "8501"
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo 启动失败。请确认已安装 Python 3.10 或以上，并查看上方错误信息。
pause
exit /b 1
