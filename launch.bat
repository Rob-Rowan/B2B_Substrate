@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM  B2B Substrate - Windows Launcher
REM
REM  Creates a virtual environment on first run, installs dependencies,
REM  and launches the Streamlit application at http://localhost:8501.
REM ===========================================================================

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "REQ_HASH_FILE=%VENV_DIR%\.requirements.sha256"
set "PYTHON_CMD=python"

echo.
echo  ============================================
echo   B2B Substrate - Lead Triage & Email Sequencer
echo  ============================================
echo.

REM --- 0. Verify app.py is present -------------------------------------------
if not exist "app.py" (
    echo  [ERROR] app.py not found.
    echo.
    echo  Make sure launch.bat is located in the project root
    echo  alongside app.py.
    echo.
    pause
    exit /b 1
)

REM --- 1. Verify Python is available -----------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo  [ERROR] Python was not found on your PATH.
        echo.
        echo  Install Python 3.11+ from https://www.python.org/downloads/
        echo  and make sure "Add python.exe to PATH" is checked.
        echo.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=py -3"
)

REM --- 2. Create virtual environment if missing -------------------------------
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [SETUP] Creating virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo  [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

REM --- 3. Install dependencies if requirements.txt changed --------------------
"%VENV_DIR%\Scripts\python.exe" -c "import hashlib; print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())" > "%REQ_HASH_FILE%.tmp"
if errorlevel 1 (
    echo  [ERROR] Failed to hash requirements.txt.
    pause
    exit /b 1
)
set /p REQ_HASH=<"%REQ_HASH_FILE%.tmp"

if not exist "%REQ_HASH_FILE%" (
    echo  [SETUP] Installing dependencies...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo  [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo %REQ_HASH%> "%REQ_HASH_FILE%"
) else (
    set /p OLD_HASH=<"%REQ_HASH_FILE%"
    if not "!REQ_HASH!"=="!OLD_HASH!" (
        echo  [SETUP] requirements.txt changed - updating dependencies...
        "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
        if errorlevel 1 (
            echo  [ERROR] Failed to update dependencies.
            pause
            exit /b 1
        )
        echo %REQ_HASH%> "%REQ_HASH_FILE%"
    )
)
del "%REQ_HASH_FILE%.tmp" >nul 2>nul

REM --- 4. Warn if port 8501 is already occupied --------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "try { $r=Invoke-WebRequest -Uri 'http://localhost:8501' -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop; exit 1 } catch { exit 0 }"
if errorlevel 1 (
    echo.
    echo  [NOTE] A server is already running on port 8501.
    echo  [NOTE] Streamlit may fall back to port 8502 - keep an eye
    echo  [NOTE] on the URL printed below after the server starts.
    echo.
)

REM --- 5. Launch the application ----------------------------------------------
echo.
echo  [LAUNCH] Starting B2B Substrate...
echo  [LAUNCH] A browser window will open automatically once the server is ready.
echo  [LAUNCH] Press Ctrl+C in this window to stop the server.
echo.

REM Auto-open the default browser as soon as the server is ready.  A hidden
REM PowerShell process polls localhost:8501, then 8502, then 8503 and
REM launches the default browser (Chrome, Firefox, Edge, etc.) on the first
REM port that responds.  This handles Streamlit's automatic port fallback
REM when 8501 is already in use.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$ports=8501,8502,8503; $opened=$false; foreach($p in $ports){ if($opened){break}; $pd=(Get-Date).AddSeconds(20); while((Get-Date) -lt $pd -and -not $opened){ try { $resp=Invoke-WebRequest -Uri ('http://localhost:'+$p) -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; if($resp.StatusCode -ge 200){ Start-Process ('http://localhost:'+$p); $opened=$true } } catch {}; if(-not $opened){ Start-Sleep -Seconds 2 } } }"

REM Launch Streamlit headless so the launcher opens exactly one browser tab.
"%VENV_DIR%\Scripts\streamlit.exe" run app.py --server.headless true

echo.
echo  [STOPPED] B2B Substrate has exited.
pause
