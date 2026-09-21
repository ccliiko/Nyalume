param(
    [string]$Version = "0.2.0",
    [switch]$PersonalAssets
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRoot = Join-Path $projectRoot "build"
$distRoot = Join-Path $projectRoot "dist"
$distDir = Join-Path $distRoot "Nyalume"
$releaseRoot = Join-Path $projectRoot "release"
$packageName = "Nyalume-v$Version-windows-x64" + $(if ($PersonalAssets) { "-personal" } else { "" })
$releaseDir = Join-Path $releaseRoot $packageName
$zipPath = Join-Path $releaseRoot "$packageName.zip"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "找不到 .venv Python：$python"
}

function Remove-ProjectItem([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $rootPrefix = [IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理项目目录之外的路径：$fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

Remove-ProjectItem (Join-Path $buildRoot "Nyalume")
Remove-ProjectItem $distDir
Remove-ProjectItem $releaseDir
Remove-ProjectItem $zipPath
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

Push-Location $projectRoot
$oldAssetSetting = $env:NYALUME_BUNDLE_PERSONAL_ASSETS
try {
    $env:NYALUME_BUNDLE_PERSONAL_ASSETS = $(if ($PersonalAssets) { "1" } else { "0" })
    & $python -m PyInstaller --noconfirm --clean `
        --workpath $buildRoot `
        --distpath $distRoot `
        (Join-Path $PSScriptRoot "Nyalume.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出码：$LASTEXITCODE"
    }
}
finally {
    $env:NYALUME_BUNDLE_PERSONAL_ASSETS = $oldAssetSetting
    Pop-Location
}

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.txt") -Destination $distDir
foreach ($notice in @("LICENSE", "EULA.md", "PRIVACY.md", "THIRD_PARTY_NOTICES.md", "ASSET_PROVENANCE.md", "3D_ASSET_NOTICE.md")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $notice) -Destination $distDir
}
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\Windows-便携版与3D桌宠教程.md") -Destination $distDir
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "launch_pet3d.cmd") -Destination (Join-Path $distDir "启动3D桌宠.cmd")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "launch_pet3d.cmd") -Destination (Join-Path $distDir "启动3D桌宠-管理员.cmd")
Set-Content -LiteralPath (Join-Path $distDir "VERSION.txt") -Value $Version -Encoding ascii
Copy-Item -LiteralPath $distDir -Destination $releaseDir -Recurse
Compress-Archive -LiteralPath $releaseDir -DestinationPath $zipPath -CompressionLevel Optimal

$zip = Get-Item -LiteralPath $zipPath
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
# ASCII output keeps Windows PowerShell 5 parsing reliable for UTF-8 files without BOM.
Write-Output ("Package: " + $zip.FullName)
Write-Output ("Executable: " + (Join-Path $releaseDir "Nyalume.exe"))
Write-Output ("Size: " + [math]::Round($zip.Length / 1MB, 1) + " MB")
Write-Output ("SHA256: " + $hash)
