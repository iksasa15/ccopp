@echo off
REM ============================================================
REM Council of Agents — Windows Launcher
REM Automatically requests Administrator privileges via UAC
REM ============================================================

REM Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo   Council of Agents requires Administrator privileges
    echo   to monitor system processes and network activity.
    echo.
    echo   مجلس الوكلاء يحتاج صلاحيات الإدارة لمراقبة العمليات
    echo   والشبكة على مستوى النظام.
    echo ============================================================
    echo.
    echo Requesting elevation via UAC...
    
    REM Re-launch this script as Administrator
    powershell -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

echo.
echo ============================================================
echo   Council of Agents v0.2 - Running as Administrator
echo   مجلس الوكلاء - يعمل بصلاحيات الإدارة
echo ============================================================
echo.

REM Change to the script's directory
cd /d "%~dp0"

REM Pass all arguments to run.py. If none, show help.
if "%~1"=="" (
    python run.py
) else (
    python run.py %*
)

echo.
pause
