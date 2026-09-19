"""
Images, audio and video for the media quiz formats.

- fetch_image(url): downloads once into ~/.cache/quiz-media and returns a PIL image
- fetch_opening(title): finds an anime opening on YouTube with yt-dlp, downloads
  the first two minutes of audio and returns the most recognisable few seconds
  (YouTube's "most replayed" heatmap when available, else the loudest window)
- fetch_scene(title): the same moment of the same opening as a ~4 s video clip (<= 720p)
- fetch_voice(name, anime): ~3 s of a character speaking, cut from a "voice lines"
  video that names the character and the show: the window with the most speech-band
  energy and the deepest pauses between words, confirmed as Japanese speech with a
  few words by faster-whisper (tiny, CPU), so laughs, grunts and music are rejected
- fetch_song(song): a K-pop / anime / J-pop / C-pop song (songs.json entry or an emoji
  answer) at its most replayed moment: audio plus the matching music-video clip; the
  upload must pass the song's match rule (title and artist named, no covers/remixes)
- commons_photo(query): a free-licence photo from Wikimedia Commons (author and licence
  kept for the credits), for the would-you-rather picks

Needs yt-dlp (and on GitHub runners a cookies file in YT_COOKIES plus Deno for
yt-dlp's JS challenges) for the opening, scene, voice, song and emoji-reveal clips only.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

CACHE = os.environ.get('QUIZ_MEDIA_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'quiz-media')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
BOT_UA = 'AsianPopQuizRenderer/1.0 (quiz video renderer; python-urllib)'  # what Wikimedia's UA policy asks for
SR = 44100
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')


def _cache_path(key, ext):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest()[:20] + ext)


# ─── subprocesses that a time budget can stop ────────────────────────────────

_procs = set()
_procs_lock = threading.Lock()
_stopped = threading.Event()


class Cancelled(RuntimeError):
    pass


def _run(cmd, timeout=300, text=True):
    """subprocess.run(capture_output=True) that cancel_all() can stop: a fetch past its time budget
    must not keep downloading (or hold the process open) while the video renders."""
    if _stopped.is_set():
        raise Cancelled('fetch budget used up')
    kw = {'encoding': 'utf-8', 'errors': 'replace'} if text else {}
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    with _procs_lock:
        _procs.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    finally:
        with _procs_lock:
            _procs.discard(p)
    if _stopped.is_set():
        raise Cancelled('fetch budget used up')
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def cancel_all():
    """Stop every running fetch (yt-dlp / ffmpeg) and make new ones fail fast."""
    _stopped.set()
    with _procs_lock:
        for p in list(_procs):
            try:
                p.kill()
            except Exception:
                pass


def run_parallel(fn, items, workers=4, budget=None, enough=None):
    """
    fn(item) for every item on daemon threads (a thread past the budget must not keep the
    process alive at exit). Returns {index: (result, error)} for what finished in time; waiting
    stops after `budget` seconds or once enough(done) is true, and stragglers are cancelled.
    """
    import queue
    _stopped.clear()
    todo = queue.Queue()
    for k, it in enumerate(items):
        todo.put((k, it))
    done, lock, finished = {}, threading.Lock(), threading.Event()

    def worker():
        while not _stopped.is_set():
            try:
                k, it = todo.get_nowait()
            except queue.Empty:
                return
            try:
                res = (fn(it), None)
            except Exception as e:
                res = (None, e)
            with lock:
                done[k] = res
                if len(done) == len(items) or (enough and enough(dict(done))):
                    finished.set()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(workers, len(items))))]
    for t in threads:
        t.start()
    if items:
        finished.wait(budget)
    with lock:
        out = dict(done)
    if len(out) < len(items):
        cancel_all()  # budget used up, or enough already: stop the rest
    return out


def fetch_image(url, tries=5):
    p = _cache_path(url, '.img')
    if not (os.path.exists(p) and os.path.getsize(p) > 1000):
        # Wikimedia throttles browser-looking clients (HTTP 429): name the tool, and back off when told to
        ua = BOT_UA if 'wikimedia.org' in url or 'wikipedia.org' in url else UA
        for k in range(tries):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': ua})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or k == tries - 1:
                    raise
                wait = e.headers.get('Retry-After') if e.headers else None
                time.sleep(min(30.0, float(wait)) if wait and str(wait).isdigit() else 2.0 * 2 ** k)
        with open(p, 'wb') as f:
            f.write(data)
    return Image.open(p).convert('RGBA')


def _ytdlp(cookies=True):
    base = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '--no-playlist']
    path = os.environ.get('YT_COOKIES')
    if cookies and path and os.path.exists(path):
        base += ['--cookies', path]
    return base


# a throttled or flagged login session gets these for videos that play fine without it
_SESSION_BLOCK = re.compile(r"video unavailable|not a bot|sign in to confirm|rate.?limit|content isn.t available|http error 429", re.I)


def _yt(args, timeout=300):
    """yt-dlp with the cookies; when the logged-in session is refused ('Video unavailable' for a
    public video, bot check, rate limit), once more without them."""
    r = _run(_ytdlp() + args, timeout=timeout)
    if r.returncode != 0 and os.environ.get('YT_COOKIES') and _SESSION_BLOCK.search(r.stderr or ''):
        r2 = _run(_ytdlp(cookies=False) + args, timeout=timeout)
        if r2.returncode == 0:
            return r2
    return r


def _search(query, n=6):
    r = _yt(['-J', '--flat-playlist', f'ytsearch{n}:{query}'], timeout=90)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout).get('entries') or []
    except ValueError:
        return []


def _video_info(vid):
    """Full metadata of one video (for the "most replayed" heatmap), or {}."""
    r = _yt(['-J', f'https://www.youtube.com/watch?v={vid}'], timeout=90)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        return {}


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


def _err(stderr):
    """The line of yt-dlp / ffmpeg output that says what went wrong (not a deprecation notice)."""
    lines = [l.strip() for l in str(stderr or '').splitlines() if l.strip()]
    errs = [l for l in lines if 'ERROR' in l or 'error' in l.lower()]
    return ((errs or lines or [''])[-1])[-200:]


def _download_audio(url, path, duration, max_sec):
    """Audio track to `path`; None on success, else the error tail. Short videos are fetched whole
    (yt-dlp's own ranged downloader); --download-sections goes through ffmpeg, which is slower and
    sometimes gets 403s, so it is only the first choice for long videos (and the fallback)."""
    whole = ['-f', 'bestaudio[ext=m4a]/bestaudio', '-N', '4']
    part = ['-f', 'bestaudio[ext=m4a]/bestaudio', '--download-sections', f'*0-{max_sec}']
    err = ''
    for args in ((whole, part) if (duration or 0) and duration <= 3 * max_sec else (part, whole)):
        r = _yt(args + ['-o', path, '--force-overwrites', url], timeout=300)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 10000:
            return None
        err = _err(r.stderr)
    return err or 'no audio'


def _decode(path):
    r = _run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'], timeout=120, text=False)
    if r.returncode != 0:
        raise RuntimeError('decode failed: ' + (r.stderr or b'')[-160:].decode('utf-8', 'replace'))
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0


def _best_window(a, heat, seconds, lo=15.0, hi=100.0, skip_end=0.0):
    """Start (s) of the most recognisable `seconds`: YouTube's "most replayed" peak when the video
    has one (ignoring the first seconds and the fade-out), else the loudest stretch in [lo, hi]."""
    dur = len(a) / SR
    heat = [h for h in heat or [] if 5 <= h.get('start_time', 0) and h.get('end_time', 0) <= dur - skip_end]
    if heat:
        top = max(heat, key=lambda h: h.get('value', 0))
        return max(0.0, min(float(top['start_time']), dur - seconds))
    win = int(0.5 * SR)
    c = np.concatenate([[0.0], np.cumsum(a ** 2)])  # moving mean via cumsum (np.convolve here took minutes)
    rms = np.sqrt(np.maximum(0, (c[win:] - c[:-win]) / win))[::win // 4]
    i0, i1 = int(lo * SR / (win // 4)), min(len(rms) - 1, int(min(hi, dur - seconds - skip_end) * SR / (win // 4)))
    if i1 > i0:
        k = i0 + int(np.argmax(np.convolve(rms[i0:i1], np.ones(int(min(seconds, 4.0) * 8)), mode='same')))
        return max(0.0, k * (win // 4) / SR - min(seconds, 4.0) / 2)
    return min(20.0, max(0.0, dur - seconds))


def _cut(a, start, seconds, fade_in=0.08, fade_out=0.08, peak=0.85):
    seg = a[int(start * SR):int(start * SR) + int(seconds * SR)].copy()
    if len(seg) < int(seconds * SR) * 0.8 or np.abs(seg).max() < 0.02:
        return None
    fi, fo = int(fade_in * SR), int(fade_out * SR)
    seg[:fi] *= np.linspace(0, 1, fi)
    seg[-fo:] *= np.linspace(1, 0, fo)
    return seg * (peak / (np.abs(seg).max() or 1))


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
        heat = _video_info(v['id']).get('heatmap') or []
        err = _download_audio(url, audio_p, v.get('duration'), 120)
        if err:
            return None, 'download failed: ' + err
        _write_json(meta_p, {'id': v['id'], 'title': v.get('title'), 'heatmap': heat})
    meta = json.load(open(meta_p, encoding='utf-8'))
    a = _decode(audio_p)
    # the chorus of a TV-size opening: the most replayed moment, else the loudest stretch in 15-100 s
    heat = [h for h in meta.get('heatmap') or [] if h.get('end_time', 0) <= len(a) / SR]
    start = _best_window(a, heat, seconds)
    seg = _cut(a, start, seconds)
    if seg is None:
        return None, 'snippet too short or silent'
    return seg, {'video': meta['id'], 'videoTitle': meta.get('title'), 'start': round(start, 2)}


# ─── scene: a few seconds of the opening's video ─────────────────────────────

def probe_size(path):
    r = _run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', path], timeout=60)
    try:
        s = (json.loads(r.stdout or '{}').get('streams') or [{}])[0]
    except ValueError:
        s = {}
    return int(s.get('width') or 0), int(s.get('height') or 0)


def cut_video(vid, start, seconds):
    """(path, info) of a silent <= 720p clip of YouTube video `vid` from `start`, or (None, reason).
    The whole video is downloaded video-only (a ranged download of ~30 MB takes seconds; yt-dlp's
    --download-sections takes over a minute), cut locally, then deleted. `info` has the picture's
    size and a crop box when the upload has black bars around the real picture."""
    p = _cache_path(f'scene:{vid}:{start}:{seconds}', '.mp4')
    if not (os.path.exists(p) and os.path.getsize(p) > 20000):
        full = _cache_path(f'scenefull:{vid}', '.mp4')
        r = _yt(['-f', 'bv*[height<=720][vcodec^=avc1]/bv*[height<=720][ext=mp4]/bv*[height<=720]', '-N', '4',
                             '-o', full, '--force-overwrites', f'https://www.youtube.com/watch?v={vid}'], timeout=300)
        if r.returncode != 0 or not os.path.exists(full):
            return None, 'video download failed: ' + _err(r.stderr)
        tmp = p + '.part.mp4'
        c = _run(['ffmpeg', '-y', '-v', 'error', '-ss', f'{start:.3f}', '-i', full, '-t', f'{seconds + 0.2:.2f}', '-an',
                  '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', tmp], timeout=180)
        try:
            os.remove(full)
        except OSError:
            pass
        if c.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 20000:
            return None, 'cut failed: ' + _err(c.stderr)
        os.replace(tmp, p)  # never leave a half-written clip in the cache
    w, h = probe_size(p)
    if w < 320 or h < 180:
        return None, f'video too small ({w}x{h})'
    crop = pillarbox(p, w, h)
    if crop:
        w, h = crop[0], crop[1]
    return p, {'width': w, 'height': h, 'crop': crop}


def fetch_scene(title, romaji=None, seconds=4.0, audio_seconds=None):
    """
    ({'path', 'audio'}, info): `seconds` of a clean creditless opening at its most replayed
    (else loudest) moment: a silent <= 720p clip plus the matching audio (`audio_seconds` long,
    so the music can play on after the picture), or (None, reason).
    """
    audio, info = fetch_opening(title, romaji, seconds=max(seconds, audio_seconds or 0), kind='scene')
    if audio is None:
        return None, info
    p, v = cut_video(info['video'], info['start'], seconds)
    if p is None:
        return None, v
    return {'path': p, 'audio': audio}, {**info, **v}


def pillarbox(path, w, h):
    """[cw, ch, x, y] when the real picture sits between black bars (a 4:3 show in a 16:9 upload,
    or a cinemascope music video letterboxed in 16:9): the card then takes the picture's own shape.
    Only symmetric bars count, so a dark scene is never mistaken for bars."""
    r = _run(['ffmpeg', '-v', 'info', '-i', path, '-vf', 'cropdetect=limit=24:round=2:reset=0', '-f', 'null', '-'], timeout=120)
    m = re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', r.stderr or '')
    if not m:
        return None
    cw, ch, x, y = map(int, m[-1])
    if ch >= h * 0.95 and abs(x - (w - cw) / 2) <= 6 and 1.28 <= cw / h <= 1.40:
        return [cw, h, x, 0]  # pillarbox
    if cw >= w * 0.97 and 0.6 <= ch / h <= 0.92 and abs(y - (h - ch) / 2) <= 6 and cw / ch <= 2.5:
        return [w, ch, 0, y]  # letterbox
    return None


# ─── songs: the real song at its hook, plus its music video ─────────────────

def _fold(s):
    """Lower case, accents dropped, punctuation -> spaces ('Pokémon' -> 'pokemon', "God's" -> 'gods')."""
    s = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', str(s or ''))).lower()
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn').replace("'", '').replace('’', '')
    return ' ' + re.sub(r'[^\w]+', ' ', s).strip() + ' '


def _has(hay, phrase):
    p = _fold(phrase).strip()
    if not p:
        return False
    return (p in hay) if re.search(r'[^\x00-\x7f]', p) else (f' {p} ' in hay)


def song_matches(song, video_title, channel=''):
    """The song's match rule: every group in song['match'] (a list of alternatives) must be named by
    the upload's title or channel, e.g. [["how you like that"], ["blackpink"]]. Without a rule the
    title and the artist must both be named. Covers, remixes, live stages and edits never pass."""
    hay = _fold(video_title) + _fold(channel)
    if SONG_BAD.search(_fold(video_title)):
        return False
    rule = song.get('match') or [[song['title']] + list(song.get('aliases') or []), [song['artist']]]
    return all(any(_has(hay, alt) for alt in group) for group in rule)


# matched against _fold()ed titles (lower case, no accents: 'sub español' is ' sub espanol ')
SONG_BAD = re.compile(r' (cover|covered|covers|reaction|reacts?|reacting|remix|sped up|speed up|slowed|reverb|nightcore|8d|karaoke|'
                      r'instrumental|inst|piano|guitar|violin|drum|drums|acoustic|dance practice|practice|dance cover|mirror|mirrored|'
                      r'fancam|fan cam|live|concert|stage|tour|showcase|english ver|english version|eng ver|'
                      r'japanese ver|japanese version|jp ver|chinese ver|ai cover|ai version|mashup|medley|hour|hours|loop|'
                      r'extended|teaser|behind|making|tutorial|lesson|amv|mad|edit|album|playlist|fmv|fanmade|fan made|shorts|'
                      r'tiktok|8 bit|8bit|lofi|lo fi|music box|orchestra|orchestral|choreography|parody|meme|ranking|top \d+|'
                      r'reversed|bass boosted|mix|remastered live|full album|trailer|preview|countdown|inkigayo|music bank|'
                      r'mcountdown|show champion|sub espanol|espanol|legendado|vostfr|turkce|traducao|clean|radio edit|'
                      r'gameplay|cinematic|fan version) ')


def _song_score(e, song):
    t = _fold(e.get('title'))
    s = 0.0
    if re.search(r' (creditless|ncop|nced|textless|clean) ', t):
        s += 4 if song.get('type') == 'anime' else 1
    if re.search(r' (m v|mv|music video|official video|official mv) ', t):
        s += 4
    if ' official ' in t:
        s += 1.5
    if e.get('channel_is_verified'):
        s += 2
    if re.search(r' (audio|visualizer|visualiser|mp3|lyric|lyrics|lyric video) ', t) or ' topic ' in _fold(e.get('channel')):
        s -= 3  # a still picture: fine for the sound, not for the reveal
    if re.search(r' (sub|subs|subbed|subtitles|romaji|eng sub|english sub) ', t):
        s -= 1.5  # subtitles may be burned into the picture
    s += min(4.0, np.log10(max(10, e.get('view_count') or 10)) / 2)
    return s


def pick_song_video(entries, song):
    ok = [e for e in entries if 60 <= (e.get('duration') or 0) <= 420 and song_matches(song, e.get('title'), e.get('channel'))]
    return max(ok, key=lambda e: _song_score(e, song)) if ok else None


def fetch_song(song, seconds=8.0, video=True):
    """
    ({'audio', 'path' or None, 'thumb'}, info) for a song (songs.json entry or an emoji answer:
    title, artist, query, match): `seconds` of audio from the upload's most replayed moment (the
    chorus / hook), and when `video` a silent clip of the same moment; or (None, reason).
    Only an upload that passes song_matches() is ever used, so a search that drifts to another
    song (or a cover) is a failed fetch, never a wrong answer.
    """
    key = f"song3:{song.get('title')}|{song.get('artist')}|{song.get('query')}|{json.dumps(song.get('match'))}"
    meta_p = _cache_path(key, '.json')
    meta = json.load(open(meta_p, encoding='utf-8')) if os.path.exists(meta_p) else None
    if meta and meta.get('fail') and time.time() - meta.get('at', 0) < 7 * 86400:
        return None, meta['fail']
    if not meta or meta.get('fail'):
        queries = [song.get('query')] if song.get('query') else []
        queries += [f"{song['artist']} {song['title']} official MV", f"{song['title']} {song['artist']}"]
        v, seen = None, set()
        for q in dict.fromkeys(q for q in queries if q):
            entries = [e for e in _search(q, n=8) if e.get('id') not in seen]
            seen.update(e.get('id') for e in entries)
            v = pick_song_video(entries, song)
            if v:
                break
        if not v:
            if seen:  # searches worked, nothing matched: remember for a week (a failed search is retried next time)
                _write_json(meta_p, {'fail': 'no upload passed the match rule', 'at': time.time()})
            return None, 'no upload passed the match rule' if seen else 'search failed'
        heat = _video_info(v['id']).get('heatmap') or []
        audio_p = _cache_path(f"songaudio:{v['id']}", '.m4a')
        err = _download_audio(f"https://www.youtube.com/watch?v={v['id']}", audio_p, v.get('duration'), 240)
        if err:
            return None, 'download failed: ' + err
        meta = {'id': v['id'], 'title': v.get('title'), 'channel': v.get('channel'), 'heatmap': heat}
        _write_json(meta_p, meta)
    a = _decode(_cache_path(f"songaudio:{meta['id']}", '.m4a'))
    start = _best_window(a, meta.get('heatmap'), seconds, lo=20.0, hi=150.0, skip_end=8.0)
    seg = _cut(a, start, seconds)
    if seg is None:
        return None, 'snippet too short or silent'
    info = {'video': meta['id'], 'videoTitle': meta.get('title'), 'channel': meta.get('channel'), 'start': round(start, 2),
            'thumb': f"https://i.ytimg.com/vi/{meta['id']}/maxresdefault.jpg"}
    out = {'audio': seg, 'path': None}
    # lyric / subtitled uploads would put the words on screen: the reveal shows the thumbnail instead
    lyric = re.search(r' (lyric|lyrics|sub|subs|subbed|subtitles|romaji|eng sub|english sub) ', _fold(meta.get('title'))) \
        or any(x in str(meta.get('title')) for x in ('歌詞', '字幕'))
    if video and song.get('clip', True) and not lyric:
        p, vi = cut_video(meta['id'], info['start'], seconds)
        if p is None:
            info['videoError'] = vi
        else:
            out['path'] = p
            info.update(vi)
    return out, info


def fetch_thumb(vid):
    """The upload's own thumbnail (1280x720, else the 480x360 one), as a PIL image, or None."""
    for name in ('maxresdefault', 'hqdefault'):
        try:
            img = fetch_image(f'https://i.ytimg.com/vi/{vid}/{name}.jpg', tries=2)
            if img.size[0] >= 400:
                return img
        except Exception:
            continue
    return None


