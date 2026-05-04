@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND=%ROOT%Backend"
set "FRONTEND=%ROOT%Frontend"

if /I "%~1"=="-stop" goto stop
if /I "%~1"=="-restart" goto restart

goto start

:start
call "%BACKEND%\local.bat" --gpu || exit /b %errorlevel%
pushd "%FRONTEND%"
docker compose up -d --build
popd
echo.
echo Backend and frontend started.
exit /b %errorlevel%

:restart
call "%BACKEND%\local.bat" --down
pushd "%FRONTEND%"
docker compose down --remove-orphans
popd
goto start

:stop
call "%BACKEND%\local.bat" --down
pushd "%FRONTEND%"
docker compose down --remove-orphans
popd
echo.
echo Backend and frontend stopped.
exit /b %errorlevel%
