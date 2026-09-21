param(
    [string]$Version = "0.3.0",
    [switch]$PersonalAssets,
    # 带模型/动作的个人版：把 -ModelSource 下含 .pmx 或 .vmd 的子目录一起打进包
    [switch]$WithModels,
    [string]$ModelSource = "D:\download\模型"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRoot = Join-Path $projectRoot "build"
$distRoot = Join-Path $projectRoot "dist"
$distDir = Join-Path $distRoot "Nyalume"
$releaseRoot = Join-Path $projectRoot "release"
$packageName = "Nyalume-v$Version-windows-x64" + $(if ($PersonalAssets) { "-personal" } else { "" }) + $(if ($WithModels) { "-models" } else { "" })
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
# 前缀匹配：文档名带版本后缀（3D桌宠使用说明v0.2.0.md）也不会漏
Copy-Item -Path (Join-Path $projectRoot "docs\3D桌宠使用说明*.md") -Destination $distDir

# cmd.exe 只认 CRLF：仓库里的 .cmd 若是 LF（编辑器/补丁工具写出来的），双击就会
# 把每一行拆错（报 '65001' is not recognized 之类）。拷贝时统一成 CRLF、不带 BOM。
function Copy-CmdLauncher([string]$Source, [string]$Destination) {
    $text = [IO.File]::ReadAllText($Source)
    $text = $text -replace "`r?`n", "`r`n"
    [IO.File]::WriteAllText($Destination, $text, [Text.UTF8Encoding]::new($false))
}

Copy-CmdLauncher (Join-Path $PSScriptRoot "launch_pet3d.cmd") (Join-Path $distDir "启动3D桌宠.cmd")
Copy-CmdLauncher (Join-Path $PSScriptRoot "launch_pet3d.cmd") (Join-Path $distDir "启动3D桌宠-管理员.cmd")

if ($WithModels) {
    # 只带"有模型或有动作"的子目录：没有 .pmx/.vmd 的目录（纯贴图、半成品）打进去只是白占空间
    # 目录名用 ASCII：cmd 启动脚本要按这个路径找模型/动作，中文在 .cmd 里会被代码页搞坏
    $bundleDir = Join-Path $distDir "models"
    New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
    $picked = @()
    foreach ($dir in Get-ChildItem -LiteralPath $ModelSource -Directory) {
        $hit = Get-ChildItem -LiteralPath $dir.FullName -Recurse -File -Include *.pmx, *.vmd -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $hit) { continue }
        $target = if ($dir.Name -eq "动作配布") { "motions" } else { $dir.Name }
        Copy-Item -LiteralPath $dir.FullName -Destination (Join-Path $bundleDir $target) -Recurse
        $picked += $target
    }
    if (-not $picked) { throw "没找到含 .pmx/.vmd 的子目录：$ModelSource" }
    Write-Output ("Bundled models: " + ($picked -join ", "))
    Copy-CmdLauncher (Join-Path $PSScriptRoot "launch_pet3d_bundled.cmd") (Join-Path $distDir "启动3D桌宠（含模型）.cmd")
    Copy-CmdLauncher (Join-Path $PSScriptRoot "launch_pet3d_bundled.cmd") (Join-Path $distDir "启动3D桌宠（含模型）-admin.cmd")
    Copy-Item -LiteralPath (Join-Path $projectRoot "3D_ASSET_NOTICE.md") -Destination (Join-Path $bundleDir "素材声明-必读.md")
}
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
