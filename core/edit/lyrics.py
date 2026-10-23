#!/usr/bin/env python3
"""
Lyric timing for the edit pipelines: which lyric word is sung when, in the edit's own time.

The owner's rule: "there should be text too and the text should follow the lyrics". The references that
show lyric text (fx/WGvtliE1298, character/yUuiSey5kmE, kpop/pB7PRQKPUuA, general/m3lv7H6cu_Y, zawBCooJwLw
...) put the first ink of a word on its sung syllable (fx-av.md: median 0 frames; kpop-av.md: +27 ms) and it
becomes OCR-legible 2-3 frames later (fx.json: 83 ms), so a word track needs the sung onset of every word to
within a frame or two. This module produces it.

    python core/edit/lyrics.py --song "End of Beginning" --artist Djo --audio song.m4a --t0 60 --t1 80
        [--duration 159] [--song-t0 60] [--rate 1.0] [--lines] [--out words.json] [--threads 2]
    python core/edit/lyrics.py --fetch --song "Hush" --artist "The Marias" [--duration 200]
    python core/edit/lyrics.py --selftest            (offline, a few seconds; must stay green)
    python core/edit/lyrics.py --verify-refs [--ids WGvtliE1298,yUuiSey5kmE]   (local only: needs the
        reference videos and rapidocr; compares our word starts with the frame each word appears on screen)
    python core/edit/lyrics.py --bench --audio song.m4a --song ... --artist ... --t0 --t1 --threads 2

API
    fetch(song, artist, duration=None)  -> {id, track, artist, duration, synced, lines: [{t, text}], source}
    words(song, artist, audio_path, t0=0, t1=None, duration=None, song_t0=None, rate=1.0, ...)
        -> [{text, t_start, t_end, line_id, confidence, (roman), (partial)}]  times in SECONDS OF audio_path
    lines(...)  same arguments -> [{line_id, text, t_start, t_end, words: [...]}]
    overlay_words(ws, lag=2.5/30) -> [{text, t0, t1}] for the engine's "lyrics" / "word3d" overlays and
        planners' --words (display lag added: a word is shown `lag` s after its sung onset; pass lag=0 for
        an in-animation that should START on the syllable, as the references do)
    lyric=False words: sung / spoken words that are not in the fetched lyrics (a TikTok sound over the
        song); `partial`: the word was cut by the window or only partly heard

How
 1. Lyrics: LRCLIB (https://lrclib.net) /api/get (exact title / artist / duration) then /api/search, the best
    candidate by title / artist similarity, synced lyrics first, duration closest; cached as JSON under
    <edit-cache>/lyrics/records. Every request is paced to 1 / s across all processes on the machine
    (lock file + shared timestamp in <edit-cache>/lyrics). Plain lyrics are used when no synced ones exist.
 2. Vocal stem: Demucs htdemucs on the window only (+-1 s pad), cached under <edit-cache>/lyrics/stems.
 3. Emissions: wav2vec2 base 960h CTC (torchaudio, English letters, 20 ms frames) on the vocal stem. With
    `rate` != 1 (a sped-up / pitched edit) the stem is resampled back to the original speed first and the
    times are mapped back, so everything is still in the edit's own time.
 4. Forced alignment: a Viterbi over the lyric letters that may START and END anywhere, JUMP between word
    boundaries (a cut edit re-sequences the song; a chorus repeats), SKIP a word at a cost (backing vocals in
    brackets for free) and park unsung / ad-lib frames in a gap state. When the song time is known (the
    audio IS the song, the caller passes song_t0 / song_map, or locate() maps the edit onto song_audio)
    the words of the LRC lines in the window are aligned with their line spans as a soft prior ('lrc');
    otherwise faster-whisper's words anchor the lyric words they match ('anchor').
    Heard-vs-expected check ('auto', on by default; LYRICS_CHECK=0 turns it off): faster-whisper base also
    runs in 'lrc' mode, and where it confidently hears >= 3 words that are not the lyrics expected there
    (segment avg log-prob >= -0.6, no hallucination loop) those lyrics are replaced by the heard words
    (lyric=False). kpop/pB7PRQKPUuA needs it: a "me? obsessed with you? yes, yes I am" TikTok sound sits
    over Mariah Carey's verse and the on-screen text follows the sound.
    Non-Latin lyrics: Hangul is romanised algorithmically; scripts that cannot be romanised here (kanji)
    fall back to faster-whisper word timestamps matched to the lyric words.
 5. Refinement (REFINE): a monotone DP snaps every word start to a vocal-stem onset (5 ms log-mel flux)
    within +-250 ms of its CTC start, scored by onset prominence, the stem-energy RISE across the onset
    (a syllable starts after a dip, not in a decaying tail) and a Laplacian distance to the CTC time; two
    words never share an onset. Word ends follow the stem's energy until it falls (capped by the next word).
    CTC on singing is typically right to a syllable but 100-250 ms off on 1 word in 5; the DP fixes most.

Measured (--verify-refs, 6 references, 42 words that appear on their own, manual first-ink frames or OCR):
|our start - (screen - that reference's own median display offset)| median 50 ms, p90 239 ms; against the
sung onsets the fx / kpop studies read off the stems: median 34 ms (n 12). The references' own text is
looser than the 40 ms target in places (i4f types whole phrases; WGvt shows "of me" ~0.4 s before it is
sung - the song's second window confirms our times), see <edit-cache>/lyrics/verify-auto.json.
Cost: a cold 20 s window with --threads 2 = ~50 s here (demucs 37, whisper 3.4, CTC 5.8, refine 2.5).

Models: htdemucs (demucs), wav2vec2 base 960h (torchaudio, 360 MB; LYRICS_W2V=large for lv60k or mms for
the MMS forced-alignment model - neither was better on singing), faster-whisper base (LYRICS_WHISPER).
Dependencies: numpy, scipy, librosa, torch, torchaudio (pipelines or models), demucs, faster-whisper;
rapidocr-onnxruntime and opencv only for --verify-refs. ffmpeg on PATH.
"""
import argparse
import difflib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get('EDIT_CACHE') or os.path.join(tempfile.gettempdir(), 'edit-cache')
LYR = os.path.join(CACHE, 'lyrics')
API = 'https://lrclib.net/api'
UA = 'zero-yen-otaku-edit/1.0 (lyric timing for short edits; lrclib client)'
GAP_S = 1.0                  # LRCLIB politeness: one request per second across every process here
SR = 16000                   # model rate
HOP = 0.02                   # wav2vec2 frame stride
FRAME_T0 = 0.0125            # centre of frame 0 (400-sample receptive field, 320-sample stride)
ONSET_LAT = 0.005            # the flux peak sits one 5 ms hop after a synthetic attack (selftest)
VERSION = 12                 # bump when the word output changes (invalidates the words cache)


def log(*a):
    print('[lyrics]', *a, file=sys.stderr, flush=True)


def _no_store():
    """LYRICS_NO_STORE=1 (the vibe edits, owner 2026-10-12): lyrics are fetched at render time only; no lyric text
    (records, ASR words, word timings) is read from or written to disk."""
    return os.environ.get('LYRICS_NO_STORE', '0') == '1'


# --------------------------------------------------------------------------------------------- pacing / http

class _Pace:
    """1 request / GAP_S across all processes: an O_EXCL lock file serialises the decision and a JSON file
    holds the last request time. A stale lock (a crashed process) is broken after 30 s."""

    def __init__(self, root=LYR, gap=GAP_S):
        self.root, self.gap = root, gap
        self.lock = os.path.join(root, 'pace.lock')
        self.state = os.path.join(root, 'pace.json')

    def _acquire(self, timeout=60.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lock) > 30:
                        os.remove(self.lock)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
            except OSError:
                return False
        return False

    def wait(self):
        os.makedirs(self.root, exist_ok=True)
        got = self._acquire()
        try:
            try:
                with open(self.state, encoding='utf-8') as f:
                    last = float(json.load(f).get('last') or 0.0)
            except (OSError, ValueError, AttributeError):
                last = 0.0
            d = last + self.gap - time.time()
            if d > 0:
                time.sleep(min(d, self.gap))
            tmp = f'{self.state}.{os.getpid()}.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'last': time.time()}, f)
            os.replace(tmp, self.state)
        except OSError:
            time.sleep(self.gap)
        finally:
            if got:
                try:
                    os.remove(self.lock)
                except OSError:
                    pass


_PACE = None


def _get(path, params, tries=3):
    """GET API/path?params -> parsed JSON, None on 404. Paced, retried on 429 / 5xx / network errors."""
    global _PACE
    _PACE = _PACE or _Pace()
    url = f'{API}/{path}?{urllib.parse.urlencode(params)}'
    err = None
    for k in range(tries):
        _PACE.wait()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, OSError, ValueError) as e:
            err = e
        time.sleep(2.0 * (k + 1))
    raise RuntimeError(f'lrclib {path} failed: {err}')


# --------------------------------------------------------------------------------------------- lyrics source

def _norm_name(s):
    s = unicodedata.normalize('NFKD', str(s or '')).lower()
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'\((feat|ft|with)[^)]*\)|\[(feat|ft)[^\]]*\]|\b(feat|ft)\.? .*$', ' ', s)
    return ' '.join(re.findall(r'[\w]+', s))


def _sim(a, b):
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        r = max(r, 0.85)
    return r


VERSION_WORDS = ('sped up', 'speed up', 'slowed', 'nightcore', 'remix', 'live', 'instrumental', 'karaoke',
                 'acoustic', 'reverb', 'mix)', 'edit)', 'version')


def _score(c, song, artist, duration, med_dur):
    s = 2.0 * _sim(c.get('trackName') or c.get('name'), song) + 1.2 * _sim(c.get('artistName'), artist)
    if c.get('instrumental'):
        s -= 5
    if c.get('syncedLyrics'):
        s += 1.0
    elif c.get('plainLyrics'):
        s += 0.2
    else:
        s -= 3
    name = f"{c.get('trackName') or ''} {c.get('albumName') or ''}".lower()
    ask = f'{song} {artist}'.lower()
    for w in VERSION_WORDS:
        if w in name and w not in ask:
            s -= 0.6
    d = c.get('duration')
    ref = duration or med_dur
    if d and ref:
        s -= min(2.0, abs(float(d) - float(ref)) / 10.0)
    return s


def parse_lrc(text):
    """LRC text -> [{t, text}] sorted by time. Handles several stamps per line, [mm:ss], [mm:ss.xx(x)],
    metadata tags and enhanced <mm:ss.xx> word stamps (dropped)."""
    out = []
    for raw in (text or '').replace('﻿', '').splitlines():
        stamps = re.findall(r'\[(\d+):(\d+(?:[.:]\d+)?)\]', raw)
        body = re.sub(r'\[[^\]]*\]', '', raw)
        body = re.sub(r'<\d+:\d+(?:[.:]\d+)?>', '', body).strip()
        for mm, ss in stamps:
            out.append({'t': round(int(mm) * 60 + float(ss.replace(':', '.')), 3), 'text': body})
    out.sort(key=lambda x: x['t'])
    return out


def _record(c, source):
    if not c:
        return None
    synced = parse_lrc(c.get('syncedLyrics') or '')
    if synced:
        lines = synced
    else:
        lines = [{'t': None, 'text': ln.strip()} for ln in (c.get('plainLyrics') or '').replace('﻿', '').splitlines()]
    tn, an = _norm_name(c.get('trackName') or c.get('name')), _norm_name(c.get('artistName'))
    if tn and an:                                 # a "<title> - <artist>" header line is not sung
        lines = [L for L in lines if not (tn in _norm_name(L['text']) and an in _norm_name(L['text'])
                                          and len(_norm_name(L['text'])) <= len(tn) + len(an) + 4)]
    return {'id': c.get('id'), 'track': c.get('trackName') or c.get('name'), 'artist': c.get('artistName'),
            'album': c.get('albumName'), 'duration': c.get('duration'), 'synced': bool(synced),
            'instrumental': bool(c.get('instrumental')), 'lines': lines, 'source': source,
            'fetched': round(time.time())}


def _slug(s):
    s = _norm_name(s).replace(' ', '-')[:60]
    return f"{s}-{hashlib.sha1(s.encode('utf-8')).hexdigest()[:8]}"


