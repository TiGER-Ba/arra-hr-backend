@echo off
set PYTHONUTF8=1
set PATH=C:\GTK3\bin;%PATH%
cd /d "%~dp0"
call venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
