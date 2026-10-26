param(
    [string]$GameDir = 'E:\SteamLibrary\steamapps\common\Slay the Spire 2',
    [int]$ClientId = 2026102501,
    [int]$ApiPort = 18124
)
$ErrorActionPreference = 'Stop'
$ameathProject = Split-Path -Parent $PSScriptRoot
$ameathOutput = Join-Path $ameathProject '.local\validation'
New-Item -ItemType Directory -Force -Path $ameathOutput | Out-Null
$ameathGame = Join-Path (Resolve-Path -LiteralPath $GameDir).Path 'SlayTheSpire2.exe'
if (Get-Process -Name SlayTheSpire2 -ErrorAction SilentlyContinue) {
    throw 'A game is already running. Close it before the isolated read-only smoke check.'
}
$ameathUserRoot = Join-Path $env:APPDATA 'SlayTheSpire2'
$ameathTestRoot = [IO.Path]::GetFullPath((Join-Path $ameathUserRoot ('default\' + $ClientId)))
$ameathAllowedRoot = [IO.Path]::GetFullPath((Join-Path $ameathUserRoot 'default')) + '\'
if (-not $ameathTestRoot.StartsWith($ameathAllowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Invalid isolated client directory.'
}
$ameathTestSettings = Join-Path $ameathTestRoot 'settings.save'
if (Test-Path -LiteralPath $ameathTestSettings) {
    throw 'The smoke-test client already has settings. Use a new numeric ClientId to preserve it.'
}
New-Item -ItemType Directory -Force -Path $ameathTestRoot | Out-Null
$ameathSettingsJson = @'
{
  "schema_version": 8,
  "mod_settings": {
    "mods_enabled": true,
    "mod_list": [{"id":"STS2AIAgent","is_enabled":true,"source":"mods_directory"}]
  },
  "seen_ea_disclaimer": true,
  "skip_intro_logo": true,
  "fullscreen": false,
  "limit_fps_in_background": false,
  "vsync": "adaptive",
  "window_size": {"X":1280,"Y":720},
  "window_position": {"X":-1,"Y":-1},
  "language": "en",
  "aspect_ratio": "sixteen_by_nine",
  "fps_limit": 30,
  "msaa": 0,
  "resize_windows": true,
  "target_display": 0,
  "volume_master": 0,
  "volume_bgm": 0,
  "volume_sfx": 0,
  "volume_ambience": 0,
  "controller_mapping": {},
  "keyboard_mapping": {},
  "keyboard_only_mapping": {}
}
'@
[IO.File]::WriteAllText($ameathTestSettings, $ameathSettingsJson, (New-Object Text.UTF8Encoding $false))
$env:STS2_API_PORT = [string]$ApiPort
$env:STS2_AGENT_AUTOPLAY = '0'
$env:STS2_AGENT_SETTINGS_PATH = Join-Path $ameathOutput 'mod-settings.json'
Remove-Item Env:STS2_ENABLE_DEBUG_ACTIONS -ErrorAction SilentlyContinue
$ameathTestProcess = $null
try {
    $ameathTestProcess = Start-Process -FilePath $ameathGame -WorkingDirectory $GameDir -ArgumentList @('--headless','--force-steam','off','--clientId',[string]$ClientId) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $ameathOutput 'game-stdout.log') -RedirectStandardError (Join-Path $ameathOutput 'game-stderr.log')
    $ameathReady = $false
    $ameathDeadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $ameathDeadline) {
        Start-Sleep -Seconds 1
        if ($ameathTestProcess.HasExited) { throw 'The isolated game process exited before the bridge became ready.' }
        try {
            $ameathRequest = [Net.HttpWebRequest]::Create("http://127.0.0.1:$ApiPort/health")
            $ameathRequest.Proxy = $null
            $ameathRequest.Timeout = 1000
            $ameathRequest.ReadWriteTimeout = 1000
            $ameathResponse = $ameathRequest.GetResponse()
            try {
                $ameathReader = New-Object IO.StreamReader($ameathResponse.GetResponseStream())
                try { $ameathHealth = $ameathReader.ReadToEnd() | ConvertFrom-Json } finally { $ameathReader.Dispose() }
            } finally { $ameathResponse.Dispose() }
            if ($ameathHealth.ok -eq $true -and $ameathHealth.data.service -eq 'sts2-ai-agent') {
                $ameathReady = $true
                break
            }
        } catch { }
    }
    if (-not $ameathReady) { throw 'The installed bridge did not become ready. See .local/validation game logs.' }
    $ameathFrame = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/decision-snapshot" -TimeoutSec 10
    $ameathReport = [ordered]@{
        connected = [bool]$ameathFrame.ok
        game_version = $ameathHealth.data.game_version
        mod_version = $ameathHealth.data.mod_version
        compatibility = $ameathHealth.data.status
        reflected_members_missing = $ameathHealth.data.compatibility.reflected_members_missing
        screen = $ameathFrame.data.state.screen
        isolated_client_id = $ClientId
        model_calls = 0
        game_actions = 0
    }
    $ameathReport | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ameathOutput 'game-bridge-smoke.json') -Encoding UTF8
    $ameathReport | ConvertTo-Json -Compress
} finally {
    if ($ameathTestProcess -and -not $ameathTestProcess.HasExited) {
        Stop-Process -Id $ameathTestProcess.Id
    }
}
