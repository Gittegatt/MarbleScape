@echo off
setlocal
cd /d "%~dp0"

echo Starting marblescape_download.py...
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
pause
exit /b %APP_EXIT_CODE%
