@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creation de l environnement virtuel...
  python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :error
.venv\Scripts\python.exe run.py %*
goto :eof
:error
echo Echec de l installation.
pause
