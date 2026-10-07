@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist .venv\Scripts\python.exe goto :fail

rem Folders the app writes into. Django makes most of these on demand, but a
rem missing media/ turns the first upload into a stack trace. setup.bat does
rem the full job; this is the cheap subset so a bare clone still runs.
for %%D in (
    "detection\media" "detection\media\uploads" "detection\media\processed"
    "detection\media\audio_segments" "detection\media\models"
) do if not exist "%%~D" mkdir "%%~D" 2>nul

rem re-run every launch: pip is a no-op when satisfied and self-heals a partial
rem install. No pydub - audio is decoded with soundfile/pyav, which need no
rem external binary. See setup.bat for the optional extras.
.venv\Scripts\python -m pip install -q django librosa soundfile av numpy scipy scikit-learn joblib "psycopg[binary]"
if errorlevel 1 goto :fail

rem ffmpeg is no longer required - soundfile and pyav handle wav/mp3/m4a/flac.
rem Still prepended when present, since other tooling may want it.
rem quoted set and %~dp0 (not %CD%) because this path contains spaces.
if exist "%~dp0ffmpeg\bin\ffmpeg.exe" set "PATH=%~dp0ffmpeg\bin;%PATH%"


rem =====================================================================
rem  DATABASE
rem
rem  Postgres if we can reach it, SQLite if we cannot. The password is
rem  remembered in db_password.txt (gitignored) so this is asked once,
rem  not every launch.
rem =====================================================================

if not defined CYBERSHIELD_DB_NAME set "CYBERSHIELD_DB_NAME=cybershield"
if not defined CYBERSHIELD_DB_USER set "CYBERSHIELD_DB_USER=postgres"
if not defined CYBERSHIELD_DB_HOST set "CYBERSHIELD_DB_HOST=localhost"
if not defined CYBERSHIELD_DB_PORT set "CYBERSHIELD_DB_PORT=5432"

rem a password already in the environment always wins
if defined CYBERSHIELD_DB_PASSWORD goto :have_password

rem otherwise use the one saved last time
if exist "%~dp0db_password.txt" (
    set /p CYBERSHIELD_DB_PASSWORD=<"%~dp0db_password.txt"
    if defined CYBERSHIELD_DB_PASSWORD goto :have_password
)

rem no password anywhere. Only ask if a server is actually listening -
rem otherwise SQLite is the right answer and a prompt is just noise.
call :port_open %CYBERSHIELD_DB_HOST% %CYBERSHIELD_DB_PORT%
if errorlevel 1 (
    echo No PostgreSQL server on %CYBERSHIELD_DB_HOST%:%CYBERSHIELD_DB_PORT% - using SQLite.
    goto :database_ready
)

echo.
echo   PostgreSQL is running on %CYBERSHIELD_DB_HOST%:%CYBERSHIELD_DB_PORT% but no password is set.
echo   Enter the password for the "%CYBERSHIELD_DB_USER%" user to use it,
echo   or press Enter alone to stay on SQLite.
echo.
set "CYBERSHIELD_DB_PASSWORD="
set /p "CYBERSHIELD_DB_PASSWORD=  password: "

if not defined CYBERSHIELD_DB_PASSWORD (
    echo   Nothing entered - using SQLite.
    goto :database_ready
)

:have_password

rem Create the database if this is the first run. Harmless when it exists:
rem "already exists" is reported and ignored.
echo.
echo Checking the PostgreSQL database...

.venv\Scripts\python "%~dp0detection\ensure_database.py"

if errorlevel 2 (
    echo.
    echo   Falling back to SQLite - the app still works, it just stores data
    echo   in a file instead of PostgreSQL.
    echo.
    echo   To use PostgreSQL, run this again with the right password. If you
    echo   do not know it, reset it from an admin PowerShell:
    echo     ^& "D:\PostgreSQL\bin\psql.exe" -U postgres -c "ALTER USER postgres PASSWORD 'newpass';"       #Added
    echo.
    rem nothing was saved - a password that did not work is not worth keeping
    set "CYBERSHIELD_DB_PASSWORD="
    goto :database_ready
)

if errorlevel 1 goto :fail

rem worked - remember it so the next launch does not ask
> "%~dp0db_password.txt" echo %CYBERSHIELD_DB_PASSWORD%

:database_ready

cd detection

if defined CYBERSHIELD_DB_PASSWORD (
    echo Database: PostgreSQL "%CYBERSHIELD_DB_NAME%" on %CYBERSHIELD_DB_HOST%:%CYBERSHIELD_DB_PORT%
) else (
    echo Database: SQLite
)

rem A SQLite database made before the project owned its user model cannot be
rem migrated forward. Detect that and rebuild it, but only when it holds no
rem users - otherwise say so and stop.
if not defined CYBERSHIELD_DB_PASSWORD (
    ..\.venv\Scripts\python ensure_migratable.py
    if errorlevel 1 goto :fail
)

..\.venv\Scripts\python manage.py migrate
if errorlevel 1 goto :fail

rem open the browser once the port is actually accepting, then hand over to the server
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "$n=0; while($n -lt 120){ try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8000); $c.Close(); Start-Process 'http://127.0.0.1:8000/'; break } catch{ Start-Sleep -Milliseconds 500; $n++ } }"

rem 0.0.0.0 so the phone can reach it over the LAN, not just localhost
..\.venv\Scripts\python manage.py runserver 0.0.0.0:8000
echo.
pause
exit /b %errorlevel%


rem ---------------------------------------------------------------------
rem  Is anything listening on host:port? errorlevel 0 = yes, 1 = no.
rem ---------------------------------------------------------------------
:port_open
powershell -NoProfile -Command "try{ $c=New-Object Net.Sockets.TcpClient; $c.Connect('%~1',%~2); $c.Close(); exit 0 } catch { exit 1 }"
exit /b %errorlevel%


:fail
echo.
echo Setup failed. Need Python 3.9+ on PATH.
pause
exit /b 1
