# AutoClip Windows Migration Checklist

Use this checklist to clone the prepared project onto the RTX 4070 Ti PC and validate existing functionality. It does **not** add demo rendering. The source branch is intended to be pushed before Windows setup; verify the checked-out commit and a clean worktree before installing dependencies.

## 1. Clone and verify the source tree

- [ ] Clone the pushed repository on Windows:

  ```powershell
  New-Item -ItemType Directory -Force C:\Projects | Out-Null
  git clone https://github.com/artbyjazi/autoclip.git C:\Projects\AI-youtube-shorts-clipping
  Set-Location C:\Projects\AI-youtube-shorts-clipping
  git status --short --branch
  git rev-parse --short HEAD
  git remote -v
  ```

- [ ] Confirm the branch and commit match the Mac push and the worktree is clean before setup.
- [ ] Do not copy `.venv`, `frontend/node_modules`, `frontend/dist`, `.pytest_cache`, `.ruff_cache`, or `backend/autoclip/static`; rebuild these on Windows.
- [ ] Source control carries project code, tests, and docs. It does not carry local demos, recordings, database history, generated media, or keyring secrets.

### Tracked Git files in the inspected base tree

`git ls-files` enumerated the tracked files in these groups. The exact inventory can be regenerated on either machine with `git ls-files`.

- **Root and CI:** `.gitattributes`, `.gitignore`, `.python-version`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `LICENSE`, `README.md`, `SECURITY.md`, `.github/` templates and `.github/workflows/ci.yml`, `pyproject.toml`.
- **Backend:** `backend/autoclip/` package including API, configuration, CLI, CUDA/runtime probes, database, jobs, providers, all pipeline stages, CS2 OCR pipeline, reframe and bundled fonts.
- **Docker:** `docker/Dockerfile`, `docker/compose.yaml`.
- **Frontend:** `frontend/index.html`, `frontend/package.json`, `frontend/package-lock.json`, TypeScript/Vite config, `frontend/src/` app, API client, pages, components, styles and bundled fonts.
- **Docs/reference:** existing tracked docs under `docs/`, `reference/cs2_shorts_converter.py`.
- **Tests:** `tests/conftest.py`, tracked test modules and `tests/fixtures/cs2_ground_truth_E04_f2nCTnE.json`.

The full source inventory is available at any time through `git ls-files`. No manual overlay of the Mac working tree should be necessary after the reviewed changes are pushed.

## 2. Transfer ignored local application data separately

Git does not carry AutoClip runtime data. `.gitignore` excludes `.autoclip/`, `*.db`, `*.sqlite3`, `.env`, `.env.*` (except `.env.example`), uploaded/test media and build artifacts. The Mac currently has data under `~/.autoclip`, including:

- `autoclip.db` plus current `autoclip.db-wal` and `autoclip.db-shm` files (SQLite is configured for WAL mode).
- `media/`, including the uploaded CS2 demo at `media/demos/73fa424473544f40/source.dem`, source recordings, and video files.
- `cache/cs2demo/` (normalized parser cache keyed by demo hash/parser/schema version), `work/`, and `exports/`.
- `config.json` and secrets in the Mac OS keyring. **Never commit or post the config or any secret.** Keychain entries do not transfer; re-enter provider keys on Windows through AutoClip/Windows Credential Manager.

### Decide whether to migrate history or start a fresh Windows database

Database records contain absolute media paths: `sources.path`, `demos.file_path`, `transcripts.json_path`, and `exports.path`. Copying the Mac data directory to a Windows path does **not** rewrite paths like `/Users/...`; old source/demo rows will point to missing files. This is a confirmed data-migration limitation.

