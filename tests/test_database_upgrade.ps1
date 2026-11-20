param([Parameter(Mandatory)][int]$Port, [Parameter(Mandatory)][string]$FixtureDirectory, [Parameter(Mandatory)][string]$FixtureId)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'scripts/upgrade-database.ps1')
function Assert-Gate($condition, [string]$label) {
    if (!$condition) { throw "Failed: $label" }
    Write-Host "Passed: $label"
}
Get-ChildItem Env:PG* | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
$env:PGHOST='127.0.0.1'; $env:PGPORT=[string]$Port; $env:PGUSER='postgres'
$env:PGDATABASE='postgres'; $env:PGSSLMODE='disable'; $env:PGCLIENTENCODING='UTF8'
$fixture=(Resolve-Path -LiteralPath $FixtureDirectory).ProviderPath
if (!$fixture.StartsWith((Join-Path $repo '.tmp/'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Expected repository temporary fixture.' }
if ($FixtureId -notmatch '^[a-f0-9]{32}$' -or (Invoke-GateSql "select current_setting('gate.fixture_id',true);") -cne $FixtureId) { throw 'Expected isolated PostgreSQL fixture token.' }
$taskRoot=Join-Path $fixture 'checkout'
$taskTemp=Join-Path $fixture 'queries'
$taskState=Join-Path $fixture 'state'
[void][IO.Directory]::CreateDirectory((Join-Path $taskRoot 'supabase'))
$migration=Join-Path $repo 'supabase/202611190001_login_approval.sql'
$target=Join-Path $taskRoot 'supabase/202611190001_login_approval.sql'
$auth='create schema auth; create table auth.users(id uuid primary key); create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting(''request.jwt.claim.sub'',true),'''')::uuid$$; insert into auth.users values(''11111111-1111-4111-8111-111111111111'');'
Invoke-GateSql ('create role anon; create role authenticated; ' + $auth)
Invoke-GatePg 'psql' @('-X','-w','-q','-v','ON_ERROR_STOP=1','-f',(Join-Path $repo 'supabase/workspace.sql')) | Out-Null
Invoke-GateSql 'insert into public.gate_admin values(true,''11111111-1111-4111-8111-111111111111''); insert into public.gate_workspace values(true,42,''{"fixture":true}'',now());'
$before=Get-GateFingerprint
Assert-Gate (!(Test-GateUpgrade)) 'old database needs upgrade'

$sql=[IO.File]::ReadAllText($migration)
[IO.File]::WriteAllText($target,$sql.Replace('commit;','select 1/0; commit;'),$taskUtf8)
try { Invoke-GateUpgrade | Out-Null; throw 'Expected rollback' } catch { if ($_.Exception.Message -notlike '*psql failed*') { throw } }
Assert-Gate ((Get-GateFingerprint) -ceq $before -and (Invoke-GateSql "select to_regclass('public.gate_editor_session') is null;") -eq 't') 'failed update rolls back schema and preserves original data'

[IO.File]::WriteAllText($target,$sql,$taskUtf8)
$result=Invoke-GateUpgrade
Assert-Gate ($result.Applied -and $result.Verified -and $result.DataUnchangedAtCheck) 'successful migration and full permissions check'
Assert-Gate ((Get-GateFingerprint) -ceq $before) 'workspace and administrator unchanged'
$again=Invoke-GateUpgrade
Assert-Gate (!$again.Applied -and $again.Verified -and !$again.Backup) 'repeat is a no-op'

Invoke-GateSql 'create table public.autoschedule_workspace(id int);'
try { Invoke-GateUpgrade | Out-Null; throw 'Expected wrong project rejection' } catch { if ($_.Exception.Message -notlike '*dedicated gate database*') { throw } }
Invoke-GateSql 'drop table public.autoschedule_workspace;'
Assert-Gate ((Get-GateFingerprint) -ceq $before) 'check-in project marker prevents changes'
try { Set-GateConnection ([pscustomobject]@{Host='db.wrong.supabase.co';User='postgres';Password='unused'}) 'aaaaaaaaaaaaaaaaaaaa'; throw 'Expected connection rejection' } catch { if ($_.Exception.Message -notlike '*another project*') { throw } }
Write-Host 'Passed: mismatching saved connection rejected before password use'

Invoke-GateSql 'create database gate_restore;'
$env:PGDATABASE='gate_restore'
Invoke-GateSql $auth
$dump=Join-Path $result.Backup 'public-before.dump'
$list=Join-Path $fixture 'restore-list.txt'
$entries=@(Invoke-GatePg 'pg_restore' @('--list',$dump) | Where-Object { $_ -notmatch ' SCHEMA - public ' })
[IO.File]::WriteAllLines($list,[string[]]$entries,$taskUtf8)
Invoke-GatePg 'pg_restore' @('-w','--exit-on-error','--no-owner','--dbname=gate_restore','--use-list',$list,$dump) | Out-Null
Assert-Gate ((Get-GateFingerprint) -ceq $before -and !(Test-GateUpgrade)) 'independent restore recovers pre-update schema and original data'
