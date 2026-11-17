@echo off
title AStock Data API Server

if not defined PYTHON set PYTHON=E:\Conda_env\Qlib_env\python.exe
set SERVER=%~dp0server.py

echo ========================================
echo   AStock Data API Server
echo   Path: %~dp0
echo   Port: 5000
echo ========================================
echo.

if not exist "%SERVER%" (
    echo [ERROR] Server file not found: %SERVER%
    pause
    exit /b 1
)

echo Starting server...
echo Keep this window open. Press Ctrl+C to stop.
echo.

"%PYTHON%" "%SERVER%"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server exited with code %errorlevel%
    echo Possible causes: port 5000 already in use, or network error
    echo.
    pause
)
