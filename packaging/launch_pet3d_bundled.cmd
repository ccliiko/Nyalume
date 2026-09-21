@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
rem Models and dances live in .\models inside this package.
rem Keep every path literal ASCII here: cmd.exe parses this file with the OEM
rem code page, so Chinese literals written as UTF-8 turn into garbage paths.
set "ADMIN="
echo %~n0 | findstr /I /C:"admin" >nul && set "ADMIN=--admin"

set "ROOT=%~dp0models"
set "NAME=%~1"
if not defined NAME (
  for /d %%D in ("%ROOT%\*") do (
    if not defined NAME (
      for %%F in ("%%D\*.pmx") do if not defined NAME set "NAME=%%~nxD"
    )
  )
)
if not defined NAME (
  echo No model folder found under: %ROOT%
  pause
  exit /b 1
)
set "MODEL=%ROOT%\%NAME%"
if not exist "%MODEL%" (
  echo Model not found: %MODEL%
  echo Tip: pass a folder name as the first argument, e.g.  this.cmd 洛茜
  pause
  exit /b 1
)
set "VMD=%ROOT%\motions"
if not exist "%VMD%" set "VMD="

if defined VMD (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%MODEL%" --vmd "%VMD%"
) else (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%MODEL%"
)
