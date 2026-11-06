@echo off
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
title 자막 읽어주기
echo ============================================
echo   자막 읽어주기 (영어 강의 - 한국어 목소리)
echo ============================================
echo   1. 바탕화면 'LiveCaptions Translator' 바로가기로 번역기 켜기 (홈 화면)
echo   2. 강의 영상 재생
echo   3. 멈출 때는 이 창에서 Ctrl+C
echo.
if "%~1"=="--check" (
  echo [check] 준비 확인만 하고 끝냄
  ".venv\Scripts\python.exe" -c "import pyttsx3, uiautomation; print('[check] python OK')"
  exit /b
)
".venv\Scripts\python.exe" step4_speak_translation.py 7200
echo.
echo 기록은 logs 폴더에 남았어. 창을 닫으려면 아무 키나 누르기.
pause >nul
