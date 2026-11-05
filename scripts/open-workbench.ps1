param(
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskRuntime = Join-Path $taskRoot 'portable/GateSchedule-ExcelConfig-20261029'
$taskExe = Join-Path $taskRuntime 'GateSchedule-ExcelConfig-20261029.exe'
$taskPage = Join-Path $taskRuntime '_internal/web/dist/index.html'
$taskBase = 'http://127.0.0.1:' + $Port
$taskExpectedHash = (Get-FileHash -LiteralPath $taskPage -Algorithm SHA256).Hash.ToLowerInvariant()

function Test-CurrentPage {
    try {
        $taskResponse = Invoke-WebRequest -Uri ($taskBase + '/') -UseBasicParsing -TimeoutSec 2
        $taskHashAlgorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $taskBytes = $taskResponse.RawContentStream.ToArray()
            $taskActualHash = ([BitConverter]::ToString($taskHashAlgorithm.ComputeHash($taskBytes))).Replace('-', '').ToLowerInvariant()
            return $taskActualHash -eq $taskExpectedHash
        } finally {
            $taskHashAlgorithm.Dispose()
        }
    } catch {
        return $false
    }
}

$taskStarted = $null
if (-not (Test-CurrentPage)) {
    $taskSocket = New-Object Net.Sockets.TcpClient
    try {
        try { $taskSocket.Connect('127.0.0.1', $Port) } catch {}
        if ($taskSocket.Connected) { throw 'This port is serving a different page. Close the other application and try again.' }
    } finally {
        $taskSocket.Dispose()
    }
    $taskOldTemp = $env:TEMP
    $taskOldTmp = $env:TMP
    try {
        $taskTemp = Join-Path $taskRoot '.tmp'
        [IO.Directory]::CreateDirectory($taskTemp) | Out-Null
        $env:TEMP = $taskTemp
        $env:TMP = $taskTemp
        $taskStarted = Start-Process -FilePath $taskExe -ArgumentList '--no-browser','--port',([string]$Port) -WorkingDirectory $taskRuntime -WindowStyle Hidden -PassThru
    } finally {
        $env:TEMP = $taskOldTemp
        $env:TMP = $taskOldTmp
    }
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 60; $taskAttempt++) {
        if (Test-CurrentPage) { $taskReady = $true; break }
        if ($taskStarted.HasExited) { throw 'The local scheduling application stopped before it was ready.' }
        Start-Sleep -Milliseconds 250
    }
    if (-not $taskReady) { throw 'The local scheduling application did not become ready. Existing data was not changed by the launcher.' }
}

$taskAddress = $taskBase + '/?v=' + $taskExpectedHash.Substring(0, 12)
if ($VerifyOnly) {
    [pscustomobject]@{ url = $taskAddress; startedProcessId = $(if ($taskStarted) { $taskStarted.Id } else { $null }) } | ConvertTo-Json -Compress
} else {
    Start-Process -FilePath $taskAddress
}
