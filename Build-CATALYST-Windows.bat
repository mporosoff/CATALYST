@echo off
rem Build the portable Windows app (desktop-dist\CATALYST-Windows.exe) from this folder.
cd /d "%~dp0"
set PY=.desktop-build\release-venv\Scripts\python.exe
if not exist "%PY%" (
  echo The build Python was not found at %PY%.
  pause
  exit /b 1
)
"%PY%" scripts\build-desktop.py
if errorlevel 1 (
  echo Build failed - see the messages above.
) else (
  echo.
  echo Done: desktop-dist\CATALYST-Windows.exe
)
pause
