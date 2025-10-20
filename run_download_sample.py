"""CLI helper to start a download using the app's download logic.

Usage (PowerShell):
  & "Z:\Projects\Coding\Python\Any-vid-downloader\.venv\Scripts\python.exe" "run_download_sample.py" --url "<YOUR_URL>" [--audio]

This script does NOT run automatically in the assistant environment. Run it locally
inside your venv to perform the download.
"""

import argparse
import threading
import time
import os
import yt_dlp

from main import download_video, progress_data, video_store, DOWNLOAD_FOLDER


def choose_720p_or_nearest(formats):
    """Return the format dict for 720p if available, else the nearest lower, else best available."""
    if not formats:
        return None
    for f in formats:
        if f.get('height') == 720:
            return f
    candidates = [f for f in formats if f.get('height')]
    if candidates:
        leq = [f for f in candidates if f.get('height') <= 720]
        if leq:
            return max(leq, key=lambda x: x.get('height'))
        return max(candidates, key=lambda x: x.get('height'))
    # fallback
    return formats[-1]


def main():
    p = argparse.ArgumentParser(description='Download a video using app logic (run locally).')
    p.add_argument('--url', '-u', required=True, help='Video URL to download')
    p.add_argument('--audio', '-a', action='store_true', help='Extract audio only (mp3)')
    args = p.parse_args()

    url = args.url
    print('Extracting video info...')
    ydl_opts = {'quiet': True, 'skip_download': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    vid = info.get('id') or str(int(time.time()))
    video_store[vid] = {
        'formats': info.get('formats', []),
        'title': info.get('title') or vid,
        'thumbnail': info.get('thumbnail'),
        'url': url,
    }

    selected = choose_720p_or_nearest(video_store[vid]['formats'])
    if not selected:
        print('No formats found; aborting')
        return

    format_id = selected.get('format_id')
    height = selected.get('height')
    title = video_store[vid]['title']

    print(f'Selected format_id={format_id}, height={height}, audio={args.audio}')

    # start download thread
    print('Starting download thread; check downloads/ for the resulting file')
    t = threading.Thread(target=download_video, args=(url, format_id, vid, title, height, args.audio, None, None), daemon=True)
    t.start()

    # Poll progress until done
    try:
        while True:
            st = progress_data.get(vid, {'status': 'starting', 'percent': 0})
            print(f"Progress: status={st.get('status')}, percent={st.get('percent')}")
            if st.get('status') in ('finished', 'error'):
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print('Cancelled by user')

    print('Final state:', progress_data.get(vid))

    print('\nRecent files in downloads:')
    if os.path.isdir(DOWNLOAD_FOLDER):
        files = sorted([os.path.join(DOWNLOAD_FOLDER, f) for f in os.listdir(DOWNLOAD_FOLDER)], key=os.path.getmtime, reverse=True)
        for p in files[:20]:
            print('-', p)


if __name__ == '__main__':
    main()