# ─── Wikimedia Commons photos (would-you-rather picks) ───────────────────────

COMMONS_API = 'https://commons.wikimedia.org/w/api.php'
FREE_LICENCE = re.compile(r'^(cc0|cc[ -]by([ -]sa)?([ -]\d\.\d)?|public domain|pd\b|attribution)', re.I)
PHOTO_BAD = re.compile(r'\b(nude|naked|nsfw|sex|sexy|erotic|porn|bikini|lingerie|underwear|topless|breast|swimsuit|gore|corpse|dead|'
                       r'blood|war|execution|nazi|weapon|gun|protest|riot|logo|map|diagram|chart|flag|coat of arms|drawing|'
                       r'painting|poster|screenshot|svg|president|white house|senator|minister|parliament|election|campaign|'
                       r'rally|military|army|soldier|police|funeral|wedding|hospital|accident|damage|disaster)\b', re.I)


_commons_lock = threading.Lock()
_commons_last = [0.0]


def _commons_json(params):
    url = COMMONS_API + '?' + urllib.parse.urlencode({**params, 'format': 'json'})
    for k in range(5):
        with _commons_lock:  # one API call at a time, >= 1 s apart (the API answers bursts with HTTP 429)
            time.sleep(max(0.0, _commons_last[0] + 1.0 - time.time()))
            _commons_last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': BOT_UA}), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or k == 4:
                raise
            wait = e.headers.get('Retry-After') if e.headers else None
            time.sleep(min(30.0, float(wait)) if wait and str(wait).isdigit() else 3.0 * 2 ** k)
    return {}