- [ ] Keep an untouched backup of the Mac `~/.autoclip` data before any transfer.
- [ ] If preserving full history, stop AutoClip cleanly on the Mac first and create a consistent SQLite backup with SQLite’s backup facility. Do not copy only `autoclip.db` while a process is writing; WAL may contain committed data not yet checkpointed. Copy media/cache/work/exports as appropriate, then use a reviewed path-rebase migration for every path column before opening the copied DB on Windows.
- [ ] If no path-rebase utility has been approved, keep the old DB as an archive and start Windows with a new `AUTOCLIP_HOME`; re-upload/re-analyze the target demo and local media. Save the Mac highlight JSON export as the comparison baseline. This preserves the original data untouched but does not migrate the old app history into the live Windows DB.
- [ ] Transfer `.dem`, `.dem.zst` archive (if desired), and gameplay recordings separately through an external drive or trusted LAN share. Verify file sizes and SHA-256 on both sides. Do not assume repository fixtures include these files.
- [ ] Never transfer `.venv` or `node_modules` between macOS and Windows; rebuild from manifests.

The checked-in `tests/fixtures/cs2_ground_truth_E04_f2nCTnE.json` is a small JSON fixture, not the real gameplay recording. The local `test-data` directory inspected in the repository contains only `.DS_Store` entries. The actual CS2 demo and generated videos are ignored user data.

## 3. Windows prerequisites and installation

- [ ] Windows 10/11 x64 and current NVIDIA driver.
- [ ] CPython 3.11 x64 or 3.12 x64. Avoid Python 3.13 (`requires-python = ">=3.11,<3.13"`).
- [ ] Node.js 20+ and npm; `frontend/package-lock.json` is present, so use `npm ci`.
- [ ] Git for Windows.
- [ ] Full FFmpeg build with `ffmpeg`, `ffprobe`, `libass` (`ass` filter), `libx264`, crop/scale/concat/loudnorm. NVENC is optional and must pass AutoClip’s runtime probe.
- [ ] Tesseract OCR executable on `PATH` with English `eng` data for the existing OCR/video detector.
- [ ] Enough local storage for the source demo/media, normalized cache, intermediates and exports. Keep `AUTOCLIP_HOME` short (for example `D:\AutoClipData`) to reduce Windows path-length issues.
- [ ] Install Python dependencies from `pyproject.toml`; do not copy the Mac environment. `demoparser2==0.42.2` has PyPI Windows x86-64 wheels for CPython 3.11 and 3.12, and MediaPipe 0.10.14 provides Windows x64 wheels for these versions. Still validate installation and real demo parsing locally.
- [ ] Optional: install AutoClip’s `.[gpu]` extra for CUDA 12 runtime dependencies used by faster-whisper/CTranslate2 transcription. GPU support is unrelated to demo parsing and does not guarantee NVENC.

Use [WINDOWS_SETUP.md](WINDOWS_SETUP.md) for exact PowerShell install, startup, parser, OCR, and export commands.

## 4. Application startup and persistence checks

- [ ] Create a new Windows virtual environment; install `.[dev]` or `.[dev,gpu]`.
- [ ] Run `autoclip doctor`; resolve Python, FFmpeg filter and binary failures. Record the output, but redact any user-specific paths before sharing it.
- [ ] Build the frontend with `npm ci` then `npm run build`; verify `backend/autoclip/static/index.html` is generated.
- [ ] Start `autoclip serve --host 127.0.0.1 --port 8000 --no-open`.
- [ ] Confirm `Invoke-RestMethod http://127.0.0.1:8000/api/health` reports healthy and the React UI loads from port 8000.
- [ ] Create a benign demo job or settings change, restart the app, and confirm SQLite persistence. Use only the Windows test `AUTOCLIP_HOME`, not the only copy of user data.
- [ ] Confirm demo upload accepts `.dem`, rejects `.dem.zst` with the decompression message, and writes to `<AUTOCLIP_HOME>\media\demos\<id>\source.dem`.
- [ ] Confirm parser cancellation/timeout and app shutdown do not leave a running parser process or partial uploaded file.

## 5. Demo and highlight regression checks

