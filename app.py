"""Simple Flask app to download videos/audio using yt-dlp.

Features:
- Fetch video info and list available formats
- Select format and start a background download (threaded)
- Optional audio-only extraction (mp3)
- Optional trimming via ffmpeg
- Progress reporting via a JSON endpoint polled by the client

Notes:
- Requires `yt-dlp` and `ffmpeg` on PATH for trimming/extraction.
"""

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
import os
import yt_dlp
import threading
import time
import shutil
import re
import subprocess
import json

app = Flask(__name__)
app.secret_key = 'change_this_to_a_secret'
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
PREVIEW_FOLDER = os.path.join(DOWNLOAD_FOLDER, 'previews')
os.makedirs(PREVIEW_FOLDER, exist_ok=True)

progress_data = {}
video_store = {}

# Detect ffmpeg/ffprobe availability once at startup.
# You can override by setting FFMPEG_BIN_DIR below or using the environment variable FFMPEG_DIR.
# If user provided a local path to the ffmpeg "bin" folder, we'll try that first.
FFMPEG_BIN_DIR = os.environ.get('FFMPEG_DIR') or r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin"

def _find_ffmpeg_from_dir(dir_path):
    ff = os.path.join(dir_path, 'ffmpeg.exe') if os.name == 'nt' else os.path.join(dir_path, 'ffmpeg')
    fp = os.path.join(dir_path, 'ffprobe.exe') if os.name == 'nt' else os.path.join(dir_path, 'ffprobe')
    if os.path.exists(ff) and os.path.exists(fp):
        return ff, fp
    return None, None

# Prefer embedded ffmpeg from imageio_ffmpeg if installed — this bundles a lightweight platform
# specific ffmpeg binary with the Python package and is convenient for portability.
FFMPEG_PATH = None
FFPROBE_PATH = None
try:
    import imageio_ffmpeg as _iioff
    # imageio-ffmpeg exposes a helper to download/get a bundled ffmpeg executable
    bundled = _iioff.get_ffmpeg_exe()
    if bundled:
        FFMPEG_PATH = bundled
        # imageio-ffmpeg doesn't ship ffprobe; try to locate ffprobe next
        FFPROBE_PATH = shutil.which('ffprobe')
except Exception:
    # imageio-ffmpeg not available or failed - we'll fallback to other methods
    FFMPEG_PATH = None
    FFPROBE_PATH = None

# Next try configured dir
if not (FFMPEG_PATH and FFPROBE_PATH):
    if FFMPEG_BIN_DIR:
        try:
            FFMPEG_BIN_DIR = os.path.normpath(FFMPEG_BIN_DIR)
            if os.path.isdir(FFMPEG_BIN_DIR):
                current_path = os.environ.get('PATH', '')
                if FFMPEG_BIN_DIR not in current_path:
                    os.environ['PATH'] = FFMPEG_BIN_DIR + os.pathsep + current_path
                ff, fp = _find_ffmpeg_from_dir(FFMPEG_BIN_DIR)
                if ff and fp:
                    FFMPEG_PATH, FFPROBE_PATH = ff, fp
        except Exception:
            FFMPEG_PATH = FFPROBE_PATH = None

# fallback to PATH for anything still missing
FFMPEG_PATH = FFMPEG_PATH or shutil.which('ffmpeg')
FFPROBE_PATH = FFPROBE_PATH or shutil.which('ffprobe')

FFMPEG_AVAILABLE = bool(FFMPEG_PATH and FFPROBE_PATH)
if FFMPEG_AVAILABLE:
    print(f'ffmpeg detected. ffmpeg: {FFMPEG_PATH}, ffprobe: {FFPROBE_PATH}')
else:
    print('Warning: ffmpeg or ffprobe not found. Trimming and audio extraction will not work until ffmpeg is available, FFMPEG_DIR is set, or imageio-ffmpeg is installed.')

