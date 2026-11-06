@echo off
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
title 자막 읽어주기 (앱)
echo 앱 창을 띄우는 중... 창을 닫으면 이 창도 몇 초 뒤 알아서 닫혀.
".venv\Scripts\python.exe" app.py
