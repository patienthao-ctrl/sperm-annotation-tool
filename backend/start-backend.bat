@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
echo.
echo ================================================
echo Unified FastAPI Backend
 echo URL: http://127.0.0.1:3000
echo ================================================
echo.
%PY% -m uvicorn app.main:app --host 127.0.0.1 --port 3000
pause
