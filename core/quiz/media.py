"""
Images and audio for the media quiz formats (character, idol, opening).

- fetch_image(url): downloads once into ~/.cache/quiz-media and returns a PIL image
- fetch_opening(title): finds an anime opening on YouTube with yt-dlp, downloads
  the first two minutes of audio and returns the most recognisable few seconds
  (YouTube's "most replayed" heatmap when available, else the loudest window)

Needs yt-dlp (and on GitHub runners a cookies file in YT_COOKIES plus Deno for
yt-dlp's JS challenges) for the opening format only.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

import numpy as np
from PIL import Image

CACHE = os.environ.get('QUIZ_MEDIA_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'quiz-media')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
SR = 44100


def _cache_path(key, ext):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest()[:20] + ext)


def fetch_image(url):
    p = _cache_path(url, '.img')
    if not (os.path.exists(p) and os.path.getsize(p) > 1000):
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=30) as r, open(p, 'wb') as f:
            f.write(r.read())
    return Image.open(p).convert('RGBA')


def _ytdlp():
    base = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '--no-playlist']
    cookies = os.environ.get('YT_COOKIES')
    if cookies and os.path.exists(cookies):
        base += ['--cookies', cookies]
    return base


def _search(query, n=6):
    r = subprocess.run(_ytdlp() + ['-J', '--flat-playlist', f'ytsearch{n}:{query}'], capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        return []
    return json.loads(r.stdout).get('entries') or []


def _pick_video(entries, title):
    """Prefer real openings: 'opening'/'OP' in the title, 70 s - 5 min long, not covers or reactions."""
    bad = ('cover', 'reaction', 'piano', 'guitar', 'lyrics video', 'amv', 'karaoke', 'nightcore', 'remix', '8d', 'slowed')
    best, score = None, -1
    for e in entries:
        t = (e.get('title') or '').lower()
        d = e.get('duration') or 0
        if not (70 <= d <= 330) or any(b in t for b in bad):
            continue
        s = (3 if ('opening' in t or ' op' in t or 'op ' in t) else 0) + (2 if 'creditless' in t or 'official' in t else 0)
        s += 2 if any(w in t for w in title.lower().split()[:2]) else 0
        if s > score:
            best, score = e, s
    return best


def _decode(path):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0


def fetch_opening(title, romaji=None, seconds=3.5):
    """(samples, info) for a recognisable snippet of the anime's opening, or (None, reason)."""
    key = f'op:{title}'
    meta_p = _cache_path(key, '.json')
    audio_p = _cache_path(key, '.m4a')
    if not os.path.exists(meta_p):
        entries = []
        for q in (f'{romaji or title} opening creditless', f'{title} opening full'):
            entries = _search(q)
            if _pick_video(entries, romaji or title):
                break
        v = _pick_video(entries, romaji or title)
        if not v:
            return None, 'no opening found'
        url = f"https://www.youtube.com/watch?v={v['id']}"
        info = subprocess.run(_ytdlp() + ['-J', url], capture_output=True, text=True, timeout=90)
        heat = []
        if info.returncode == 0:
            heat = json.loads(info.stdout).get('heatmap') or []
        r = subprocess.run(_ytdlp() + ['-f', 'bestaudio[ext=m4a]/bestaudio', '--download-sections', '*0-120',
                                       '-o', audio_p, '--force-overwrites', url], capture_output=True, text=True, timeout=240)
        if r.returncode != 0 or not os.path.exists(audio_p):
            return None, 'download failed: ' + (r.stderr or '')[-160:]
        json.dump({'id': v['id'], 'title': v.get('title'), 'heatmap': heat}, open(meta_p, 'w', encoding='utf-8'))
    meta = json.load(open(meta_p, encoding='utf-8'))
    a = _decode(audio_p)
    n = int(seconds * SR)
    start = None
    heat = [h for h in meta.get('heatmap') or [] if h.get('end_time', 0) <= len(a) / SR]
    if heat:
        top = max(heat, key=lambda h: h.get('value', 0))
        start = max(0.0, float(top['start_time']))
    if start is None or start < 5:
        # loudest window between 15 s and 100 s (the chorus of a TV-size opening)
        win = int(0.5 * SR)
        rms = np.sqrt(np.convolve(a ** 2, np.ones(win) / win, mode='valid'))[::win // 4]
        lo, hi = int(15 * SR / (win // 4)), min(len(rms) - 1, int(100 * SR / (win // 4)))
        if hi > lo:
            k = lo + int(np.argmax(np.convolve(rms[lo:hi], np.ones(int(seconds * 8)), mode='same')))
            start = max(0.0, k * (win // 4) / SR - seconds / 2)
        else:
            start = 20.0
    seg = a[int(start * SR):int(start * SR) + n]
    if len(seg) < n * 0.8 or np.abs(seg).max() < 0.02:
        return None, 'snippet too short or silent'
    fade = int(0.08 * SR)
    seg = seg.copy()
    seg[:fade] *= np.linspace(0, 1, fade)
    seg[-fade:] *= np.linspace(1, 0, fade)
    seg *= 0.85 / (np.abs(seg).max() or 1)
    return seg, {'video': meta['id'], 'videoTitle': meta.get('title'), 'start': round(start, 2)}
