# CSDM Windows Standalone Rendering POC

**This is a staged procedure, not an instruction to render now.** No CS2/CSDM process was started and no database was changed during this planning task. Execute the rendering stage only after the user explicitly approves the separate Windows run. Do not run these commands in the Mac project environment.

The prior Windows reports contain app setup and data-transfer instructions. Use a fresh Windows AutoClip home such as `D:\AutoClipData-CSDM-POC`; keep the original Mac checkout, Mac database, Mac keychain and Mac media unchanged. Use the same `.dem` bytes in AutoClip and CSDM. CSDM analysis writes CSDM’s own database and is a required separate prerequisite; it must not alter AutoClip’s analysis.

## POC target and immutable AutoClip evidence

| Field | Value |
|---|---|
| Player | ThakurShaaab |
| Selected player key | `steamid:76561198865074734` |
| CSDM target option | `--focus-player 76561198865074734` |
| Round label | 15 (verification metadata; direct tick-range CLI does not take an AutoClip round ID) |
| AutoClip start tick | `89511` |
| Clutch-state transition | `90104` |
| AutoClip end/last kill tick | `90345` |
| Expected kills | tick `89511`: Buug_11, Deagle; tick `90345`: Keepup-_, Deagle |
| Existing AutoClip tags/score | `2K`, `multi_kill`, `clutch_won`, `clutch_1v1`, `round_winning_elimination`; 53 |

These are current cached AutoClip analysis values, not visual ground truth. Do not change the stored AutoClip score/tags/event bounds for this experiment. Do not use CSDM `--mode player --steamids ... --event kills`: that asks CSDM to create event-based intervals and no longer tests AutoClip’s chosen `89511–90345` range. The clutch transition is evidence to inspect in the footage, not a separate CSDM input field.

## Stage A — Windows CLI capability preflight (no rendering)

Run in PowerShell on Windows after CSDM is already installed. These commands check PATH/version/help only; they do not install CSDM or launch CS2. If `csdm` is not found, stop and follow CSDM’s install process separately; do not install during this POC stage without approval.

```powershell
$ErrorActionPreference = 'Stop'
$Evidence = 'D:\AutoClipData-CSDM-POC\poc-evidence'
New-Item -ItemType Directory -Force $Evidence | Out-Null

$CsdmCommand = Get-Command csdm -ErrorAction Stop
$CsdmCommand.Source
csdm --version 2>&1 | Tee-Object (Join-Path $Evidence 'csdm-version.txt')
csdm --help 2>&1 | Tee-Object (Join-Path $Evidence 'csdm-help.txt')

$VideoHelp = (csdm video --help 2>&1 | Out-String)
$VideoHelp | Set-Content -Encoding utf8 (Join-Path $Evidence 'csdm-video-help.txt')
$AnalyzeHelp = (csdm analyze --help 2>&1 | Out-String)
$AnalyzeHelp | Set-Content -Encoding utf8 (Join-Path $Evidence 'csdm-analyze-help.txt')

$RequiredVideoOptions = @(
  '<demo-path>', '<start-tick>', '<end-tick>',
  '--focus-player', '--record-audio', '--recording-system',
  '--recording-output', '--encoder-software', '--framerate',
  '--width', '--height', '--ffmpeg-video-container',
  '--ffmpeg-video-codec', '--ffmpeg-audio-codec',
  '--ffmpeg-executable-path', '--output', '--output-file-name',
  '--no-show-only-death-notices', '--close-game-after-recording'
)
$MissingVideoOptions = @($RequiredVideoOptions | Where-Object { $VideoHelp -notmatch [regex]::Escape($_) })
if ($MissingVideoOptions.Count -gt 0) {
  throw "Installed CSDM help is missing expected documented video options: $($MissingVideoOptions -join ', '). Stop and reconcile the installed version; do not guess alternate flags."
}

if ($AnalyzeHelp -notmatch [regex]::Escape('--source') -or $AnalyzeHelp -notmatch [regex]::Escape('--json')) {
  throw 'Installed CSDM analyze help does not show documented --source/--json options. Stop and review its help.'
}

Write-Host 'CLI preflight passed. No demo analysis or render was submitted.'
```

