@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -c "import printer_controller" 2>nul || pip install -e .
python -m printer_controller %*
