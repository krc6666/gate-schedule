param(
    [string]$OutputDirectory = "portable",
    [string]$ApplicationName = "",
    [string]$PythonExecutable = ".venv-local/Scripts/python.exe"
)

$ErrorActionPreference = "Stop"
$buildStamp = Get-Date -Format "yyyyMMdd-HHmmss"
if ([string]::IsNullOrWhiteSpace($ApplicationName)) {
    $ApplicationName = "GateSchedule-$buildStamp"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$projectTemp = Join-Path $projectRoot '.tmp'
[IO.Directory]::CreateDirectory($projectTemp) | Out-Null
$env:TEMP = $projectTemp
$env:TMP = $projectTemp
Push-Location $projectRoot
try {
    $applicationDirectory = Join-Path $OutputDirectory $ApplicationName
    if (Test-Path $applicationDirectory) {
        throw "Refusing to replace existing package directory '$applicationDirectory'. Choose a new application name or output directory."
    }
    if ([IO.Path]::GetFileName($ApplicationName) -ne $ApplicationName -or $ApplicationName -in @('.', '..')) { throw "Invalid application name" }
    if (!(Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw "Python not found: $PythonExecutable. Follow README to create .venv-local." }
    $buildDirectory = Join-Path "build" $ApplicationName
    if (Test-Path -LiteralPath $buildDirectory) { throw "Build directory already exists: $buildDirectory" }
    npm.cmd run build --prefix web
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
    & $PythonExecutable -m PyInstaller --noconfirm --onedir --name $ApplicationName --add-data "$projectRoot\web\dist;web\dist" --add-data "$projectRoot\examples;examples" --collect-all ortools --distpath $OutputDirectory --workpath $buildDirectory --specpath $buildDirectory "$projectRoot\portable_entry.py"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    if (Test-Path -LiteralPath 'templates') {
        Copy-Item -LiteralPath 'templates' -Destination (Join-Path $applicationDirectory 'templates') -Recurse
    }
    $sourceFiles = @(Get-ChildItem gate_schedule -File -Filter *.py; Get-ChildItem web/src -File; Get-ChildItem web/dist -File -Recurse; Get-Item requirements-lock.txt, pyproject.toml, portable_entry.py; if (Test-Path -LiteralPath 'templates') { Get-ChildItem templates -File })
    $manifest = [ordered]@{builtAt=(Get-Date -Format o); python=(& $PythonExecutable --version); files=@($sourceFiles | ForEach-Object { @{path=$_.FullName.Substring($projectRoot.Length + 1); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} }); executableSha256=(Get-FileHash -LiteralPath (Join-Path $applicationDirectory "$ApplicationName.exe") -Algorithm SHA256).Hash}
    [IO.File]::WriteAllText((Join-Path $projectRoot (Join-Path $applicationDirectory "build-manifest.json")), ($manifest | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