def progress_hook(video_id, d):
    def _human_size(n):
        try:
            n = float(n)
        except Exception:
            return '0 B'
        if n <= 0:
            return '0 B'
        for unit in ['B','KB','MB','GB','TB']:
            if n < 1024.0:
                return f"{n:3.1f} {unit}"
            n /= 1024.0
        return f"{n:.1f} PB"

    def _human_time(s):
        try:
            s = int(s)
        except Exception:
            return 'Unknown'
        if s < 0:
            return 'Unknown'
        if s < 60:
            return f"{s}s"
        m, sec = divmod(s, 60)
        if m < 60:
            return f"{m}m {sec}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m"

    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes', 0)
        speed = d.get('speed') or 0
        percent = int(downloaded / total * 100) if total else 0
        # compute ETA if possible
        eta = None
        if total and speed:
            try:
                remain = max(0, (total - downloaded))
                eta = int(remain / speed) if speed else None
            except Exception:
                eta = None
        else:
            eta = d.get('eta')

        progress_data[video_id] = {
            'status': 'downloading',
            'percent': percent,
            'speed': speed,
            'speed_text': _human_size(speed) + '/s' if speed else '0 B/s',
            'eta': eta,
            'eta_text': _human_time(eta) if eta is not None else 'Unknown'
        }
    elif d['status'] == 'finished':
        progress_data[video_id] = {'status': 'finished', 'percent': 100, 'speed': 0, 'speed_text': '0 B/s', 'eta': 0, 'eta_text': '0s'}
    elif d['status'] == 'error':
        progress_data[video_id] = {'status': 'error', 'message': d.get('error', 'Unknown error')}

def sanitize_filename(name):
    """Sanitize filenames: remove problematic characters and trim length."""
    cleaned = re.sub(r'[^a-zA-Z0-9_\-\. ]', '_', name)
    return cleaned[:200]


def process_formats(raw_formats):
    """Normalize, filter, dedupe and sort formats for display.

    Returns a list of dicts with keys: format_id, label, height, ext, tbr, filesize, format_note, url
    """
    if not raw_formats:
        return []

    allowed_exts = {'mp4', 'm4a', 'webm', 'mkv', 'mp3'}
    candidates = []
    for f in raw_formats:
        ext = (f.get('ext') or '').lower()
        # include audio-only and common video containers
        if ext not in allowed_exts:
            continue
        fmt = {
            'format_id': f.get('format_id'),
            'height': f.get('height'),
            'ext': ext,
            'tbr': f.get('tbr') or f.get('abr') or 0,
            'filesize': f.get('filesize') or f.get('filesize_approx') or 0,
            'format_note': f.get('format_note') or f.get('format') or '',
            'url': f.get('url')
        }
        candidates.append(fmt)

    # dedupe by (height, ext) keeping the one with largest filesize or bitrate
    dedup = {}
    for f in candidates:
        key = (f['height'] or 0, f['ext'])
        prev = dedup.get(key)
        if not prev:
            dedup[key] = f
        else:
            # prefer larger filesize, then higher tbr
            if (f['filesize'] or 0) > (prev['filesize'] or 0):
                dedup[key] = f
            elif (f['tbr'] or 0) > (prev['tbr'] or 0):
                dedup[key] = f

    out = list(dedup.values())

    # sort: video (height desc) then audio-only (by bitrate desc)
    def sort_key(f):
        h = f['height'] or 0
        is_audio = 1 if f['height'] is None else 0
        return (is_audio, -h, -(f['tbr'] or 0), -(f['filesize'] or 0))

    out.sort(key=sort_key)

    # friendly label
    for f in out:
        if f['height']:
            note = f['format_note'] or f"{f['height']}p"
        else:
            note = f['format_note'] or 'audio'
        size_text = (f"{f['filesize']/1024/1024:.2f} MB" ) if f['filesize'] else 'Size unknown'
        br_text = (f"~{int(f['tbr'])} kbps") if f.get('tbr') else ''
        parts = [note, f['ext']]
        if br_text:
            parts.append(br_text)
        parts.append(size_text)
        f['label'] = ' • '.join([p for p in parts if p])

    return out

def valid_time_format(t):
    """Accept either seconds (digits) or HH:MM:SS / MM:SS formats.

    Examples: '30', '1:23', '00:01:23'
    """
    if t is None:
        return False
    t = t.strip()
    if re.match(r'^\d+$', t):
        return True
    return bool(re.match(r'^\d{1,2}(:\d{2}){0,2}$', t))

def trim_media(input_path, output_path, start_time=None, end_time=None):
    cmd = ['ffmpeg', '-y', '-i', input_path]
    if start_time:
        cmd.extend(['-ss', start_time])
    if end_time:
        # Calculate duration from start/end times
        if start_time:
            cmd.extend(['-to', end_time])
        else:
            cmd.extend(['-t', end_time])
    cmd.append(output_path)

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0

