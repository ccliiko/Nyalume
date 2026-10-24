@echo off
cd /d "%~dp0"
set "NYALUME_UNITY_PLAYER=%~dp0build\eku-v052-release\player\EkuPlayer.exe"
if not exist "%NYALUME_UNITY_PLAYER%" (
    echo Eku v0.5.2 player missing. Build the release player first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m nyalume.frontends.pet.pet3d.pet3d_win --model "models\Eku\Eku.vrm" --renderer unity
