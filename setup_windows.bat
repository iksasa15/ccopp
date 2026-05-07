@echo off
REM ============================================================
REM Council of Agents — First-Time Setup
REM Run this once after extracting the project
REM ============================================================

echo.
echo ============================================================
echo   Council of Agents - Initial Setup
echo   مجلس الوكلاء - الإعداد الأولي
echo ============================================================
echo.

REM Check Python
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found.
    echo Please install Python 3.11+ from https://python.org
    pause
    exit /b 1
)
python --version
echo.

REM Check pip
echo [2/5] Upgrading pip...
python -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo WARNING: Could not upgrade pip
)
echo.

REM Install dependencies
echo [3/5] Installing Python dependencies (this may take 5-10 minutes)...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install some dependencies.
    echo Check the error above and try manually:
    echo   pip install -r requirements.txt
    pause
    exit /b 1
)
echo.

REM Check Ollama
echo [4/5] Checking Ollama installation...
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo Ollama is not installed.
    echo.
    echo Please install it from: https://ollama.com/download
    echo Then run:
    echo   ollama pull qwen2.5:7b-instruct-q5_K_M
    echo   ollama pull deepseek-r1:7b
    echo   ollama pull nomic-embed-text
    echo.
) else (
    ollama --version
    echo.
    echo Pulling AI models (this will take 10-20 minutes for first download)...
    ollama pull qwen2.5:7b-instruct-q5_K_M
    ollama pull nomic-embed-text
    echo.
    echo NOTE: deepseek-r1:7b is optional but recommended for deeper analysis
    echo To install it later: ollama pull deepseek-r1:7b
)

REM Create initial directories
echo [5/5] Creating data directories...
if not exist "data" mkdir data
if not exist "data\quarantine" mkdir data\quarantine
if not exist "data\datasets" mkdir data\datasets
if not exist "logs" mkdir logs
echo.

echo ============================================================
echo   Setup complete!
echo   Now you can run: run_as_admin.bat scan-system
echo ============================================================
echo.
pause