def download_video(url, format_id, video_id, title, height, extract_audio, start_time, end_time):
    safe_title = sanitize_filename(title)
    resolution = f"{height}p" if height else format_id
    ext = 'mp3' if extract_audio else 'mp4'
    filename_template = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}_{resolution}.%(ext)s")
    # More robust ydl options to handle flaky connections
    ydl_opts = {
        'format': format_id,
        'outtmpl': filename_template,
        'progress_hooks': [lambda d: progress_hook(video_id, d)],
        'continuedl': True,               # resume partial downloads
        'retries': 10,
        'fragment_retries': 10,
        'socket_timeout': 15,
        'http_chunk_size': 1024 * 1024,   # 1MB
        'noprogress': False,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
        # Optional: use an external downloader like aria2c for better fragment handling
        # 'external_downloader': 'aria2c',
        # 'external_downloader_args': ['-x', '16', '-k', '1M'],
    }
    if extract_audio:
        if not FFMPEG_AVAILABLE:
            progress_data[video_id] = {'status': 'error', 'message': 'ffmpeg not found - cannot extract audio (mp3).'}
            return
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    # Wrap the download in a retry loop with exponential backoff to handle transient network errors
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Handle trimming if requested and not audio-only
            if (start_time or end_time) and not extract_audio:
                original_file = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}_{resolution}.mp4")
                trimmed_file = os.path.join(DOWNLOAD_FOLDER, f"{safe_title}_{resolution}_trimmed.mp4")
                success = trim_media(original_file, trimmed_file, start_time, end_time)
                if success:
                    os.replace(trimmed_file, original_file)

            # success - break out of retry loop
            break
        except Exception as e:
            msg = str(e)
            # surface the error to the UI
            progress_data[video_id] = {'status': 'error', 'message': msg, 'attempt': attempt}
            # if last attempt, leave error; otherwise backoff and retry
            if attempt == max_attempts:
                return
            backoff = 3 * attempt
            time.sleep(backoff)
            # reset status so UI shows restarting
            progress_data[video_id] = {'status': 'starting', 'percent': 0}

    # Ensure percent is set to 100 when finished
    if progress_data.get(video_id, {}).get('status') == 'finished':
        progress_data[video_id]['percent'] = 100


