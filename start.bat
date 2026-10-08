@echo off
setlocal
cd /d "%~dp0"

echo Starting marblescape_download.py...
rem Prefer the project's virtual environment, which holds the dependencies.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "marblescape_download.py"
    goto done
)

where python >nul 2>&1
if not errorlevel 1 (
    python "marblescape_download.py"
    goto done
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "marblescape_download.py"
    goto done
)

echo Error: Python 3.11 or newer was not found.
echo Install Python and run this file again.
set "APP_EXIT_CODE=1"
goto report

:done
set "APP_EXIT_CODE=%ERRORLEVEL%"

:report
echo.
echo Exit code: %APP_EXIT_CODE%
echo.
rem A normal exit closes the window; an error stays open so it can be read.
if "%APP_EXIT_CODE%"=="0" (
    echo This window closes in 3 seconds.
    timeout /t 3 /nobreak >nul
    exit /b 0
)
pause
exit /b %APP_EXIT_CODE%
