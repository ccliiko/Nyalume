param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRoot = Join-Path $projectRoot "build"
$distRoot = Join-Path $projectRoot "dist"
$distDir = Join-Path $distRoot "Nyalume"
$releaseRoot = Join-Path $projectRoot "release"
$zipPath = Join-Path $releaseRoot "Nyalume-v$Version-windows-x64.zip"

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
Remove-ProjectItem $zipPath
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean `
        --workpath $buildRoot `
        --distpath $distRoot `
        (Join-Path $PSScriptRoot "Nyalume.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出码：$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.txt") -Destination $distDir
Compress-Archive -LiteralPath $distDir -DestinationPath $zipPath -CompressionLevel Optimal

$zip = Get-Item -LiteralPath $zipPath
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
# ASCII output keeps Windows PowerShell 5 parsing reliable for UTF-8 files without BOM.
Write-Output ("Package: " + $zip.FullName)
Write-Output ("Size: " + [math]::Round($zip.Length / 1MB, 1) + " MB")
Write-Output ("SHA256: " + $hash)