def fetch(song, artist='', duration=None, refresh=False, offline=False):
    """Synced (else plain) lyrics for a song from LRCLIB, cached. Returns a record with 'lines' ([] when
    nothing was found; a miss is cached for 7 days)."""
    key = _slug(f"{artist}|{song}|{int(round(float(duration))) if duration else ''}")
    path = os.path.join(LYR, 'records', key + '.json')
    if os.path.exists(path) and not refresh and not _no_store():
        try:
            with open(path, encoding='utf-8') as f:
                rec = json.load(f)
            if rec.get('lines') or time.time() - float(rec.get('fetched') or 0) < 7 * 86400 or offline:
                return rec
        except (OSError, ValueError):
            pass
    if offline:
        return {'id': None, 'track': song, 'artist': artist, 'lines': [], 'synced': False, 'source': 'offline'}
    rec = None
    if duration and artist:
        c = _get('get', {'track_name': song, 'artist_name': artist, 'duration': int(round(float(duration)))})
        if c and not c.get('instrumental') and (c.get('syncedLyrics') or c.get('plainLyrics')):
            rec = _record(c, 'lrclib:get')
    if rec is None or not rec['synced']:
        cands = []
        if artist:
            cands = _get('search', {'track_name': song, 'artist_name': artist}) or []
        if not cands:
            cands = _get('search', {'q': f'{song} {artist}'.strip()}) or []
        ds = sorted(float(c['duration']) for c in cands if c.get('duration') and c.get('syncedLyrics')
                    and _sim(c.get('trackName'), song) > 0.7)
        med = ds[len(ds) // 2] if ds else None
        cands = [c for c in cands if _sim(c.get('trackName'), song) >= 0.55]
        if cands:
            best = max(cands, key=lambda c: _score(c, song, artist, duration, med))
            r2 = _record(best, 'lrclib:search')
            if rec is None or (r2 and r2['synced']):
                rec = r2
    if rec is None:
        rec = {'id': None, 'track': song, 'artist': artist, 'lines': [], 'synced': False, 'source': 'none',
               'fetched': round(time.time())}
    if _no_store():
        return rec
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return rec


# --------------------------------------------------------------------------------------------- tokenising

_L = ['g', 'kk', 'n', 'd', 'tt', 'r', 'm', 'b', 'pp', 's', 'ss', '', 'j', 'jj', 'ch', 'k', 't', 'p', 'h']
_V = ['a', 'ae', 'ya', 'yae', 'eo', 'e', 'yeo', 'ye', 'o', 'wa', 'wae', 'oe', 'yo', 'u', 'wo', 'we', 'wi',
      'yu', 'eu', 'ui', 'i']
_T = ['', 'k', 'k', 'k', 'n', 'n', 'n', 't', 'l', 'k', 'm', 'p', 'l', 'l', 'p', 'l', 'm', 'p', 'p', 't', 't',
      'ng', 't', 't', 'k', 't', 'p', 't']
_ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen ' \
        'sixteen seventeen eighteen nineteen'.split()
_TENS = 'x x twenty thirty forty fifty sixty seventy eighty ninety'.split()


def _num(n):
    n = int(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ('' if n % 10 == 0 else ' ' + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + _num(n % 100))
    return ' '.join(_ONES[int(d)] for d in str(n))


def romanise(s):
    """Letters the English CTC model can align: Latin with accents stripped, Hangul romanised (Revised
    Romanisation, syllable by syllable), digits spelled out. Characters it cannot romanise are dropped."""
    out = []
    for ch in unicodedata.normalize('NFC', s):
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            k = o - 0xAC00
            out.append(_L[k // 588] + _V[(k % 588) // 28] + _T[k % 28])
        else:
            out.append(ch)
    s = unicodedata.normalize('NFKD', ''.join(out))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.replace('&', ' and ').replace('’', "'").replace('‘', "'").replace('`', "'")
    s = re.sub(r'\d+', lambda m: ' ' + _num(m.group(0)) + ' ', s)
    s = re.sub(r"[^A-Za-z' ]+", ' ', s).upper()
    s = re.sub(r"(^|\s)'+|'+(\s|$)", ' ', s)
    return ' '.join(s.split())


def _unromanisable(s):
    for ch in s:
        if ch.isalpha() and not ('\u0000' <= ch <= 'ɏ' or 0xAC00 <= ord(ch) <= 0xD7A3):
            return True
    return False


def tokens(lines):
    """Lyric lines -> words [{text, norm, line_id, line_t, optional}]. Words inside (...) are backing
    vocals (optional); a word the English model cannot spell gets norm '' (aligned by the fallback)."""
    out = []
    depth = 0
    for li, L in enumerate(lines):
        text = (L.get('text') or '').replace('♪', ' ').strip()
        if not text or re.fullmatch(r'[\[(]?(verse|chorus|pre-chorus|bridge|outro|intro|hook)[^\])]*[\])]?',
                                    text.lower()):
            continue
        for raw in text.split():
            opt = depth > 0 or raw.startswith('(')
            depth += raw.count('(') - raw.count(')')
            depth = max(0, depth)
            disp = raw.strip('()[]{}"“”,.!?;:…—–-*~')
            if not disp:
                continue
            norm = romanise(disp)
            out.append({'text': disp, 'norm': norm.replace(' ', ''), 'line_id': li, 'line_t': L.get('t'),
                        'optional': bool(opt), 'script': 'x' if _unromanisable(disp) else 'l'})
    return out


# --------------------------------------------------------------------------------------------- audio

def _probe_duration(path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of',
                            'default=nw=1:nk=1', path], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def load_audio(path, a, b, sr=44100, ch=2):
    """Decode [a, b] s of any ffmpeg-readable file -> float32 (ch, n)."""
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, a):.3f}', '-t', f'{max(0.05, b - max(0.0, a)):.3f}',
           '-i', path, '-vn', '-ac', str(ch), '-ar', str(sr), '-f', 'f32le', '-']
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f'ffmpeg failed on {path}: {r.stderr.decode("utf-8", "replace")[:300]}')
    y = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, ch).T.copy()
    if a < 0:                                   # window starts before the file: pad with silence
        y = np.concatenate([np.zeros((ch, int(round(-a * sr))), np.float32), y], axis=1)
    return y


def _file_key(path):
    st = os.stat(path)
    return f'{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}'


_DEMUCS = None


def vocal_stem(path, a, b, threads=None):
    """Demucs htdemucs vocals of [a, b] (seconds of `path`) -> mono float32 at SR, cached."""
    global _DEMUCS
    key = hashlib.sha1(f'{_file_key(path)}|{a:.3f}|{b:.3f}|htdemucs'.encode()).hexdigest()[:20]
    cp = os.path.join(LYR, 'stems', key + '.npy')
    if os.path.exists(cp):
        try:
            return np.load(cp)
        except (OSError, ValueError):
            pass
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    if threads:
        torch.set_num_threads(int(threads))
    y = load_audio(path, a, b, 44100, 2)
    if _DEMUCS is None:
        _DEMUCS = get_model('htdemucs')
        _DEMUCS.eval()
    wav = torch.from_numpy(y)
    ref = wav.mean(0)
    mu, sd = ref.mean(), ref.std() + 1e-8
    try:
        import gpu as _gpu
        dev = _gpu.torch_device()   # 'cuda' when usable (not CI, EDIT_GPU != 0), else 'cpu' as before
    except ImportError:
        dev = 'cpu'
    with torch.no_grad():
        out = apply_model(_DEMUCS, ((wav - mu) / sd)[None], shifts=0, split=True, overlap=0.25,
                          progress=False, device=dev)[0]
    out = out.cpu() * sd + mu
    voc = out[_DEMUCS.sources.index('vocals')].mean(0).numpy().astype(np.float32)
    from scipy.signal import resample_poly
    v16 = resample_poly(voc, 160, 441).astype(np.float32)
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    np.save(cp, v16)
    return v16


# --------------------------------------------------------------------------------------------- emissions

_W2V = None
LABELS = ('-', '|', 'E', 'T', 'A', 'O', 'N', 'I', 'H', 'S', 'R', 'D', 'L', 'U', 'M', 'W', 'C', 'F', 'G', 'Y',
          'P', 'B', 'V', 'K', "'", 'X', 'J', 'Q', 'Z')
W2V_URL = 'https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_base_ls960_asr_ls960.pth'


W2V_MODEL = os.environ.get('LYRICS_W2V', 'base')     # 'base' (360 MB) or 'large' (lv60k, 1.2 GB)


def _w2v():
    global _W2V
    if _W2V is None:
        import torch
        if W2V_MODEL == 'mms':                   # MMS forced-alignment model (uroman letters, 1.2 GB)
            import torchaudio.pipelines as tp
            m = tp.MMS_FA.get_model(with_star=False)
            m._lyr_norm = True
            d = tp.MMS_FA.get_dict(star=None)
            m._lyr_map = [(LABELS.index(c.upper()) if c != '-' else 0, i) for c, i in d.items()
                          if c == '-' or c.upper() in LABELS]
            m.eval()
            _W2V = m
            return _W2V
        try:
            import torchaudio.pipelines as tp
            B = tp.WAV2VEC2_ASR_LARGE_LV60K_960H if W2V_MODEL == 'large' else tp.WAV2VEC2_ASR_BASE_960H
            m = B.get_model()
            m._lyr_norm = bool(getattr(B, '_normalize_waveform', False))
            assert tuple(B.get_labels()) == LABELS
        except Exception:                         # a torchaudio without pipelines: build + load by hand
            from torchaudio.models import wav2vec2_base
            m = wav2vec2_base(aux_num_out=len(LABELS))
            m.load_state_dict(torch.hub.load_state_dict_from_url(W2V_URL, progress=False))
        m.eval()
        _W2V = m
    return _W2V


def emissions(v16, threads=None, chunk_s=30.0, ctx_s=1.0):
    """log-probabilities (T, 29) of the CTC letters, 20 ms frames, frame i centred at FRAME_T0 + i*HOP."""
    import torch
    if threads:
        torch.set_num_threads(int(threads))
    m = _w2v()
    x = v16.astype(np.float32)
    act = np.abs(x) > 1e-4
    rms = float(np.sqrt(np.mean(x[act] ** 2))) if act.any() else 0.0
    if rms > 1e-5:
        x = x * (0.06 / rms)                     # LibriSpeech-like level; singing stems are much louder
    x = np.clip(x, -1, 1)
    if getattr(m, '_lyr_norm', False):           # the large model was trained on normalised input
        x = (x - x.mean()) / (x.std() + 1e-7)
    n = len(x)
    T = n // 320
    out = np.full((T, len(LABELS)), -30.0, np.float32)
    step = int(chunk_s * SR)
    ctx = int(ctx_s * SR)
    with torch.inference_mode():
        for s in range(0, n, step):
            a, b = max(0, s - ctx), min(n, s + step + ctx)
            y, _ = m(torch.from_numpy(x[a:b])[None])
            lp = torch.log_softmax(y[0], -1).numpy()
            if getattr(m, '_lyr_map', None):     # MMS letters -> LABELS order ('|' never emitted)
                q = np.full((len(lp), len(LABELS)), -30.0, np.float32)
                for dst, src in m._lyr_map:
                    q[:, dst] = lp[:, src]
                lp = q
            f0 = a // 320
            lo, hi = s // 320, min(T, (s + step) // 320)
            seg = lp[lo - f0: hi - f0]
            out[lo:lo + len(seg)] = seg
    return out


# --------------------------------------------------------------------------------------------- alignment

NEG = -1e9


def build_graph(ws):
    """CTC state graph over the aligned words. Per word: letters C1..Cn with a blank Bi between Ci and
    Ci+1, preceded by the gap state G_k. Returns (kind, label, word, pred, pen, G, first, last)."""
    kind, label, word = [], [], []
    G, first, last = [], [], []
    for k, w in enumerate(ws):
        G.append(len(kind))
        kind.append(0), label.append(-1), word.append(k)
        chars = [LABELS.index(c) for c in w['norm'] if c in LABELS]
        first.append(len(kind))
        for i, c in enumerate(chars):
            if i:
                kind.append(2), label.append(0), word.append(k)      # intra-word blank
            kind.append(1), label.append(c), word.append(k)
        last.append(len(kind) - 1)
    G.append(len(kind))
    kind.append(0), label.append(-1), word.append(len(ws))
    return (np.array(kind), np.array(label), np.array(word), np.array(G), np.array(first), np.array(last))


