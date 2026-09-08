$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonEnv = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonEnv)) {
    $bootstrapPython = $null
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython -and $systemPython.Source -notmatch 'WindowsApps') { $bootstrapPython = $systemPython.Source }
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (-not $bootstrapPython -and (Test-Path -LiteralPath $bundledPython)) { $bootstrapPython = $bundledPython }
    if (-not $bootstrapPython) { throw 'Install Python 3.11 or newer, then run this script again.' }
    & $bootstrapPython -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python environment.' }
}
$marker = Join-Path $projectRoot '.venv\yannian-dependencies-ready'
$legacyMarker = Join-Path $projectRoot '.venv\yanji-dependencies-ready'
if (-not (Test-Path -LiteralPath $marker) -and -not (Test-Path -LiteralPath $legacyMarker)) {
    & $pythonEnv -m pip install -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
    Set-Content -LiteralPath $marker -Value '0.6.1'
}
& $pythonEnv (Join-Path $projectRoot 'launch.pyw')