The spelling above is verified in official docs and the inspected v3.21.1 CLI source. Still require the installed Windows `--help` output to confirm it before execution. CLI help is not proof that the renderer can load this demo or produce correct video.

Check installed tools and paths without changing configuration:

```powershell
Get-Command ffmpeg.exe -ErrorAction Stop | Select-Object Source
Get-Command ffprobe.exe -ErrorAction Stop | Select-Object Source
Get-Command csdm.exe -ErrorAction Stop | Select-Object Source
ffmpeg -hide_banner -version
ffmpeg -hide_banner -encoders | Select-String 'libx264|aac'
```

CSDM docs say video generation can automatically download missing HLAE/FFmpeg, and `csdm analyze`/`video` share its daemon and database. The render command below explicitly chooses the built-in `CS` recording method, so it should not request HLAE, and passes a verified FFmpeg executable path so the CLI’s missing-FFmpeg auto-install path is skipped. If the Windows help differs, or the FFmpeg binary/path/build is not already installed and verified, stop; do not allow an implicit dependency download.

## Stage B — Prepare a request manifest (no database write, no render)

Set these variables to the actual Windows demo copy and fresh AutoClip root. No fixed Mac absolute path belongs in this command. `Test-Path` and `Get-FileHash` are read-only. Compare the SHA-256 with AutoClip’s `demo_sha256` before proceeding.

```powershell
$ErrorActionPreference = 'Stop'
$Demo = 'D:\AutoClipData-CSDM-POC\media\demos\73fa424473544f40\source.dem'
$AutoClipHome = 'D:\AutoClipData-CSDM-POC'
$OutputRoot = Join-Path $env:USERPROFILE 'Videos\AutoClip-CSDM-POC\attempt-01'
$Evidence = Join-Path $AutoClipHome 'poc-evidence'
$SteamId64 = '76561198865074734'
$StartTick = 89511
$EndTick = 90345
$Ffmpeg = (Get-Command ffmpeg.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $Demo -PathType Leaf)) { throw "Demo missing: $Demo" }
if ((Get-Item -LiteralPath $Demo).Extension -ne '.dem') { throw 'Use an uncompressed .dem file, not .dem.zst.' }
if (Test-Path -LiteralPath $OutputRoot) { throw "Output folder already exists; choose a new attempt path: $OutputRoot" }
if (-not (Test-Path -LiteralPath $Ffmpeg -PathType Leaf)) { throw "FFmpeg missing: $Ffmpeg" }

Get-FileHash -Algorithm SHA256 -LiteralPath $Demo | Format-List | Out-File (Join-Path $Evidence 'demo-sha256.txt')
Get-Item -LiteralPath $Demo | Select-Object FullName, Length, LastWriteTimeUtc | Format-List | Out-File (Join-Path $Evidence 'demo-file.txt')
Write-Host "Demo: $Demo"
Write-Host "FFmpeg: $Ffmpeg"
Write-Host "Render window: $StartTick-$EndTick"
```

**Stop if the Windows SHA-256 differs from AutoClip’s parsed `demo_sha256`.** Do not run CSDM analysis on a different file. Check free space on both the CS2 installation volume and `$OutputRoot`’s volume before the approved render. CS2’s built-in `startmovie` route creates temporary TGA frames in the game installation area plus WAV audio; a short 1080p60 interval can still need multiple GB. Use CSDM’s own estimate if available in its UI, add safety margin, and stop if the game drive lacks space. Do not assume the output volume is the temporary-frame volume.

For later output collection, create the output root only in the approved execution stage:

```powershell
New-Item -ItemType Directory -Force $OutputRoot | Out-Null
```