def viterbi(lp, ws, prior=None, skip_pen=-7.0, jump_pen=-14.0, star_pen=-2.2, start_mid_pen=-3.0, free_ends=True,
            start_ks=None, start_skip=-3.0):
    """Best path of the letters of `ws` through the log-probs `lp` (T, 29).

    States: gap G_k before word k (blank or word separator, or ANY letter at `star_pen` per frame: ad-libs,
    unsung syllables), letters C and intra-word blanks B. Transitions: stay; G_k -> C1(k); Ci -> Bi -> Ci+1;
    Ci -> Ci+1 when the letters differ; Cn(k) -> G_k+1; G_k -> G_k+1 (skip a word: free if optional);
    G_j -> G_k for any k (a jump: the edit re-sequences the song) at `jump_pen`. The path starts in any
    gap state (or mid-word at `start_mid_pen`) and ends anywhere; with free_ends=False (the words ARE what
    is sung in the window) it starts in G_0 / inside word 0 and ends inside the last word or in the final
    gap, so no word escapes its skip penalty. `prior` (T, W) adds a per-frame penalty
    for being in word k at frame t (the LRC line times, when the song time is known)."""
    kind, label, word, G, first, last = build_graph(ws)
    S, T = len(kind), len(lp)
    gap_e = np.logaddexp(lp[:, 0], lp[:, 1])
    star_e = lp[:, 2:].max(1) + star_pen
    gem = np.maximum(gap_e, star_e)
    # predecessor table: up to 4 predecessors per state, with a transition penalty
    P = np.tile(np.arange(S)[:, None], (1, 4))
    W = np.full((S, 4), NEG, np.float32)
    W[:, 0] = 0.0                                        # stay
    for k in range(len(ws)):
        f, l = first[k], last[k]
        if l >= f:
            P[f, 1], W[f, 1] = G[k], 0.0                  # gap -> first letter
            for s in range(f + 1, l + 1):
                if kind[s] == 2:                          # blank after letter s-1
                    P[s, 1], W[s, 1] = s - 1, 0.0
                else:                                     # letter: from blank, or straight from previous letter
                    P[s, 1], W[s, 1] = s - 1, 0.0
                    if label[s] != label[s - 2]:
                        P[s, 2], W[s, 2] = s - 2, 0.0
            P[G[k + 1], 1], W[G[k + 1], 1] = l, 0.0      # last letter -> next gap
        P[G[k + 1], 2] = G[k]
        if 'skip' in ws[k] and l >= f:              # an explicit cost wins over "optional"
            W[G[k + 1], 2] = float(ws[k]['skip'])
        else:
            W[G[k + 1], 2] = 0.0 if (ws[k].get('optional') or l < f) else skip_pen
    is_gap = kind == 0
    emit_idx = np.where(kind == 0, 0, np.where(kind == 2, 0, label))
    delta = np.full(S, NEG, np.float32)
    delta[is_gap] = 0.0
    delta[kind != 0] = start_mid_pen
    if not free_ends and len(ws):
        delta[:] = NEG
        # start_ks: a line cut by the window start may begin at any of its words, each word left out before the start
        # costing its own skip penalty (start_skip)
        for n, k in enumerate(start_ks or [0]):
            c = n * float(start_skip)
            delta[G[k]] = c
            if k < len(ws):
                delta[first[k]:last[k] + 1] = c + start_mid_pen
    wordcol = np.clip(word, 0, max(0, len(ws) - 1))

    prevcol = np.clip(word - 1, 0, max(0, len(ws) - 1))

    def emit(t):
        e = lp[t, emit_idx].astype(np.float32)
        e[is_gap] = gem[t]
        if prior is not None and len(ws):
            pk = prior[t, wordcol]
            e = e + np.where(is_gap, np.maximum(pk, prior[t, prevcol]), pk)
        return e

    delta = delta + emit(0)
    back = np.zeros((T, S), np.int32)
    back[0] = np.arange(S)
    Gi = G
    for t in range(1, T):
        cand = delta[P] + W
        j = cand.argmax(1)
        best = cand[np.arange(S), j]
        bp = P[np.arange(S), j]
        g = int(Gi[np.argmax(delta[Gi])])
        jv = delta[g] + jump_pen
        m = is_gap & (jv > best)
        best = np.where(m, jv, best)
        bp = np.where(m, g, bp)
        delta = best + emit(t)
        back[t] = bp
    path = np.zeros(T, np.int32)
    if not free_ends and len(ws):
        fin = np.full(S, NEG, np.float32)
        fin[G[-1]] = delta[G[-1]]
        fin[first[-1]:last[-1] + 1] = delta[first[-1]:last[-1] + 1] + start_mid_pen
        path[-1] = int(np.argmax(fin))
    else:
        path[-1] = int(np.argmax(delta))
    for t in range(T - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path, (kind, label, word, G, first, last), float(delta.max())


def _segments(path, graph, n_words):
    """Path -> per visit of a word: (word k, first frame, last letter frame, frames of its first letter,
    whether it started mid-word). A word visited twice (a jump back) yields two segments."""
    kind, label, word, G, first, last = graph
    segs = []
    cur = None
    for t, s in enumerate(path):
        if kind[s] == 0:
            if cur is not None:
                segs.append(cur)
                cur = None
            continue
        k = int(word[s])
        if cur is None or cur['k'] != k or s < cur['s']:
            if cur is not None:
                segs.append(cur)
            cur = {'k': k, 'f0': t, 'f1': t, 's': s, 'mid': s != first[k], 'frames': [], 'states': []}
        cur['f1'] = t
        cur['s'] = s
        cur['frames'].append(t)
        cur['states'].append(s)
    if cur is not None:
        segs.append(cur)
    return segs


# --------------------------------------------------------------------------------------------- refinement

def vocal_onsets(v16, hop=80):
    """Vocal-stem onsets (s) and a 5 ms onset-strength envelope: log-mel spectral flux, peak picked."""
    import librosa
    from scipy.signal import find_peaks
    env = librosa.onset.onset_strength(y=v16, sr=SR, hop_length=hop, n_fft=1024, n_mels=64, fmax=8000,
                                       lag=1, max_size=3)
    env = env / (np.percentile(env, 99) + 1e-9)
    rms = librosa.feature.rms(y=v16, frame_length=640, hop_length=hop, center=True)[0]
    rms = rms[:len(env)] if len(rms) >= len(env) else np.pad(rms, (0, len(env) - len(rms)))
    pk, pr = find_peaks(env, distance=max(1, int(0.05 * SR / hop)), prominence=0.08)
    gate = 0.08 * (np.percentile(rms, 95) + 1e-9)
    ok = rms[np.minimum(pk + int(0.03 * SR / hop), len(rms) - 1)] > gate
    pk, prom = pk[ok], pr['prominences'][ok]
    return pk * hop / SR - ONSET_LAT, prom, env, rms, hop / SR


REFINE = dict(win=(-0.25, 0.25), sigma=0.10, wp=1.0, keep=0.12, min_gap=0.07, wr=6.0, lap=1)


def refine(ws_out, v16, win=None, sigma=None, bias=0.0, wp=None, keep=None, min_gap=None):
    """Snap the word starts to vocal-stem onsets with a monotone DP: every word picks one onset within
    `win` of its CTC start (or keeps the CTC time as a weak virtual onset of prominence `keep`), scored by
    wp * log(onset prominence) + wr * (stem energy rise across the onset) - 0.5 * ((onset - ctc) / sigma)^2
    (or |onset - ctc| / sigma with lap=1: CTC errors on singing are heavy-tailed), and the starts must rise by at
    least `min_gap` s from word to word. The DP fixes the typical CTC error on singing (the whole word
    100-250 ms early or late) where a nearest-onset snap would grab the wrong syllable. Word ends then
    follow the stem energy (capped by the next word's start)."""
    if not ws_out:
        return ws_out
    R = REFINE
    win = win or R['win']
    sigma = sigma or R['sigma']
    wp = R['wp'] if wp is None else wp
    keep = R['keep'] if keep is None else keep
    min_gap = R['min_gap'] if min_gap is None else min_gap
    on, prom, env, rms, dt = vocal_onsets(v16)
    peak = np.percentile(rms, 95) + 1e-9
    pn = prom / (np.percentile(prom, 90) + 1e-9) if len(prom) else prom
    cr = np.concatenate([[0.0], np.cumsum(rms)])

    def rise(t):                                  # (mean rms 20..100 ms after - 20..100 ms before) / peak
        i = np.clip((np.asarray(t) / dt).astype(int), 0, len(rms))
        n1, n2 = int(0.02 / dt), int(0.10 / dt)
        a0, a1 = np.clip(i + n1, 0, len(rms)), np.clip(i + n2, 0, len(rms))
        b0, b1 = np.clip(i - n2, 0, len(rms)), np.clip(i - n1, 0, len(rms))
        aft = (cr[a1] - cr[a0]) / np.maximum(a1 - a0, 1)
        bef = (cr[b1] - cr[b0]) / np.maximum(b1 - b0, 1)
        return np.clip((aft - bef) / peak, -0.6, 0.6)
    cands = []
    for w in ws_out:
        c = w['_t'] + bias
        sel = (on >= c + win[0]) & (on <= c + win[1])
        ts = np.concatenate([[c], on[sel]])
        ps = np.concatenate([[keep], np.minimum(pn[sel], 2.0)])
        dist = np.abs(ts - c) / sigma if R.get('lap') else 0.5 * ((ts - c) / sigma) ** 2
        sc = wp * np.log(ps + 0.02) + R.get('wr', 0.0) * rise(ts) - dist
        cands.append((ts, sc))
    best = [cands[0][1].copy()]
    back = [None]
    for i in range(1, len(ws_out)):
        ts, sc = cands[i]
        pts, pbest = cands[i - 1][0], best[-1]
        ok = pts[None, :] <= ts[:, None] - min_gap
        m = np.where(ok, pbest[None, :], -1e9)
        j = m.argmax(1)
        v = m[np.arange(len(ts)), j]
        # an impossible order (two words on one onset, a jump back): fall back to the best previous
        bad = v <= -1e8
        j = np.where(bad, int(np.argmax(pbest)), j)
        v = np.where(bad, pbest.max() - 20.0, v)
        best.append(v + sc)
        back.append(j)
    k = int(np.argmax(best[-1]))
    for i in range(len(ws_out) - 1, -1, -1):
        ts = cands[i][0]
        ws_out[i]['_t'] = float(ts[k])
        if k:
            ws_out[i]['_snap'] = True
        if i:
            k = int(back[i][k])
    for i in range(1, len(ws_out)):               # no feasible order (crowded words): push the later word on
        if ws_out[i]['_t'] < ws_out[i - 1]['_t'] + min_gap:
            ws_out[i]['_t'] = ws_out[i - 1]['_t'] + min_gap
            ws_out[i]['_snap'] = False
    for i, w in enumerate(ws_out):
        nxt = ws_out[i + 1]['_t'] if i + 1 < len(ws_out) else None
        e = w['_e']
        f = int(e / dt)
        thr = 0.18 * peak
        while f + 1 < len(rms) and rms[f + 1] > thr and (f + 1) * dt < e + 1.5:
            f += 1
        e = max(e, f * dt)
        if nxt is not None:
            e = min(e, nxt - 0.01)
        w['_e'] = max(w['_t'] + 0.06, e)
    return ws_out


# --------------------------------------------------------------------------------------------- whisper fallback

_FW = None


def _whisper_words(v16, lang=None, threads=None, prompt=None):
    """faster-whisper word timestamps on the vocal stem (no prompt: a lyric prompt makes it hallucinate)."""
    global _FW
    from faster_whisper import WhisperModel
    if _FW is None:
        _FW = WhisperModel(os.environ.get('LYRICS_WHISPER', 'base'), device='cpu', compute_type='int8',
                           cpu_threads=int(threads or 2))
    segs, info = _FW.transcribe(v16, language=lang, word_timestamps=True, vad_filter=False, beam_size=3,
                                condition_on_previous_text=False, initial_prompt=prompt)
    out = []
    for s in segs:
        for w in s.words or []:
            out.append({'text': w.word.strip(), 't0': float(w.start), 't1': float(w.end), 'p': float(w.probability),
                        'alp': float(s.avg_logprob)})
    return out


def _wsim(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def map_hyp(hyp_texts, ref, jump=-2.5, unmatched=-0.35, gap1=-0.6, max_fill=4):
    """Which lyric word each recognised word is. A DP over (hypothesis word i, lyric word j) with three
    states: i matched to j (fuzzy spelling, score 2*sim-1), i unmatched while the last match was j (a
    misrecognition, an ad-lib), or nothing matched yet. Advancing to the next lyric word is free, leaving
    out up to `max_fill` lyric words costs `gap1` each (the recogniser missed them), anything else is a
    jump (`jump`: the edit re-cut the song, or a chorus repeats). Returns [j or None] per word."""
    H, R = len(hyp_texts), len(ref)
    if not H or not R:
        return [None] * H
    hn = [_ntok(h) for h in hyp_texts]
    rn = [re.sub(r'[^a-z0-9]', '', (r.get('norm') or '').lower()) for r in ref]
    M = np.full((H, R), -1.5)
    for i in range(H):
        for j in range(R):
            s = _wsim(hn[i], rn[j])
            if s >= (0.8 if min(len(hn[i]), len(rn[j])) <= 3 else 0.67):
                M[i, j] = 2 * s - 1
    B = np.full((H, R), NEG)
    U = np.full((H, R), NEG)
    S = np.full(H, NEG)
    bB = np.zeros((H, R, 2), np.int64)      # (state 0=B 1=U 2=S, j)
    bU = np.zeros((H, R), np.int64)         # 0 = from B, 1 = from U
    ar = np.arange(R)

    def shift(x, d):
        return np.concatenate([np.full(d, NEG), x[:-d]])[:R] if d < R else np.full(R, NEG)

    for i in range(H):
        if i == 0:
            B[0] = M[0]
            bB[0, :, 0] = 2
            S[0] = unmatched
            continue
        pb, pu = B[i - 1], U[i - 1]
        best = np.full(R, NEG)
        src_s = np.zeros(R, np.int64)
        src_j = np.zeros(R, np.int64)
        for d in range(1, max_fill + 2):
            for st, arr in ((0, pb), (1, pu)):
                c = shift(arr, d) + gap1 * (d - 1)
                m = c > best
                best = np.where(m, c, best)
                src_s = np.where(m, st, src_s)
                src_j = np.where(m, ar - d, src_j)
        allmax = np.maximum(pb, pu)
        jj = int(np.argmax(allmax))
        c = np.full(R, allmax[jj] + jump)
        m = c > best
        best, src_s, src_j = np.where(m, c, best), np.where(m, np.where(pb[jj] >= pu[jj], 0, 1), src_s), np.where(m, jj, src_j)
        c = np.full(R, S[i - 1])
        m = c > best
        best, src_s, src_j = np.where(m, c, best), np.where(m, 2, src_s), np.where(m, -1, src_j)
        B[i] = best + M[i]
        bB[i, :, 0], bB[i, :, 1] = src_s, src_j
        U[i] = np.maximum(pb, pu) + unmatched
        bU[i] = np.where(pb >= pu, 0, 1)
        S[i] = S[i - 1] + unmatched
    out = [None] * H
    cands = [(B[H - 1].max(), 0, int(np.argmax(B[H - 1]))), (U[H - 1].max(), 1, int(np.argmax(U[H - 1]))),
             (S[H - 1], 2, -1)]
    _, st, j = max(cands, key=lambda x: x[0])
    for i in range(H - 1, -1, -1):
        if st == 2:
            break
        if st == 0:
            out[i] = j
            st, j = int(bB[i, j, 0]), int(bB[i, j, 1])
        else:
            st = int(bU[i, j]) if i else 2
    return out


def _anchor_sequence(hyp, mapj, ref, min_p=0.45):
    """Recognised words + their lyric mapping -> the word sequence to force-align, each with an anchor
    interval (model time) from the recogniser: lyric words the recogniser matched, lyric words it missed
    between two matches (<= 2), and runs of >= 2 confident words that are not in the lyrics at all (a
    spoken intro, an ad-lib the editor kept) - misrecognised words between two matches are dropped."""
    seq = []
    idx = [i for i, j in enumerate(mapj) if j is not None]
    for n, i in enumerate(idx):
        j = mapj[i]
        if n:
            pi = idx[n - 1]
            pj = mapj[pi]
            if 1 < j - pj <= 5:
                for jj in range(pj + 1, j):
                    seq.append({'tok': ref[jj], 'a': hyp[pi]['t1'], 'b': hyp[i]['t0'], 'fill': True})
        tok = dict(ref[j])
        tok['skip'] = -1e4 if hyp[i]['p'] >= 0.5 else -12.0       # heard clearly: the aligner must place it
        seq.append({'tok': tok, 'a': hyp[i]['t0'], 'b': hyp[i]['t1']})
        seq[-1]['_i'] = i
    # unmatched runs outside the matched stretches (before the first, after the last, or across a jump)
    runs, cur = [], []
    for i, j in enumerate(mapj):
        if j is None:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    extra = []
    for r in runs:
        lo, hi = r[0] - 1, r[-1] + 1
        between = lo >= 0 and hi < len(mapj) and mapj[lo] is not None and mapj[hi] is not None \
            and 0 < mapj[hi] - mapj[lo] <= 6 and len(r) <= mapj[hi] - mapj[lo] + 1
        good = [i for i in r if hyp[i]['p'] >= min_p and romanise(hyp[i]['text'])]
        if between or len(good) < 2:
            continue
        for i in good:
            txt = hyp[i]['text'].strip(',.!?;:"…')
            extra.append({'tok': {'text': txt, 'norm': romanise(txt).replace(' ', ''), 'line_id': None,
                                  'line_t': None, 'optional': False, 'script': 'l', 'lyric': False, 'skip': -12.0},
                          'a': hyp[i]['t0'], 'b': hyp[i]['t1'], '_i': i})
    seq = sorted(seq + extra, key=lambda x: (x['a'], x.get('_i', -1)))
    return seq


def _lrc_sequence(lat, song_map, a, rate, edge=0.3):
    """The words the song map says are sung, in audio order: for every piece of the song in the window,
    the lyric lines its song-time span covers, each word anchored to its line's span (model time). Words of
    lines fully inside a piece must be placed (skip -30), backing vocals in brackets cost -8 to leave out,
    lines cut by a piece's edge -3; backing vocals in brackets -20 (the references show those hooks)."""
    starts = sorted(set(w['line_t'] for w in lat if w['line_t'] is not None))
    nxt = {u: (starts[i + 1] if i + 1 < len(starts) else u + 8.0) for i, u in enumerate(starts)}
    seq = []
    for s0, s1, song0, r in song_map:
        ss0, ss1 = song0, song0 + (s1 - s0) * r
        for w in lat:
            L = w['line_t']
            if L is None:
                continue
            E = min(nxt[L], L + 12.0)
            if E <= ss0 + edge or L >= ss1 - edge:
                continue
            inside = L >= ss0 - edge and E <= ss1 + edge
            tok = dict(w)
            tok['skip'] = (-20.0 if inside else -3.0) if w.get('optional') else (-30.0 if inside else -3.0)
            aa = s0 + (max(L, ss0) - song0) / r
            bb = s0 + (min(E, ss1) - song0) / r
            seq.append({'tok': tok, 'a': (aa - a) * rate, 'b': (bb - a) * rate, 'head': L < ss0 - edge})
            if L < ss0 - edge:
                # a line cut by the window start: where its words fall in song time, spread over the line's sung span
                # by letter count (the span = up to the next line, at most ~0.5 s per word + 1 s), to tell the words
                # sung BEFORE the window (not heard here) from the ones sung in it
                lw = [x for x in lat if x['line_t'] == L]
                wt = np.array([len(x.get('norm') or x['text']) + 1.0 for x in lw])
                span = min(E - L, 0.5 * len(lw) + 1.0)
                k = next(i for i, x in enumerate(lw) if x is w)
                seq[-1]['est'] = L + span * float(wt[:k].sum() + 0.5 * wt[k]) / float(wt.sum())
                seq[-1]['pre'] = seq[-1]['est'] < ss0
    return seq


def _foreign(hyp, anchors, min_p=0.5, min_alp=-0.6, tol=1.0, run=3):
    """Stretches where the recogniser clearly hears words that are NOT the lyrics the song map expects there
    (a TikTok-sound intro or a spoken hook laid over the song: kpop/pB7PRQKPUuA sings "me? obsessed with you?
    yes, yes I am" over Mariah Carey's verse). A hypothesis word counts when it is confident (p >= min_p,
    2+ letters, its segment's avg log-prob >= min_alp: a hallucinating recogniser is ignored) and agrees
    when it spells like a lyric token whose anchor span (+- tol s) holds it. `run` confident disagreeing
    words with no agreeing one between them make a foreign stretch. Returns [(t0, t1)] in model time."""
    conf = []
    for h in hyp:
        k = _ntok(h['text'])
        if h.get('alp', 0.0) < min_alp or not k:
            continue
        ok = any(x['a'] - tol <= h['t0'] <= max(x['b'], x['a']) + tol and
                 _wsim(k, re.sub(r'[^a-z0-9]', '', (x['tok'].get('norm') or '').lower())) >= 0.75 for x in anchors)
        conf.append((h, ok, h['p'] >= min_p and len(k) >= 2))
    def looped(hs):                               # a hallucination loop: a repeated phrase, stacked stamps
        allw = [g for g in hyp if hs[0]['t0'] - 0.3 <= g['t0'] <= hs[-1]['t1'] + 0.3]
        ks = [_ntok(g['text']) for g in allw]
        tri = [tuple(ks[i:i + 2]) for i in range(len(ks) - 1)]
        rep = max((tri.count(t) for t in set(tri)), default=0)
        t0s = [round(g['t0'], 2) for g in allw]
        stacked = sum(1 for t in t0s if t0s.count(t) > 1) / max(1, len(t0s))
        return rep >= 3 or stacked >= 0.3

    out, cur = [], []                             # runs of disagreeing words (any confidence) between two
    for h, ok, strong in conf + [(None, True, True)]:      # agreeing ones, kept when `run` of them are strong
        if ok and strong:
            ss = [g for g, st in cur if st]
            if len(ss) >= run and not looped(ss):
                out.append((cur[0][0]['t0'] - 0.25, cur[-1][0]['t1'] + 0.25))
            cur = []
        elif not ok:
            cur.append((h, strong))
    # 2026-10-23 (blue @0.9, the window starts inside a line): the line cut by the window start is sung there - the
    # recogniser mishearing its short words (base model) must not turn it into a 'foreign' stretch when at least one heard
    # word in that line's span agrees with it (a TikTok intro over the song agrees with nothing)
    hd = [x for x in anchors if x.get('head')]
    if hd and out:
        h0, h1 = min(x['a'] for x in hd), max(max(x['b'], x['a']) for x in hd)
        agree = any(ok for h, ok, _s in conf if h0 - tol <= h['t0'] <= h1 + tol and
                    any(_wsim(_ntok(h['text']), re.sub(r'[^a-z0-9]', '', (x['tok'].get('norm') or '').lower())) >= 0.75 for x in hd))
        if agree:
            out = [(f0, f1) for f0, f1 in out if f1 <= h0 or f0 >= h1]
    return out


def _anchor_prior(seq, T, tol=0.7, slope=-0.25):
    """(T, W) penalty for word k outside its recogniser interval +- tol (model time)."""
    ts = FRAME_T0 + np.arange(T) * HOP
    tl = np.array([float(s.get('tol', tol)) for s in seq])        # a word pinned by the recogniser: its own tolerance
    lo = np.array([s['a'] for s in seq]) - tl
    hi = np.array([max(s['b'], s['a']) for s in seq]) + tl
    d = np.maximum(lo[None] - ts[:, None], 0) + np.maximum(ts[:, None] - hi[None], 0)
    return (slope * np.minimum(d, 20.0)).astype(np.float32)


# --------------------------------------------------------------------------------------------- song map

def _feats(y, sr, hop):
    import librosa
    on = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    ch = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop, n_fft=4096)
    n = min(len(on), ch.shape[1])
    on = on[:n]
    on = (on - on.mean()) / (on.std() + 1e-9)
    ch = ch[:, :n]
    ch = (ch - ch.mean(1, keepdims=True)) / (ch.std(1, keepdims=True) + 1e-9)
    return on, ch


def _ncc(block, ref):
    """Normalised cross-correlation of `block` (n,) against every position of `ref` (m,) -> (m - n + 1,)."""
    from scipy.signal import fftconvolve
    n = len(block)
    b = (block - block.mean()) / (block.std() * n + 1e-9)
    num = fftconvolve(ref, b[::-1], mode='valid')
    c1 = np.cumsum(np.concatenate([[0.0], ref]))
    c2 = np.cumsum(np.concatenate([[0.0], ref ** 2]))
    mu = (c1[n:] - c1[:-n]) / n
    sd = np.sqrt(np.maximum((c2[n:] - c2[:-n]) / n - mu ** 2, 1e-12))
    return num / sd


def locate(audio_path, song_path, t0=0.0, t1=None, rate=None, block=4.0, hop_s=1.0, rates=None):
    """Where [t0, t1] of an edit's audio comes from in the original song: [(audio_start, audio_end,
    song_start, rate)] segments (a re-cut edit gives several). Onset-envelope + chroma cross-correlation of
    `block`-second blocks every `hop_s` s, after resampling the edit back to the song's speed (a sped-up
    edit is resampled, pitch and tempo together). `rate` None searches 0.85-1.30."""
    sr, hop = 22050, 256
    fr = sr / hop
    if t1 is None:
        t1 = _probe_duration(audio_path)
    ye = load_audio(audio_path, t0, t1, sr, 1)[0]
    ys = load_audio(song_path, 0.0, (_probe_duration(song_path) or 600.0) + 1.0, sr, 1)[0]
    s_on, s_ch = _feats(ys, sr, hop)
    from scipy.signal import resample_poly

    def blocks_for(r):
        y = resample_poly(ye, int(round(r * 1000)), 1000).astype(np.float32) if abs(r - 1) > 1e-4 else ye
        e_on, e_ch = _feats(y, sr, hop)
        out = []
        nb = int(block * fr)
        for st in range(0, max(1, len(e_on) - nb + 1), max(1, int(hop_s * fr))):
            bo, bc = e_on[st:st + nb], e_ch[:, st:st + nb]
            if len(bo) < nb // 2 or bo.std() < 1e-6:
                continue
            sc = _ncc(bo, s_on)
            cc = np.mean([_ncc(bc[k], s_ch[k]) for k in range(12)], axis=0)
            tot = 0.5 * sc + cc
            j = int(np.argmax(tot))
            out.append({'m': st / fr, 'song': j / fr, 'score': float(tot[j]), 'on': float(sc[j]), 'ch': float(cc[j])})
        return out

    if rate is None:
        cand = rates or list(np.round(np.arange(0.85, 1.3001, 0.0125), 4))
        best = max(cand, key=lambda r: np.median([b['score'] for b in blocks_for(r)] or [0]))
        rate = float(best)
    bl = blocks_for(rate)
    good = [b for b in bl if b['score'] >= 0.6]
    segs = []
    for b in good:
        off = b['song'] - b['m']                     # song time minus model time: constant inside a piece
        if segs and abs(segs[-1]['off'] - off) <= 0.08 and b['m'] - segs[-1]['m1'] <= hop_s + 1e-6:
            segs[-1]['m1'] = b['m']
            segs[-1]['n'] += 1
            continue
        segs.append({'off': off, 'm0': b['m'], 'm1': b['m'], 'n': 1})
    segs = [s for s in segs if s['n'] >= 2 or len(good) <= 2]
    out = []
    for i, s in enumerate(segs):
        m0 = 0.0 if i == 0 else (segs[i - 1]['m1'] + block + s['m0']) / 2.0
        m1 = (len(ye) / sr * rate) if i == len(segs) - 1 else (s['m1'] + block + segs[i + 1]['m0']) / 2.0
        m0 = max(m0, 0.0 if i == 0 else m0)
        out.append((round(t0 + m0 / rate, 3), round(t0 + m1 / rate, 3), round(s['off'] + m0, 3), rate))
    return out


def _song_times(song_map, a, T, rate):
    """Song time of every model frame (model time 0 = audio time a, running at song speed): NaN where the
    map says nothing."""
    ts = np.full(T, np.nan)
    tm = FRAME_T0 + np.arange(T) * HOP                # model time
    ta = a + tm / rate                                # audio time
    for s0, s1, song0, r in song_map:
        m = (ta >= s0) & (ta < s1)
        ts[m] = song0 + (ta[m] - s0) * r
    return ts


# --------------------------------------------------------------------------------------------- main API

def _prior(ws, ts, song_start=None, tol=1.0, slope=-0.35):
    """(T, W) penalty for word k at model frame t: 0 while the frame's song time is inside the word's LRC
    line span +- tol, else `slope` per second outside. `ts` is the song time of every frame (NaN = unknown:
    no penalty), or the frame count T with `song_start` = song time of model time 0."""
    if song_start is not None:
        ts = song_start + FRAME_T0 + np.arange(int(ts)) * HOP
    ts = np.asarray(ts, float)
    lt = np.array([w['line_t'] if w['line_t'] is not None else np.nan for w in ws], float)
    if not np.isfinite(lt).any():
        return None
    uniq = sorted(set(x for x in lt if np.isfinite(x)))                 # line end = next line's start
    nxt = {u: (uniq[i + 1] if i + 1 < len(uniq) else u + 8.0) for i, u in enumerate(uniq)}
    lo = np.where(np.isfinite(lt), lt, -1e9)
    hi = np.array([nxt.get(x, 1e9) if np.isfinite(x) else 1e9 for x in lt])
    tsf = np.where(np.isfinite(ts), ts, 0.0)
    d = np.maximum(lo[None] - tol - tsf[:, None], 0) + np.maximum(tsf[:, None] - hi[None] - tol, 0)
    d[~np.isfinite(ts)] = 0.0
    return (slope * np.minimum(d, 20.0)).astype(np.float32)


_STOP = {
    'en': 'the you and i to me my a it in your is that of on we be all so know love what dont',
    'es': 'que de la el y en me te no lo un una por mi tu yo con se',
    'pt': 'que de o a e eu não você me meu no um uma com se',
    'fr': 'je de la le et tu les que pas un une me mon moi est',
    'de': 'ich die der und du nicht das ist mich mir ein zu es',
    'it': 'che di il la e non mi ti un una per io sei',
}


def _lyrics_lang(ws):
    """The recogniser's language from the lyrics: Hangul -> ko, kana -> ja, CJK -> zh, Cyrillic -> ru,
    otherwise the Latin language whose stop words are most frequent (None when unsure)."""
    text = ' '.join(w['text'] for w in ws)
    if not text:
        return None
    cnt = lambda lo, hi: sum(1 for ch in text if lo <= ord(ch) <= hi)
    if cnt(0xAC00, 0xD7A3) > 20:
        return 'ko'
    if cnt(0x3040, 0x30FF) > 10:
        return 'ja'
    if cnt(0x4E00, 0x9FFF) > 20:
        return 'zh'
    if cnt(0x0400, 0x04FF) > 20:
        return 'ru'
    toks = [re.sub(r"[^\w']", '', w['text'].lower()) for w in ws]
    sc = {k: sum(1 for t in toks if t in set(v.split())) for k, v in _STOP.items()}
    best = max(sc, key=sc.get)
    return best if sc[best] >= 3 else None


def _cache_path(*parts):
    key = hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:24]
    return os.path.join(LYR, 'words', key + '.json')