def _generate_preview_ffmpeg(stream_url, out_path, duration=10):
    """Generate a short preview clip using ffmpeg from a stream URL.
    This re-encodes to mp4/aac to maximize browser compatibility.
    """
    if not FFMPEG_AVAILABLE:
        return False, b'ffmpeg not found on PATH'

    cmd = [
        FFMPEG_PATH or 'ffmpeg', '-y', '-i', stream_url,
        '-t', str(duration),
        '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', '-b:a', '128k',
        out_path
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return proc.returncode == 0, proc.stdout + proc.stderr
    except Exception as e:
        return False, str(e).encode()


def generate_preview(video_id, format_id):
    """Generate a preview file for the given video and format.

    Returns path (relative) on success or None.
    """
    store = video_store.get(video_id)
    if not store:
        return None
    formats = store.get('formats', [])
    fmt = next((f for f in formats if f.get('format_id') == format_id), None)
    if not fmt:
        return None

    stream_url = fmt.get('url')
    # If the format entry lacks a direct URL, try re-extracting info
    if not stream_url:
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(store.get('url'), download=False)
                for f in info.get('formats', []):
                    if f.get('format_id') == format_id:
                        stream_url = f.get('url')
                        break
        except Exception:
            stream_url = None

    if not stream_url:
        return None

    safe_name = sanitize_filename(store.get('title') or video_id)
    preview_fname = f"{safe_name}_{format_id}_preview.mp4"
    preview_path = os.path.join(PREVIEW_FOLDER, preview_fname)

    # If preview already exists, return it
    if os.path.exists(preview_path):
        return preview_fname

    ok, out = _generate_preview_ffmpeg(stream_url, preview_path, duration=10)
    if ok:
        return preview_fname
    # failed: cleanup
    try:
        if os.path.exists(preview_path):
            os.remove(preview_path)
    except Exception:
        pass
    return None


@app.route('/generate_preview/<video_id>/<format_id>')
def generate_preview_route(video_id, format_id):
    """Endpoint to request or fetch a preview clip for a given video and format.
    Returns JSON: {status: 'ready'|'processing'|'error', url: <preview_url>}.
    Generation is done synchronously here but could be threaded for better UX.
    """
    key = f"{video_id}___{format_id}"
    # If preview already generated in store, return
    store = video_store.get(video_id, {})
    existing = store.get('previews', {})
    if existing and existing.get(format_id):
        fname = existing[format_id]
        return jsonify({'status': 'ready', 'url': url_for('preview_file', filename=fname)})

    # generate (blocking); could be offloaded to thread if desired
    fname = generate_preview(video_id, format_id)
    if fname:
        store.setdefault('previews', {})[format_id] = fname
        return jsonify({'status': 'ready', 'url': url_for('preview_file', filename=fname)})
    return jsonify({'status': 'error', 'message': 'Could not generate preview'})


@app.route('/previews/<path:filename>')
def preview_file(filename):
    return send_from_directory(PREVIEW_FOLDER, filename)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        url = request.form.get('url')
        if not url:
            flash('Please enter a video URL.')
            return redirect(url_for('index'))
        try:
            ydl_opts = {'quiet': True, 'skip_download': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            # store only a small id in the session and keep the potentially large
            # formats list server-side to avoid overflowing the client cookie
            vid = info.get('id')
            session['video_url'] = url
            session['video_title'] = info.get('title')
            session['video_id'] = vid
            # keep formats server-side
            raw = info.get('formats', [])
            processed = process_formats(raw)
            video_store[vid] = {
                'formats': processed,
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'url': url,
            }
            return redirect(url_for('select_format'))
        except Exception as e:
            flash(f'Error fetching video info: {e}')
            return redirect(url_for('index'))
    return render_template('index.html')

@app.route('/select_format', methods=['GET', 'POST'])
def select_format():
    # This view primarily serves the formats selection page. The actual download
    # is started by POSTing to /start_download (the form's action).
    if request.method == 'POST':
        # If the form incorrectly posts here, forward to the start handler.
        return redirect(url_for('start_download'))

    video_id = session.get('video_id')
    if not video_id or video_id not in video_store:
        flash('No video selected. Paste a URL first.')
        return redirect(url_for('index'))

    store_entry = video_store.get(video_id, {})
    formats = store_entry.get('formats', [])
    title = store_entry.get('title', session.get('video_title', 'Unknown'))
    thumbnail = store_entry.get('thumbnail')
    duration = store_entry.get('duration')
    return render_template('select_format.html', formats=formats, title=title, thumbnail=thumbnail, duration=duration, video_id=video_id)


@app.route('/start_download', methods=['POST'])
def start_download():
    # This route receives the form from select_format and redirects to progress
    format_id = request.form.get('format_id')
    extract_audio = request.form.get('extract_audio') == 'yes'
    start_time = request.form.get('start_time')
    end_time = request.form.get('end_time')

    # Validate trimming inputs
    if start_time and not valid_time_format(start_time):
        flash('Invalid start time format. Use HH:MM:SS')
        return redirect(url_for('select_format'))
    if end_time and not valid_time_format(end_time):
        flash('Invalid end time format. Use HH:MM:SS')
        return redirect(url_for('select_format'))

    url = session.get('video_url')
    video_id = session.get('video_id')
    title = session.get('video_title')

    # Find height for filename if possible
    store_entry = video_store.get(video_id, {})
    selected_format = next((f for f in store_entry.get('formats', []) if f.get('format_id') == format_id), None)
    height = selected_format.get('height') if selected_format else None

    # Prepare filename and check if exists
    safe_title = sanitize_filename(title)
    resolution = f"{height}p" if height else format_id
    ext = 'mp3' if extract_audio else 'mp4'
    filename = f"{safe_title}_{resolution}.{ext}"
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)

    if os.path.exists(filepath):
        return redirect(url_for('download_file', filename=filename))

    progress_data[video_id] = {'status': 'starting', 'percent': 0}
    threading.Thread(target=download_video, args=(url, format_id, video_id, title, height, extract_audio, start_time, end_time)).start()

    return redirect(url_for('progress_page', video_id=video_id, filename=filename))

@app.route('/progress/<video_id>/<filename>')
def progress_page(video_id, filename):
    return render_template('progress.html', video_id=video_id, filename=filename)

@app.route('/progress/<video_id>')
def progress_status(video_id):
    """Return JSON progress for a given video id. Polled by the progress page."""
    return jsonify(progress_data.get(video_id, {'status': 'not started', 'percent': 0}))

@app.route('/downloads/<filename>')
def download_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)
    

if __name__ == '__main__':
    print('Starting Flask app (host=127.0.0.1, port=5000, reloader disabled)')
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
