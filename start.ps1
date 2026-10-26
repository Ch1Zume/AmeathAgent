param([switch]$Game)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$ameathTemporary = Join-Path $PSScriptRoot '.tmp'
New-Item -ItemType Directory -Force -Path $ameathTemporary | Out-Null
$env:TEMP = $ameathTemporary
$env:TMP = $ameathTemporary
$ameathPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ameathPython)) {
    & python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment. Install Python 3.11 or newer.' }
}
$ErrorActionPreference = 'Continue'
& $ameathPython -c 'import PySide6.QtWidgets, pymupdf' *> $null
$ameathImportStatus = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($ameathImportStatus -ne 0) {
    & $ameathPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the dependencies. Please check the network.' }
}
$ameathData = Join-Path $PSScriptRoot '.local'
New-Item -ItemType Directory -Force -Path $ameathData | Out-Null
$ameathGuiPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$ameathEntry = Join-Path $PSScriptRoot 'main.py'
$ameathLaunchArguments = @(('"{0}"' -f $ameathEntry))
if ($Game) { $ameathLaunchArguments += '--game' }
Start-Process -FilePath $ameathGuiPython -ArgumentList $ameathLaunchArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardError (Join-Path $ameathData 'startup.log') | Out-Null
