# Any-vid-downloader

A small Flask-based video downloader that uses yt-dlp and ffmpeg for optional trimming and audio extraction.

Features
- Fetch video metadata and formats with yt-dlp
- Select format and start background downloads
- Optional audio extraction (mp3) using ffmpeg
- Optional trimming and preview generation using ffmpeg
- Progress updates (percent, speed, ETA)

Prerequisites
- Python 3.8+ and a virtual environment
- A working `ffmpeg` + `ffprobe` binary (required for trimming and audio extraction)
- `yt-dlp` and `Flask` (installed via `requirements.txt`)

Setup
1. Create and activate a virtual environment (PowerShell):

```powershell
python -m venv .venv
.\venv\Scripts\Activate.ps1
```

2. Install Python dependencies:

```powershell
pip install -r requirements.txt
```

FFmpeg

This project requires `ffmpeg` and `ffprobe` for trimming, audio extraction and preview generation. You have two options to make them available to the app:

A) Add ffmpeg to your PATH (recommended)
- Download a Windows build (for example BtbN or gyan.dev builds).
- Extract and add the `bin` folder to your user PATH.

B) Set the application environment variable `FFMPEG_DIR` to point to the ffmpeg `bin` folder.

Example (PowerShell) — temporary for current session:

```powershell
$env:FFMPEG_DIR = 'C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin'
```

To persistently set it, add it to your system/user environment variables via Windows Settings.

Running the app (development)

From the repository root, with the virtualenv activated:

```powershell
# start the Flask dev server (binds to 127.0.0.1:5000)
.\.venv\Scripts\python.exe .\main.py
```

Open http://127.0.0.1:5000 in your browser.

Smoke tests

There is a small smoke test file `smoke_test.py` that uses Flask's test client to verify templates and endpoints:

```powershell
.\.venv\Scripts\python.exe .\smoke_test.py
```

Troubleshooting

- If you see: `ffmpeg or ffprobe not found` — either install ffmpeg and add it to PATH, or set `FFMPEG_DIR` to the `bin` folder and restart the app.
- If downloads fail with network errors, the app performs retries and backoff. Check terminal logs for stack traces and the `progress` UI for messages.

Notes and next steps

- Preview generation currently runs synchronously; it may block the request while generating. For better UX it should be offloaded to a background worker/queue (to be implemented).
- Waveform visualization is not implemented yet.

License
- This is a small personal utility. Use it responsibly and respect site terms of service when downloading content.