## Stage C — Separately approved CSDM demo analysis prerequisite

**Do not run this step as part of the current planning task.** It writes CSDM’s own demo/match/player data to CSDM’s SQLite database; it does not write AutoClip’s database. CSDM’s official CLI guide says demos must be analyzed and present in its database before `video` generation, and it lists `faceit` as an analyzer source. Use a fresh Windows CSDM database for the POC if possible; do not import the Mac CSDM or AutoClip database.

After explicit approval to make the CSDM prerequisite DB write:

```powershell
$AnalyzeStdout = & csdm analyze $Demo --source faceit --json 2> (Join-Path $Evidence 'csdm-analyze-stderr.log')
$AnalyzeExit = $LASTEXITCODE
$AnalyzeStdout | Set-Content -Encoding utf8 (Join-Path $Evidence 'csdm-analyze-result.json')
if ($AnalyzeExit -ne 0) { throw "CSDM analyze failed with exit code $AnalyzeExit. Stop before video generation." }
```

Inspect the CSDM result/roster and confirm the same demo is present and `76561198865074734` resolves to ThakurShaaab. The direct-range source uses CSDM’s `fetchPlayer(SteamID64)` and creates the camera target from that player’s CSDM row. **If the player is absent, duplicated, or name/SteamID does not match, stop.** Do not substitute `--mode player`; do not change SteamID; do not rely on a successful CLI exit as POV evidence. Do not use `--force` unless a separate reason and approval exists.

## Stage D — Separately approved one-clip render

The following is the exact CLI shape documented for CSDM and represented in v3.21.1 source. Run only after Stage A help matches, Stage B hash/disk checks pass, and the user approves CSDM DB analysis and game launch. It uses the AutoClip tick range and SteamID directly.

```powershell
New-Item -ItemType Directory -Force $OutputRoot | Out-Null
$RenderLog = Join-Path $Evidence 'csdm-video-attempt-01.log'

& csdm video $Demo $StartTick $EndTick `
  --focus-player $SteamId64 `
  --recording-system CS `
  --recording-output video `
  --encoder-software FFmpeg `
  --record-audio `
  --framerate 60 `
  --width 1920 `
  --height 1080 `
  --ffmpeg-video-container mp4 `
  --ffmpeg-video-codec libx264 `
  --ffmpeg-audio-codec aac `
  --ffmpeg-executable-path $Ffmpeg `
  --output $OutputRoot `
  --output-file-name 'round15-thakurshaaab' `
  --no-show-only-death-notices `
  --close-game-after-recording 2>&1 | Tee-Object -FilePath $RenderLog
$RenderExit = $LASTEXITCODE
if ($RenderExit -ne 0) { throw "CSDM video failed with exit code $RenderExit. Preserve logs and inspect CSDM/CS2 state before retry." }
```

All flags above appear in the CSDM CLI guide and inspected source. `CS` means Counter-Strike’s own `startmovie` capture; `recording-output video` asks CSDM/FFmpeg for a video output and removes successful raw image/audio sequence files. The command sets full HUD (`--no-show-only-death-notices`), audio, 1920×1080/60, H.264/AAC in MP4, and closes the game after recording. It avoids event-mode sequence generation and does not set an AutoClip score/tag.

The command starts CSDM’s queue and waits for this video to finish. Its documented terminal exit codes are 0 success, 1 failure, 2 invalid command/arguments, 130 Ctrl+C. It can print phase/elapsed progress; no reliable percent completion is documented. CSDM documents Ctrl+C as cancellation. If cancellation is necessary, use Ctrl+C in this terminal, wait for CSDM to report termination, and verify CS2, CSDM, FFmpeg and temporary file cleanup. Do not close the terminal, kill game processes, or rerun immediately while CSDM is still cleaning up.

## Stage E — Locate and validate the artifact

CSDM writes beneath a unique video-ID folder inside `--output`. Collect exactly one MP4; do not assume the filename is at `$OutputRoot` itself:

```powershell
$Mp4Files = @(Get-ChildItem -LiteralPath $OutputRoot -Filter '*.mp4' -File -Recurse)
if ($Mp4Files.Count -ne 1) { throw "Expected exactly one MP4 beneath $OutputRoot; found $($Mp4Files.Count). Inspect the log/tree; do not pick an arbitrary file." }
$Mp4 = $Mp4Files[0].FullName
if ((Get-Item -LiteralPath $Mp4).Length -le 0) { throw 'Output MP4 is empty.' }
Get-FileHash -Algorithm SHA256 -LiteralPath $Mp4 | Format-List | Out-File (Join-Path $Evidence 'mp4-sha256.txt')
ffprobe -v error -show_entries format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,sample_rate,channels -of json $Mp4 |
  Set-Content -Encoding utf8 (Join-Path $Evidence 'ffprobe.json')
ffmpeg -v error -i $Mp4 -f null -
if ($LASTEXITCODE -ne 0) { throw "FFmpeg decode validation failed with exit code $LASTEXITCODE." }
Write-Host "Validated container/streams; review visually before accepting: $Mp4"
```

Metadata validation checks that a decodable file has the expected video/audio streams, 1920×1080 output and nominal 60 fps. It does not prove correct round, player, kills, HUD, clutch, smoothness, or A/V sync. Windows Media Player can lack codecs; use an already-installed capable player such as VLC for human inspection.

### Manual content review checklist

- [ ] The clip shows the expected `de_mirage` round 15 segment, identified by its timing/event context. CSDM round labels and AutoClip round labels may differ if warmup/restarts are reconstructed differently; use the tick and kills as primary evidence.
- [ ] The camera is ThakurShaaab’s player POV, targeted through SteamID64 `76561198865074734`, for the relevant span. It is not an observer/free camera, enemy POV, or another player. Record any momentary camera switch.
- [ ] The kill feed/HUD is visible and both expected Deagle kills are present: Buug_11 at tick 89511 and Keepup-_ at tick 90345. Review around both event boundaries, not only a thumbnail.
- [ ] The interval includes the clutch segment beginning around tick 90104 and the final kill/outcome moment. If the result cuts off immediately after tick 90345, derive an extended render-only end tick from AutoClip’s already-parsed round end evidence if available; retain the stored highlight `end_tick=90345` unchanged and record the separate render bound. Do not guess tick rate or padding.
- [ ] Audio stream exists, gameplay sound is audible and synchronized with action; silence is not enough to count as audio success.
- [ ] No black/frozen frames, missing first frames, visible TGA capture stalls, unintended overlays or surprising X-Ray.
- [ ] Duration and actual frame rates are plausible for the requested interval and settings. CSDM docs state CS2 standard timing as 64 ticks/second, which makes 834 ticks roughly 13 seconds as a sanity check only; AutoClip’s parser warned tick-rate metadata was unavailable. Keep AutoClip ticks canonical and verify actual duration rather than deriving bounds from that assumption.
- [ ] MP4 opens and decodes to completion. Record output path/hash and preserve the CSDM logs.
- [ ] CS2 closes as requested; no CSDM plugin/config residue or changed user settings remain. Compare relevant install/config state against a pre-run snapshot and inspect CSDM logs. Source has `finally` cleanup/uninstall code, but that is not a guarantee after a process crash, power loss, or hard kill.
- [ ] A second approved run of the same request captures the same two events/player/round and expected streams. Do not require byte-identical files; compare content and media properties.

## Stop conditions

Stop and preserve evidence if installed help is missing an option, CSDM analyze fails, SteamID mapping fails, the demo hash mismatches, available space is inadequate, CSDM changes the recording method, the first clip shows the wrong POV, audio is absent, game/plugin cleanup is uncertain, or output count is ambiguous. Do not change AutoClip data/detector or turn on HLAE to “make it work” without a separate plan/approval.
