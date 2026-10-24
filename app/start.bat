@echo off
title LabelLie
cd /d "%~dp0"
echo.
echo  ==============================
echo    LabelLie - starting up
echo  ==============================
echo.

set "PY="
py -3.12 -V >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3 -V >nul 2>nul && set "PY=py -3"
if not defined PY python -V >nul 2>nul && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY (
  echo  Python was not found.
  echo  Install it from https://www.python.org/downloads/
  echo  and tick "Add Python to PATH" during setup. Then run this file again.
  echo.
  pause
  exit /b 1
)
echo  Using Python: %PY%

echo  [1/3] Installing packages (first time takes a minute)...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo  Could not install packages. Check your internet and run this file again.
  pause
  exit /b 1
)

echo.
if "%GEMINI_API_KEY%"=="" (
  echo  [2/3] Paste your Gemma key from https://aistudio.google.com/apikey
  echo        Right-click to paste, then press Enter.
  set /p GEMINI_API_KEY=       Key:
)
if "%GEMINI_API_KEY%"=="" (
  echo  No Gemma key entered. The app will try Ollama, then Claude.
) else (
  echo  [2/3] Key set for this window only. It is not saved anywhere.
)

echo.
if "%ANTHROPIC_API_KEY%"=="" (
  echo  Recommended: paste a Claude key from https://console.anthropic.com
  echo  Claude reads the label if Gemma fails, and explains unknown codes. Press Enter to skip.
  set /p ANTHROPIC_API_KEY=       Claude key: 
)
echo.
echo  [3/3] Starting LabelLie at http://localhost:8000
echo        Keep this window open. Close it to stop the app.
echo.
start "" /min cmd /c "timeout /t 5 >nul & start http://localhost:8000"
%PY% -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
