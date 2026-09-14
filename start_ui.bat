@echo off
setlocal
set PY=C:\Python314\python.exe
set APP_DIR=D:\Agent\git\google-map-lead-filter\webapp
set URL=http://127.0.0.1:8766/email-review

rem -- already running? then just open the browser --
curl -s -o nul --max-time 2 %URL%
if %errorlevel%==0 goto open

if not exist "%PY%" (
  echo Python not found: %PY%
  pause
  exit /b 1
)

rem -- start server minimized --
start "leads-webapp" /min cmd /c "cd /d %APP_DIR% && "%PY%" app.py"

rem -- wait up to 15s for the server to come up --
set /a tries=0
:wait
timeout /t 1 /nobreak >nul
curl -s -o nul --max-time 2 %URL%
if %errorlevel%==0 goto open
set /a tries+=1
if %tries% lss 15 goto wait
echo Server failed to start. Check port 8766.
pause
exit /b 1

:open
start "" %URL%
exit /b 0
