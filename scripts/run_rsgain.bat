@echo off
REM Run ReplayGain 2.0 scanning using bundled rsgain
cd /d "%~dp0.."

echo ============================================
echo   rsgain - ReplayGain 2.0 Tagger
echo ============================================
echo.

set RSGAIN_EXE=vendor\rsgain-3.7-win64\rsgain.exe
if not exist "%RSGAIN_EXE%" (
    echo Error: rsgain not found at %RSGAIN_EXE%
    pause
    exit /b 1
)

echo Music library: Z:\music
echo Preset:        no_album (songs not organized by album)
echo Threads:       MAX
echo Skip existing: YES
echo Logging:       ON (replaygain.csv in each scanned directory)
echo.
echo Starting scan at %date% %time%...
echo.

"%RSGAIN_EXE%" easy -p no_album -m MAX -S -O "Z:\music"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================
    echo   ERROR: rsgain exited with code %ERRORLEVEL%
    echo ============================================
) else (
    echo.
    echo ============================================
    echo   Scan complete!
    echo   Finished at %date% %time%
    echo   Check replaygain.csv files for scan logs.
    echo ============================================
)

echo.
echo Press any key to close this window...
pause >nul
