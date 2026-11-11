@echo off
rem PixelView_CC - compile the Rhino plug-in (.rhp) into dist\. Writes logs\build_<date>.log.
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set STAMP=%%i
set LOG=logs\build_%STAMP%.log
echo PixelView_CC build %STAMP% > "%LOG%"

where dotnet >nul 2>nul
if errorlevel 1 goto nodotnet
dotnet --list-sdks >> "%LOG%" 2>&1
dotnet --list-sdks | findstr /r "^[6-9]\. ^1[0-9]\." >nul
if errorlevel 1 goto nodotnet

echo Building PixelView_CC (first time: about 1 minute, packages are downloaded)...
dotnet build "src\PixelView_CC.csproj" -c Release -nologo -v minimal >> "%LOG%" 2>&1
if errorlevel 1 goto failed
if not exist "dist\PixelView_CC.rhp" goto failed
if not exist "dist\pdfium.dll" goto nopdfium

echo BUILD OK >> "%LOG%"
echo.
echo BUILD OK: dist\PixelView_CC.rhp and dist\pdfium.dll
echo Log: %LOG%
echo.
pause
exit /b 0

:nodotnet
echo NO .NET SDK >> "%LOG%"
echo.
echo The .NET SDK is not installed.
echo Install the ".NET 8.0 SDK" (x64, free) from https://dotnet.microsoft.com/download/dotnet/8.0
echo then double-click build.cmd again.
echo.
pause
exit /b 1

:nopdfium
echo BUILD FAILED: pdfium.dll was not copied to dist >> "%LOG%"
echo.
echo BUILD FAILED: pdfium.dll was not copied to dist\. Log: %LOG%
echo.
pause
exit /b 1

:failed
echo BUILD FAILED >> "%LOG%"
echo.
echo BUILD FAILED. Log: %LOG%
echo.
pause
exit /b 1
