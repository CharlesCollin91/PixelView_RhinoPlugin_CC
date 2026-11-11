@echo off
rem PixelView_CC - make the Package Manager package (.yak) from dist\. Run it after the build has been tested in Rhino.
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set STAMP=%%i
set LOG=logs\pack_%STAMP%.log
set YAK=%ProgramFiles%\Rhino 8\System\Yak.exe
echo PixelView_CC pack %STAMP% > "%LOG%"
if not exist "%YAK%" goto noyak
if not exist "dist\PixelView_CC.rhp" goto nobuild
if not exist "dist\pdfium.dll" goto nobuild

set STAGE=dist\package
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"
copy /y "dist\PixelView_CC.rhp" "%STAGE%\" >> "%LOG%"
copy /y "dist\pdfium.dll" "%STAGE%\" >> "%LOG%"
copy /y "manifest.yml" "%STAGE%\" >> "%LOG%"
copy /y "icon.png" "%STAGE%\" >> "%LOG%"
if exist "LICENSE.txt" (copy /y "LICENSE.txt" "%STAGE%\" >> "%LOG%") else (copy /y "LICENSE" "%STAGE%\LICENSE.txt" >> "%LOG%")
copy /y "THIRD-PARTY-NOTICES.md" "%STAGE%\" >> "%LOG%"
pushd "%STAGE%"
"%YAK%" build --platform win >> "..\..\%LOG%" 2>&1
set ERR=%errorlevel%
popd
if not "%ERR%"=="0" goto failed
move /y "%STAGE%\*.yak" "dist\" >> "%LOG%"
echo PACK OK >> "%LOG%"
dir /b dist\*.yak >> "%LOG%"
echo.
echo PACK OK:
dir /b dist\*.yak
echo Log: %LOG%
echo.
pause
exit /b 0

:noyak
echo NO YAK at %YAK% >> "%LOG%"
echo Rhino 8 Yak.exe not found at "%YAK%". Is Rhino 8 installed in the default folder?
pause
exit /b 1

:nobuild
echo NO BUILD >> "%LOG%"
echo dist\PixelView_CC.rhp or dist\pdfium.dll missing: run build.cmd first.
pause
exit /b 1

:failed
echo PACK FAILED >> "%LOG%"
echo PACK FAILED. Log: %LOG%
pause
exit /b 1
