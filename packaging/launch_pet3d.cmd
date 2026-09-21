@echo off
chcp 65001 >nul
setlocal
set "ADMIN="
if /I "%~n0"=="启动3D桌宠-管理员" set "ADMIN=--admin"

set "MODEL=%~1"
if defined MODEL (
  if not exist "%MODEL%" (
    echo 找不到模型：%MODEL%
    pause
    exit /b 1
  )
)

rem 不带参数时由程序用"上次记住的模型目录"启动；第一次跑会给个提示窗。
if not defined MODEL (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN%
  exit /b 0
)
if "%~2"=="" (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%MODEL%"
) else (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%MODEL%" --vmd "%~2"
)
