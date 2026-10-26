<#
  Install this skill into a DSH skills directory.

    .\install.ps1                     # -> $env:DSH_HOME\skills  (or ~/.dsh-home\skills)
    .\install.ps1 -Dest D:\my\skills  # explicit target directory
    .\install.ps1 -SkipDeps           # do not run pip

  Restart DSH afterwards; the skill appears as 'generic-pixel-svg-rebuild'.
#>
param(
    [string]$Dest = '',
    [switch]$SkipDeps
)
$ErrorActionPreference = 'Stop'

$src = $PSScriptRoot
if (-not $Dest) {
    $base = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $HOME '.dsh-home' }
    $Dest = Join-Path $base 'skills'
}
$target = Join-Path $Dest 'generic-pixel-svg-rebuild'

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item $src $target -Recurse
Get-ChildItem $target -Recurse -Directory -Filter '__pycache__' |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ('installed -> ' + $target)

if (-not $SkipDeps) {
    Write-Host 'installing Python dependencies ...'
    python -m pip install --quiet --disable-pip-version-check -r (Join-Path $target 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'pip failed; run: python -m pip install -r requirements.txt' }
}

python -c 'import numpy, PIL, scipy, potrace; print("deps ok:", numpy.__version__, scipy.__version__)'
Write-Host ''
Write-Host 'Try it:'
Write-Host ('  python "' + $target + '\tests\run_tests.py"')
Write-Host ('  python "' + $target + '\scripts\vectorize.py" ref.png out.html 192')