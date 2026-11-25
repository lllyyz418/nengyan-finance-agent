@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0runtime\python.exe" "%~dp0launch.py" --check
pause
