param([switch]$NoBrowser, [switch]$Foreground)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$candidates = @()
if ($env:YANNIAN_PYTHON) {
    $candidates += $env:YANNIAN_PYTHON
} else {
    $candidates += Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython -and $systemPython.Source -notmatch 'WindowsApps') { $candidates += $systemPython.Source }
    $candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$bootstrapPython = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'
        if ($LASTEXITCODE -eq 0) { $bootstrapPython = $candidate; break }
    }
}
if (-not $bootstrapPython) { throw 'Install Python 3.11+ from https://www.python.org/downloads/ and retry.' }
$launchArgs = @((Join-Path $PSScriptRoot 'scripts\bootstrap.py'))
if ($NoBrowser) { $launchArgs += '--no-browser' }
if ($Foreground) { $launchArgs += '--foreground' }
& $bootstrapPython @launchArgs
exit $LASTEXITCODE
