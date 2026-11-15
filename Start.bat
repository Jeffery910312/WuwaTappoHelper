@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run Setup.bat first.
  pause
  exit /b 1
)
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0Launch.pyw"
if errorlevel 1 (
  echo Unable to start the app. Please run Setup.bat again.
  pause
  exit /b 1
)
exit /b 0
