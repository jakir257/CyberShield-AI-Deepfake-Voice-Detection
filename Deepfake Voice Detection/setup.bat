@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem =====================================================================
rem  CyberShield - one-time setup.
rem
rem  Makes the folders the app writes into, creates the virtualenv and
rem  installs everything. Safe to re-run: every step is a no-op when it
rem  is already done.
rem
rem  run.bat does the light version of this on every launch. This script
rem  is the one to run on a fresh machine, and the only one that offers
rem  the big optional downloads.
rem =====================================================================

echo.
echo   CyberShield setup
echo   =================
echo.


rem ---------------------------------------------------------------------
rem  1. Folders the app writes into.
rem
rem  Django creates most of these on demand, but a missing media/ turns a
rem  first upload into a stack trace instead of a result. Cheap to make
rem  up front. Datasets and models are gitignored, so a fresh clone has
rem  none of them.
rem ---------------------------------------------------------------------

echo [1/5] Creating folders...

for %%D in (
    "detection\media"
    "detection\media\uploads"
    "detection\media\processed"
    "detection\media\audio_segments"
    "detection\media\models"
    "detection\media\forensics"
    "detection\media\model_tests"
    "..\training"
    "..\raw"
) do (
    if not exist "%%~D" (
        mkdir "%%~D" 2>nul
        echo       made %%~D
    )
)

echo       folders ready.
echo.


rem ---------------------------------------------------------------------
rem  2. Virtualenv.
rem ---------------------------------------------------------------------

rem .venv lives beside run.bat, not at the repo root - run.bat uses this one,
rem so installing anywhere else would set up an environment the app never uses.
echo [2/5] Python environment...

if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist .venv\Scripts\python.exe goto :nopython

set "PY=.venv\Scripts\python.exe"
echo       using %~dp0.venv
echo.


rem ---------------------------------------------------------------------
rem  3. Core dependencies - everything the app needs to run.
rem
rem  No pydub and no ffmpeg: audio is decoded with soundfile and pyav,
rem  which are pip-installable and need no external binary. That was a
rem  deliberate change - pydub shells out to ffmpeg and every mp3 upload
rem  failed with "[WinError 2] The system cannot find the file specified"
rem  on a machine without it.
rem ---------------------------------------------------------------------

echo [3/5] Installing core packages (a few minutes on a fresh machine)...

%PY% -m pip install -q --upgrade pip
%PY% -m pip install -q django librosa soundfile av numpy scipy scikit-learn joblib
if errorlevel 1 goto :pipfail

echo       core packages ready.
echo.


rem ---------------------------------------------------------------------
rem  4. PostgreSQL driver - optional.
rem
rem  settings.py falls back to SQLite when CYBERSHIELD_DB_PASSWORD is
rem  unset, so this is only worth installing if Postgres is actually
rem  being used. A failure here is not fatal.
rem ---------------------------------------------------------------------

echo [4/5] PostgreSQL driver (optional)...

%PY% -m pip install -q "psycopg[binary]" 2>nul
if errorlevel 1 (
    echo       skipped - SQLite will be used.
) else (
    echo       installed.
)
echo.


rem ---------------------------------------------------------------------
rem  5. Forensics 0.3B - optional, large.
rem
rem  ~2 GB of torch plus 1.3 GB of weights. The app runs fine without it;
rem  it just will not appear in the model list. Asked rather than assumed
rem  because that is a lot to download onto a machine that may not want it.
rem
rem  Licence: CC-BY-NC-4.0 - research and personal use, not commercial.
rem ---------------------------------------------------------------------

echo [5/5] Forensics 0.3B deepfake model (optional)
echo.
echo       A pretrained 300M-parameter model. Far more accurate than the
echo       small models trained in the app - and the only one that gets
echo       real phone recordings right:
echo.
echo         ASVspoof 2019 LA     0.26%% EER   (trained-here model: 11%%)
echo         real phone call      correctly REAL (others say FAKE)
echo.
echo       Cost: ~3.3 GB download. The very first score is slow - it also
echo       fetches WavLM-large (~1.2 GB) - then ~12s for the first clip of
echo       a session and ~3s after. Licence CC-BY-NC-4.0, non-commercial.
echo.

set "GETFX="
set /p "GETFX=      Download it now? [y/N]: "

if /i not "%GETFX%"=="y" (
    echo       Skipped. Re-run this script later to add it.
    goto :done
)

echo.
echo       Installing torch (CPU build)...
%PY% -m pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto :fxfail

%PY% -m pip install -q transformers safetensors
if errorlevel 1 goto :fxfail

echo       Downloading model files...
set "FXDIR=detection\media\forensics"
set "FXURL=https://huggingface.co/eliya/forensics_0.3B_base_deepfake_classifier/resolve/main"

for %%F in (model.py config.json) do (
    if not exist "%FXDIR%\%%F" (
        powershell -NoProfile -Command "try{ Invoke-WebRequest -Uri '%FXURL%/%%F' -OutFile '%FXDIR%\%%F' -UseBasicParsing } catch { exit 1 }"
        if errorlevel 1 goto :fxfail
        echo         got %%F
    )
)

if not exist "%FXDIR%\checkpoint_epoch_5.safetensors" (
    echo         downloading weights, 1.3 GB - this takes a while...
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; try{ Invoke-WebRequest -Uri '%FXURL%/checkpoint_epoch_5.safetensors' -OutFile '%FXDIR%\checkpoint_epoch_5.safetensors' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 goto :fxfail
)

rem a truncated download loads as a corrupt model, so check the size
for %%A in ("%FXDIR%\checkpoint_epoch_5.safetensors") do set "FXSIZE=%%~zA"
if not "%FXSIZE%"=="1269932956" (
    echo.
    echo       WARNING: weights are %FXSIZE% bytes, expected 1269932956.
    echo       The download was cut short. Delete the file and re-run.
    goto :done
)

echo       Forensics 0.3B ready.
echo       Note: the first score also fetches WavLM-large (~1.2 GB).

:done
echo.
echo   ------------------------------------------------------------
echo   Setup complete. Start the app with:  run.bat
echo   ------------------------------------------------------------
echo.
pause
exit /b 0


:nopython
echo.
echo   Python not found. Install Python 3.9 or newer and tick
echo   "Add Python to PATH", then run this again.
echo   https://www.python.org/downloads/
echo.
pause
exit /b 1

:pipfail
echo.
echo   Could not install the core packages. Check the internet
echo   connection and run this again.
echo.
pause
exit /b 1

:fxfail
echo.
echo   Forensics download failed. The app still works without it -
echo   run this script again to retry.
echo.
pause
exit /b 1
