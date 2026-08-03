@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
rem ===== AI Programming Practice Assistant - Launcher =====
rem Prefer bundled portable Python, then system Python.
set "PYEXE="
if exist "runtime\python\python.exe" set "PYEXE=runtime\python\python.exe"
if not defined PYEXE if exist "runtime\python\python3.exe" set "PYEXE=runtime\python\python3.exe"
if not defined PYEXE set "PYEXE=python"
"%PYEXE%" code\launcher_gui.py
if errorlevel 2 (
    echo [!] tkinter not found, fallback to web launcher.
    "%PYEXE%" code\launcher.py
)
if errorlevel 1 pause
endlocal
