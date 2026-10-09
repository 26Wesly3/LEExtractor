@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem The default entry opens the integrated Web workspace.
call "%~dp0启动Web版.bat" %*
exit /b %errorlevel%
