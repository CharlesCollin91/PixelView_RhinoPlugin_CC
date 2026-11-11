@echo off
rem PixelView_CC - send the .yak to McNeel's package server. First the TEST server, then the public one.
setlocal
cd /d "%~dp0"
set YAK=%ProgramFiles%\Rhino 8\System\Yak.exe
if not exist "%YAK%" (
  echo Rhino 8 Yak.exe not found at "%YAK%".
  pause
  exit /b 1
)
set PKG=
for /f "delims=" %%f in ('dir /b /o-d dist\*.yak 2^>nul') do if not defined PKG set PKG=dist\%%f
if not defined PKG (
  echo No .yak in dist\: run pack.cmd first.
  pause
  exit /b 1
)
echo Package: %PKG%
echo.
echo   1 = TEST server (https://test.yak.rhino3d.com) - do this first
echo   2 = PUBLIC Package Manager - only after the test install worked
echo   3 = sign in to your Rhino account (yak login)
echo.
set /p CHOICE=Your choice (1, 2 or 3):
if "%CHOICE%"=="3" "%YAK%" login
if "%CHOICE%"=="1" "%YAK%" push --source https://test.yak.rhino3d.com "%PKG%"
if "%CHOICE%"=="2" (
  echo.
  echo A published version can never be replaced or reused.
  set /p SURE=Type PUBLISH to send %PKG% to the public Package Manager:
)
if "%CHOICE%"=="2" if "%SURE%"=="PUBLISH" "%YAK%" push "%PKG%"
echo.
pause
