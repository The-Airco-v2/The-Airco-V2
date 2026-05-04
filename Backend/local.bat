@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "LOCAL_SH=%SCRIPT_DIR%local.sh"
set "BASH_EXE="

if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH_EXE for /f "delims=" %%I in ('where bash 2^>nul') do if not defined BASH_EXE set "BASH_EXE=%%I"

if not defined BASH_EXE (
    echo ERROR: Bash was not found.
    echo Install Git Bash, or run ^"%LOCAL_SH%^" from a Bash shell instead.
    exit /b 1
)

"%BASH_EXE%" "%LOCAL_SH%" %*
exit /b %errorlevel%
