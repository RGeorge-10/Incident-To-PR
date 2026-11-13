param([switch]$Visible)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')
Add-ProjectToolPaths
$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) { throw 'Run .\scripts\bootstrap.ps1 first.' }
if (-not (Test-Path (Join-Path $Root '.env'))) { throw 'Missing .env; run bootstrap first.' }
$envFile = Get-Content (Join-Path $Root '.env') | Where-Object { $_ -match '^\s*[^#\s][^=]*=' }
foreach ($line in $envFile) {
    $pair = $line -split '=', 2
    if (-not [Environment]::GetEnvironmentVariable($pair[0].Trim())) { [Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim()) }
}
& (Join-Path $PSScriptRoot 'observer-kubeconfig.ps1')
$env:KUBECONFIG = Join-Path $Root '.local\observer-kubeconfig.json'
$env:K8S_CONTEXT = 'incident-to-pr-observer'
$env:PROMETHEUS_URL = 'http://127.0.0.1:9090'
$env:LOKI_URL = 'http://127.0.0.1:3100'
$env:ARGOCD_URL = 'https://127.0.0.1:8081'
$env:ARGOCD_VERIFY_TLS = 'false'
$env:API_PROXY_TARGET = 'http://127.0.0.1:8000'
$pidDir = Join-Path $Root '.local'
New-Item -ItemType Directory -Force $pidDir | Out-Null
$backendDir = Join-Path $Root 'backend'
$backendArgs = "-m uvicorn app.main:app --app-dir `"$backendDir`" --host 127.0.0.1 --port 8000"
$frontArgs = '/c npm run dev -- --hostname 127.0.0.1 --port 3000'
$apiOut = Join-Path $pidDir 'api.out.log'
$apiErr = Join-Path $pidDir 'api.err.log'
$webOut = Join-Path $pidDir 'web.out.log'
$webErr = Join-Path $pidDir 'web.err.log'
if ($Visible) {
    Start-Process -FilePath $venvPython -ArgumentList $backendArgs -WorkingDirectory $Root -NoNewWindow
    Start-Process -FilePath 'cmd.exe' -ArgumentList $frontArgs -WorkingDirectory (Join-Path $Root 'frontend') -NoNewWindow
} else {
    $api = Start-Process -FilePath $venvPython -ArgumentList $backendArgs -WorkingDirectory $Root -PassThru -WindowStyle Hidden -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr
    $web = Start-Process -FilePath 'cmd.exe' -ArgumentList $frontArgs -WorkingDirectory (Join-Path $Root 'frontend') -PassThru -WindowStyle Hidden -RedirectStandardOutput $webOut -RedirectStandardError $webErr
    Set-Content (Join-Path $pidDir 'api.pid') $api.Id
    Set-Content (Join-Path $pidDir 'web.pid') $web.Id
}
Write-Host 'App starting: http://127.0.0.1:3000 (API docs: http://127.0.0.1:8000/docs).'
