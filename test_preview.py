# Small helper to test preview generation for a URL using the app's functions
import sys
from main import process_formats, video_store, generate_preview
import yt_dlp

URL = sys.argv[1] if len(sys.argv) > 1 else 'https://streamable.com/lf027o?src=player-page-share'

print('Fetching info for', URL)
with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
    info = ydl.extract_info(URL, download=False)

vid = info.get('id') or 'testvideo'
raw = info.get('formats', [])
processed = process_formats(raw)
video_store[vid] = {
    'formats': processed,
    'title': info.get('title'),
    'thumbnail': info.get('thumbnail'),
    'duration': info.get('duration'),
    'url': URL,
}

# pick the first format that has a url
fmt = next((f for f in processed if f.get('url')), None)
if not fmt:
    print('No usable format found')
    sys.exit(1)

print('Selected format:', fmt.get('format_id'), fmt.get('label'))
res = generate_preview(vid, fmt.get('format_id'))
if res:
    print('Preview generated:', res)
else:
    print('Preview generation failed')
    sys.exit(2)
