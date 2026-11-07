@echo off
chcp 65001 >nul
rem Video Translator updater: git pull -> .venv -> update.py (Korean messages live in update.py)
cd /d "%~dp0"
title Video Translator update
echo [0/4] git pull
git pull --ff-only
if errorlevel 1 (
  echo.
  echo  ! git pull failed. Local changes or no network. Nothing was changed.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [0/4] creating .venv with Python 3.14
  py -3.14 -m venv .venv
  if errorlevel 1 (
    echo  ! Python 3.14 not found. Install it first: winget install Python.Python.3.14
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" update.py
if errorlevel 1 (
  pause
  exit /b 1
)
timeout /t 4 >nul