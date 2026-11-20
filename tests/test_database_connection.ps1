$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/upgrade-database.ps1')
foreach ($placeholder in '[YOUR-PASSWORD]','[YOUR-DATABASE-PASSWORD]') {
    $connection = ConvertFrom-GateConnectionUrl ("postgresql://postgres.aaaaaaaaaaaaaaaaaaaa:${placeholder}@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres")
    if ($connection.User -cne 'postgres.aaaaaaaaaaaaaaaaaaaa' -or $connection.Host -cne 'aws-1-ap-northeast-1.pooler.supabase.com') { throw 'Session URL was not parsed correctly.' }
    if ($connection.PSObject.Properties.Name -contains 'Password') { throw 'URL parser must not retain or infer a password.' }
    Write-Host "Passed: $placeholder accepted without a password in the URL"
}
foreach ($url in @(
    'postgresql://postgres.aaaaaaaaaaaaaaaaaaaa:literal-secret@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres',
    'postgresql://postgres.aaaaaaaaaaaaaaaaaaaa:[YOUR-PASSWORD]@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres',
    'postgresql://postgres.aaaaaaaaaaaaaaaaaaaa:[YOUR-PASSWORD]@aws-1-ap-northeast-1.pooler.supabase.com:5432/other'
)) {
    $rejected = $false
    try { ConvertFrom-GateConnectionUrl $url | Out-Null } catch {
        if ($_.Exception.Message -like '*literal-secret*') { throw 'Secret echoed in error.' }
        $rejected = $true
    }
    if (!$rejected) { throw 'Unsupported URL was accepted.' }
}
Write-Host 'Passed: raw passwords, transaction port and wrong database are rejected without exposing input'

$taskState = Join-Path (Split-Path -Parent $PSScriptRoot) ('.tmp/connection-test-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($taskState)
try {
    $connection = ConvertFrom-GateConnectionUrl 'postgresql://postgres.aaaaaaaaaaaaaaaaaaaa:[YOUR-DATABASE-PASSWORD]@aws-1-ap-south-1.pooler.supabase.com:5432/postgres'
    $connection | Add-Member -NotePropertyName Password -NotePropertyValue (ConvertTo-SecureString 'fixture-only' -AsPlainText -Force)
    Set-GateConnection $connection 'aaaaaaaaaaaaaaaaaaaa'
    if ($env:PGSSLMODE -cne 'verify-full') { throw 'TLS hostname verification must remain enabled.' }
    $official = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/certs/supabase-prod-ca-2021.crt'
    if ((Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash -cne '700723581420DD1AC98FD7E9AC529F0EF210EADCAF87FC868A3AD7D114C2F3B7') { throw 'Bundled certificate differs from the official Supabase HTTPS download.' }
    # The file may contain multiple roots: compare decoded PEM blocks instead.
    $roots = [regex]::Matches([IO.File]::ReadAllText($env:PGSSLROOTCERT), '(?s)-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----')
    $found = $false
    foreach ($block in $roots) {
        $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2
        $cert.Import([Convert]::FromBase64String($block.Groups[1].Value))
        if ($cert.Subject -like '*CN=Supabase Root 2021 CA*') { $found = $true }
    }
    if (!$found) { throw 'Official Supabase database CA is missing from the connection trust bundle.' }
    Write-Host 'Passed: connection trusts the official Supabase CA and keeps full hostname verification'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    $certificate = Join-Path $taskState 'windows-trusted-roots.pem'
    if (Test-Path -LiteralPath $certificate) { Remove-Item -LiteralPath $certificate }
    Remove-Item -LiteralPath $taskState
}

# Exercise the actual first-setup path: a mismatching project must be refused
# before asking the user for a password or opening a database connection.
$taskState = Join-Path (Split-Path -Parent $PSScriptRoot) ('.tmp/connection-test-' + [guid]::NewGuid().ToString('N'))
function Get-GateProject { 'aaaaaaaaaaaaaaaaaaaa' }
function Read-Host([string]$Prompt, [switch]$AsSecureString) {
    if ($AsSecureString) { throw 'Password requested before checking project identity.' }
    'postgresql://postgres.bbbbbbbbbbbbbbbbbbbb:[YOUR-DATABASE-PASSWORD]@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres'
}
function Invoke-GateSql([string]$sql) { throw 'Database access attempted with mismatching project.' }
try {
    try { Start-GateUpgrade; throw 'Expected mismatching project rejection.' } catch {
        if ($_.Exception.Message -notlike '*another project*') { throw }
    }
    Write-Host 'Passed: first setup rejects another project before requesting a password or accessing the database'
} finally {
    if (Test-Path -LiteralPath $taskState) { Remove-Item -LiteralPath $taskState }
}
