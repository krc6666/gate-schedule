param([switch]$Configure, [switch]$CheckOnly, [switch]$Pause)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskState = Join-Path $taskRoot 'data/database-tools'
$taskTemp = Join-Path $taskRoot '.tmp/database-tools'
# Reuse the installed PostgreSQL client, never the check-in project's migrations.
$taskBin = Join-Path $env:LOCALAPPDATA 'AutoscheduleDatabaseTools/pgsql/bin'
$taskUtf8 = New-Object Text.UTF8Encoding($false)

function Invoke-GatePg([string]$tool, [string[]]$arguments) {
    $output = & (Join-Path $taskBin "$tool.exe") @arguments
    if ($LASTEXITCODE -ne 0) { throw "$tool failed (exit $LASTEXITCODE)." }
    $output
}

function Invoke-GateSql([string]$sql) {
    [void][IO.Directory]::CreateDirectory($taskTemp)
    $file = Join-Path $taskTemp ([guid]::NewGuid().ToString('N') + '.sql')
    try {
        [IO.File]::WriteAllText($file, $sql, $taskUtf8)
        Invoke-GatePg 'psql' @('-X', '-w', '-qAt', '-v', 'ON_ERROR_STOP=1', '-f', $file)
    } finally {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
    }
}

function Get-GateProject {
    $line = Get-Content -LiteralPath (Join-Path $taskRoot 'web/.env.local') -Encoding UTF8 |
        Where-Object { $_ -match '^\s*VITE_SUPABASE_URL\s*=' } | Select-Object -Last 1
    if ($line -notmatch 'https://([a-z0-9]{20})\.supabase\.co/?["'']?\s*$') { throw 'Cannot identify the gate Supabase project in web/.env.local.' }
    $Matches[1]
}

function ConvertFrom-GateConnectionUrl([string]$uri) {
    if ($uri.Trim() -notmatch '^postgres(?:ql)?://([^:@/]+):\[YOUR-(?:DATABASE-)?PASSWORD\]@([a-z0-9.-]+):5432/postgres/?$') {
        throw 'Use the Session pooler address with [YOUR-PASSWORD] or [YOUR-DATABASE-PASSWORD].'
    }
    [pscustomobject]@{ User=$Matches[1]; Host=$Matches[2] }
}

function Assert-GateConnectionProject($connection, [string]$project) {
    $pooler = $connection.Host -match '^aws-[0-9]+-[a-z0-9-]+\.pooler\.supabase\.com$' -and $connection.User -ceq "postgres.$project"
    $direct = $connection.Host -ceq "db.$project.supabase.co" -and $connection.User -ceq 'postgres'
    if (!$pooler -and !$direct) { throw "Connection belongs to another project or is not a session connection. Expected gate project: $project." }
}

function Set-GateConnection($connection, [string]$project) {
    Assert-GateConnectionProject $connection $project
    Get-ChildItem Env:PG* | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    $env:PGHOST = $connection.Host
    $env:PGUSER = $connection.User
    $env:PGPORT = '5432'
    $env:PGDATABASE = 'postgres'
    $env:PGSSLMODE = 'verify-full'
    $certificate = Join-Path $taskState 'windows-trusted-roots.pem'
    $roots = @(Get-ChildItem 'Cert:/LocalMachine/Root', 'Cert:/CurrentUser/Root' | Sort-Object Thumbprint -Unique)
    $pem = foreach ($cert in $roots) {
        '-----BEGIN CERTIFICATE-----'
        [Convert]::ToBase64String($cert.RawData, [Base64FormattingOptions]::InsertLineBreaks)
        '-----END CERTIFICATE-----'
    }
    if (!$pem) { throw 'No trusted root certificates were found.' }
    # Supabase uses its own CA, not a public Windows trust-store root.
    # Official dashboard source: apps/studio/hooks/custom-content/custom-content.json
    # https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
    $supabaseCa = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'certs/supabase-prod-ca-2021.crt'))
    [IO.File]::WriteAllLines($certificate, [string[]](@($pem) + $supabaseCa), $taskUtf8)
    $env:PGSSLROOTCERT = $certificate
    $env:PGCONNECT_TIMEOUT = '12'
    $env:PGCLIENTENCODING = 'UTF8'
    $env:PGPASSWORD = [Net.NetworkCredential]::new('', $connection.Password).Password
}

function Get-GateFingerprint {
    Invoke-GateSql "select md5(jsonb_build_object('workspace',(select jsonb_agg(to_jsonb(w) order by singleton) from public.gate_workspace w),'admin',(select jsonb_agg(to_jsonb(a) order by singleton) from public.gate_admin a))::text);"
}

function Test-GateUpgrade {
    $sql = @'
select count(*) = 9 and bool_and(
  coalesce(has_function_privilege('authenticated',to_regprocedure(signature),'execute'),false)
  and not coalesce(has_function_privilege('anon',to_regprocedure(signature),'execute'),true))
  and not has_function_privilege('authenticated','public.gate_read_workspace()','execute')
  and not has_function_privilege('authenticated','public.gate_save_workspace(bigint,jsonb)','execute')
from unnest(array['public.gate_acquire_session(text)','public.gate_touch_session(text)',
'public.gate_release_session(text)','public.gate_poll_login(text,text)',
'public.gate_request_login(text,text)','public.gate_decide_login(text,text,boolean)',
'public.gate_cancel_login(text,text)','public.gate_read_workspace_session(text)',
'public.gate_save_workspace_session(text,bigint,jsonb)']) signature;
'@
    (Invoke-GateSql $sql) -eq 't'
}