def _cached_array(path, make):
    if os.path.exists(path):
        try:
            return np.load(path).astype(np.float32)
        except (OSError, ValueError):
            pass
    x = make()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.save(path, x.astype(np.float16))
    return np.asarray(x, np.float32)


def words(song, artist, audio_path, t0=0.0, t1=None, duration=None, song_t0=None, rate=1.0, lang=None,
          threads=None, pad=1.0, refine_onsets=True, record=None, use_cache=True, offline=False, stats=None,
          mode='auto', song_map=None, song_audio=None):
    """
    Word timings of the lyrics sung in [t0, t1] of `audio_path` (the song file, or the edit's own audio -
    sped up, pitched or re-cut). Times are seconds of audio_path. Returns
    [{text, t_start, t_end, line_id, confidence, lyric, roman?, partial?}] sorted by t_start; [] when
    there are no lyrics and no vocals. lyric=False marks sung / spoken words that are not in the lyrics
    (a TikTok-sound intro): shown by the references, so they are kept, spelled as recognised.

    duration   the full song's length (helps LRCLIB pick the right version); defaults to the file's length
    song_t0    song time at audio time t0, when known (enables the LRC prior); inferred when the file is the
               whole song (its length is within 3 s of the LRCLIB duration) and rate == 1
    rate       song seconds per audio second (1.1 for a 110 % sped-up edit)
    song_map   [(audio_start, audio_end, song_start, rate)] for a re-cut edit (several pieces of the song);
               overrides song_t0 / rate for the prior (the model still runs at `rate`)
    song_audio the original song file: when no song time is given, locate() maps the window onto it
    mode       'auto' (LRC prior when the song time is known, else recogniser anchors), 'lrc', 'anchor'
    lang       the recogniser's language (default: guessed from the lyrics' spelling)
    """
    t_file = _probe_duration(audio_path)
    if t1 is None:
        t1 = t_file or (t0 + 20.0)
    rec = record or fetch(song, artist, duration or (t_file if t_file and t_file > 60 else None), offline=offline)
    ws = tokens(rec.get('lines') or [])
    # the file IS the song only when no separate original is given: a speed version (blue @0.9 = 238 s) can match an
    # LRCLIB record's length by chance (237 s) and would then show the lyrics ~5 s early (2026-10-21 dry run)
    _vm = re.search(r'@v([0-9.]+)\.[A-Za-z0-9]+$', os.path.basename(str(audio_path)))
    is_version = bool(_vm) and abs(float(_vm.group(1).rstrip('.') or 1) - 1) > 1e-3     # <id>@v0.9.m4a = a speed version
    if song_t0 is None and not is_version and rec.get('synced') and rec.get('duration') and t_file and abs(rate - 1) < 1e-3 \
            and abs(float(rec['duration']) - t_file) <= 3.0:
        song_t0 = float(t0)
    a, b = t0 - pad, t1 + pad
    rate = float(rate or 1.0)
    if song_map is None and song_t0 is not None:
        song_map = [(a, b, float(song_t0) - pad * rate, rate)]
    if song_map is None and song_audio and rec.get('synced') and mode != 'anchor':
        lk = os.path.join(LYR, 'maps', hashlib.sha1(f'{_file_key(audio_path)}|{_file_key(song_audio)}|{a:.3f}|'
                                                    f'{b:.3f}|{rate}{"|ver" if is_version else ""}'.encode()).hexdigest()[:20] + '.json')
        if os.path.exists(lk):
            with open(lk, encoding='utf-8') as f:
                song_map = [tuple(x) for x in json.load(f)]
        else:
            # a speed version (<id>@v0.9) runs at its own speed vs the original: locate at that rate (rate 1 finds nothing)
            loc_rate = float(_vm.group(1).rstrip('.')) if is_version and abs(rate - 1) < 1e-3 else rate
            song_map = locate(audio_path, song_audio, max(0.0, a), b, loc_rate) or None
            os.makedirs(os.path.dirname(lk), exist_ok=True)
            with open(lk, 'w', encoding='utf-8') as f:
                json.dump(song_map or [], f)
    if lang is None:
        lang = _lyrics_lang(ws)
    cp = _cache_path(VERSION, _file_key(audio_path), rec.get('id'), len(ws), f'{a:.3f}', f'{b:.3f}',
                     json.dumps(song_map), rate, lang, refine_onsets, mode, W2V_MODEL)
    if use_cache and os.path.exists(cp) and not _no_store():
        try:
            with open(cp, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    tm = {}
    t = time.time()
    v16 = vocal_stem(audio_path, a, b, threads)
    tm['demucs'] = time.time() - t
    if abs(rate - 1.0) > 1e-3:                    # undo the speed-up for the models, map times back later
        from scipy.signal import resample_poly
        vm = resample_poly(v16, int(round(rate * 1000)), 1000).astype(np.float32)
    else:
        vm, rate = v16, 1.0
    wkey = hashlib.sha1(f'{_file_key(audio_path)}|{a:.3f}|{b:.3f}|{rate:.5f}'.encode()).hexdigest()[:20]
    out = []
    lat = [w for w in ws if w['norm'] and w['script'] == 'l']
    latin = bool(ws) and len(lat) >= 0.6 * len(ws)
    hyp = None
    check = mode == 'auto' and latin and bool(song_map) and rec.get('synced') and os.environ.get('LYRICS_CHECK', '1') != '0'
    if mode == 'auto':
        mode = 'lrc' if (song_map and rec.get('synced')) else 'anchor'
    if mode == 'anchor' or not latin or check:
        t = time.time()
        hp = os.path.join(LYR, 'asr', f"{wkey}-{os.environ.get('LYRICS_WHISPER', 'base')}-{lang}-v2.json")
        if os.path.exists(hp) and not _no_store():
            with open(hp, encoding='utf-8') as f:
                hyp = json.load(f)
        else:
            hyp = _whisper_words(vm, lang, threads)
        if not _no_store() and not os.path.exists(hp):
            os.makedirs(os.path.dirname(hp), exist_ok=True)
            with open(hp, 'w', encoding='utf-8') as f:
                json.dump(hyp, f, ensure_ascii=False)
        tm['whisper'] = time.time() - t
    if latin or (hyp and not ws):
        if mode == 'lrc':
            anchors = _lrc_sequence(lat, song_map, a, rate)
            fz = _foreign(hyp, anchors) if (check and hyp) else []
            if fz:                                 # heard words that are not these lyrics: align what is heard
                inside = lambda t: any(f0 <= t <= f1 for f0, f1 in fz)
                anchors = [x for x in anchors if not inside(0.5 * (x['a'] + max(x['a'], x['b'])))]
                for h in hyp:
                    txt = h['text'].strip(',.!?;:"…')
                    if inside(h['t0']) and h['p'] >= 0.3 and romanise(txt):
                        anchors.append({'tok': {'text': txt, 'norm': romanise(txt).replace(' ', ''), 'line_id': None,
                                                'line_t': None, 'optional': False, 'script': 'l', 'lyric': False,
                                                'skip': -12.0}, 'a': h['t0'], 'b': h['t1']})
                anchors.sort(key=lambda x: x['a'])
                tm['foreign'] = [[round(f0 / rate + a, 2), round(f1 / rate + a, 2)] for f0, f1 in fz]
            seq = [x['tok'] for x in anchors]
            pr_fn = lambda T: _anchor_prior(anchors, T, tol=0.6)
            kw = {'jump_pen': NEG, 'free_ends': False}
            # 2026-10-22 (blue @0.9 dry run 6): the window starts mid-line. With a cheap skip (-3) for every word of that
            # line the aligner smeared its first word over the next two and skipped two ('I' shown 1.35 s late). The
            # words sung before the window are a PREFIX of that line: the path may start at any of its words (the prefix
            # is not sung here), and every word from the start on must be placed like a full line's word.
            head = []
            for k, x in enumerate(anchors):
                if not x.get('head'):
                    break
                head.append(k)
            if head and os.environ.get('LYRICS_HEAD_START', '1') != '0':
                for k in head:
                    if not seq[k].get('optional'):
                        seq[k] = dict(seq[k], skip=-30.0)
                # 2026-10-23: the path starts at the first word estimated inside the window (+- 1 word, the estimate's
                # error); starting anywhere later (skipping the whole sung prefix, what the -3 per word allowed: blue r6
                # dry run, the prefix vanished) is not offered
                k_est = sum(1 for k in head if anchors[k].get('pre'))
                # the recogniser's own times for the prefix words it heard (same spelling, in order): the line's span
                # says nothing about WHERE in the window a word is sung, and the CTC path smeared a held one-letter
                # word ('I', a melisma) 1.2 s late. A heard word pins its token to +-0.2 s of the recogniser's start.
                if hyp:
                    hs = sorted((h for h in hyp if h.get('p', 0) >= 0.4 and _ntok(h['text'])), key=lambda h: h['t0'])
                    j0, pinned = 0, 0
                    for k in head:
                        nk = re.sub(r'[^a-z0-9]', '', (seq[k].get('norm') or '').lower())
                        for j in range(j0, len(hs)):
                            if hs[j]['t0'] > anchors[k]['b'] + 0.6:
                                break
                            if _wsim(_ntok(hs[j]['text']), nk) >= 0.75 and hs[j]['t0'] >= anchors[k]['a'] - 0.6:
                                anchors[k] = dict(anchors[k], a=hs[j]['t0'], b=hs[j]['t0'], tol=0.2)
                                j0, pinned = j + 1, pinned + 1
                                break
                    tm['head_pinned'] = pinned
                kw['start_ks'] = [k for k in (k_est, k_est - 1, k_est + 1) if 0 <= k <= head[-1] + 1]
                kw['start_skip'] = float(os.environ.get('LYRICS_HEAD_SKIP', '-1.5'))
                tm['head_line'] = [len(head), k_est]
        else:
            mapj = map_hyp([h['text'] for h in hyp], lat) if lat else [None] * len(hyp)
            anchors = _anchor_sequence(hyp, mapj, lat, min_p=0.3 if not lat else 0.45)
            seq = [s['tok'] for s in anchors]
            pr_fn = lambda T: _anchor_prior(anchors, T)
            kw = {'jump_pen': NEG, 'free_ends': False}
        if seq:
            t = time.time()
            lp = _cached_array(os.path.join(LYR, 'emis', f'{wkey}-w2v-{W2V_MODEL}-960h.npy'), lambda: emissions(vm, threads))
            tm['ctc'] = time.time() - t
            t = time.time()
            path, graph, score = viterbi(lp, seq, pr_fn(len(lp)), **kw)
            segs = _segments(path, graph, len(seq))
            tm['viterbi'] = time.time() - t
            post = np.exp(lp)
            for sg in segs:
                k = sg['k']
                w = seq[k]
                letters = [(f, s) for f, s in zip(sg['frames'], sg['states']) if graph[0][s] == 1]
                if not letters:
                    continue
                conf = float(np.mean([post[f, graph[1][s]] for f, s in letters]))
                n_let = len(set(s for _, s in letters))
                n_all = int((graph[0][graph[4][k]:graph[5][k] + 1] == 1).sum())
                conf *= n_let / max(1, n_all)
                item = {'text': w['text'], '_t': (FRAME_T0 + letters[0][0] * HOP) / rate + a,
                        '_e': (FRAME_T0 + (sg['f1'] + 1) * HOP) / rate + a, 'line_id': w['line_id'],
                        'confidence': conf, 'lyric': w.get('lyric', True)}
                if sg['mid'] or n_let < n_all:
                    item['partial'] = True
                if not re.fullmatch(r"[A-Za-z'’.,!?-]+", w['text']):
                    item['roman'] = w['norm'].lower()
                out.append(item)
    elif hyp:
        # a script the English letters cannot spell (kanji): the recogniser's own word times, in the
        # lyrics' spelling where they match
        mapj = map_hyp([h['text'] for h in hyp], ws) if ws else [None] * len(hyp)
        for h, j in zip(hyp, mapj):
            txt = ws[j]['text'] if j is not None else h['text'].strip(',.!?')
            if txt:
                out.append({'text': txt, '_t': h['t0'] / rate + a, '_e': h['t1'] / rate + a,
                            'line_id': ws[j]['line_id'] if j is not None else None,
                            'confidence': h['p'] * (1.0 if j is not None else 0.5), 'lyric': j is not None})
    out.sort(key=lambda w: w['_t'])
    if refine_onsets and out:
        t = time.time()
        vr = v16
        for w in out:                            # refine in the stem's own (edit) time
            w['_t'] -= a
            w['_e'] -= a
        refine(out, vr)
        for w in out:
            w['_t'] += a
            w['_e'] += a
        tm['refine'] = time.time() - t
    res = []
    for w in out:
        if w['_e'] < t0 or w['_t'] > t1:
            continue
        r = {'text': w['text'], 't_start': round(w['_t'], 4), 't_end': round(w['_e'], 4),
             'line_id': w['line_id'], 'confidence': round(float(w['confidence']), 3),
             'lyric': bool(w.get('lyric', True))}
        for k in ('roman', 'partial'):
            if w.get(k):
                r[k] = w[k]
        res.append(r)
    if stats is not None:
        stats.update({k: (round(v, 3) if isinstance(v, float) else v) for k, v in tm.items()})
        stats['lyrics'] = {'id': rec.get('id'), 'source': rec.get('source'), 'synced': rec.get('synced'),
                           'words': len(ws), 'song_t0': song_t0, 'mode': mode}
    if _no_store():
        return res
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    tmp = f'{cp}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, cp)
    return res


def lines(song, artist, audio_path, t0=0.0, t1=None, **kw):
    """The words grouped by lyric line: [{line_id, text, t_start, t_end, words}]."""
    ws = words(song, artist, audio_path, t0, t1, **kw)
    out = []
    for w in ws:
        if out and out[-1]['line_id'] == w['line_id'] and w['t_start'] - out[-1]['t_end'] < 2.5:
            L = out[-1]
            L['words'].append(w)
            L['text'] += ' ' + w['text']
            L['t_end'] = max(L['t_end'], w['t_end'])
        else:
            out.append({'line_id': w['line_id'], 'text': w['text'], 't_start': w['t_start'], 't_end': w['t_end'],
                        'words': [w]})
    return out


def overlay_words(ws, lag=2.5 / 30.0, min_conf=0.0, offset=0.0):
    """Words -> the engine's lyric overlay format [{text, t0, t1}] with the display lag the references
    use (fx.json: legible 2.5 frames after the sung onset). `offset` maps audio time to edit time."""
    return [{'text': w['text'], 't0': round(w['t_start'] + lag + offset, 4),
             't1': round(w['t_end'] + lag + offset, 4)} for w in ws if w['confidence'] >= min_conf]


# --------------------------------------------------------------------------------------------- self test

def selftest():
    """Offline checks (no network, no models): LRC parsing, tokenising, romanisation, the Viterbi on
    synthetic emissions (a plain pass, a start mid-lyrics, a jump back to a repeated chorus, a skipped
    word, ad-lib frames), the onset snap and the pacer. Must stay green."""
    ok = True

    def check(name, cond, info=''):
        nonlocal ok
        print(f"  {'ok ' if cond else 'FAIL'} {name} {info}")
        ok = ok and bool(cond)

    L = parse_lrc('[ar:x]\n[00:01.50]Just trust me\n[00:03.00][00:10.25]You\'ll be fine (yeah)\n[01:02.5]<01:02.5>word')
    check('parse_lrc', [x['t'] for x in L] == [1.5, 3.0, 10.25, 62.5] and L[0]['text'] == 'Just trust me'
          and L[-1]['text'] == 'word', L)
    tk = tokens(L)
    check('tokens', [w['norm'] for w in tk][:6] == ['JUST', 'TRUST', 'ME', "YOU'LL", 'BE', 'FINE']
          and tk[6]['optional'] and not tk[5]['optional'], [(w['text'], w['optional']) for w in tk[:7]])
    check('romanise hangul', romanise('사랑해 22') == 'SARANGHAE TWENTY TWO', romanise('사랑해 22'))
    check('romanise accents', romanise('Marías & café') == 'MARIAS AND CAFE', romanise('Marías & café'))
    check('unromanisable', _unromanisable('愛して') and not _unromanisable('사랑 love'))

    # synthetic emissions: each letter spikes on one frame, blanks elsewhere
    def synth(seq, T, noise=0.0, seed=0):
        rng = np.random.default_rng(seed)
        lp = np.full((T, len(LABELS)), -9.0, np.float32)
        lp[:, 0] = -0.05
        for f, c in seq:
            lp[f] = -9.0
            lp[f, LABELS.index(c)] = -0.05
        lp += rng.normal(0, noise, lp.shape).astype(np.float32)
        return lp - np.log(np.exp(lp).sum(1, keepdims=True))

    def mk(words):
        return [{'text': w, 'norm': w, 'line_id': 0, 'line_t': None, 'optional': False, 'script': 'l'} for w in words]

    def place(words, starts, T):
        seq = []
        for w, s in zip(words, starts):
            for i, c in enumerate(w):
                seq.append((s + 2 * i, c))
        return seq

    def run(lp, ws):
        path, g, _ = viterbi(lp, ws)
        return [(ws[s['k']]['text'], [f for f, st in zip(s['frames'], s['states']) if g[0][st] == 1][0])
                for s in _segments(path, g, len(ws))]

    lyr = ['HELLO', 'WORLD', 'SING', 'ALONG', 'TONIGHT', 'HELLO', 'WORLD']
    ws = mk(lyr)
    # 1. a window that starts at word 2 ("SING ALONG TONIGHT")
    lp = synth(place(['SING', 'ALONG', 'TONIGHT'], [10, 40, 70], 100), 100, 0.3)
    r = run(lp, ws)
    check('start mid-lyrics', [x[0] for x in r] == ['SING', 'ALONG', 'TONIGHT'] and [x[1] for x in r] == [10, 40, 70], r)
    # 2. a jump: TONIGHT then back to SING (the edit re-cut the song)
    lp = synth(place(['ALONG', 'TONIGHT', 'SING', 'ALONG'], [5, 30, 60, 85], 110), 110, 0.3, 1)
    r = run(lp, ws)
    check('jump back', [x[0] for x in r] == ['ALONG', 'TONIGHT', 'SING', 'ALONG'] and r[2][1] == 60, r)
    # 3. a word the singer skips (WORLD missing between HELLO and SING)
    lp = synth(place(['HELLO', 'SING', 'ALONG'], [5, 30, 55], 80), 80, 0.2, 2)
    r = run(lp, ws)
    check('skipped word', [x[0] for x in r] == ['HELLO', 'SING', 'ALONG'], r)
    # 4. ad-lib letters between words go to the gap, not to a lyric word
    seq = place(['SING', 'ALONG'], [5, 60], 80) + [(25, 'O'), (27, 'O'), (29, 'H')]
    r = run(synth(seq, 80, 0.2, 3), ws)
    check('ad-lib in gap', [x[0] for x in r] == ['SING', 'ALONG'] and r[1][1] == 60, r)
    # 5. prior: two identical choruses, the prior picks the second
    ws2 = mk(['HELLO', 'WORLD', 'SING', 'HELLO', 'WORLD'])
    for i, w in enumerate(ws2):
        w['line_t'] = 0.0 if i < 3 else 20.0
    lp = synth(place(['HELLO', 'WORLD'], [10, 40], 80), 80, 0.2, 4)
    pr = _prior(ws2, 80, 25.0)
    path, g, _ = viterbi(lp, ws2, pr)
    ks = [s['k'] for s in _segments(path, g, len(ws2))]
    check('prior picks the right repeat', ks == [3, 4], ks)

    # onset snap: a click train on a quiet tone
    t = np.arange(int(2.0 * SR)) / SR
    y = 0.01 * np.sin(2 * np.pi * 220 * t)
    for s0 in (0.5, 1.2):
        m = (t >= s0) & (t < s0 + 0.3)
        y[m] += 0.5 * np.sin(2 * np.pi * 440 * t[m]) * np.exp(-(t[m] - s0) * 6)
    ww = [{'_t': 0.56, '_e': 0.7}, {'_t': 1.15, '_e': 1.3}]
    refine(ww, y.astype(np.float32))
    check('onset snap', abs(ww[0]['_t'] - 0.5) < 0.02 and abs(ww[1]['_t'] - 1.2) < 0.02, [round(w['_t'], 3) for w in ww])
    # DP snap: a CTC start 210 ms early in a quiet gap goes to the syllable, and two words never share one
    ww = [{'_t': 0.52, '_e': 0.7}, {'_t': 0.99, '_e': 1.1}]
    refine(ww, y.astype(np.float32))
    check('dp snap (early CTC word)', abs(ww[0]['_t'] - 0.5) < 0.02 and abs(ww[1]['_t'] - 1.2) < 0.02,
          [round(w['_t'], 3) for w in ww])
    ww = [{'_t': 0.49, '_e': 0.6}, {'_t': 0.53, '_e': 0.7}]
    refine(ww, y.astype(np.float32))
    check('dp snap keeps order', ww[1]['_t'] - ww[0]['_t'] >= REFINE['min_gap'] - 1e-6, [round(w['_t'], 3) for w in ww])

    # foreign speech: confident heard words that are not the expected lyrics -> a stretch; a loop is ignored
    def tok(w, a, b):
        return {'tok': {'text': w, 'norm': romanise(w).replace(' ', '')}, 'a': a, 'b': b}
    anchors = [tok(w, 5.0 + 0.3 * i, 5.2 + 0.3 * i) for i, w in enumerate('cause you be poppin heard you get it'.split())]
    hyp = [{'text': w, 't0': 5.0 + 0.5 * i, 't1': 5.3 + 0.5 * i, 'p': 0.9, 'alp': -0.45}
           for i, w in enumerate('me obsessed with yes yes am'.split())]
    fz = _foreign(hyp, anchors)
    check('foreign stretch', len(fz) == 1 and fz[0][0] <= 5.0 and fz[0][1] >= 7.8, fz)
    same = [{'text': w, 't0': 5.0 + 0.3 * i, 't1': 5.2 + 0.3 * i, 'p': 0.9, 'alp': -0.45}
            for i, w in enumerate('cause you be poppin heard you get it'.split())]
    check('lyrics heard -> no stretch', _foreign(same, anchors) == [])
    loop = [{'text': w, 't0': 5.0 + 0.2 * i, 't1': 5.2 + 0.2 * i, 'p': 0.9, 'alp': -0.45}
            for i, w in enumerate('it was just it was just it was just'.split())]
    check('hallucination loop ignored', _foreign(loop, anchors) == [])
    check('low log-prob ignored', _foreign([dict(h, alp=-1.2) for h in hyp], anchors) == [])

    # pacer: 3 waits take >= 2 gaps
    root = tempfile.mkdtemp(prefix='lyrpace')
    p = _Pace(root, 0.2)
    t = time.time()
    for _ in range(3):
        p.wait()
    check('pacer', time.time() - t >= 0.38, round(time.time() - t, 3))
    print('selftest', 'PASSED' if ok else 'FAILED')
    return ok


# --------------------------------------------------------------------------------------------- reference check

REF_ROOT = os.environ.get('EDIT_REFS') or os.path.join(tempfile.gettempdir(), 'edit-refs')
LAG_REF = 2.5 / 30.0          # fx.json: a word is LEGIBLE (OCR-sharp) a median 2.5 frames after its vocal onset
# display lag of the FIRST INK of a word after its sung onset, as the audio-visual studies measured it:
# fx-av.md 3 (WGvt: first visible frame = the syllable's first frame, median 0), kpop-av.md (pB7P: text t10 a
# median +27 ms after the vocal onset), general-av.md (m3lv +13 ms, zawB +3 ms medians); 0 where unmeasured
STUDY_LAG = {'WGvtliE1298': 0.0, 'pB7PRQKPUuA': 0.027, 'm3lv7H6cu_Y': 0.013, 'zawBCooJwLw': 0.003}
# sung syllable onsets the studies read off the vocal stem (fx-av.md 3 table, kpop-av.md timing table):
# an independent check of our word starts, not produced by this module
STUDY_SUNG = {
    'WGvtliE1298': [('and', 4.90), ('when', 5.30), ("i'm", 5.73), ('back', 6.04), ('another', 10.95),
                    ('version', 11.90), ('of', 12.78)],
    'pB7PRQKPUuA': [('me', 0.23), ('obsessed', 1.457), ('with', 1.84), ('you', 2.125), ('why', 5.625)],
}
# references whose on-screen text follows the lyrics (core/edit/refs/*.md), with the song and its rate
REFS = {
    'WGvtliE1298': dict(video='fx/WGvtliE1298.mp4', song='End of Beginning', artist='Djo', duration=160, t1=16.77),
    'yUuiSey5kmE': dict(video='character/yUuiSey5kmE.mp4', song='Hush', artist='The Marías'),
    'pB7PRQKPUuA': dict(video='kpop/pB7PRQKPUuA.mp4', song='Obsessed', artist='Mariah Carey', rate=1.1025),
    'i4fSruIkkxk': dict(video='general/i4fSruIkkxk.mp4', song='Bad Romance', artist='Lady Gaga'),
    'm3lv7H6cu_Y': dict(video='general/m3lv7H6cu_Y.mp4', song='Alibi', artist='Sevdaliza'),
    'zawBCooJwLw': dict(video='general/zawBCooJwLw.mp4', song='Espresso', artist='Sabrina Carpenter'),
    '_sX2EUexbW8': dict(video='general/_sX2EUexbW8.mp4', song='golden hour', artist='JVKE', rate=0.95),
    'rfsjEohxVaw': dict(video='character/rfsjEohxVaw.mp4', song='golden hour', artist='JVKE'),
    'TG_hmVvoWC4': dict(video='character/TG_hmVvoWC4.mp4', song='Meant To Be', artist='Arc North'),
    'YH_GH1nk1c0': dict(video='character/YH_GH1nk1c0.mp4', song='Call Me Girl', artist=''),
}


def ocr_frames(video, cache_dir=None, step=3):
    """Frames of `video` through RapidOCR (long side <= 1280) -> {fps, n, w, h, frames: {i: [[text, conf,
    box]]}, sampled: [i...]}, cached as JSON (local verification only). Every `step`-th frame is read first;
    wherever the words on screen change between two samples, the frames in between are read too, so every
    word's first legible frame is exact while static stretches cost 1/step. Unsampled frames equal the
    previous sampled one."""
    cache_dir = cache_dir or os.path.join(LYR, 'refocr')
    vid = os.path.splitext(os.path.basename(video))[0]
    dst = os.path.join(cache_dir, vid + '.json')
    if os.path.exists(dst):
        with open(dst, encoding='utf-8') as f:
            return json.load(f)
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    o = RapidOCR()
    cap = cv2.VideoCapture(video)
    fps, w, h = cap.get(5), int(cap.get(3)), int(cap.get(4))
    sc = min(1.0, 1280.0 / max(w, h))
    imgs = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if sc < 1:
            f = cv2.resize(f, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        imgs.append(cv2.imencode('.jpg', f, [cv2.IMWRITE_JPEG_QUALITY, 95])[1])
    n = len(imgs)
    fr = {}

    def read(i):
        if i in fr:
            return fr[i]
        r, _ = o(cv2.imdecode(imgs[i], cv2.IMREAD_COLOR))
        fr[i] = [[x[1], round(float(x[2]), 3), [[round(float(a) / sc, 1) for a in pt] for pt in x[0]]]
                 for x in (r or [])]
        return fr[i]

    def toks(i):
        return sorted(_ntok(t) for x in read(i) if x[1] >= 0.6 for t in x[0].split())

    grid = list(range(0, n, step)) + ([n - 1] if (n - 1) % step else [])
    for a, b in zip(grid, grid[1:]):
        if toks(a) != toks(b):
            for i in range(a + 1, b):
                read(i)
    d = {'fps': fps, 'n': n, 'w': w, 'h': h, 'scale': sc, 'sampled': sorted(fr),
         'frames': {str(i): v for i, v in fr.items() if v}}
    os.makedirs(cache_dir, exist_ok=True)
    with open(dst + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f)
    os.replace(dst + '.tmp', dst)
    return d


def _ntok(s):
    return re.sub(r'[^a-z0-9]', '', romanise(s).lower())


def screen_events(ocr, min_conf=0.6, persist=2, absent=3, back=15):
    """OCR frames -> word appearances [{t, t_legible, frame, text}]. A word is LEGIBLE on the first frame its
    token count rises (after `absent` frames lower) and stays for `persist` of the next 3 frames. It
    APPEARS (`t`) on the earliest frame before that, walking back up to `back` frames, where a partial
    reading of it (a prefix, a substring or a >= 0.5 fuzzy match: letters typing / flying in) sits near the
    same place. `t` is the reference time used by verify_refs."""
    fps, n = float(ocr['fps']), int(ocr['n'])
    sampled = set(ocr.get('sampled') or range(n))
    items, counts = [], []
    for i in range(n):
        if i not in sampled and items:
            items.append(items[-1])
            counts.append(counts[-1])
            continue
        it, c = [], {}
        for text, conf, box in ocr['frames'].get(str(i), []):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            toks = text.split()
            tot = max(1, sum(len(t) + 1 for t in toks) - 1)
            pos = 0
            for tok in toks:
                k = _ntok(tok)
                bx = (x0 + (x1 - x0) * pos / tot, y0, x0 + (x1 - x0) * (pos + len(tok)) / tot, y1)
                pos += len(tok) + 1
                if not k:
                    continue
                it.append((k, bx, conf))
                if conf >= min_conf:
                    c[k] = c.get(k, 0) + 1
        items.append(it)
        counts.append(c)

    def near(b1, b2):
        cx1, cy1, cx2, cy2 = (b1[0] + b1[2]) / 2, (b1[1] + b1[3]) / 2, (b2[0] + b2[2]) / 2, (b2[1] + b2[3]) / 2
        s = max(b1[2] - b1[0], b1[3] - b1[1], b2[2] - b2[0], b2[3] - b2[1])
        return abs(cx1 - cx2) <= 1.2 * s and abs(cy1 - cy2) <= 1.2 * s

    def partial(t, w):
        return t == w or (len(t) <= len(w) and (w.startswith(t) or (len(t) >= 2 and t in w))) or \
            (len(t) >= 2 and _wsim(t, w) >= 0.5)

    ev = []
    for i in range(n):
        for k, v in counts[i].items():
            before = [counts[j].get(k, 0) for j in range(max(0, i - absent), i)]
            if before and max(before) >= v:
                continue
            ahead = [counts[j].get(k, 0) >= v for j in range(i, min(n, i + 3))]
            if sum(ahead) < persist:
                continue
            boxes = [bx for t, bx, cf in items[i] if t == k]
            first, bx = i, boxes[0] if boxes else None
            miss = 0
            for j in range(i - 1, max(-1, i - back - 1), -1):
                hit = None
                if bx is not None:
                    for t, b2, cf in items[j]:
                        if partial(t, k) and near(bx, b2) and not (t == k and counts[j].get(k, 0) >= v):
                            hit = b2
                            break
                if hit is not None:
                    first, bx, miss = j, hit, 0
                elif j in sampled:
                    miss += 1
                    if miss >= 2:
                        break
            ev.append({'t': first / fps, 't_legible': i / fps, 'frame': first, 'frame_legible': i, 'text': k})
    return ev


def _match(events, ws, lag, max_dt=0.6):
    """Monotone DP between on-screen events and our words (same text, |appear - lag - start| <= max_dt).
    Returns [(event, word)]."""
    E, W = len(events), len(ws)
    if not E or not W:
        return []
    ok = np.zeros((E, W), bool)
    dt = np.zeros((E, W))
    for i, e in enumerate(events):
        for j, w in enumerate(ws):
            a, b = e['text'], _ntok(w['text'])
            d = e['t'] - lag - w['t_start']
            dt[i, j] = d
            if abs(d) <= max_dt and a and b and (a == b or (len(a) >= 4 and
                                                            difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)):
                ok[i, j] = True
    D = np.zeros((E + 1, W + 1))
    bk = np.zeros((E + 1, W + 1), np.int8)
    for i in range(1, E + 1):
        for j in range(1, W + 1):
            o = [D[i - 1, j], D[i, j - 1],
                 D[i - 1, j - 1] + (2.0 - abs(dt[i - 1, j - 1]) if ok[i - 1, j - 1] else -1e9)]
            bk[i, j] = int(np.argmax(o))
            D[i, j] = o[bk[i, j]]
    i, j, out = E, W, []
    while i > 0 and j > 0:
        if bk[i, j] == 2:
            out.append((events[i - 1], ws[j - 1]))
            i, j = i - 1, j - 1
        elif bk[i, j] == 0:
            i -= 1
        else:
            j -= 1
    return out[::-1]


def _stats(x):
    x = np.abs(np.asarray(x, float))
    if not len(x):
        return {'n': 0}
    return {'n': int(len(x)), 'median_ms': round(float(np.median(x)) * 1000, 1),
            'p90_ms': round(float(np.percentile(x, 90)) * 1000, 1), 'mean_ms': round(float(np.mean(x)) * 1000, 1)}


def _ref_song(rid):
    import glob
    for ext in ('m4a', 'mp3', 'webm', 'opus'):
        g = glob.glob(os.path.join(REF_ROOT, '_songs', f'{rid}.{ext}'))
        if g:
            return g[0]
    return None


def verify_refs(ids=None, threads=4, report=None, mode='auto'):
    """Our word starts vs the frame each lyric word appears on screen in the references. Ground truth: the
    manually checked first-ink frames in <edit-cache>/lyrics/refgt/<id>.json when present, else RapidOCR
    events. Only words that appear ON THEIR OWN count (a word typed on within 2 frames of the previous one
    belongs to a phrase card and says nothing about its own syllable). Words are paired with ours by text
    (monotone DP, |dt| <= 0.5 s). Errors are reported two ways: after the display lag the studies measured
    (STUDY_LAG) and after each reference's own median offset (bias removed). The song time comes from
    locate() on the original song in <refs>/_songs (our planners know which song window they cut). Also
    compares with the sung onsets the studies read off the stems (STUDY_SUNG). Prints and saves
    <edit-cache>/lyrics/verify-<mode>.json."""
    import cv2
    rows, all_fix, all_fit, all_sung = [], [], [], []
    for rid, cfg in REFS.items():
        if ids and rid not in ids:
            continue
        video = os.path.join(REF_ROOT, cfg['video'])
        if not os.path.exists(video):
            log(rid, 'missing', video)
            continue
        cap = cv2.VideoCapture(video)
        fps, nfr = cap.get(5), cap.get(7)
        cap.release()
        gtp = os.path.join(LYR, 'refgt', rid + '.json')
        if os.path.exists(gtp):
            with open(gtp, encoding='utf-8') as f:
                gt = json.load(f)
            ev = [{'t': fr / fps, 't_legible': fr / fps, 'frame': fr, 'text': _ntok(w)} for w, fr in gt['words']]
            gt_src = 'manual'
        else:
            ev = screen_events(ocr_frames(video))
            gt_src = 'ocr'
        t1 = cfg.get('t1') or nfr / fps
        ev = sorted([e for e in ev if e['t'] <= t1 + 0.5], key=lambda e: e['t'])
        own = [e for i, e in enumerate(ev) if i == 0 or e['t'] - ev[i - 1]['t'] > 2.5 / fps]
        st = {}
        song = _ref_song(rid)
        t = time.time()
        ws = words(cfg['song'], cfg['artist'], video, cfg.get('t0', 0.0), t1,
                   duration=cfg.get('duration') or (_probe_duration(song) if song else None),
                   rate=cfg.get('rate', 1.0), threads=threads, stats=st, song_audio=song, mode=mode)
        st['wall'] = round(time.time() - t, 2)
        m = _match(own, ws, 0.0, max_dt=0.5)
        d = [e['t'] - w['t_start'] for e, w in m]
        lag = STUDY_LAG.get(rid, 0.0)
        fix = [x - lag for x in d]
        bias = float(np.median(d)) if d else 0.0
        fit = [x - bias for x in d]
        sung = []
        for word, ts in STUDY_SUNG.get(rid, []):
            # pB7P shows its own words ("wonyoung" for "you"): its study onsets are matched by time only
            c = [w for w in ws if abs(w['t_start'] - ts) < 0.6 and (_ntok(w['text']) == _ntok(word) or rid == 'pB7PRQKPUuA')]
            if c:
                w = min(c, key=lambda w: (_ntok(w['text']) != _ntok(word), abs(w['t_start'] - ts)))
                sung.append({'word': word, 'study': ts, 'ours': w['t_start'], 'err_ms': round((w['t_start'] - ts) * 1000, 1)})
        row = {'id': rid, 'song': f"{cfg['artist']} - {cfg['song']}", 'rate': cfg.get('rate', 1.0), 'gt': gt_src,
               'screen_words': len(ev), 'own_words': len(own), 'our_words': len(ws), 'matched': len(m),
               'study_lag_ms': round(lag * 1000, 1), 'study_lag': _stats(fix), 'bias_ms': round(bias * 1000, 1),
               'bias_removed': _stats(fit), 'sung': sung,
               'pairs': [{'text': w['text'], 'screen_t': round(e['t'], 3), 'ours': w['t_start'],
                          'err_ms': round((e['t'] - lag - w['t_start']) * 1000, 1), 'conf': w['confidence'],
                          'lyric': w.get('lyric', True)} for e, w in m], 'timing': st}
        rows.append(row)
        all_fix += fix
        all_fit += fit
        all_sung += [x['err_ms'] / 1000.0 for x in sung]
        print(f"{rid:13s} {row['song'][:30]:30s} screen {len(ev):3d} own {len(own):3d} ours {len(ws):3d} matched "
              f"{len(m):3d} | study lag {lag * 1000:.0f} ms: median {row['study_lag'].get('median_ms')} p90 "
              f"{row['study_lag'].get('p90_ms')} | own bias {row['bias_ms']} ms -> median "
              f"{row['bias_removed'].get('median_ms')} p90 {row['bias_removed'].get('p90_ms')}"
              + (f" | vs study sung onsets {[x['err_ms'] for x in sung]}" if sung else ''), flush=True)
    tot = {'study_lag': _stats(all_fix), 'bias_removed': _stats(all_fit), 'study_sung': _stats(all_sung)}
    print('ALL', json.dumps(tot))
    out = report or os.path.join(LYR, f'verify-{mode}.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'refs': rows, 'total': tot, 'version': VERSION, 'refine': REFINE, 'w2v': W2V_MODEL}, f,
                  ensure_ascii=False, indent=1)
    return rows, tot


# --------------------------------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description='lyric word timing (LRCLIB + demucs + CTC forced alignment)')
    ap.add_argument('--song')
    ap.add_argument('--artist', default='')
    ap.add_argument('--audio', help='song file or the edit audio / video')
    ap.add_argument('--t0', type=float, default=0.0)
    ap.add_argument('--t1', type=float)
    ap.add_argument('--duration', type=float, help='full song length (LRCLIB version pick)')
    ap.add_argument('--song-t0', type=float, help='song time at audio time t0, when known')
    ap.add_argument('--rate', type=float, default=1.0, help='song seconds per audio second (1.1 = sped up 10%%)')
    ap.add_argument('--lang')
    ap.add_argument('--threads', type=int)
    ap.add_argument('--lines', action='store_true')
    ap.add_argument('--out')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--fetch', action='store_true', help='only fetch + print the lyrics record')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--verify-refs', action='store_true')
    ap.add_argument('--ids', help='comma list of reference ids for --verify-refs')
    ap.add_argument('--bench', action='store_true', help='time each stage on a cold cache')
    ap.add_argument('--mode', choices=['auto', 'lrc', 'anchor'], help="force a mode (default auto) for words() and --verify-refs")
    ap.add_argument('--song-audio', help='the original song file (maps an edit onto the song when no --song-t0)')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.verify_refs:
        verify_refs(a.ids.split(',') if a.ids else None, threads=a.threads or 4, mode=a.mode or 'auto')
        return
    if not a.song:
        ap.error('--song is required')
    if a.fetch:
        rec = fetch(a.song, a.artist, a.duration)
        print(json.dumps({k: v for k, v in rec.items() if k != 'lines'}, ensure_ascii=False))
        for L in rec['lines']:
            print(f"{L['t'] if L['t'] is not None else '':>8}  {L['text']}")
        return
    if not a.audio:
        ap.error('--audio is required')
    st = {}
    t = time.time()
    kw = dict(duration=a.duration, song_t0=a.song_t0, rate=a.rate, lang=a.lang, threads=a.threads,
              use_cache=not (a.no_cache or a.bench), stats=st, mode=a.mode or 'auto', song_audio=a.song_audio)
    if a.bench:
        key = hashlib.sha1(f'{_file_key(a.audio)}|{a.t0 - 1.0:.3f}|{(a.t1 or 0) + 1.0:.3f}|htdemucs'.encode()).hexdigest()[:20]
        cp = os.path.join(LYR, 'stems', key + '.npy')
        if os.path.exists(cp):
            os.remove(cp)
    res = lines(a.song, a.artist, a.audio, a.t0, a.t1, **kw) if a.lines else words(a.song, a.artist, a.audio, a.t0, a.t1, **kw)
    st['total'] = round(time.time() - t, 2)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    else:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    log('timing', json.dumps(st, ensure_ascii=False))


if __name__ == '__main__':
    main()
