@echo off
rem Run Phoque Al from source (creates a virtual environment the first time).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: installing Python packages, please wait...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
".venv\Scripts\python.exe" -m phoqueal.main
if errorlevel 1 pause