function Invoke-GateUpgrade {
    $expected = Invoke-GateSql "select to_regclass('public.gate_workspace') is not null and to_regclass('public.gate_admin') is not null and to_regclass('public.autoschedule_workspace') is null;"
    if ($expected -ne 't') { throw 'Expected the dedicated gate database; no changes made.' }
    if (Test-GateUpgrade) { return [pscustomobject]@{ Applied = $false; Verified = $true } }
    if ($CheckOnly) { throw 'The gate login upgrade has not been installed.' }
    $backup = Join-Path $taskRoot ('backups/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-gate-cloud-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    [void][IO.Directory]::CreateDirectory($backup)
    Write-Host 'Backing up the gate cloud database...'
    $dump = Join-Path $backup 'public-before.dump'
    $before = Get-GateFingerprint
    Invoke-GatePg 'pg_dump' @('-w', '--format=custom', '--schema=public', '--lock-wait-timeout=10s', '--file', ($dump + '.partial')) | Out-Null
    $entries = @(Invoke-GatePg 'pg_restore' @('--list', ($dump + '.partial')))
    foreach ($name in 'gate_workspace','gate_admin') {
        if (!($entries -cmatch (' TABLE DATA public ' + $name + ' '))) { throw "Backup missing $name; no changes made." }
    }
    Invoke-GatePg 'pg_restore' @('--file=NUL', ($dump + '.partial')) | Out-Null
    Move-Item -LiteralPath ($dump + '.partial') -Destination $dump
    [IO.File]::WriteAllText(($dump + '.sha256'), (Get-FileHash -LiteralPath $dump -Algorithm SHA256).Hash, $taskUtf8)
    $source = Join-Path $taskRoot 'supabase/202611190001_login_approval.sql'
    Copy-Item -LiteralPath $source -Destination (Join-Path $backup 'applied.sql')
    $sql = [IO.File]::ReadAllText($source)
    $outer = [regex]::Match($sql, '(?ims)\A(?:\s|--[^\r\n]*\r?\n)*begin;\s*(?<body>.*?)\s*commit;\s*\z')
    if (!$outer.Success) { throw 'Expected one transactional gate migration.' }
    # Lock and compare inside the same transaction: concurrent business writes
    # stop the upgrade; migration errors roll back without restoring old data.
    $guard = "if (select md5(jsonb_build_object('workspace',(select jsonb_agg(to_jsonb(w) order by singleton) from public.gate_workspace w),'admin',(select jsonb_agg(to_jsonb(a) order by singleton) from public.gate_admin a))::text)) <> '$before' then raise exception 'Workspace changed; upgrade stopped'; end if;"
    $check = 'do $gate_check$ begin ' + $guard + ' end $gate_check$;'
    $transaction = @('begin;', "set local lock_timeout='10s';", 'select pg_advisory_xact_lock(214722445,218117302);',
        'lock table public.gate_workspace,public.gate_admin in share mode;', $check, $outer.Groups['body'].Value, $check, 'commit;') -join "`n"
    Write-Host 'Installing the gate update and verifying existing data...'
    Invoke-GateSql $transaction | Out-Null
    if (!(Test-GateUpgrade)) { throw 'Upgrade permissions verification failed. Backup retained.' }
    $same = (Get-GateFingerprint) -ceq $before
    $result = [ordered]@{ Applied = $true; Verified = $true; DataUnchangedAtCheck = $same; Backup = $backup; MigrationSha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash }
    [IO.File]::WriteAllText((Join-Path $backup 'installation-check.json'), ($result | ConvertTo-Json), $taskUtf8)
    [pscustomobject]$result
}

function Start-GateUpgrade {
    foreach ($tool in 'psql.exe','pg_dump.exe','pg_restore.exe') {
        if (!(Test-Path -LiteralPath (Join-Path $taskBin $tool))) { throw 'PostgreSQL client missing. Install the client used by the desktop database tool first.' }
    }
    $project = Get-GateProject
    [void][IO.Directory]::CreateDirectory($taskState)
    $saved = Join-Path $taskState ($project + '.connection.xml')
    $new = $Configure -or !(Test-Path -LiteralPath $saved)
    if ($new) {
        if ($CheckOnly) { throw 'No saved gate database connection. Run without -CheckOnly to configure once.' }
        Write-Host 'First setup for GATE SCHEDULE. Open its Supabase project: Connect > Session pooler. Settings will be reused.'
        Write-Host "Gate project: https://supabase.com/dashboard/project/$project"
        $uri = Read-Host 'Paste the connection URL with [YOUR-PASSWORD] or [YOUR-DATABASE-PASSWORD] placeholder'
        $connection = ConvertFrom-GateConnectionUrl $uri
        Assert-GateConnectionProject $connection $project
        $connection | Add-Member -NotePropertyName Password -NotePropertyValue (Read-Host 'Gate database password (hidden)' -AsSecureString)
    } else { $connection = Import-Clixml -LiteralPath $saved }
    try {
        Set-GateConnection $connection $project
        if ((Invoke-GateSql 'select 1;') -ne '1') { throw 'Database connection verification failed.' }
        if ($new) { $connection | Export-Clixml -LiteralPath $saved }
        $result = Invoke-GateUpgrade
        Write-Host 'Gate database update verified.'
        $result
    } finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Start-GateUpgrade }
    catch { Write-Host ('Update stopped: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
    finally { if ($Pause) { [void](Read-Host 'Press Enter to close') } }
}
