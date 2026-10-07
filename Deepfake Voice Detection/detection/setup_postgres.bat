@echo off
rem ---------------------------------------------------------------------
rem  One-time PostgreSQL setup for CyberShield AI.
rem
rem  Run this once, then set CYBERSHIELD_DB_PASSWORD before run.bat.
rem  You will be prompted for the postgres user's password twice.
rem ---------------------------------------------------------------------
setlocal
set PSQL="D:\PostgreSQL\bin\psql.exe"       # Added

echo Creating database "cybershield"...
%PSQL% -U postgres -h localhost -p 5432 -c "CREATE DATABASE cybershield;"
if errorlevel 1 (
    echo.
    echo Could not create it. If it already exists that is fine - carry on.
)

echo.
echo Done. Now start the app with:
echo.
echo     set CYBERSHIELD_DB_PASSWORD=your-postgres-password
echo     run.bat
echo.
pause