- [ ] Confirm the transferred `source.dem` SHA-256 matches the Mac source. The previously inspected demo hash was `5babddf8a13e21c6b207a690e8759aa58ad214acff91039b4cda0ea7e2dafb49`.
- [ ] Run `autoclip cs2demo inspect <demo.dem>` and `autoclip cs2demo players <demo.dem>`; confirm map, roster and SteamID.
- [ ] Select ThakurShaaab by SteamID `76561198865074734`, not display name alone.
- [ ] Run `autoclip cs2demo analyze <demo.dem> --steamid 76561198865074734` and save JSON output on both systems.
- [ ] Compare the candidate Round 15 reference: ticks `89511–90345`; kills at `89511` (Buug_11, Deagle) and `90345` (Keepup-_, Deagle); clutch transition `90104`; tags `2K`, `multi_kill`, `clutch_won`, `clutch_1v1`, `round_winning_elimination`; score 53.
- [ ] Compare event IDs, round number, tags, score, kill count, victims, weapons, source event provenance, ticks, and warnings. Derived seconds may remain null because the parsed demo reports no tick rate. Do not compare only the UI sort order.
- [ ] Confirm changing selected player uses the Windows cached normalized demo and does not parse again. Cache identity should match demo hash, parser version and normalized schema.
- [ ] Do not claim detector accuracy from this one regression record; it is only a platform equivalence check against current local output.

## 6. FFmpeg export and OCR regression checks

- [ ] Run `autoclip doctor`; confirm `ffmpeg` and `ffprobe` found, required filters present, `libx264` usable, and NVENC status explained.
- [ ] Run `python -m pytest tests/test_export_render.py -q`; inspect test output MP4 metadata and playback.
- [ ] Exercise each currently supported aspect ratio (9:16, 1:1, 16:9), caption styles, subtitles, audio normalization, font loading and fit/blur background behavior through existing regression tests/UI.
- [ ] If NVENC is available, verify the actual encoder probe succeeds. Also confirm fallback to software H.264 when NVENC is missing or driver-incompatible.
- [ ] Confirm `tesseract --version`, `tesseract --list-langs` includes `eng`, and Windows can run the existing CS2 OCR detection test path with the same recording/settings.
- [ ] Compare diagnostic evidence/candidates from the existing OCR/video detector on a local reference recording. Ensure OCR failures do not get misreported as demo-event detector failures.
- [ ] Run backend unit tests and the slow FFmpeg test marker as documented in [WINDOWS_SETUP.md](WINDOWS_SETUP.md); run `npm run typecheck` and `npm run build`.

## 7. Windows compatibility findings and limits

