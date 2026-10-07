param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) {
    $dockerPath = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dockerPath) { throw 'Docker CLI not found. Start Docker Desktop and add its resources/bin directory to PATH.' }
} else { $dockerPath = $dockerCommand.Source }
$previousPath = $env:PATH
$env:PATH = "$(Split-Path -Parent $dockerPath);$env:PATH"
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.env.web')) {
        if ($Stop) { throw '.env.web does not exist.' }
        $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $bytes = New-Object byte[] 32
            $random.GetBytes($bytes)
            $databaseSecret = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
            $random.GetBytes($bytes)
            $tokenSecret = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
        } finally { $random.Dispose() }
        $config = [IO.File]::ReadAllText((Join-Path $projectRoot '.env.web.example'))
        $config = $config.Replace('replace_with_64_random_hex_characters', $databaseSecret).Replace('replace_with_another_64_random_hex_characters', $tokenSecret)
        [IO.File]::WriteAllText((Join-Path $projectRoot '.env.web'), $config, (New-Object Text.UTF8Encoding $false))
    }
    if ($Stop) {
        & $dockerPath compose --env-file .env.web -f compose.web.yaml stop
    } else {
        & $dockerPath compose --env-file .env.web -f compose.web.yaml up -d --build --wait --wait-timeout 180
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE" }
    if (-not $Stop) { Write-Host 'Web preview is running. Default local URL: http://localhost:8088' }
} finally {
    Pop-Location
    $env:PATH = $previousPath
}
