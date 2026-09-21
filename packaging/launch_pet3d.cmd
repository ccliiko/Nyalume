@echo off
chcp 65001 >nul
setlocal
set "ADMIN="
if /I "%~n0"=="启动3D桌宠-管理员" set "ADMIN=--admin"

rem 模型放本目录下的 models\，动作放 motions\，程序自己去找——所以这里不用拼任何
rem 中文路径（cmd.exe 按 OEM 代码页解析本文件，中文字面量会变乱码）。
rem 想临时指到别处就给两个参数：本.cmd  <模型目录>  [动作目录]
if not "%~1"=="" (
  if not "%~2"=="" (
    start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%~1" --vmd "%~2"
  ) else (
    start "" "%~dp0Nyalume.exe" --pet3d %ADMIN% --model "%~1"
  )
  exit /b 0
)

start "" "%~dp0Nyalume.exe" --pet3d %ADMIN%
