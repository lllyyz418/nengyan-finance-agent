param(
    [string]$BackendPython = 'D:\marktext\dcf-agent-env\python.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
if (Test-Path -LiteralPath $bundledNode) { $nodeRuntime = $bundledNode }
else { $nodeRuntime = (Get-Command node -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $BackendPython)) { throw "Python runtime missing: $BackendPython" }
function Read-LocalJson([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 } catch { return $null }
}
function Is-Listening([int]$Port) {
    $probe = [Net.Sockets.TcpClient]::new()
    try { $probe.Connect('127.0.0.1', $Port); return $true } catch { return $false } finally { $probe.Dispose() }
}
$backendRunning = Is-Listening 8001
$frontendRunning = Is-Listening 5173
if ($backendRunning) {
    $health = Read-LocalJson 'http://127.0.0.1:8001/api/health'
    if ($health.announcement_revision -ne 'evidence-agent-1.1.0') {
        throw 'Port 8001 belongs to an older or different service. Stop its original terminal and run this script again. No process was terminated.'
    }
}
if ($frontendRunning) {
    throw 'Port 5173 is already in use. Keep the existing frontend, or close its original terminal before using this compiled launcher. No process was terminated.'
}
Push-Location $projectRoot
try {
    & $nodeRuntime node_modules/typescript/bin/tsc -b
    if ($LASTEXITCODE -ne 0) { throw 'Type checking failed; nothing was started.' }
    & $nodeRuntime scripts/build-upload-error-qa.mjs dist
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; nothing was started.' }
    $runDir = Join-Path $projectRoot ('.local-run\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $runDir | Out-Null
    $started = @()
    if (-not $backendRunning) {
        $backendProcess = Start-Process -FilePath $BackendPython -ArgumentList @('-m','uvicorn','app:app','--host','127.0.0.1','--port','8001') -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir 'backend.out.log') -RedirectStandardError (Join-Path $runDir 'backend.err.log') -PassThru
        $started += [pscustomobject]@{ kind='backend'; pid=$backendProcess.Id; started_at=$backendProcess.StartTime.ToString('o') }
    }
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        $health = Read-LocalJson 'http://127.0.0.1:8001/api/health'
        if ($health.announcement_revision -eq 'evidence-agent-1.1.0') { break }
        Start-Sleep -Milliseconds 500
    }
    if ($health.announcement_revision -ne 'evidence-agent-1.1.0') { throw "Backend did not become ready; inspect $runDir" }
    $frontendProcess = Start-Process -FilePath $nodeRuntime -ArgumentList @('scripts/serve-workbench.mjs') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir 'frontend.out.log') -RedirectStandardError (Join-Path $runDir 'frontend.err.log') -PassThru
    $started += [pscustomobject]@{ kind='frontend'; pid=$frontendProcess.Id; started_at=$frontendProcess.StartTime.ToString('o') }
    $started | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir 'processes.json') -Encoding utf8
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        if (Is-Listening 5173) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Is-Listening 5173)) { throw "Frontend did not become ready; inspect $runDir" }
    Write-Output 'Workbench ready: http://127.0.0.1:5173'
    Write-Output "Logs and owned process IDs: $runDir"
    Write-Output 'This launcher serves dist. Re-run it after future code updates, or use the original Vite development terminal.'
} finally { Pop-Location }