| Area | Audit result |
|---|---|
| Python/Node support | Compatible by declared bounds; CI matrix explicitly runs Windows + Python 3.12, frontend builds on Node 22 Linux. Validate target versions locally. |
| File paths | Most code uses `pathlib`; FFmpeg filtergraph escaping explicitly handles Windows drive-letter colon/backslashes. Keep app data and repo paths short. |
| Subprocesses | Most invoke argument lists (not shell strings). Demo parser kills a child with `process.kill()` on Windows; POSIX group termination is correctly guarded by `os.name`. Descendant-tree behavior is less strong on Windows; test parser cancellation. |
| Temporary files | Uses `tempfile`, closes staged files before rename, and writes atomic temp files alongside destinations. Cross-volume rename must be avoided; managed temp files are created in destination directory. |
| SQLite | Standard `sqlite3`, WAL mode, foreign keys, 5s busy timeout. Copy/restore must be done with the DB stopped or SQLite backup facility. Windows file locking differs; test simultaneous web/worker reads and writes. |
| `demoparser2` | The pinned release has Windows x64 wheels for CPython 3.11 and 3.12; parser API integration and FACEIT file behavior remain target-PC verification items. |
| FFmpeg | Discovered with `shutil.which`; full build and required filters are mandatory. Filter escaping is explicitly designed for Windows paths. |
| OCR | Tesseract is an external binary discovered from `PATH`; no executable means OCR returns no lines. Must install separately for OCR parity. |
| Permissions | Config chmod 600 is best-effort and guarded for unsupported platforms. Windows secrets should rely on Credential Manager, not POSIX mode bits. |
| Environment | `AUTOCLIP_HOME`, `AUTOCLIP_NO_WORKER`, and provider/token environment overrides exist. Child parser environment includes Windows `SYSTEMROOT`, `WINDIR`, `TEMP`, `TMP` and `PATH`. No `.env` file is tracked. |
| Bash/Unix assumptions | Core startup/tests are Python/PowerShell-compatible; CI’s FFmpeg filter-verification step explicitly uses Bash on GitHub Actions. There is no app startup dependency on shell scripts. |
| Long paths | No repository-level Windows long-path policy was found. Use a short checkout and `AUTOCLIP_HOME`; test large source/demo paths and user-controlled filenames. |
| Native libs | MediaPipe/OpenCV/faster-whisper/CTranslate2 wheels are platform-dependent. Correct supported Python versions and successful `pip install` are mandatory; CUDA DLL/driver availability is runtime-probed. |
| OCR/render regression | No Windows real-footage OCR/e2e result was run during this read-only audit. CI’s Windows FFmpeg tests are configured, but they do not prove the user’s local recording and OCR setup. |

## 8. Git, environment, and secret safety

- [ ] Never commit `.autoclip`, databases, `.dem`/`.dem.zst`, source videos, generated media, model downloads, virtual environments, `node_modules`, or `.env` files.
- [ ] `config.json` may contain settings and a plaintext fallback secret if no OS keyring was available. Do not inspect or copy it into the repository. Use it only as a protected backup; prefer recreating settings on Windows and re-entering secrets into Credential Manager.
- [ ] Mac Keychain secrets are not part of the project folder and do not automatically transfer. Do not export them in plaintext.
- [ ] Keep the Mac source data backup until Windows has passed persistence, demo, video-export and OCR regressions.
- [ ] Before any future commit, inspect `git status --short --untracked-files=all` and stage only intended source/docs. This audit does not stage, commit, push, or change Git config.

## 9. Rollback procedure

- [ ] Keep the original Mac checkout and untouched `~/.autoclip` backup unchanged until acceptance passes.
- [ ] If Windows dependency installation or app startup fails, do not alter/delete source data; capture the sanitized error and `autoclip doctor` output, then return to the Mac app.
- [ ] If Windows SQLite migration or path rebasing fails, stop AutoClip, quarantine the Windows data copy, and restore from the untouched backup. Never “repair” the only source DB in place.
- [ ] If parser output differs, retain both JSON outputs, demo SHA-256, parser version, schema version and settings; do not overwrite the Mac baseline.
- [ ] If exports differ, preserve source recording, settings, test command and output; revert only by restoring the Windows environment/data copy, not by changing existing presets during migration.
- [ ] Once rollback is needed, point the user back to the Mac `AUTOCLIP_HOME` and original working tree; Windows can be rebuilt without affecting the preserved Mac copy.

## Acceptance sign-off

- [ ] Backend starts and health endpoint works.
- [ ] React dashboard loads and persists settings/state in SQLite.
- [ ] Original FACEIT demo hash, upload, parse and roster are verified on Windows.
- [ ] Same player SteamID can be selected.
- [ ] Round 15 reference events, score and evidence match the Mac baseline.
- [ ] Existing FFmpeg export tests and all three aspect ratios work; NVENC or software fallback is confirmed.
- [ ] Existing OCR/video workflow detects the expected evidence using Windows Tesseract and a local reference recording.
- [ ] No Mac-only absolute paths remain unresolved in the migrated DB, or a new DB/re-upload strategy is explicitly accepted.
- [ ] No unexpected Git changes, missing untracked files, committed secrets, or accidental demo/media staging.