def _commons_meta(page):
    ii = (page.get('imageinfo') or [{}])[0]
    meta = ii.get('extmetadata') or {}
    artist = re.sub(r'\s+', ' ', re.sub('<[^>]+>', '', (meta.get('Artist') or {}).get('value', ''))).strip()
    return {'url': ii.get('thumburl') or ii.get('url'), 'page': ii.get('descriptionurl'), 'file': page.get('title', '')[5:],
            'width': ii.get('width') or 0, 'height': ii.get('height') or 0, 'mime': ii.get('mime', ''),
            'license': (meta.get('LicenseShortName') or {}).get('value', ''), 'author': artist[:80] or 'Wikimedia Commons',
            'nonfree': bool((meta.get('NonFree') or {}).get('value'))}


def commons_photo(query, min_w=800):
    """
    (PIL image, {'credit', 'page', 'file'}) for a Wikimedia Commons photo, or (None, reason).
    `query` is a search ('tonkotsu ramen bowl') or 'file:<exact file name>'. Only JPEG/PNG photos
    at least `min_w` wide with a free licence count (author and licence kept for the credits);
    file names that suggest maps, logos, drawings or anything unsafe are skipped.
    """
    key = f'commons2:{query}:{min_w}'  # v2: file names must match the query
    meta_p = _cache_path(key, '.json')
    meta = json.load(open(meta_p, encoding='utf-8')) if os.path.exists(meta_p) else None
    if meta is None or (meta.get('fail') and time.time() - meta.get('at', 0) > 7 * 86400):
        props = {'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': 1280}
        try:
            if query.startswith('file:'):
                d = _commons_json({'action': 'query', 'titles': 'File:' + query[5:].strip(), **props})
            else:
                d = _commons_json({'action': 'query', 'generator': 'search', 'gsrsearch': f'{query} filetype:bitmap',
                                   'gsrnamespace': 6, 'gsrlimit': 15, **props})
        except Exception as e:
            return None, f'commons: {str(e)[:100]}'
        pages = sorted((d.get('query') or {}).get('pages', {}).values(), key=lambda p: p.get('index', 0))
        # a search result must be named after what was searched ('BTS' found a storm-damage photo whose
        # description mentioned it): rank by how many query words the file name has, then by relevance
        words = [w for w in _fold(query).split() if len(w) >= 3 and w not in ('the', 'and', 'with', 'photo', 'image')]
        hits = lambda m: sum(f' {w}' in _fold(m['file']) for w in words)
        meta, best = {'fail': 'no free photo found', 'at': time.time()}, None
        for k, p in enumerate(pages):
            m = _commons_meta(p)
            if (m['mime'] in ('image/jpeg', 'image/png') and m['width'] >= min_w and 0.5 <= m['width'] / max(1, m['height']) <= 2.4
                    and not m['nonfree'] and FREE_LICENCE.match(m['license'] or '') and m['url']
                    and (query.startswith('file:') or (not PHOTO_BAD.search(m['file']) and hits(m) >= max(1, (len(words) + 1) // 2)))):
                score = (hits(m) if not query.startswith('file:') else 0, -k)
                if best is None or score > best[0]:
                    best = (score, m)
        if best:
            meta = best[1]
        _write_json(meta_p, meta)
    if meta.get('fail'):
        return None, meta['fail']
    try:
        img = fetch_image(meta['url'])
    except Exception as e:
        return None, f'download: {str(e)[:100]}'
    return img, {'credit': f"{meta['author']}, {meta['license']}", 'page': meta['page'], 'file': meta['file']}


# ─── voice: a character speaking ─────────────────────────────────────────────

# plain voice-line uploads ("Killua Voice Lines (Japanese)", game voice packs), not scene compilations
VOICE_WANT = ('voice line', 'voiceline', 'voice pack', 'voice clip', 'voice collection', 'voice sample', 'all voice', ' voices ')
VOICE_WEAK = (' japanese ', ' jp ', ' quotes ', ' lines ')  # "Sanji Voice (Japanese)": ' voice ' plus one of these
VOICE_BAD = ('amv', 'cover', 'sing', 'song', 'reaction', 'react', 'dub', 'english', 'ai voice', 'ai cover', 'tts', 'voice actor',
             'voice actors', 'voice actress', 'actress', 'actor', 'reads', 'by me', 'voice by me', 'dubbed by',
             'roles', 'role', 'cast', 'famous', 'same voice', 'characters', 'everyone', 'mlbb', 'mobile legends',
             'codm', 'call of duty', 'garena', 'pubg',
             # another game's hero wearing the character's skin: maybe another voice
             'skin', 'skins', 'collab skin', 'honor of kings', 'hok', 'free fire', 'fortnite', 'brawl stars',
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
    toks = [t for t in _fold(name.replace('-', ' ')).split() if len(t) >= 3]
    out = set(toks)
    for t in toks:  # AniList romanisation vs. how uploaders spell it (Gojou / Gojo, Satoru)
        out.add(t.replace('ou', 'o').replace('uu', 'u'))
    return out


_chars = None
# words that are also character names ('Power', 'Light', 'Mob'): another character's name only
# disqualifies an upload when it is not an everyday word
_COMMON_WORDS = {'power', 'light', 'king', 'queen', 'lord', 'rock', 'star', 'moon', 'mob', 'rem', 'ram', 'ace', 'law', 'zero',
                 'sky', 'boy', 'girl', 'man', 'kid', 'lady', 'captain', 'doctor', 'master', 'sensei', 'all', 'might', 'death',
                 'god', 'devil', 'angel', 'ghost', 'shadow', 'beast', 'lines', 'line', 'voice', 'best', 'the', 'and', 'san', 'kun',
                 'chan', 'sama', 'senpai', 'black', 'white', 'red', 'blue', 'gold', 'silver', 'jojo', 'japanese', 'anime', 'game'}


def _char_index():
    """token -> names of the snapshot characters that use it; English title -> romaji of the shows."""
    global _chars
    if _chars is None:
        idx, romaji = {}, {}
        try:
            for c in json.load(open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']:
                for t in _name_tokens(c['name']):
                    idx.setdefault(t, set()).add(c['name'])
            for a in json.load(open(os.path.join(ASSETS, 'anime-list.json'), encoding='utf-8'))['anime']:
                romaji[str(a.get('title') or '').lower()] = a.get('romaji')
        except Exception:
            pass
        _chars = (idx, romaji)
    return _chars


def voice_tokens(name):
    """The tokens of `name` no other snapshot character shares ('uchiha' is Sasuke's and Itachi's,
    so only 'sasuke' identifies Sasuke). Empty: the character cannot be told apart by title."""
    idx = _char_index()[0]
    return {t for t in _name_tokens(name) if not (idx.get(t, set()) - {name}) and t not in _COMMON_WORDS}


# how uploaders shorten the big shows
SHOW_ABBR = {'attack on titan': ('aot', 'snk', 'shingeki no kyojin'), 'jujutsu kaisen': ('jjk',), 'demon slayer': ('kny', 'kimetsu no yaiba'),
             'my hero academia': ('mha', 'bnha', 'boku no hero'), 'hunter x hunter': ('hxh',), 'one punch man': ('opm',),
             'fullmetal alchemist': ('fma', 'fmab'), 'sword art online': ('sao',), 'chainsaw man': ('csm',), 'jojo': ('jjba', 'jojo'),
             'haikyu': ('haikyuu',), 'spy x family': ('spy family',), 'dragon ball': ('dbz', 'dbs'), 'blue lock': ('bluelock',)}


def show_named(video_title, anime):
    """The upload names the character's show (English or romaji title, or a common abbreviation)."""
    if not anime:
        return False
    romaji = _char_index()[1].get(str(anime).lower())
    if names_title(video_title, anime, romaji or ''):
        return True
    t, a = _fold(video_title), _fold(anime)
    return any(f' {k} ' in a and any(f' {x} ' in t for x in v) for k, v in SHOW_ABBR.items())


def voice_title_ok(title, name, anime=None):
    """The upload title rule for the voice quiz: plain voice lines (not covers, dubs, fan imitations,
    voice-actor compilations, other games' skins or edits) that name the character by a token no
    other snapshot character has, plus the show (or, for two-word names, the full name), and that
    name no other character. Returns the cleaned title for scoring, or None."""
    mine = voice_tokens(name)
    if not mine:
        return None
    idx, romaji = _char_index()
    # the character's own name and the show's title words ('Naruto' in "Naruto Storm - Itachi Voice") are not another speaker
    own = _name_tokens(name) | set(_fold(anime).split()) | set(_fold(romaji.get(str(anime or '').lower())).split())
    full = [t for t in _fold(name.replace('-', ' ')).split() if len(t) >= 3]
    # "(no bg music)" / "without background music" is exactly what we want, not a music upload
    t = re.sub(r' (no|without|w o|removed|remove) (bg |background )?(musics?|bgm|ost) ', ' ', _fold(title))
    want = any(w in t for w in VOICE_WANT) or (' voice ' in t and any(w in t for w in VOICE_WEAK))
    if _VOICE_BAD_RE.search(t) or 'mlbb' in t or not want:
        return None
    full_name = len(full) >= 2 and all(f' {w} ' in t or f" {w.replace('ou', 'o').replace('uu', 'u')} " in t for w in full)
    if not any(f' {k} ' in t for k in mine) or not (full_name or show_named(title, anime)):
        return None
    if any(n != name for w in t.split() if len(w) >= 4 and w not in _COMMON_WORDS and w not in own for n in idx.get(w, ())):
        return None  # "Gojo vs Sukuna voice lines": another speaker
    return t


def _pick_voice_videos(entries, name, anime=None):
    good = []
    for e in entries:
        d = e.get('duration') or 0
        t = voice_title_ok(e.get('title'), name, anime) if 8 <= d <= 1500 else None
        if t is None:
            continue
        s = (3 if 'voice line' in t or 'voice pack' in t or 'voiceline' in t else 1) + (2 if ' all ' in t and 'voice' in t else 0)
        s += (1 if ' japanese ' in t or ' jp ' in t else 0) + (1 if 30 <= d <= 400 else 0)
        good.append((s, e))
    return [e for s, e in sorted(good, key=lambda x: -x[0])]


# ─── speech check: faster-whisper tiny on CPU ────────────────────────────────

_whisper = None
_whisper_lock = threading.Lock()
# what Whisper writes for music or silence ("thanks for watching", "subscribe"), and laughs
_HALLUCINATION = re.compile(r'(ご視聴ありがとうございました|チャンネル登録|ご覧いただきありがとう|字幕|thank you for watching)', re.I)
_LAUGH = re.compile(r'^(?:[はハふフへヘほホひヒあアうウえエおオっッーぁぃぅぇぉゃゅょ〜~!?！？、。…・\s]|ha|he|hi|ho|ah|oh|uh|hm|mm)+$', re.I)


def _whisper_model():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(os.environ.get('VOICE_CHECK_MODEL', 'tiny'), device='cpu', compute_type='int8',
                                cpu_threads=max(1, min(4, os.cpu_count() or 1)))
    return _whisper


def speech_check(seg, sr=SR):
    """
    (ok, info) for a candidate voice clip: Japanese speech with a few words and a low no-speech
    probability. Laughs, grunts, screams, music-only clips and Whisper's stock hallucinations fail.
    ok is None when the checker is not installed (the caller then rejects the clip).
    """
    try:
        with _whisper_lock:
            m = _whisper_model()
    except Exception as e:
        return None, {'error': f'faster-whisper unavailable: {str(e)[:80]}'}
    x = np.asarray(seg, dtype=np.float32)
    x = np.convolve(x, np.ones(3, np.float32) / 3, mode='same')  # a little anti-aliasing before 44.1 -> 16 kHz
    x16 = np.interp(np.linspace(0, len(x) - 1, int(len(x) * 16000 / sr)), np.arange(len(x)), x).astype(np.float32)
    with _whisper_lock:
        segs, info = m.transcribe(x16, beam_size=1, condition_on_previous_text=False, without_timestamps=True, vad_filter=False)
        segs = list(segs)
    text = ''.join(s.text for s in segs).strip()
    chars = re.sub(r'[\W_]+', '', text)
    nsp = max((s.no_speech_prob for s in segs), default=1.0)
    lp = float(np.mean([s.avg_logprob for s in segs])) if segs else -9.0
    out = {'text': text[:60], 'lang': info.language, 'langProb': round(float(info.language_probability), 2),
           'noSpeech': round(float(nsp), 2), 'logprob': round(lp, 2)}
    ok = (info.language == 'ja' and info.language_probability >= 0.5 and nsp <= 0.5 and lp >= -1.1
          and len(chars) >= 5 and len(set(chars)) >= 4 and not _LAUGH.match(text) and not _HALLUCINATION.search(text))
    return ok, out


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


def _voice_cut(a, start, seconds):
    """The clip as it will be played: cut points moved into pauses, short fades, peak 0.85."""
    s = _snap_to_pause(a, start)
    e = _snap_to_pause(a, s + seconds, radius=0.35)
    seg = a[int(s * SR):int(e * SR)].copy()
    if len(seg) < SR * (seconds - 0.8):
        return None, s
    fade = int(0.03 * SR)
    seg[:fade] *= np.linspace(0, 1, fade)
    seg[-fade:] *= np.linspace(1, 0, fade)
    return seg * (0.85 / (np.abs(seg).max() or 1)), s


def _voice_candidates(a, seconds, n=4):
    """The best few non-overlapping windows the DSP likes; when none passes its strict test, the
    top ones with a clear speech band and little bass (Whisper decides about those)."""
    wins = speech_windows(a, seconds + 0.6)
    picked = []
    for strict in (True, False):
        for w in wins:
            st = w[2]
            good = st['ok'] if strict else (st['ratio'] >= 0.6 and st['low'] <= 0.2 and st['depth'] >= 12)
            if good and all(abs(w[1] - x[1]) >= seconds + 0.6 for x in picked):
                picked.append(w)
            if len(picked) >= n:
                return picked
        if len(picked) >= 2:
            break
    return picked


def fetch_voice(name, anime=None, seconds=3.0, max_videos=5, pinned=None):
    """
    (samples, info): ~`seconds` of the character talking with little music, or (None, reason).
    `pinned` ({'video', 'start'}, from the voice-lines snapshot) is an upload already verified by
    scripts/build-pop-media.py --voices; it is re-checked and used first. Otherwise candidates come
    from uploads that name the character and the show (_pick_voice_videos); in each, the best speech
    windows are tried until Whisper hears Japanese speech with a few words in one (speech_check).
    A character that fails is remembered for a week.
    """
    if pinned and pinned.get('video') and voice_title_ok(pinned.get('videoTitle'), name, anime):
        try:
            p = _cache_path(f"voiceaudio:{pinned['video']}", '.m4a')
            need = max(180, int(float(pinned['start']) + seconds + 15))  # the pinned clip may start after 3 minutes
            if (os.path.exists(p) and len(_decode(p)) >= (float(pinned['start']) + seconds + 1) * SR) \
                    or not _download_audio(f"https://www.youtube.com/watch?v={pinned['video']}", p, None, need):
                seg, s = _voice_cut(_decode(p), float(pinned['start']), seconds)
                ok, asr = speech_check(seg) if seg is not None else (False, {})
                if ok:
                    return seg, {'video': pinned['video'], 'videoTitle': pinned.get('videoTitle'), 'start': round(s, 2), 'asr': asr,
                                 'pin': float(pinned['start'])}
        except Cancelled:
            raise
        except Exception:
            pass  # the pinned upload is gone: search again
    key = f'voice5:{name}|{anime}'  # v5: speaker rule (name + show) and the Whisper speech check
    meta_p = _cache_path(key, '.json')
    meta = json.load(open(meta_p, encoding='utf-8')) if os.path.exists(meta_p) else None
    if meta and meta.get('fail') and time.time() - meta.get('at', 0) < 7 * 86400:
        return None, meta['fail']
    if meta and not meta.get('fail') and not voice_title_ok(meta.get('title'), name, anime):
        meta = None  # accepted under an older, looser title rule: search again
    if not meta or meta.get('fail'):
        if not voice_tokens(name):
            return None, 'name shared with other characters: the uploader cannot be matched'
        short = (anime or '').split(':')[0].split(' -')[0].strip()
        tried, meta, searched, heard, dl_fail = set(), None, False, [], 0
        for q in (f'{name} voice lines japanese', f'{name} {short} voice lines', f'{short} {name} all voice lines',
                  f'{name} japanese voice'):
            found = _search(q, n=10)
            searched = searched or bool(found)
            for e in _pick_voice_videos(found, name, anime)[:3]:
                if e['id'] in tried or len(tried) >= max_videos:
                    continue
                tried.add(e['id'])
                p = _cache_path(f"voiceaudio:{e['id']}", '.m4a')
                if not os.path.exists(p) and _download_audio(f"https://www.youtube.com/watch?v={e['id']}", p, e.get('duration'), 180):
                    dl_fail += 1
                    continue
                a = _decode(p)[:SR * 240]
                for score, st, stats in _voice_candidates(a, seconds):
                    seg, s = _voice_cut(a, st + 0.3, seconds)
                    if seg is None:
                        continue
                    ok, asr = speech_check(seg)
                    heard.append(asr.get('text') or asr.get('error', ''))
                    if ok is None:
                        return None, asr['error']  # no checker: never play an unchecked clip
                    if ok:
                        # the candidate start (re-cutting from it gives exactly the clip that was checked)
                        meta = {'id': e['id'], 'title': e.get('title'), 'start': st + 0.3, 'score': round(score, 3),
                                'stats': stats, 'asr': asr}
                        break
                if meta:
                    break
            if meta:
                break
        if not meta:
            why = f'no clean speech in {len(tried) - dl_fail} videos' + (f', {dl_fail} downloads failed' if dl_fail else '') \
                + (f' (heard: {" | ".join(heard[:3])})' if heard else '')
            if searched and not dl_fail:  # a failed download (bot check, network) is retried next time
                _write_json(meta_p, {'fail': why, 'at': time.time()})
            return None, why
        _write_json(meta_p, meta)
    a = _decode(_cache_path(f"voiceaudio:{meta['id']}", '.m4a'))
    seg, s = _voice_cut(a, meta['start'], seconds)
    if seg is None:
        return None, 'clip too short'
    # 'pin': the candidate start the check ran on (the snapshot stores it: re-cutting from it gives the same clip)
    return seg, {'video': meta['id'], 'videoTitle': meta.get('title'), 'start': round(s, 2), 'asr': meta.get('asr'),
                 'pin': float(meta['start'])}
