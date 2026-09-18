"""
Images, audio and video for the media quiz formats.

- fetch_image(url): downloads once into ~/.cache/quiz-media and returns a PIL image
- fetch_opening(title): finds an anime opening on YouTube with yt-dlp, downloads
  the first two minutes of audio and returns the most recognisable few seconds
  (YouTube's "most replayed" heatmap when available, else the loudest window)
- fetch_scene(title): the same moment of the same opening as a ~4 s video clip (<= 720p)
- fetch_voice(name, anime): ~3 s of a character speaking, cut from a "voice lines"
  video: the window with the most speech-band energy and the deepest pauses
  between words (music under the voice fills those pauses, so it scores low)

Needs yt-dlp (and on GitHub runners a cookies file in YT_COOKIES plus Deno for
yt-dlp's JS challenges) for the opening, scene and voice formats only.
"""
import hashlib
import json
import os
import re
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


_STOP = {'the', 'and', 'of', 'no', 'wa', 'ga', 'to', 'wo', 'ni', 'in', 'on', 'a', 'an', 'season', 'part', 'tv', 'x'}


def names_title(video_title, *titles):
    """True if the upload names the show: every distinctive word of a short English or romaji title
    (or of its part before ':'), or at least half and two of a long one. Search results drift to
    other shows ('Mob Psycho 100' found a Higurashi opening), and a wrong video is a wrong answer."""
    t = re.sub(r'[^a-z0-9]+', ' ', (video_title or '').lower())
    words = set(t.split())
    cands = []
    for title in titles:
        title = (title or '').lower()
        cands.append(title)
        for sep in (':', ' -', ' –'):
            head = title.split(sep)[0]
            if head != title and any(len(w) >= 4 for w in re.findall(r'[a-z0-9]+', head)):
                cands.append(head)
    for c in cands:
        toks = [w for w in re.sub(r'[^a-z0-9]+', ' ', c).split() if w not in _STOP and (len(w) >= 3 or w.isdigit())]
        need = len(toks) if len(toks) <= 2 else max(2, (len(toks) + 1) // 2)
        if toks and sum(w in words or (len(w) >= 5 and w in t) for w in toks) >= need:
            return True
    return False


def _pick_video(entries, title, *also):
    """Prefer real openings: 'opening'/'OP' in the title, 70 s - 5 min long, not covers or reactions."""
    bad = ('cover', 'reaction', 'piano', 'guitar', 'lyrics video', 'amv', 'karaoke', 'nightcore', 'remix', '8d', 'slowed')
    best, score = None, -1
    for e in entries:
        t = (e.get('title') or '').lower()
        d = e.get('duration') or 0
        if not (70 <= d <= 330) or any(b in t for b in bad) or not names_title(t, title, *also):
            continue
        s = (3 if ('opening' in t or ' op' in t or 'op ' in t) else 0) + (2 if 'creditless' in t or 'official' in t else 0)
        s += 2 if any(w in t for w in title.lower().split()[:2]) else 0
        if s > score:
            best, score = e, s
    return best


def _write_json(path, obj):
    """Serialise first, then write: a failed dump must not leave a half-written cache file behind."""
    s = json.dumps(obj, ensure_ascii=False)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(s)


def _download_audio(url, path, duration, max_sec):
    """Audio track to `path`; None on success, else the error tail. Short videos are fetched whole
    (yt-dlp's own ranged downloader); --download-sections goes through ffmpeg, which is slower and
    sometimes gets 403s, so it is only the first choice for long videos (and the fallback)."""
    whole = ['-f', 'bestaudio[ext=m4a]/bestaudio', '-N', '4']
    part = ['-f', 'bestaudio[ext=m4a]/bestaudio', '--download-sections', f'*0-{max_sec}']
    err = ''
    for args in ((whole, part) if (duration or 0) and duration <= 3 * max_sec else (part, whole)):
        r = subprocess.run(_ytdlp() + args + ['-o', path, '--force-overwrites', url], capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 10000:
            return None
        err = (r.stderr or '')[-160:]
    return err or 'no audio'


def _decode(path):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0


def _pick_scene_video(entries, title, *also):
    """For the scene quiz: a clean creditless first opening, no burned-in lyrics or subtitles."""
    bad = re.compile(r'\b(sub|subs|subbed|subtitles?|lyrics?|romaji|english|karaoke|cover|reaction|amv|mad|remix|nightcore|'
                     r'slowed|8d|piano|guitar|fan ?made|edit|upscale[d]?|vs|ranking|top|all)\b')
    later = re.compile(r'\b(op|opening|season|s)\s*([2-9]|1\d)\b')
    best, score = None, -1
    for e in entries:
        t = (e.get('title') or '').lower()
        d = e.get('duration') or 0
        if not (60 <= d <= 200) or bad.search(t) or later.search(t) or not names_title(t, title, *also):
            continue
        if not any(k in t for k in ('creditless', 'ncop', 'clean', 'textless', 'ノンクレジット')):
            continue  # credits (staff names) on screen would give titles away and clutter the scene
        s = (3 if ('opening' in t or re.search(r'\b(nc)?op\b', t)) else 0) + (3 if 'creditless' in t or 'ncop' in t or 'clean' in t else 0)
        s += 2 if any(w in t for w in title.lower().split()[:2]) else 0
        if s > score:
            best, score = e, s
    return best if score >= 5 else None


def fetch_opening(title, romaji=None, seconds=3.5, kind='op'):
    """(samples, info) for a recognisable snippet of the anime's opening, or (None, reason).
    kind='scene' looks for a clean creditless first opening (no subtitles) for the video quiz."""
    key = f'{kind}:{title}'
    meta_p = _cache_path(key, '.json')
    audio_p = _cache_path(key, '.m4a')
    pick = _pick_scene_video if kind == 'scene' else _pick_video
    queries = ((f'{romaji or title} opening 1 creditless', f'{title} creditless opening NCOP', f'{title} opening creditless 1080p')
               if kind == 'scene' else (f'{romaji or title} opening creditless', f'{title} opening full'))
    if not os.path.exists(meta_p):
        entries = []
        for q in queries:
            entries = _search(q, n=8 if kind == 'scene' else 6)
            if pick(entries, romaji or title, title):
                break
        v = pick(entries, romaji or title, title)
        if not v:
            return None, 'no opening found'
        url = f"https://www.youtube.com/watch?v={v['id']}"
        info = subprocess.run(_ytdlp() + ['-J', url], capture_output=True, text=True, timeout=90)
        heat = []
        if info.returncode == 0:
            heat = json.loads(info.stdout).get('heatmap') or []
        err = _download_audio(url, audio_p, v.get('duration'), 120)
        if err:
            return None, 'download failed: ' + err
        _write_json(meta_p, {'id': v['id'], 'title': v.get('title'), 'heatmap': heat})
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
        c = np.concatenate([[0.0], np.cumsum(a ** 2)])  # moving mean via cumsum (np.convolve here took minutes)
        rms = np.sqrt(np.maximum(0, (c[win:] - c[:-win]) / win))[::win // 4]
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


# ─── scene: a few seconds of the opening's video ─────────────────────────────

def probe_size(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                        '-of', 'json', path], capture_output=True, text=True)
    s = (json.loads(r.stdout or '{}').get('streams') or [{}])[0]
    return int(s.get('width') or 0), int(s.get('height') or 0)


def fetch_scene(title, romaji=None, seconds=4.0):
    """
    ({'path', 'audio'}, info): `seconds` of a clean creditless opening at its most replayed
    (else loudest) moment: a silent <= 720p clip plus the matching audio, or (None, reason).
    The whole opening is downloaded video-only (a ranged download of ~30 MB takes seconds;
    yt-dlp's --download-sections takes over a minute) and cut locally, then deleted.
    """
    audio, info = fetch_opening(title, romaji, seconds=seconds, kind='scene')
    if audio is None:
        return None, info
    p = _cache_path(f"scene:{info['video']}:{info['start']}:{seconds}", '.mp4')
    if not (os.path.exists(p) and os.path.getsize(p) > 20000):
        full = _cache_path(f"scenefull:{info['video']}", '.mp4')
        r = subprocess.run(_ytdlp() + ['-f', 'bv*[height<=720][vcodec^=avc1]/bv*[height<=720][ext=mp4]/bv*[height<=720]', '-N', '4',
                                       '-o', full, '--force-overwrites', f"https://www.youtube.com/watch?v={info['video']}"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(full):
            return None, 'video download failed: ' + (r.stderr or '')[-160:]
        c = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', f"{info['start']:.3f}", '-i', full, '-t', f'{seconds + 0.2:.2f}', '-an',
                            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', p], capture_output=True, text=True)
        try:
            os.remove(full)
        except OSError:
            pass
        if c.returncode != 0 or not os.path.exists(p) or os.path.getsize(p) < 20000:
            return None, 'cut failed: ' + (c.stderr or '')[-160:]
    w, h = probe_size(p)
    if w < 320 or h < 180:
        return None, f'video too small ({w}x{h})'
    crop = pillarbox(p, w, h)
    if crop:
        w, h = crop[0], crop[1]
    return {'path': p, 'audio': audio}, {**info, 'width': w, 'height': h, 'crop': crop}


def pillarbox(path, w, h):
    """[cw, ch, x, y] when a 4:3 show sits between black bars in a 16:9 upload (the card then takes
    the show's own shape), else None. Only symmetric bars around a 4:3 picture count, so a dark
    scene is never mistaken for bars."""
    r = subprocess.run(['ffmpeg', '-v', 'info', '-i', path, '-vf', 'cropdetect=limit=24:round=2:reset=0', '-f', 'null', '-'],
                       capture_output=True, text=True)
    m = re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', r.stderr or '')
    if not m:
        return None
    cw, ch, x, y = map(int, m[-1])
    if ch < h * 0.95 or abs(x - (w - cw) / 2) > 6 or not (1.28 <= cw / h <= 1.40):
        return None
    return [cw, h, x, 0]


# ─── voice: a character speaking ─────────────────────────────────────────────

VOICE_WANT = ('voice line', 'voiceline', 'voice pack', 'voice clip', 'iconic line', 'iconic quote', 'quotes', 'best lines',
              'all lines', 'lines', 'voice')
VOICE_BAD = ('amv', 'cover', 'sing', 'song', 'reaction', 'react', 'dub', 'english', 'ai voice', 'ai cover', 'tts', 'voice actor',
             'seiyuu', 'impression', 'cosplay', 'asmr', 'phonk', 'remix', 'slowed', 'nightcore', 'lyric', 'ranking', 'top 10',
             'mmd', 'vtuber', 'meme', 'ytp', 'parody', 'abridged', 'behind the', 'interview', 'live', 'moan', 'ecchi', 'hentai',
             'lewd', 'nsfw', 'kiss', 'flirt', 'girlfriend', 'boyfriend', 'sleep', 'roleplay', 'pov',
             # fans imitating the voice, not the actual voice actor
             'voice acting', 'voice act', 'voice over', 'trying', 'tried', 'try', 'attempt', 'imitation', 'impersonation',
             'challenge', 'my voice', 'fan', 'voiced by me', 'singing', 'karaoke',
             # music, effects and mash-ups rather than plain lines
             'music', 'bgm', 'ost', 'sound effect', 'sfx', 'laugh', 'scream', 'philosophy', 'motivation', 'motivational',
             'ringtone', 'alarm', 'quotes that', 'compilation of',
             # English narration / comparisons / other games' characters
             'realistic', 'comparison', 'compare', 'if the', 'speech', 'monologue', 'words', 'mlbb', 'mobile legends', 'eng',
             'anime quotes')
_VOICE_BAD_RE = re.compile(r'\b(' + '|'.join(re.escape(b) for b in VOICE_BAD) + r')\b')


def _name_tokens(name):
    toks = [t.lower().strip('.') for t in name.replace('-', ' ').split() if len(t.strip('.')) >= 3]
    out = set(toks)
    for t in toks:  # AniList romanisation vs. how uploaders spell it (Gojou / Gojo, Satoru)
        out.add(t.replace('ou', 'o').replace('uu', 'u'))
    return out


def _pick_voice_videos(entries, name, anime=None):
    toks = _name_tokens(name)
    show = [w for w in re.findall(r'[a-z0-9]+', (anime or '').lower().split(':')[0]) if len(w) >= 4]
    good = []
    for e in entries:
        t = (e.get('title') or '').lower()
        d = e.get('duration') or 0
        if not (8 <= d <= 1500) or _VOICE_BAD_RE.search(t):
            continue
        if not any(k in t.replace('ō', 'o').replace('ū', 'u') for k in toks) or not any(w in t for w in VOICE_WANT):
            continue
        s = (3 if 'voice line' in t or 'voice pack' in t or 'voiceline' in t else 1) + (1 if 'japanese' in t or 'jp' in t.split() else 0)
        s += (1 if 30 <= d <= 400 else 0) + (2 if show and any(w in t for w in show) else 0)
        good.append((s, e))
    return [e for s, e in sorted(good, key=lambda x: -x[0])]


def speech_windows(a, seconds=3.0, sr=SR, step=0.25, skip=1.0):
    """
    Score every `seconds` window of mono audio for "one person talking, little music":
    speech-band share of the energy (250-3500 Hz), depth of the pauses between words
    (p90 - p10 of the speech-band level: music or crowd noise fills the pauses) and the
    share of low end (bass/drums). Returns [(score, start_sec, details)], best first.
    """
    n_fft, hop = 1024, 512
    if len(a) < sr * (seconds + skip):
        return []
    x = a.astype(np.float32)
    nfr = (len(x) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    band_s = (freqs >= 250) & (freqs <= 3500)
    band_l = (freqs >= 20) & (freqs < 160)
    sp, lo, tot = np.zeros(nfr, np.float32), np.zeros(nfr, np.float32), np.zeros(nfr, np.float32)
    for c in range(0, nfr, 2048):  # chunked STFT keeps memory small
        idx = np.arange(n_fft)[None, :] + hop * np.arange(c, min(nfr, c + 2048))[:, None]
        spec = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        sp[c:c + len(idx)] = spec[:, band_s].sum(1)
        lo[c:c + len(idx)] = spec[:, band_l].sum(1)
        tot[c:c + len(idx)] = spec.sum(1) + 1e-9
    db = 10 * np.log10(sp + 1e-9)
    ref = np.percentile(db, 95)
    fps = sr / hop
    wl = int(seconds * fps)
    out = []
    for k in range(int(skip * fps), nfr - wl, max(1, int(step * fps))):
        d = db[k:k + wl]
        p10, p90 = np.percentile(d, 10), np.percentile(d, 90)
        depth = p90 - p10
        act = d > p90 - 10
        active = float(act.mean())
        ratio = float((sp[k:k + wl][act] / tot[k:k + wl][act]).mean())
        low = float((lo[k:k + wl][act] / tot[k:k + wl][act]).mean())
        # syllable rhythm: speech-band level moves 2-8 times a second
        env = d - d.mean()
        spec = np.abs(np.fft.rfft(env)) ** 2
        f = np.fft.rfftfreq(len(env), 1 / fps)
        mod = float(spec[(f >= 2) & (f <= 8)].sum() / (spec[f > 0.5].sum() + 1e-9))
        loud = p90 - ref
        ok = ratio >= 0.55 and depth >= 16 and 0.3 <= active <= 0.9 and low <= 0.25 and loud >= -8 and mod >= 0.35
        score = float(ratio * min(1.0, depth / 30) * (1 - low) * min(1.0, mod / 0.6) * (1.0 if ok else 0.3))
        out.append((score, k / fps, {'ratio': round(ratio, 2), 'depth': round(float(depth), 1), 'active': round(active, 2),
                                     'low': round(low, 2), 'mod': round(mod, 2), 'loud': round(float(loud), 1), 'ok': bool(ok)}))
    return sorted(out, key=lambda o: -o[0])


def _snap_to_pause(a, t, sr=SR, radius=0.3):
    """Move a cut point to the quietest 20 ms within +-radius seconds (so words are not cut in half)."""
    w = int(0.02 * sr)
    lo, hi = max(0, int((t - radius) * sr)), min(len(a) - w, int((t + radius) * sr))
    if hi <= lo:
        return t
    e = np.convolve(a[lo:hi + w] ** 2, np.ones(w) / w, mode='valid')
    return (lo + int(np.argmin(e))) / sr


def fetch_voice(name, anime=None, seconds=3.0):
    """(samples, info): ~`seconds` of the character talking with little music, or (None, reason)."""
    key = f'voice:{name}|{anime}'
    meta_p = _cache_path(key, '.json')
    if os.path.exists(meta_p):
        meta = json.load(open(meta_p, encoding='utf-8'))
        if meta.get('fail'):
            return None, meta['fail']
    else:
        short = (anime or '').split(':')[0].split(' -')[0].strip()
        tried, meta = set(), None
        for q in (f'{name} voice lines japanese', f'{name} {short} voice lines', f'{name} iconic lines'):
            for e in _pick_voice_videos(_search(q, n=8), name, anime)[:3]:
                if e['id'] in tried or len(tried) >= 5:
                    continue
                tried.add(e['id'])
                p = _cache_path(f"voiceaudio:{e['id']}", '.m4a')
                if not os.path.exists(p) and _download_audio(f"https://www.youtube.com/watch?v={e['id']}", p, e.get('duration'), 180):
                    continue
                wins = speech_windows(_decode(p)[:SR * 240], seconds + 0.6)
                if wins and wins[0][2]['ok']:
                    meta = {'id': e['id'], 'title': e.get('title'), 'start': round(wins[0][1] + 0.3, 2), 'score': round(wins[0][0], 3),
                            'stats': wins[0][2]}
                    break
            if meta:
                break
        if not meta:
            _write_json(meta_p, {'fail': f'no clean speech in {len(tried)} videos'})
            return None, f'no clean speech in {len(tried)} videos'
        _write_json(meta_p, meta)
    a = _decode(_cache_path(f"voiceaudio:{meta['id']}", '.m4a'))
    s = _snap_to_pause(a, meta['start'])
    e = _snap_to_pause(a, s + seconds, radius=0.35)
    seg = a[int(s * SR):int(e * SR)].copy()
    if len(seg) < SR * (seconds - 0.8):
        return None, 'clip too short'
    fade = int(0.03 * SR)
    seg[:fade] *= np.linspace(0, 1, fade)
    seg[-fade:] *= np.linspace(1, 0, fade)
    seg *= 0.85 / (np.abs(seg).max() or 1)
    return seg, {'video': meta['id'], 'videoTitle': meta.get('title'), 'start': round(s, 2)}
