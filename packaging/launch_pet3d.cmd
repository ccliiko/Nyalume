@echo off
chcp 65001 >nul
setlocal
set "ADMIN="
if /I "%~n0"=="启动3D桌宠-管理员" set "ADMIN=--admin"

set "MODEL=%~1"
if not defined MODEL set /p "MODEL=请输入 PMX 文件或模型文件夹的完整路径："
if not exist "%MODEL%" (
  echo 找不到模型：%MODEL%
  pause
  exit /b 1
)

if "%~2"=="" (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%MODEL%"
) else (
  start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%MODEL%" --vmd "%~2"
)
