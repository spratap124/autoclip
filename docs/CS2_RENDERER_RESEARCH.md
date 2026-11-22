# CS2 Renderer Open-Source Research Notes

**Scope:** investigate automated demo playback/capture only. Do not adopt any candidate’s parser, detector, scoring, or dashboard as AutoClip functionality. Source state below was inspected from the named checkout revisions on 2026-11-22; upstream can change. Findings distinguish source/documentation assertions from hardware validation.

## Summary

CS2VideoGenerator (CSVG) is the strongest modular rendering candidate because it accepts explicit demo/tick/player compilation data and separates capture from highlight discovery. Its Windows support, in-engine frame/audio route and CLI are documented and visible in source. It has a significant unresolved requirement: SteamID POV targeting is experimental/unreliable and the normal route uses player name. Its real-engine compatibility has been reported against a specific CS2 build, not validated on AutoClip’s target RTX 4070 Ti or FACEIT demo.

CS Demo Manager (CSDM) is the most mature user-facing ecosystem and has a documented CLI video command, tick watch mode and player selection. Its automation is entangled with its app/database/shared queue and is not a clean rendering library. Its source uses player slots resolved from SteamIDs and game commands, but issue reports demonstrate POV and update reliability questions. It is the best fallback for a POV-focused POC.

`cs2-highlights-maker` is a small Windows/HLAE toolchain whose recorder could theoretically accept caller-provided sequences, but its code is coupled to its own models/slot assumptions, has no root license in the inspected revision, and combines its detector with recording workflow. Do not adopt without licensing clarification and a separate extraction/POC.

`cs2-dem-renderer` is a Linux-oriented research implementation that demonstrates scheduled game commands and frame piping, but it parses for its own per-player render dataset, uses Linux server assumptions and its inspected encoder path has no audio mux. It is not suitable for the stated Windows/audio workflow.

## Inspection method and revisions

Repositories were cloned into `/tmp` for read-only source inspection; no code was copied into AutoClip, no dependencies installed, no CS2 launched and no demo rendered.

