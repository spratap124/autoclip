# AutoClip Windows Setup

This guide reflects the current repository configuration inspected on 2026-11-22. It installs the existing app only; it does not set up CS2 demo rendering. The supported Python range is **3.11 or 3.12** (not 3.13), and the gaming PC should use 64-bit CPython. The frontend requires Node 20+; the checked in npm lockfile was generated with lockfile version 3. No Python lockfile is present, and `pyproject.toml` uses dependency ranges except for `demoparser2==0.42.2`.

## 0. Clone the prepared project

Once the Windows-readiness changes are committed and pushed, use a fresh clone so Windows gets the same reviewed source without copying the Mac checkout or its runtime data:

```powershell
New-Item -ItemType Directory -Force C:\Projects | Out-Null
git clone https://github.com/artbyjazi/autoclip.git C:\Projects\AI-youtube-shorts-clipping
Set-Location C:\Projects\AI-youtube-shorts-clipping
git status --short --branch
```

The clone contains source, tests, and documentation only. It does not contain your Mac `~/.autoclip` database, uploaded demo, generated media, model cache, or OS keyring secrets. Transfer a local demo separately only when you are ready to verify parsing. Start with a fresh Windows `AUTOCLIP_HOME` as described below.

## 1. Install prerequisites

From an elevated PowerShell terminal with Windows Package Manager available:

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.11 --exact
winget install --id OpenJS.NodeJS.LTS --exact
winget install --id Gyan.FFmpeg --exact
```

Restart PowerShell after installation so updated `PATH` entries are visible. The README specifically requires the **full Gyan FFmpeg build**, not `Gyan.FFmpeg.Essentials`, because exports use the `ass` subtitle filter (`libass`) and `libx264`. Confirm the IDs offered on the target PC with `winget show --id <id> --exact` if Winget reports a missing package. [Gyan FFmpeg](https://github.com/GyanD/codexffmpeg) publishes the full Windows builds; the package name comes from this repository's README and FFmpeg install hint.

For Windows OCR/video detection, install the Tesseract executable too:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
```

This is a separate program, not a Python dependency. Windows builds are distributed by [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki); verify the install folder is on `PATH`. The code calls `tesseract` from `PATH` and returns no OCR lines if it cannot locate it. The CS2 demo-event analyzer uses `demoparser2`, not Tesseract.

Check versions and binaries:

```powershell
py -3.11 --version
node --version
npm --version
git --version
ffmpeg -version
ffprobe -version
tesseract --version
```

## 2. Create the Python environment and install AutoClip

Run from the repository root. The repo supports ordinary `venv`/`pip`; `uv` is used by CI but there is no `uv.lock`.

```powershell
Set-Location C:\Projects\AI-youtube-shorts-clipping
py -3.11 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

For the RTX 4070 Ti transcription acceleration, install the optional NVIDIA CUDA 12 runtime wheels after base dependencies:

```powershell
python -m pip install -e ".[dev,gpu]"
```

The `gpu` extra installs NVIDIA CUDA runtime libraries for faster-whisper/CTranslate2. It does not install the NVIDIA display driver; install/update the official driver separately. GPU transcription is optional; CPU mode remains available. FFmpeg’s NVENC encoder is also optional, and AutoClip probes whether it can actually encode before using it.

Verify the pinned native parser wheel imports:

```powershell
python -c "import demoparser2, importlib.metadata; print(importlib.metadata.version('demoparser2'))"
```

PyPI currently publishes Windows x86-64 wheels for [`demoparser2==0.42.2`](https://pypi.org/project/demoparser2/0.42.2/) on CPython 3.11 and 3.12. It also publishes Windows x86-64 wheels for [MediaPipe 0.10.14](https://pypi.org/project/mediapipe/0.10.14/) on these CPython versions. That confirms installable native artifacts exist, not that this project has passed a FACEIT demo parse on the target Windows PC. The current GitHub Actions workflow includes a Windows/Python 3.12 test leg, but the real demo test is opt-in and requires a local file.

## 3. Install frontend dependencies

```powershell
Set-Location C:\Projects\AI-youtube-shorts-clipping\frontend
npm ci
npm run build
Set-Location ..
```

`npm ci` uses `frontend/package-lock.json`. The production build writes to `backend/autoclip/static`; it is generated output and is ignored by Git. Run `npm run dev` for the Vite development server; its `/api` requests proxy to `http://127.0.0.1:8000`.

