param(
    [string]$GameDir = 'E:\SteamLibrary\steamapps\common\Slay the Spire 2',
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$ameathProject = Split-Path -Parent $PSScriptRoot
$ameathManifest = Get-Content -LiteralPath (Join-Path $ameathProject 'integrations\sts2\bridge-release.json') -Raw | ConvertFrom-Json
$ameathGameRoot = (Resolve-Path -LiteralPath $GameDir).Path.TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $ameathGameRoot 'SlayTheSpire2.exe'))) {
    throw 'The selected directory is not a Slay the Spire 2 installation.'
}
$ameathGameVersion = (Get-Content -LiteralPath (Join-Path $ameathGameRoot 'release_info.json') -Raw | ConvertFrom-Json).version
if ($ameathGameVersion -ne 'v0.111.0') {
    throw "This staged bridge was selected for v0.111.0. Detected: $ameathGameVersion. Recheck compatibility first."
}
$ameathMods = [IO.Path]::GetFullPath((Join-Path $ameathGameRoot 'mods'))
$ameathWorkshop = Join-Path (Split-Path -Parent (Split-Path -Parent $ameathGameRoot)) 'workshop\content\2868840\3796486050'
if (Test-Path -LiteralPath $ameathWorkshop) {
    throw 'A Workshop copy of STS2 AI Agent is present. Keep one installation channel to avoid duplicate loading.'
}
$ameathFiles = @('STS2AIAgent.dll', 'STS2AIAgent.pck', 'mod_id.json')
foreach ($ameathName in $ameathFiles) {
    $ameathSource = Join-Path $ameathProject ('integrations\sts2\mod\' + $ameathName)
    $ameathExpected = $ameathManifest.files.$ameathName
    if ((Get-FileHash -LiteralPath $ameathSource -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ameathExpected) {
        throw "Staged checksum mismatch: $ameathName"
    }
    $ameathTarget = [IO.Path]::GetFullPath((Join-Path $ameathMods $ameathName))
    if (-not $ameathTarget.StartsWith($ameathMods + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The resolved destination is outside the game mods directory.'
    }
    if (Test-Path -LiteralPath $ameathTarget) {
        if ((Get-FileHash -LiteralPath $ameathTarget -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ameathExpected) {
            throw "An existing different file would be overwritten: $ameathName. Back it up before installing."
        }
    }
}
if ($ValidateOnly) {
    Write-Output "Validated bridge $($ameathManifest.version) for $ameathGameVersion at $ameathMods"
    exit 0
}
if (Get-Process -Name SlayTheSpire2 -ErrorAction SilentlyContinue) {
    throw 'Close Slay the Spire 2 before installing the bridge.'
}
New-Item -ItemType Directory -Force -Path $ameathMods | Out-Null
foreach ($ameathName in $ameathFiles) {
    Copy-Item -LiteralPath (Join-Path $ameathProject ('integrations\sts2\mod\' + $ameathName)) -Destination (Join-Path $ameathMods $ameathName)
}
Write-Output "Installed the three verified bridge files into $ameathMods. Enable the mod using Steam Play with Mods."