| Candidate | Repository | Inspected revision/status | License evidence |
|---|---|---|---|
| CS2VideoGenerator | [CS2OpenDev/CS2VideoGenerator](https://github.com/CS2OpenDev/CS2VideoGenerator) | commit `6e04580bd2ff03b41f82ace0e4bc64c60d556d38`, tag `v0.10.1`, released 2026-10-31 | v0.10.1 MIT; release notes say older `<=v0.10.0` GPL-3.0-only. Pin and review exact release. |
| CS Demo Manager | [akiver/cs-demo-manager](https://github.com/akiver/cs-demo-manager) | commit `950459e333b8563094f2e1f4d7e41948396792e3`, tag `v3.21.1`, 2026-11-18 | MIT at inspected source. Verify bundled dependency/plugin licenses before redistribution. |
| cs2-highlights-maker | [pykvarz/cs2-highlights-maker](https://github.com/pykvarz/cs2-highlights-maker) | commit `a60695ab634dc618d5c77abfbeef0941917714c7`, 2026-09-25; no root tag/release observed | No root LICENSE found in inspected checkout; bundled CSDM server plugin has a separate MIT NOTICE. Do not assume project code is permissively licensed. |
| cs2-dem-renderer | [reka-ai/cs2-dem-renderer](https://github.com/reka-ai/cs2-dem-renderer) | commit `02b09ffeaf7c3a0685a3e3e44ad6b2519f682c22`, 2026-06-25 | Root MIT; inspect Go modules, plugin and transitive license notices before bundling. |

Commit identifiers anchor the source descriptions below. Release/issue state may have changed after inspection; verify before implementation.

## Candidate A — CS2VideoGenerator

### Source path and execution flow

The repository describes CSVG as a capture/execution tool: an input compilation JSON supplies demo paths, tick ranges, player targeting metadata and output configuration. Its Core runner loads clips, groups work by demo and orders ranges; the CLI offers generation, watching, diagnostics and cleanup. The worker/plugin side coordinates with the game through a gRPC client. The rendering path controls CS2 playback, captures frames/audio and hands encoded inputs to FFmpeg; it does not score or decide which clips are interesting.

Relevant inspected paths at commit `6e04580…`:

- [`README.md`](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/README.md): project purpose, supported systems, compilation/config shape, setup and measured examples.
- [`src/Cs2VideoGenerator.CLI`](https://github.com/CS2OpenDev/CS2VideoGenerator/tree/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/src): CLI entry points/commands (`generate`, `watch`, `doctor`, backup/restore/cleanup); exact invocation in the README is `csvg generate --file ...`.
- [`Cs2CompilationRunner.cs`](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/src/Cs2VideoGenerator.Core/Cs2CompilationRunner.cs): compilation orchestration.
- [`Cs2CompilationClip.cs`](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/src/Cs2VideoGenerator.Core/Models/Cs2CompilationClip.cs): clip request model, including demo/tick and target metadata (verify exact schema against pinned release before writing adapter).
- [`ClipCaptureCoordinator.cs`](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/src/Cs2VideoGenerator.Core/Capture/ClipCaptureCoordinator.cs): capture coordination.
- [`Cs2EngineSession.cs`](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/src/Cs2VideoGenerator.Core/Engine/Cs2EngineSession.cs): game session integration.
- [`docs/in-engine-capture.md`](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/docs/in-engine-capture.md): engine capture requirements and sequence.
- [`Cs2PluginClient.cpp`](https://github.com/CS2OpenDev/CS2VideoGenerator/tree/6e04580bd2ff03b41f82ace0e4bc64c60d556d38): native plugin communication implementation (navigate source tree for exact plugin project file at pinned commit).
- [Release history](https://github.com/CS2OpenDev/CS2VideoGenerator/releases): version and license transition.

### Verified/claimed capabilities

| Capability | Finding and evidence class |
|---|---|
| CS2 support | **Documented and source-backed**, with a real-engine validation note against game build `2000879`; that is a version-specific compatibility claim, not current-target validation. |
| Windows | **Documented/supported.** Linux also documented; macOS unsupported. |
| Automated load/seek | **Source-backed** compilation requests include demo path and start/end ticks. Real target-demo seek accuracy still needs POC. |
| POV | Request supports player metadata; documented default is exact player name via `spec_player`. SteamID targeting via `spec_lock_to_accountid` is marked experimental/unreliable. **Does not yet meet robust SteamID identity acceptance.** |
| Capture | Standard in-engine approach invokes CS2 capture (`startmovie`/`host_framerate`) to image frames, plus WAV audio, piped/processed with FFmpeg. Alternative OBS capture is present. Experimental `InEngineHooked` uses Windows DXGI present capture. |
| Audio/MP4 | WAV audio and FFmpeg encoding are in the described pipeline; verify exact audio mux/output profile on target machine. |
| CLI/API | CLI (`generate`, `watch`, `doctor`, backup/restore/cleanup); no evidence of a stable external HTTP API. Suitable for worker wrapping, not an embedded library. |
| Batch | Compilation/grouping by demo and tick ordering are present; per-clip target and POV correctness during session reuse requires testing. |
| Headless | Docs describe offscreen/unfocused operation for the in-engine mode; this is **not proof** that Windows can run without logged-in graphical session, desktop/window, or monitor. |
| GPU | Experimental hooked capture is Windows-only. NVIDIA NVENC is plausible through FFmpeg only if installed build exposes a working encoder; RTX 4070 Ti support was not measured here. |
| License | Exact v0.10.1 MIT; older <=0.10.0 GPL-3.0-only per release note. Pin exact version; include notices and review dependencies. |

The project documents reference performance around 70 fps captured at 60 fps output (~1.16× real-time) for standard 1080p60 and ~162 fps (~2.7×) for hooked capture on its reference hardware. Those are upstream-reported, machine/build-specific figures, not benchmark results for AutoClip’s Windows PC. The standard path’s uncompressed 1080p TGA frames are about 8.3 MB each; at 60 fps that is roughly 500 MB/s of transient image throughput before cleanup. The source streams/deletes frames to bound staging, but disk throughput/backpressure must be measured on target hardware.

CSVG’s game setup can modify `gameinfo.gi`, plugin/search paths and configuration, launch with `-insecure`/third-party software flags, and offers backup/restore/cleanup. That is a nontrivial game-install mutation and account-safety condition, not a harmless standalone recorder. Use an isolated CS2 install or dedicated Windows user when practical; never join protected servers from modified/insecure state. Do not infer that another Steam account removes risk.

**Assessment:** best primary backend to test because the compile request resembles AutoClip’s immutable highlight contract and CLI can be wrapped. **POV is a blocker until verified**. CSVG is not accepted just based on name/SteamID fields in a JSON contract.

## Candidate B — CS Demo Manager

### Source and flow

Project: [repository](https://github.com/akiver/cs-demo-manager), [video guide](https://cs-demo-manager.com/docs/guides/video), [CLI docs](https://cs-demo-manager.com/docs/cli#generate-videos), [playback guide](https://cs-demo-manager.com/docs/guides/playback).

At inspected commit `950459e…`:

- `src/cli/commands/video-command.ts` implements the CLI video command and options.
- `src/node/video/generation/create-cs2-video-json-file.ts` builds CS2 playback/action data.
- `src/node/counter-strike/json-actions-file/json-actions-file-generator.ts` schedules demo actions/commands.
- `src/node/counter-strike/launcher/start-counter-strike-with-hlae.ts` launches CS2/HLAE path.
- `src/node/video/` contains video generation/capture orchestration and FFmpeg integration.

The CLI has `csdm video <demo> startTick endTick` and mode/player options (including `--mode player`, `--steamids`, `--event`, and config-file options in the docs). Player-mode generation uses CSDM’s own analyzed/demo database/queue; docs indicate the demo must be analyzed and present in that application’s DB. Its watch command documents `--tick` and `--focus-player SteamID`. Playback actions use `spec_mode 1`, `spec_player <slot>`, and resolve player SteamID to a player slot. Therefore, CSDM has a credible SteamID-to-entity route, but caller-provided rendering is entangled with the app’s models, shared queue and local database. This is a CLI application, not a small library or independent network render API.

The video documentation describes CS2 playback with capture via raw TGA+WAV followed by FFmpeg/VirtualDub, and an HLAE+FFmpeg route for direct capture. Windows and Linux CS2 are supported in the product; HLAE is Windows-only; macOS is not a target. Treat recording modes and dependencies as version/profile-specific; inspect selected mode at POC. The public GUI’s ability to queue videos does not automatically make every GUI option a stable external API.

### Compatibility evidence and limits

Project is active and mature; inspected release was v3.21.1 (2026-11-18), MIT. Relevant public issue reports include [#1238 wrong player POV](https://github.com/akiver/cs-demo-manager/issues/1238), [#1343 slow video report](https://github.com/akiver/cs-demo-manager/issues/1343), and [#1458 CS2 plugin failure after an Aug. 19, 2026 update](https://github.com/akiver/cs-demo-manager/issues/1458). These are **reported issue evidence**, not proof the current release remains broken; issue state should be checked before implementation. Source workarounds around skipped ticks include clamping seeks (e.g. 96 ticks) and ordering `demo_gototick` and `spec_player`; this demonstrates practical timing/command ordering sensitivity, not an exact seeking guarantee.

Audio is in the documented video workflow, but A/V sync and unattended behavior need actual run. CLI supports video generation, but no stable renderer HTTP service was verified. Batch is supported through the application’s queue; reusing the queue outside the desktop app would be tightly coupled. Use as fallback if its robust player-slot route outperforms CSVG in the POC; do not import CSDM’s detection/scoring/dashboard into AutoClip.

## Candidate C — cs2-highlights-maker

Project: [repository](https://github.com/pykvarz/cs2-highlights-maker), inspected commit `a60695a…` (2026-09-25), no release tag/root license observed.

Relevant source:

- `src/highlights/recording/recorder.py`: `record(demo: DemoData, sequences: list[Sequence], ...)` recording orchestration.
- `src/highlights/recording/sequences.py`: sequence/capture window data.
- `src/highlights/recording/actions_file.py`: playback actions.
- `src/highlights/recording/hlae.py`: HLAE invocation.
- `src/highlights/recording/toolchain.py`: external tool resolution.

Flow combines demoparser2 and its own kill/highlight detection, produces sequence/action data, invokes HLAE plus a CSDM plugin `server.dll`, uses `mirv_streams` to obtain frames and audio WAV, then FFmpeg. It is Windows-focused, requires HLAE/CS2/plugin tooling, and depends on demo player slots/model assumptions. `record()` could theoretically be fed caller-created `DemoData` and `Sequence`, but that is not a demonstrated stable renderer-only API; extraction would need decoupling and explicit tests. README reports roughly 10 clips (~20 s) in 10–15 minutes, an author report not independent benchmark.

No project-level license means reuse/redistribution cannot be recommended until clarified. The bundled CSDM plugin has its own MIT notice, which does not license the rest of the project. HLAE injection increases operational/account risk relative to a non-injected path. **Not recommended** for AutoClip backend absent licensing and high-quality isolated proof.

## Additional candidate — reka-ai/cs2-dem-renderer

Repository: [reka-ai/cs2-dem-renderer](https://github.com/reka-ai/cs2-dem-renderer), inspected commit `02b09ff…` (2026-06-25), root MIT. README identifies a Linux-first renderer based on CS2 build `1.41.6.5`.

Inspected paths: `dem-render/main.go`, `parse.go`, `render.go`, `encode.go`, `worker.go`, and `cs2-server-plugin/main.cpp`. It uses its own Go/demo parser, schedules actions through a CS2 server plugin, processes per-player spawn/death rounds and outputs MP4 plus Parquet. It is not a generic exact-interval renderer driven by AutoClip highlight metadata. The inspected encoding path passes video frames to FFmpeg without an audio input/mux. Source assumes Linux facilities/paths (`DISPLAY`, `pkill`, `journalctl`); Windows requires changes. It may inform frame piping and hang detection, but fails required Windows/player-specific/audio/reuse criteria. It must not replace AutoClip parsing or detection.

## Candidate comparison

Legend: **Y** = source/docs support observed; **P** = partial/conditional; **N** = not supported in inspected implementation; **?** = not verified. Evidence is candidate capability, not acceptance on the AutoClip demo/target hardware.

| Criterion | CSVG v0.10.1 | CS Demo Manager v3.21.1 | cs2-highlights-maker | cs2-dem-renderer |
|---|---|---|---|---|
| Current CS2 compatibility | P — validated build claim; recheck current build | P — active project; update regressions reported | ? — current compatibility requires local run | P — README pins older specific build |
| Automated demo load | Y | Y | Y | Y |
| Tick interval rendering | Y at request level; accuracy needs POC | Y CLI/action schedule; command ordering matters | P via sequence/action objects | N for AutoClip arbitrary exact intervals |
| SteamID-based POV | P — experimental/unreliable; default name | P/Y — resolves SteamID to player slot; still validate | P — own slot/model coupling | ? — per-player render is not equivalent to requested SteamID POV |
| Video capture | Y — in-engine, OBS, experimental hook | Y — TGA/FFmpeg or HLAE routes | Y — HLAE/mirv_streams | Y — frame/FFmpeg |
| Audio capture | Y — WAV path documented; verify mux | Y — video workflow documents audio | Y — WAV in workflow | N in inspected mux path |
| MP4 generation | Y via FFmpeg | Y via FFmpeg/VirtualDub modes | Y via FFmpeg | Y |
| Windows | Y | Y | Y | N/needs changes |
| Linux | Y | Y (HLAE exception) | ?/not target | Y |
| macOS | N | N | N | N |
| CLI/API | Y CLI; no stable HTTP API verified | Y CLI, app-coupled; no independent service verified | P internal Python function, not stable CLI/API | P worker/service code, but custom API and Linux assumptions |
| Batch | Y compilation | Y app queue | P sequences | Y built around broad per-player jobs |
| Unattended/headless | P automation; graphical/session requirements unresolved | P automation; verify UI/session | P automation; HLAE setup/session | P Linux display assumptions; not proven headless |
| GPU acceleration | P FFmpeg encoder depends on installed build; experimental DXGI capture | P FFmpeg/HLAE options depend on mode/build | P FFmpeg/HLAE | P VAAPI/Radeon path; not target NVIDIA Windows |
| License | MIT at v0.10.1; older GPL | MIT | Unknown root license | MIT root |
| Maintenance | Active release at inspected date | Active release at inspected date | Low activity/no releases observed | Research-scale, one inspected release state |
| Integration complexity | Medium; worker wraps CLI/JSON | High; app DB/queue coupling | High; detector and HLAE coupling | High; Linux architecture/audio mismatch |
| Account safety | Insecure/plugin setup; elevated concern | HLAE mode concern; in-engine mode still validate | HLAE/injection concern | Plugin/modified game concern |
| Long-term fit | Best conditional candidate | Strong fallback / operational reference | Poor without license and refactor | Not suitable for stated target |

## Playback and capture facts that affect AutoClip

### Demo and timing

All viable candidates ultimately ask CS2 to execute its demo playback path. A demo must be available to the game/tool in its expected filesystem context and must be parseable by the installed CS2 build. Do not assume arbitrary path handling or FACEIT-specific compatibility without POC. `.dem.zst` should be decompressed locally before renderer handoff unless the chosen tool explicitly supports it; AutoClip’s current input is the uncompressed `.dem`. Verify signature, path, build mismatch and load-complete state.

Tick commands are the correct control surface when the game accepts them; they avoid converting source highlight positions to seconds. They do not by themselves guarantee the first requested frame is captured: command scheduling, demo seek, plugin action order, POV stabilization and capture warm-up matter. Do not derive `tick_rate` as 64 for padding. The inspected cached demo reports `tick_rate: null`, so AutoClip’s current stored range must be preserved and any added context must be sourced from verified metadata or presented as unresolved.

### POV identity

AutoClip selects by SteamID64. CS2 playback tools may target a demo player by a transient entity/slot or display name. The adapter must resolve SteamID to the in-demo player at the requested time, reject duplicate/absent identity, issue the camera command and verify it. A correct map/time with wrong perspective is a failed render. CSVG’s SteamID command path is explicitly experimental; CSDM’s source resolution to a slot is promising but not an identity guarantee. Death/spectator transitions, disconnect/reconnect, duplicate names, name changes and camera handoff need fixtures/POC.

### Audio/video, FPS and unattended operation

The candidates rely on engine capture, HLAE/OBS, or desktop/engine frame mechanisms plus FFmpeg. These are not interchangeable. TGA capture uses disk bandwidth; a hook can improve throughput but changes compatibility/risk; OBS needs a functioning capture session and can drop frames. Game audio can be written separately and muxed; verify sample rate, stream duration and synchronization. A valid MP4 header is not proof that it contains the right POV/kills.

“Automated” means commands can start playback and capture without keyboard intervention. It does not prove the application works as a Windows service, when logged out, without a desktop session, with a locked screen or without a display. Recommended initial configuration is an interactive, logged-in Windows session that auto-starts the worker after logon. Test locked/monitorless behavior separately. Do not claim true headless rendering.

## Security, Steam and license notes

Valve documents Trusted Mode and VAC restrictions in [Trusted Mode FAQ](https://help.steampowered.com/en/faqs/view/09A0-4879-4353-EF95) and [VAC FAQ](https://help.steampowered.com/en/faqs/view/571A-97DA-70E9-FF74). CSVG documents `-insecure`/third-party software setup; HLAE’s [README](https://github.com/advancedfx/advancedfx/blob/main/README.md) and [FAQ](https://github.com/advancedfx/advancedfx/wiki/FAQ) also describe security/compatibility constraints, including Faceit AC concerns. FACEIT defines cheats in its [support policy](https://support.faceit.com/hc/en-us/articles/360015788779-What-is-deemed-to-be-a-cheat) and describes its anti-cheat at [this page](https://support.faceit.com/hc/en-us/articles/9394666828188-What-is-FACEIT-Anti-cheat-and-how-does-it-work). These sources do **not** establish that offline demo rendering is automatically a violation or that using a second account is safe. Get policy/legal review before distributing an integrated tool; use offline demos and never join protected servers from modified state.

Keep Steam authentication within the user-configured Steam installation; do not automate password entry, copy credentials into AutoClip, or expose them to the Mac/worker API. Prefer offline demo operations; never disable anti-cheat for online play. Inspect third-party binary hashes, signatures, release provenance and exact license. CSVG release pin matters because license changed between versions. HLAE and native plugins warrant explicit security review.

## Source links and limits

- CS2 store/platform requirements: [Steam CS2 store page](https://store.steampowered.com/app/730). It lists supported PC operating systems; no macOS CS2 rendering target was found in candidate docs.
- CSVG compatibility, CLI, compilation, license and perf: [repo](https://github.com/CS2OpenDev/CS2VideoGenerator), [releases](https://github.com/CS2OpenDev/CS2VideoGenerator/releases), [capture doc at inspected revision](https://github.com/CS2OpenDev/CS2VideoGenerator/blob/6e04580bd2ff03b41f82ace0e4bc64c60d556d38/docs/in-engine-capture.md).
- CSDM operation and CLI: [video guide](https://cs-demo-manager.com/docs/guides/video), [CLI](https://cs-demo-manager.com/docs/cli#generate-videos), [playback](https://cs-demo-manager.com/docs/guides/playback), [source](https://github.com/akiver/cs-demo-manager).
- CSDM reports: [wrong POV #1238](https://github.com/akiver/cs-demo-manager/issues/1238), [slow video #1343](https://github.com/akiver/cs-demo-manager/issues/1343), [plugin update report #1458](https://github.com/akiver/cs-demo-manager/issues/1458). User reports are not confirmed current defects.
- `cs2-highlights-maker`: [repository](https://github.com/pykvarz/cs2-highlights-maker); inspected source tree paths above. No root license was observed in pinned checkout.
- `cs2-dem-renderer`: [repository](https://github.com/reka-ai/cs2-dem-renderer); build/version/audio claims above are from pinned README/source.

No candidate was installed or executed. The present report does not prove current hardware throughput, Steam login behavior, target demo compatibility, the target player POV, synchronized audio, or true headless support. Those are explicit Phase 0 measurements, not assumptions.