## 4. Configure local data and secrets

By default, AutoClip writes data under `%USERPROFILE%\.autoclip`. This includes `media`, `work`, `exports`, `cache`, `autoclip.db`, and `config.json`. Set `AUTOCLIP_HOME` only if you want that root on another drive; for example, for the current PowerShell session:

```powershell
$env:AUTOCLIP_HOME = 'D:\AutoClipData'
```

The application will create this directory structure when it starts. To persist a custom location for the Windows user:

```powershell
[Environment]::SetEnvironmentVariable('AUTOCLIP_HOME', 'D:\AutoClipData', 'User')
```

Open a new terminal after changing a persistent environment variable. Do not put provider keys into the repository or a committed `.env`. AutoClip stores provider keys and HuggingFace tokens in the OS keyring (Windows Credential Manager through `keyring`); the current Mac Keychain entries do not move automatically. Re-enter any needed keys on Windows through Settings or `autoclip config set-secret <provider>`.

The code also recognizes secret overrides named `AUTOCLIP_ANTHROPIC_KEY`, `AUTOCLIP_OPENAI_KEY`, `AUTOCLIP_GEMINI_KEY`, and `AUTOCLIP_HUGGINGFACE_TOKEN_KEY`. Use those only in a protected user environment; never write real values into this guide, shell history, or Git.

## 5. Verify FFmpeg and OCR configuration

The `autoclip doctor` command checks `ffmpeg`, `ffprobe`, required FFmpeg filters (`crop`, `scale`, `ass`, `concat`, `loudnorm`), `libx264`, and whether `h264_nvenc` actually works. Run:

```powershell
ffmpeg -hide_banner -filters | Select-String '\sass\s'
ffmpeg -hide_banner -encoders | Select-String 'h264_nvenc|libx264'
autoclip doctor
```

`autoclip doctor` is authoritative: an encoder listed by `ffmpeg` may still fail against the installed NVIDIA driver. If NVENC is unusable, the app should use software H.264 encoding.

For OCR:

```powershell
Get-Command tesseract
tesseract --list-langs
```

The OCR pipeline uses local OpenCV image processing and the Tesseract CLI. The `autoclip` Python dependencies install OpenCV transitively through SceneDetect/MediaPipe; Tesseract itself remains an external binary. Ensure English trained data (`eng`) is installed. If `Get-Command` fails, repair the Tesseract installation or add its install directory to the Windows user `PATH`, then restart the terminal.

## 6. Start backend and frontend

With the environment activated, from the repository root:

```powershell
autoclip serve --host 127.0.0.1 --port 8000 --no-open
```

The API and built frontend are served at `http://127.0.0.1:8000`; health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

For frontend development, open a second PowerShell window:

```powershell
Set-Location C:\Projects\AI-youtube-shorts-clipping\frontend
npm run dev -- --host 127.0.0.1
```

Open the Vite URL printed by the command (normally `http://127.0.0.1:5173`). Stop a foreground server with `Ctrl+C`. The API defaults to localhost binding; do not bind to `0.0.0.0` on an untrusted network.

## 7. Run tests

Backend unit/regression suite, excluding long, golden, and environment-dependent full-pipeline tests:

```powershell
Set-Location C:\Projects\AI-youtube-shorts-clipping
python -m pytest -m "not slow and not golden and not e2e" -q
```

Real FFmpeg export tests:

```powershell
python -m pytest -m "slow and not golden and not e2e" -q
```

Frontend typecheck/build:

```powershell
Set-Location .\frontend
npm run typecheck
npm run build
```

The GitHub Actions workflow runs backend tests on Windows/Python 3.12 and FFmpeg render tests there. This does not substitute for testing the checked-out commit and local demo on the gaming PC.

## 8. Verify CS2 demo parsing and highlight regression

Supply a **local uncompressed `.dem`**. Do not point this command at the `.dem.zst` archive; decompress it first. For the existing Round 15 regression reference, the Mac copy was stored under `~/.autoclip/media/demos/73fa424473544f40/source.dem`, but this runtime folder is not in Git. After separately copying the demo to Windows, set its real path:

