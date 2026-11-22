# CSDM Rendering Feasibility Plan

**Status:** analysis only. No CSDM execution, game launch, database writes, installs, or AutoClip code changes were performed for this plan. **Source checked:** CS Demo Manager v3.21.1, commit `950459e333b8563094f2e1f4d7e41948396792e3` (2026-11-18); official docs checked 2026-11-22.

## Recommendation

CSDM is a credible candidate for the **first standalone Windows rendering experiment**, not yet a preferred production integration. Its official CLI supports a specific `.dem` plus exact start/end ticks, a `--focus-player` SteamID64, game-audio capture, output FPS/dimensions, and output directory/container controls. Its source turns the focus SteamID into a camera event at the requested start tick. Those features fit AutoClip’s existing highlight data without adopting CSDM’s player-event sequence detection.

There are material gates:

1. CSDM requires the demo to be analyzed and persisted in **CSDM’s own database** first. This is a rendering prerequisite only; CSDM's event analysis must not determine or alter AutoClip's highlight.
2. SteamID focus is resolved to a CSDM player row/slot, then a `spec_player` camera command is scheduled. The documented option and source mapping do not prove that the resulting recording is consistently the correct first-person POV for this FACEIT demo. Visual confirmation is mandatory.
3. CSDM’s CLI video job uses a shared CSDM daemon/queue/database. It is a blocking subprocess interface, not a stateless rendering library or documented HTTP API. AutoClip cancellation/progress will need an adapter around that process and the CSDM lifecycle.
4. CSDM documents automatic dependency installation during `video` if dependencies are missing. The experiment must preflight CSDM and FFmpeg paths and pass `--ffmpeg-executable-path` explicitly; do not let a command unexpectedly download software.
5. CS2 game/plugin/capture state restoration after crash or forced termination has not been proven on the target PC. A successful CLI exit is not proof of correct footage.

The minimal experiment should use CSDM’s **`CS` recording system + FFmpeg**, which records CS2 `startmovie` frames and audio, avoiding automatic HLAE installation. It creates temporary raw images/audio, so available space on the CS2 install volume is a preflight requirement. If that built-in path fails or cannot meet throughput, test HLAE+FFmpeg separately with explicit review of HLAE’s security and game-compatibility implications; do not silently switch methods.

## Reuse the Windows migration audit

The existing Windows audit reports Python 3.11/3.12, Node 20+, local FFmpeg, Tesseract for OCR, the project’s Windows CI leg, and Windows x64 wheels for pinned `demoparser2`/MediaPipe. The app uses `pathlib`, SQLite and PATH-discovered FFmpeg, with Windows-aware FFmpeg filter escaping and process termination. Windows execution on the actual PC, OCR on footage, native parser behavior on the FACEIT demo, and export equivalence still require validation. The prior setup and migration docs contain the broader installation instructions and platform checklist; do not duplicate them here.

The Mac SQLite database stores absolute Mac paths for demos and media. Copying it to Windows does not make those paths portable. For this first experiment, create a **fresh Windows AutoClip data root** and leave the Mac checkout, database, config, keychain and media untouched. Copy the `.dem` to an explicit Windows path and upload/analyze it in AutoClip’s fresh data directory. Keep the Windows CSDM database separate; it will independently need the demo analysis prerequisite before the video CLI can resolve the SteamID. No AutoClip path-rebase or database migration is proposed here.

The application itself has no additional Windows blocker identified by the prior audit. The practical CSDM gates are: CSDM CLI installed and discoverable, CS2 launchable by CSDM, the demo accepted by CSDM analysis, FFmpeg discoverable, adequate temporary disk space, and a valid CSDM roster entry for the selected SteamID. The hardware/software setup and CSDM installation state have not been inspected on the Windows PC.

## Official CSDM CLI facts

Official sources:

- [CSDM CLI guide](https://cs-demo-manager.com/docs/cli)
- [CSDM video guide](https://cs-demo-manager.com/docs/guides/video)
- [CSDM repository](https://github.com/akiver/cs-demo-manager)
- [Inspected CLI source at v3.21.1](https://github.com/akiver/cs-demo-manager/blob/950459e333b8563094f2e1f4d7e41948396792e3/src/cli/commands/video-command.ts)
- [Inspected CS2 playback-action generator](https://github.com/akiver/cs-demo-manager/blob/950459e333b8563094f2e1f4d7e41948396792e3/src/node/video/generation/create-cs2-video-json-file.ts)
- [Inspected video lifecycle/cleanup](https://github.com/akiver/cs-demo-manager/blob/950459e333b8563094f2e1f4d7e41948396792e3/src/node/video/generation/generate-video.ts)

| Capability | Verified from official docs/source | Limit / validation needed |
|---|---|---|
| Demo input | `csdm video <demo-path> <start-tick> <end-tick> [options]`; docs say `.dem` path may be relative or absolute in analyze. | Demo must already be analyzed and in CSDM’s database. FACEIT is a documented analyzer source, but this exact file has not been analyzed in CSDM. `.dem.zst` must be decompressed first. |
| Exact ticks | Tick range is a first-class CLI sequence and its source creates a sequence with `startTick` and `endTick`. | Seek/action timing must be visually checked. CSDM source itself documents command ordering and skipped-tick sensitivity (issue #1238). Do not infer that requested first/last image lands precisely on an event without inspecting output. |
| Player target | `--focus-player <steamID64>` is documented. Source resolves the ID with `fetchPlayer()` and adds a player camera event at the sequence start tick. | Depends on the target player existing in CSDM DB from demo analysis. Source schedules the camera command via the player’s slot. It does not provide a separate machine-readable assertion that the rendered camera stayed on that SteamID. |
| Audio | `--record-audio` is a documented boolean; the video guide describes game `.wav` capture and muxing. | Verify MP4 has an audio stream and synchronize it by listening/inspection. Player voice capture is a distinct option; do not enable unless required. |
| Recording method | `--recording-system` accepts `CS` or `HLAE`; `--encoder-software` accepts FFmpeg or VirtualDub; `--recording-output` accepts video/images/images-and-video. | Windows video guide recommends HLAE+FFmpeg for less raw-file I/O. The planned initial attempt uses `CS`+FFmpeg to avoid HLAE auto-install and limits; raw-frame disk use will be higher. |
| Resolution/FPS | `--width`, `--height` (minima 800×600), and `--framerate` are documented and parsed by source. | Requested settings do not prove output stream dimensions/frame rate. Validate with ffprobe and playback. |
| Container/codecs | `--ffmpeg-video-container` supports `mp4`, `avi`, `mkv`; video/audio codecs can be set. CSDM source default video codec is `libx264`; planned command explicitly sets `aac`. | Confirm actual output file and streams. CLI uses CSDM settings unless options override them; make important properties explicit. |
| Output path | `--output <folder>` selects the parent output folder; CSDM creates a subfolder named with a unique video ID. `--output-file-name` sets video name. | Collect recursively and require exactly one expected MP4. CSDM may retain raw files on error depending on option/settings. |
| Progress | CLI prints status phases (recording, moving, converting, concatenating) and elapsed time; source waits for status messages from CSDM daemon. `video list --json` can inspect queue status. | No documented stable percent-progress stream for the `video` job. Capture stdout/stderr for diagnostics; avoid parsing human text as a permanent protocol. |
| Exit codes | Official CLI docs define 0 success, 1 failure, 2 invalid usage, 130 Ctrl+C. | Nonzero exits matter, but zero only says CSDM completed; it does not prove correct POV/content/audio. |
| Cancellation | Docs say Ctrl+C aborts the video launched by the command; source has an abort path that terminates game/capture/FFmpeg processes and cleans temporary files. The application queue can also cancel a video. | Verify termination and restoration on this Windows version. Do not use a hard process kill as the normal cancellation mechanism. |
| Temporary files | CS recording produces TGA images and WAV audio; after video output CSDM deletes raw sequence files. HLAE+FFmpeg can write video directly without image frames; audio WAV remains part of muxing. Source cleans playback files/raw files and uninstalls the CSDM server plugin in a `finally` path. | Crash/power loss may interrupt cleanup; game-folder configuration/plugin restoration is not proven on the user’s install. Check before/after and retain error diagnostics. |
| Repeatability | CLI creates unique video IDs/folders and a job can be re-run. | No deterministic pixel/boundary guarantee documented. Repeat the same request and compare event visibility, bounds, FPS, audio and output duration. |

The official docs also say the CLI and app share a daemon/database, video queue defaults to paused then CLI resumes it, and third-party software may be downloaded automatically if missing. That shared state is why the AutoClip adapter should initially be a simple sequential subprocess, not a new worker cluster.

## The actual AutoClip input to preserve

The existing AutoClip `Highlight` in `backend/autoclip/cs2demo/detection_models.py` is immutable and already carries:

- `highlight_id`, `demo_sha256`, `player_id` (`steamid:<SteamID64>`), `round_id`, `round_number`.
- `start_tick`, `end_tick`, `start_seconds`, `end_seconds` (seconds can be `None`).
- `tags`, `score`, `score_breakdown`, `kill_event_ids`, `kills`, `source_event_ids`.
- `clutch`, data-quality warnings, timing warning and detector version.

The demo record is `DemoFile` (`id`, `content_sha256`, `file_path`, parser/schema versions, parse status) in `backend/autoclip/db/models.py`. AutoClip’s `DemoParseQueue` in `backend/autoclip/cs2demo/queue.py` exists for parsing and is already independent from the general video queue. The selected-player endpoint returns the stored/analyzed highlight JSON; see `backend/autoclip/api/demos.py`.

Known regression values from the existing local analysis (not visual ground truth): ThakurShaaab, SteamID64 `76561198865074734`, round 15; start tick `89511`, clutch-state transition `90104`, last kill tick `90345`; two Deagle kills; tags include `2K`, `clutch_won`, and `clutch_1v1`; score 53. The AutoClip parser reported no tick rate, so preserve ticks exactly and do not derive padding from `tick/64` or from seconds. The CSDM video input does not need AutoClip’s score/tags/kill IDs; retain these as sidecar provenance for comparison.

## Minimal integration boundary

An approved later integration should add one replaceable `CsdmCliRenderer` implementing a narrow render-request/result protocol. It should:

1. Accept a selected, existing AutoClip `highlight_id`; resolve its `DemoFile` by `demo_id` and verify demo hash/path.
2. Pass the exact selected SteamID64 and source `start_tick/end_tick` to CSDM. Do not use `--mode player --steamids ... --event kills`, because that makes CSDM regenerate event intervals and would replace AutoClip’s boundaries.
3. Ensure the CSDM DB has analyzed the exact same demo hash and resolves the exact SteamID. CSDM analysis is only a prerequisite for camera lookup and cannot overwrite or filter AutoClip detection results.
4. Invoke a fixed argument list with `subprocess` (no shell string), isolated per-job working/output directory, explicit settings, timeout, output capture, and version/hash metadata. Avoid adding paths or arbitrary console commands from the request.
5. Wait for CLI completion; distinguish CSDM process exit from valid output. Find the one MP4 under CSDM’s ID subfolder, verify nonempty/hash/media streams and place a copy/import in AutoClip’s managed media/export area.
6. Retain a diagnostic manifest linking AutoClip demo/highlight IDs, hash, player SteamID, exact original ticks, CSDM version, command options, output hash, FFprobe metadata, status, logs and warnings.

The adapter must not invoke `analyze_player`, regroup clips, change ticks, score plays, change the candidate, or alter `cs2demo/` records. The existing demo parse queue and existing export presets remain unchanged. Initially use a separate rendering job entry point/queue; CSDM’s own queue is internal and must not be confused with AutoClip job state.

## Windows-specific blockers for this experiment

- **Data portability:** resolved for the POC by fresh Windows AutoClip data root and reuploading the demo; do not copy/open the Mac DB. Preserve all Mac data untouched.
- **CSDM not verified installed/on PATH:** check `Get-Command csdm` and version/help on Windows. Do not install during this task.
- **CSDM database prerequisite:** approved execution stage must run CSDM analyze on the same local `.dem`; this writes CSDM’s database, not AutoClip’s. No DB was modified in this planning stage.
- **SteamID resolution:** CSDM’s separate analysis database must resolve SteamID `76561198865074734`; the CLI docs alone cannot guarantee a camera for this FACEIT demo.
- **Game/capture setup:** CS2 is reported installed, but CSDM’s game path, plugin, actual window session and restore behavior were not checked.
- **Temporary disk:** built-in CS startmovie path places raw images in the game installation location. Check free space on that volume before execution; output folder free space alone is insufficient.
- **FFmpeg discovery:** explicitly pass a verified `ffmpeg.exe` path through the documented option to prevent CSDM dependency auto-download; confirm FFmpeg works with CSDM’s command.
- **POV/content validation:** must be done by visually reviewing the output and checking both events. CSDM exit code/FFprobe metadata do not establish correctness.

## Recommendation gate

Do not write the subprocess adapter until the separate Windows procedure in [04_csdm_windows_poc.md](04_csdm_windows_poc.md) has produced and visually validated one MP4. If CSDM’s target lookup, first-person POV, or state restoration fails, stop and review the backend choice before integration. A successful POC qualifies CSDM as a candidate; it still does not establish multi-demo reliability, batch performance, crash recovery, or production readiness.
