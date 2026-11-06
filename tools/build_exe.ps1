# Build the Windows one-folder app and its release zip.
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
# Output: dist\AmeathAgent\AmeathAgent.exe and dist\AmeathAgent-<version>-win64.zip(.sha256)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Install Python 3.11 or newer.' }
}
& $python -m pip install --disable-pip-version-check -r requirements.txt 'pyinstaller>=6.10,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $python -m PyInstaller packaging\AmeathAgent.spec --noconfirm --clean --distpath dist --workpath .tmp\pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
& $python tools\package_release.py dist\AmeathAgent --out dist
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
Write-Host 'Built dist\AmeathAgent\AmeathAgent.exe'
