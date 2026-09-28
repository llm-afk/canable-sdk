# Run from any directory; select a compatible installed Python without changing PATH.
$ErrorActionPreference = 'Stop'
$sdkBuilding = $args.Count -gt 0 -and $args[0] -eq 'build'
$sdkCandidates = @()
if ($env:CANABLE25_PYTHON) { $sdkCandidates += $env:CANABLE25_PYTHON }
$sdkCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($sdkCommand -and $sdkCommand.Source -notlike '*WindowsApps*') { $sdkCandidates += $sdkCommand.Source }
$sdkCandidates += @(
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe')
)
$sdkCheck = 'import sys; assert sys.version_info >= (3,10)'
if ($sdkBuilding) {
    $sdkCheck += '; import setuptools, wheel; assert int(setuptools.__version__.split(".")[0]) >= 77'
}
$sdkPython = $null
foreach ($sdkCandidate in ($sdkCandidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $sdkCandidate)) { continue }
    # A missing/old optional runtime is an expected probe result, not a fatal PS error.
    $sdkOldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $sdkCandidate -c $sdkCheck 2>$null
    $sdkProbeExit = $LASTEXITCODE
    $ErrorActionPreference = $sdkOldPreference
    if ($sdkProbeExit -eq 0) { $sdkPython = $sdkCandidate; break }
}
if (-not $sdkPython) {
    if ($sdkBuilding) { throw 'Build needs Python 3.10+, setuptools>=77 and wheel. Set CANABLE25_PYTHON to an environment containing these tools.' }
    throw 'Python 3.10+ was not found. Install Python or set CANABLE25_PYTHON to python.exe.'
}
$sdkPreviousPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Join-Path $PSScriptRoot 'src') + [IO.Path]::PathSeparator + $sdkPreviousPath
    if ($args.Count -gt 0 -and $args[0] -eq 'test') {
        & $sdkPython -m unittest discover -s (Join-Path $PSScriptRoot 'tests') -v
    } elseif ($sdkBuilding) {
        Write-Host "Build Python: $sdkPython"
        & $sdkPython -m pip --disable-pip-version-check wheel $PSScriptRoot --no-deps --no-build-isolation --wheel-dir (Join-Path $PSScriptRoot 'dist')
    } elseif ($args.Count -gt 0 -and $args[0] -eq 'loopback-suite') {
        $sdkExtraArgs = @()
        if ($args.Count -gt 1) { $sdkExtraArgs = $args[1..($args.Count - 1)] }
        & $sdkPython (Join-Path $PSScriptRoot 'examples\hardware_loopback.py') @sdkExtraArgs
    } else {
        & $sdkPython -m canable25 @args
    }
    $sdkExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $sdkPreviousPath
}
exit $sdkExitCode