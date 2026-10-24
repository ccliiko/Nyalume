# Runs outside the install directory, after downloading and verifying an official ZIP.
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$installRoot = [IO.Path]::GetFullPath($plan.root).TrimEnd('\')
$stageRoot = [IO.Path]::GetFullPath($plan.stage).TrimEnd('\')
$payloadRoot = Join-Path $stageRoot 'payload'
$backupRoot = Join-Path $stageRoot 'backup'
$changed = [Collections.Generic.List[object]]::new()

function Assert-Contained([string]$Base, [string]$Relative) {
    $target = [IO.Path]::GetFullPath((Join-Path $Base $Relative))
    if (-not $target.StartsWith($Base + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Update path escapes its directory.'
    }
    $cursor = $target
    while ($cursor -and $cursor.Length -ge $Base.Length) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked update paths are not supported.' }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $target
}

function Write-Result([string]$Message, [string]$Phase) {
    @{ message = $Message } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $installRoot 'update-result.json') -Encoding UTF8
    @{ message = $Message; phase = $Phase } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stageRoot 'result.json') -Encoding UTF8
}

try {
    if ($stageRoot -ne $PSScriptRoot -or $stageRoot -eq $installRoot -or
        -not (Test-Path -LiteralPath (Join-Path $installRoot 'VERSION.txt'))) { throw 'Invalid update directories.' }
    foreach ($relative in $plan.files) {
        $null = Assert-Contained $installRoot $relative
        $source = Assert-Contained $payloadRoot $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Missing staged file.' }
    }
    # Never force-kill a chat or the database writer. All EXEs from this install must exit.
    $deadline = [DateTime]::UtcNow.AddMinutes(30)
    do {
        $running = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($installRoot + '\', [StringComparison]::OrdinalIgnoreCase)
        })
        if (-not $running.Count) { break }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Timed out waiting for Nyalume to exit. Check for updates again.' }
        Start-Sleep -Seconds 1
    } while ($true)

    foreach ($relative in $plan.files) {
        # Re-check links after waiting for exit, immediately before writing.
        $target = Assert-Contained $installRoot $relative
        $source = Assert-Contained $payloadRoot $relative
        $backup = Assert-Contained $backupRoot $relative
        $existed = Test-Path -LiteralPath $target -PathType Leaf
        if ($existed) {
            $null = New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($backup))
            Copy-Item -LiteralPath $target -Destination $backup -Force
        }
        $changed.Add(@{ target = $target; backup = $backup; existed = $existed })
        $null = New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target))
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
    Write-Result '更新完成，本地配置、聊天记录及自备模型已保留。' 'installed'
    if ($plan.restart) {
        if ($plan.restart -notin @('Nyalume.exe', 'Nyalume2D.exe', 'Nyalume3D.exe')) { throw 'Invalid restart entry.' }
        Start-Process -FilePath (Assert-Contained $installRoot $plan.restart) -WorkingDirectory $installRoot -WindowStyle Hidden
    }
    # Backup remains next to the installation for manual recovery; no recursive deletion.
}
catch {
    $failure = $_.Exception.Message
    $rollbackErrors = @()
    for ($index = $changed.Count - 1; $index -ge 0; $index--) {
        $entry = $changed[$index]
        try {
            if ($entry.existed) { Copy-Item -LiteralPath $entry.backup -Destination $entry.target -Force }
            else { Remove-Item -LiteralPath $entry.target -Force }
        }
        catch { $rollbackErrors += $_.Exception.Message }
    }
    $message = "更新失败，请重新检查更新。错误：$failure。备份目录：$backupRoot"
    if ($rollbackErrors.Count) { $message += '；部分文件恢复失败，请从备份目录手动恢复。' }
    Write-Result $message 'error'
    exit 1
}
