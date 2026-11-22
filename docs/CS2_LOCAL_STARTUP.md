# CS2 local workflow startup

AutoClip uses the existing Python environment and local data directory. No
provider key, Whisper model, Docker service, or cloud account is needed for CS2
analysis. Tesseract OCR and the full FFmpeg build must be on `PATH`.

From the project root, verify the local environment:

```bash
PATH=/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/bin:/bin ./.venv/bin/autoclip doctor
```

The React bundle is built into `backend/autoclip/static/`. Rebuild it after
frontend changes or if the local bundle is missing:

```bash
cd frontend
npm run build
cd ..
```

Start the local app:

```bash
PATH=/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/bin:/bin ./.venv/bin/autoclip serve --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, import a video, choose **CS2 signals · no provider
required** under **Options**, then start analysis. Stop the server with `Ctrl+C`
in the terminal running it.

Jobs, source media, caches, and exports remain in `~/.autoclip` unless
`AUTOCLIP_HOME` is set to another local directory.