```powershell
$Demo = 'D:\AutoClipData\media\demos\73fa424473544f40\source.dem'
Test-Path $Demo
autoclip cs2demo inspect $Demo
autoclip cs2demo players $Demo
autoclip cs2demo analyze $Demo --steamid 76561198865074734
```

Expected cached Mac reference, to compare rather than assume: ThakurShaaab, SteamID `76561198865074734`, Round 15, ticks `89511–90345`, two Deagle kills, tags `2K`, `multi_kill`, `clutch_won`, `clutch_1v1`, `round_winning_elimination`, score 53. These values are recorded from current local analysis data, not independently video-verified. Differences in scores/tags may indicate detector settings, data version, or platform regression; compare the same source demo SHA-256 and settings. The demo’s tick rate was missing, so derived seconds can remain null.

The optional real-demo test is gated by `AUTOCLIP_TEST_DEMO_PATH`:

```powershell
$env:AUTOCLIP_TEST_DEMO_PATH = $Demo
python -m pytest tests/test_cs2demo_real.py -q
Remove-Item Env:\AUTOCLIP_TEST_DEMO_PATH
```

This test checks event provenance and internal invariants, not independently labeled accuracy. It can parse the full demo if no compatible Windows cache exists. The normalized cache is content-hash/parser/schema keyed under `<AUTOCLIP_HOME>\cache\cs2demo`; player changes reuse it.

To verify the dashboard’s stored analysis over the local API, replace `$DemoId` with the ID shown in the app and request the player export:

```powershell
$DemoId = '73fa424473544f40'
$Uri = "http://127.0.0.1:8000/api/demos/$DemoId/analysis/steamid%3A76561198865074734/export/json"
Invoke-RestMethod $Uri | ConvertTo-Json -Depth 100 | Set-Content -Encoding utf8 "$env:TEMP\autoclip-round15.json"
```

## 9. Verify existing video exports and OCR path

The export test suite exercises actual FFmpeg rendering, filters, fonts, and output streams without requiring a provider key:

```powershell
python -m pytest tests/test_export_render.py -q
```

For an end-to-end OCR/video detector regression, supply a local gameplay recording to the existing tests or run the UI workflow with the same recording and settings used on Mac. The repository’s e2e marker uses `AUTOCLIP_E2E_MEDIA`:

```powershell
$env:AUTOCLIP_E2E_MEDIA = 'D:\AutoClipData\media\cs2-validation\gameplay.mp4'
python -m pytest -m e2e -q
Remove-Item Env:\AUTOCLIP_E2E_MEDIA
```

The test may require the same model files and settings as the Mac, and its transcript/provider path may require an API key or local Ollama. Do not treat an unset/failed provider check as an OCR detector failure. Test the CS2 OCR detector separately with `tesseract --version` and the existing `tests/test_cs2*.py` coverage. Full success requires the Windows `doctor` checks and real export tests to pass.

## Current Windows audit findings

- **Compatible/in CI:** FastAPI/SQLite/Pathlib layout, localhost serving, Node/Vite build, direct FFmpeg discovery on `PATH`, UTF-8 subprocess invocation, path escaping for FFmpeg filtergraphs, and a Windows CI test job are present.
- **Requires configuration:** full FFmpeg build with `libass` and `libx264`; Tesseract executable and English language data for OCR; CUDA runtime wheels and current NVIDIA driver for GPU transcription; optional `AUTOCLIP_HOME`.
- **Requires careful data migration:** SQLite rows persist absolute source/demo/transcript/export paths. Copying the Mac database and files to another Windows path does not rewrite those references. Preserve a backup and rebase stored paths through a reviewed migration procedure, or start with a fresh Windows database and re-upload media/demos. Do not open the only copied database before its paths are reconciled.
- **Unverified on this machine:** successful Windows install/import and FACEIT parse with `demoparser2 0.42.2`; full current branch tests; OCR on real Windows footage; GPU runtime detection; export output quality on the RTX PC.
- **No source blocker identified for the required Python/Node versions:** PyPI publishes `demoparser2 0.42.2` Windows wheels for CPython 3.11/3.12; MediaPipe 0.10.14 has Windows x86-64 wheels for both versions. Native wheel availability is not a passing integration test.
