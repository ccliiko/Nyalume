param(
    [string]$Version = "0.3.2",
    [switch]$PersonalAssets,
    # 带模型/动作的个人版：-ModelSource 下含 .pmx 的模型目录全带，动作只带 -MotionPick 白名单
    [switch]$WithModels,
    # 本机素材根：模型和动作是分开的两个目录
    [string]$ModelSource = "D:\download\模型&动作\模型",
    [string]$MotionSource = "D:\download\模型&动作\动作",
    # 动作只挑这几个子目录进包。动作配布里混着 .blend/.blend1 工程文件（实测 600 MB），
    # 整个目录拷进去纯属白占空间，所以这里用白名单；要全带就自己传 -MotionPick @()
    [string[]]$MotionPick = @(
        "だいあるのーと_by_若梦Romy_4f280529292577cf776c7063ed8c41fb",
        "IRIS OUT_by_pronxy-迫奈熏_3000e43d1f56a24c3bc1c4a6e39a1a4b",
        "【动作配布】Stay Tonight Heaven Lee Ver"
    )
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
        # -Force 不会删掉只读文件（打包进来的 numpy .pyd 就是只读的，重打包时会报
        # "Access to the path ... is denied"），所以先把只读属性摘掉
        Get-ChildItem -LiteralPath $fullPath -Recurse -Force -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Attributes = $_.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly) }
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
# 声明类：第三方组件/服务/素材合成一份（THIRD_PARTY_NOTICES.md）。
# ASSET_PROVENANCE.md 是内部合规记录，只留在源码仓库，不进发行包。
foreach ($notice in @("LICENSE", "EULA.md", "PRIVACY.md", "THIRD_PARTY_NOTICES.md")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $notice) -Destination $distDir
}
# 3D 桌宠文档：说明 + 教程合成一份（docs\3D桌宠说明.md），不再按版本后缀改名
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\3D桌宠说明.md") -Destination $distDir

# 固定素材目录必须解压后就看得见：启动时那句弹窗让用户"把模型放进 models\"，
# 目录不存在的话他只能自己猜。空目录进 zip 不可靠，所以各放一行说明当占位。
$hints = @(
    @("models",  "放模型到这里.txt", "把模型文件夹整个放进来（每个模型一个子目录，例如 锁瞑\xxx.pmx），重启桌宠即可看见。程序不附带任何模型，请自行从作者发布处取得。"),
    @("motions", "放动作到这里.txt", ".vmd 放进来就行，子目录会被递归查找；不放的话只播程序自带的待机动作。程序不附带任何第三方动作。")
)
foreach ($h in $hints) {
    $hintDir = Join-Path $distDir $h[0]
    New-Item -ItemType Directory -Force -Path $hintDir | Out-Null
    Set-Content -LiteralPath (Join-Path $hintDir $h[1]) -Value $h[2] -Encoding utf8
}

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
    # 布局跟不带模型的包一致：模型进 models\、动作进 motions\（程序按这两个固定目录找）。
    $bundleDir = Join-Path $distDir "models"
    $motionsDir = Join-Path $distDir "motions"
    New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
    New-Item -ItemType Directory -Force -Path $motionsDir | Out-Null
    $picked = @()
    # 模型：只挑含 .pmx 的子目录（纯贴图、半成品打进去只是白占空间）
    foreach ($dir in Get-ChildItem -LiteralPath $ModelSource -Directory) {
        $hit = Get-ChildItem -LiteralPath $dir.FullName -Recurse -File -Include *.pmx -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $hit) { continue }
        Copy-Item -LiteralPath $dir.FullName -Destination (Join-Path $bundleDir $dir.Name) -Recurse
        $picked += $dir.Name
    }
    # 动作：只按白名单拷（动作目录里常混着 .blend 工程文件，整个拷会白多几百 MB）
    foreach ($pick in $MotionPick) {
        $from = Join-Path $MotionSource $pick
        if (-not (Test-Path -LiteralPath $from)) { throw "动作目录里没有 $pick：$MotionSource" }
        Copy-Item -LiteralPath $from -Destination (Join-Path $motionsDir $pick) -Recurse
        $picked += $pick
    }
    if (-not $picked) { throw "没找到模型或动作：$ModelSource / $MotionSource" }
    Write-Output ("Bundled models: " + ($picked -join ", "))
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "素材声明-必读.md") -Destination (Join-Path $bundleDir "素材声明-必读.md")
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
