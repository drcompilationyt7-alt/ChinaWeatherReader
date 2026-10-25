#!/usr/bin/env python3
"""Ranking / list compilation from other creators' edits (Zero Yen Otaku, PROTOTYPE, 2026-10-24).

NOT wired into any rotation and never uploads. Study: core/edit/refs/ranking-compilation-study.md.

Format (measured on 4 the_snow00 references): 1080x1440 (3:4) canvas, the source edit (16:9) in a
band in the lower middle, a blurred copy of the same clip fills the frame, a 2-line title on top and a
numbered list (5 slots) that fills in as each entry starts (countdown #5, #4[, #3]; #2 / #1 stay blank);
2-3 entries in ~60 s, each a 15-35 s window of its source edit WITH THAT EDIT'S OWN AUDIO; short dip to
black between entries; no intro / outro. Credits: plain-text @handles in the description (no links).

Pipeline per entry (topic -> ranked entries -> source edit -> segment):
  1. search YouTube (yt-dlp, spare cookies, paced), title must name the entry, landscape, >= 720p,
     views / like gates, not a whole-fight AMV, no explicit words;
  2. download (small, one at a time, paced) and gate: watermark / handle / persistent overlay (OCR +
     static-edge logo map), burned-in subtitles, per-shot safety (NudeNet + SigLIP, hentai blocked);
  3. segment picker: the drop (energy jump on a beat) + motion + cut density, window of the target
     length, never more than MAX_SHARE of the source edit, never its intro / end card;
  4. character check: the entry's character (AniList art from the quiz pools, face crops + SigLIP
     name prompts vs the other list entries / same-show characters) must win the segment's frames;
  5. render: blurred-fill 3:4 layout, list overlay (optional on-screen credit), each edit's own audio
     loudness-normalised, dip to black between entries; description via describe().

    python core/edit/ranking_compilation.py --topic jjk_strongest --out <mp4>
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse

os.environ.setdefault('EDIT_THREADS', '4')           # library.Siglip torch threads
os.environ.setdefault('OMP_NUM_THREADS', '4')
import cv2
import numpy as np

cv2.setNumThreads(4)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import library as L  # noqa: E402  (AnimeFaces, Siglip, face_crops, fetch_image, Cache, EXPLICIT_RE)
cf = L.cf             # clip_finder (NudeNet)
cj = L.cj             # clip_judge (safety prompts)

FONT = os.path.join(HERE, 'assets', 'fonts', 'Montserrat-Variable.ttf')
W, H, FPS = 1080, 1440, 30
BAND_Y = 590                     # top of the 16:9 clip band (ref: 0.41 of the height)
# Pacing = the references' (owner 2026-10-24: "longer so they can get the feel of the edit"):
# 2-3 entries revealed in ~60 s (#5, #4[, #3]); #2 / #1 stay blank (the comment hook).
REVEAL = (3, 3, 3, 2)            # 3 of the 4 references reveal 3 entries, one reveals 2
TOTAL = 59.5
SEG_3 = ((19.0, 25.0), (18.0, 23.0))   # first two of three; the third gets the rest (refs: 21/20/18.5, 25/19/15.5)
SEG_2 = ((29.0, 35.0),)                # first of two; the second gets the rest (ref: 35/24.5)
CHAR_MIN = 0.6                   # share of character-voting frames the entry's character must win
MAX_SHARE = 0.85                 # never the whole edit (its intro / end card stay out), see the study's risk part
MIN_VIEWS, MIN_LIKE_RATIO = 20_000, 0.015
MAX_SRC_DUR = 150                # longer uploads are full-fight AMVs / compilations, not edits
COOKIES = os.environ.get('YT_SPARE_COOKIES', os.path.join(os.path.expanduser('~'), 'yt-cookies.txt'))
PACE_S = 20                      # between YouTube requests (shared spare account)

# ---------------------------------------------------------------- topics
# rank 1 = best / strongest. 'chars' = AniList names in core/quiz/assets/anime-characters.json.
# Strongest lists: community power-scaling consensus after the manga ended (Sept 2024), sanity-checked
# against the JJK wiki's feats (Sukuna beat Gojo; Yuta in Gojo's body; Yuji's Shinjuku feats; Toji the
# strongest non-sorcerer). Every list is a judgement call: the title says "ranking", not "fact".
TOPICS = {
    'jjk_strongest': {
        'title': ['STRONGEST', 'JJK CHARACTERS'], 'kind': 'character',
        'entries': [
            {'name': 'SUKUNA', 'chars': ['Sukuna'], 'tags': ['ryoumen_sukuna_(jujutsu_kaisen)'], 'anime': 'JUJUTSU KAISEN',
             'q': ['sukuna edit 4k', 'ryomen sukuna amv edit'], 'alias': [r'sukuna']},
            {'name': 'GOJO', 'chars': ['Satoru Gojo'], 'tags': ['gojou_satoru'], 'anime': 'JUJUTSU KAISEN',
             'q': ['gojo edit 4k', 'gojo satoru amv edit'], 'alias': [r'gojo']},
            {'name': 'YUTA', 'chars': ['Yuta Okkotsu'], 'anime': 'JUJUTSU KAISEN',
             'q': ['yuta okkotsu edit 4k', 'yuta amv edit'], 'alias': [r'yuta', r'okkotsu']},
            {'name': 'YUJI', 'chars': ['Yuji Itadori'], 'tags': ['itadori_yuuji'], 'anime': 'JUJUTSU KAISEN',
             'q': ['yuji itadori edit 4k', 'itadori amv edit'], 'alias': [r'yuji', r'itadori']},
            {'name': 'TOJI', 'chars': ['Toji Fushiguro'], 'anime': 'JUJUTSU KAISEN',
             'q': ['toji fushiguro edit 4k', 'toji amv edit'], 'alias': [r'toji']},
        ]},
    # best waifu: ranked by fans = AniList favourites (quiz pool), adult female characters only
    'best_waifu': {
        'title': ['BEST', 'WAIFU'], 'kind': 'character',
        'entries': [
            {'name': 'FRIEREN', 'chars': ['Frieren'], 'tags': ['frieren'], 'anime': 'Frieren', 'q': ['frieren edit 4k'], 'alias': [r'frieren']},
            {'name': 'KURISU', 'chars': ['Kurisu Makise'], 'tags': ['makise_kurisu'], 'anime': 'Steins;Gate', 'q': ['kurisu makise edit'],
             'alias': [r'kurisu', r'makise']},
            {'name': 'NICO ROBIN', 'chars': ['Nico Robin'], 'tags': ['nico_robin'], 'anime': 'ONE PIECE', 'q': ['nico robin edit 4k'],
             'alias': [r'robin']},
            {'name': 'YOR', 'chars': ['Yor Forger'], 'tags': ['yor_briar'], 'anime': 'SPY x FAMILY', 'q': ['yor forger edit 4k'], 'alias': [r'\byor\b']},
            {'name': 'C.C.', 'chars': ['C.C.'], 'tags': ['c.c.'], 'anime': 'Code Geass', 'q': ['c.c. code geass edit'],
             'alias': [r'\bc\.?c\.?\b', r'code geass']},
        ]},
    'romance_peak': {
        'title': ['PEAK', 'ROMANCE ANIME'], 'kind': 'anime',
        'entries': [
            {'name': 'KIMI NI TODOKE', 'chars': ['Sawako Kuronuma', 'Shouta Kazehaya'], 'anime': 'Kimi ni Todoke',
             'q': ['kimi ni todoke edit 4k'], 'alias': [r'kimi ni todoke', r'sawako']},
            {'name': 'BLUE BOX', 'chars': ['Chinatsu Kano', 'Taiki Inomata'], 'anime': 'Blue Box',
             'q': ['blue box anime edit 4k'], 'alias': [r'blue box', r'ao no hako', r'chinatsu']},
            {'name': 'HORIMIYA', 'chars': ['Kyouko Hori', 'Izumi Miyamura'], 'anime': 'Horimiya',
             'q': ['horimiya edit 4k'], 'alias': [r'horimiya']},
            {'name': 'MY DRESS-UP DARLING', 'chars': ['Marin Kitagawa', 'Wakana Gojo'], 'tags': ['kitagawa_marin', 'gojou_wakana'], 'anime': 'Sono Bisque Doll',
             'q': ['marin kitagawa edit 4k'], 'alias': [r'marin', r'dress.?up darling', r'bisque']},
            {'name': 'THE FRAGRANT FLOWER', 'chars': ['Kaoruko Waguri', 'Rintarou Tsumugi'], 'anime': 'Kaoru Hana',
             'q': ['fragrant flower blooms with dignity edit 4k'], 'alias': [r'fragrant flower', r'kaoru hana', r'waguri']},
        ]},
}

# ---------------------------------------------------------------- helpers


MIN_FREE_GB = float(os.environ.get('RANKING_MIN_FREE_GB') or 5.2)   # owner rule; low-memory mode: 1.5


def wait_ram(what, need=MIN_FREE_GB, max_wait=6 * 3600):
    """Block until >= `need` GB RAM is available (owner rule for heavy local work)."""
    import psutil
    t0 = time.time()
    while psutil.virtual_memory().available / 2 ** 30 < need:
        if time.time() - t0 > max_wait:
            raise SystemExit(f'not enough free RAM for {what}')
        log(f'waiting for {need} GB free RAM before {what} '
            f'({psutil.virtual_memory().available / 2 ** 30:.1f} GB now)')
        time.sleep(60)


def idle():
    try:
        import psutil
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)    # children (ffmpeg) inherit it
    except Exception:
        pass


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def ytdlp(args, timeout=600):
    cmd = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '-q', '--js-runtimes', 'node', '--cookies', COOKIES] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.stdout


_last = [0.0]


def mining_busy():
    """True while a GitHub pool-mining run (same spare YouTube account) is queued / running."""
    try:
        r = subprocess.run(['gh', 'run', 'list', '--workflow', 'pool-mine-nightly.yml', '-L', '3', '--json', 'status'],
                           capture_output=True, text=True, timeout=60, cwd=ROOT)
        return any(x.get('status') in ('in_progress', 'queued', 'waiting', 'pending') for x in json.loads(r.stdout or '[]'))
    except Exception:
        return False


def pace():
    while mining_busy():
        log('GitHub pool mining is running on the spare account: waiting before the next YouTube request')
        time.sleep(300)
    w = _last[0] + PACE_S - time.time()
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()


YT_CACHE = [None]          # dir for cached search / info JSON (set by build), 7-day reuse
OFFLINE = [False]          # --offline: only cached searches / infos / downloads (no YouTube requests)


def _cached(key, fn):
    if YT_CACHE[0]:
        p = os.path.join(YT_CACHE[0], re.sub(r'[^A-Za-z0-9_-]+', '_', key)[:120] + '.json')
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
            return json.load(open(p, encoding='utf-8'))
    if OFFLINE[0]:
        return None
    v = fn()
    if YT_CACHE[0] and v:
        os.makedirs(YT_CACHE[0], exist_ok=True)
        json.dump(v, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    return v


def search(q, n=20):
    def run():
        pace()
        out = ytdlp(['--flat-playlist', '-J', f'ytsearch{n}:{q}'])
        try:
            return json.loads(out).get('entries') or []
        except ValueError:
            return []
    return _cached(f's_{q}_{n}', run)


def full_info(vid):
    def run():
        pace()
        out = ytdlp(['-J', '--skip-download', f'https://www.youtube.com/watch?v={vid}'])
        try:
            d = json.loads(out)
        except ValueError:
            return None
        keep = ('id', 'title', 'description', 'tags', 'channel', 'uploader', 'uploader_id', 'channel_id', 'view_count',
                'like_count', 'width', 'height', 'duration', 'upload_date', 'license')
        return {k: d.get(k) for k in keep}
    return _cached(f'i_{vid}', run)


def download(info, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    p = os.path.join(dest_dir, info['id'] + '.mp4')
    if os.path.exists(p):
        return p
    if OFFLINE[0]:
        return None
    pace()
    ytdlp(['-f', 'bv*[height<=1080][height>=720]+ba/b[height<=1080][height>=720]', '--merge-output-format', 'mp4',
           '-o', p, f'https://www.youtube.com/watch?v={info["id"]}'], timeout=900)
    return p if os.path.exists(p) else None


def prefilter(e, entry, min_views=None):
    """Flat search entry -> (ok, reason)."""
    t = (e.get('title') or '')
    if L.EXPLICIT_RE.search(t):
        return False, 'explicit'
    if not any(re.search(a, t, re.I) for a in entry['alias']):
        return False, 'title_no_name'
    d = e.get('duration') or 0
    if not 12 <= d <= MAX_SRC_DUR:
        return False, f'dur_{d}'
    if (e.get('view_count') or 0) < (MIN_VIEWS if min_views is None else min_views):
        return False, 'views'
    if re.search(r'\b(vs\.?|versus|full fight|compilation|ranking|tier ?list|top \d+)\b', t, re.I) and entry.get('solo', True):
        return False, 'not_solo_edit'      # a vs-fight edit or another list: not "an edit of X"
    return True, ''


def info_gate(info):
    if not info:
        return False, 'no_info'
    w, h = info.get('width') or 0, info.get('height') or 0
    if h > w:
        return False, 'vertical'            # the layout needs a 16:9 edit (as the references)
    if h < 720:
        return False, f'res_{h}'
    v, lk = info.get('view_count') or 0, info.get('like_count') or 0
    if v and lk and lk / v < MIN_LIKE_RATIO:
        return False, f'like_ratio_{lk / v:.3f}'
    if L.EXPLICIT_RE.search(' '.join([info.get('title') or '', info.get('description') or '', ' '.join(info.get('tags') or [])])):
        return False, 'explicit_meta'
    return True, ''


# ---------------------------------------------------------------- analysis of a downloaded edit

def decode(path, step=0.5, width=448):
    """Frames every `step` s (resized), full-video cut list (hist diff at 10 fps)."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    every = max(1, int(round(fps * step)))
    frames, motion, cuts, prev_h, prev_g = {}, [], [], None, None
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % max(1, int(round(fps / 10))) == 0 or i % every == 0:
            ok, f = cap.retrieve()
            if not ok:
                break
            t = i / fps
            if i % every == 0:
                frames[round(t, 3)] = cv2.resize(f, (width, int(f.shape[0] * width / f.shape[1])))
            s = cv2.resize(f, (96, 54))
            g = cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype(np.float32)
            hh = cv2.normalize(cv2.calcHist([cv2.cvtColor(s, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8],
                                            [0, 180, 0, 256]), None).flatten()
            if prev_h is not None:
                d = 1 - cv2.compareHist(prev_h, hh, cv2.HISTCMP_CORREL)
                if d > 0.45:
                    cuts.append(t)
                motion.append((t, float(np.abs(g - prev_g).mean())))
            prev_h, prev_g = hh, g
        i += 1
    return {'fps': fps, 'dur': n / fps, 'frames': frames, 'motion': motion, 'cuts': cuts}


def content_box(frames):
    """Letterbox-free box (x0, y0, x1, y1) in 0-1 from the mean brightness / variance."""
    st = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in list(frames.values())[::4]]).astype(np.float32)
    m = st.mean(0) + st.std(0)
    rows, cols = np.where(m.mean(1) > 14)[0], np.where(m.mean(0) > 14)[0]
    h, w = m.shape
    if not len(rows) or not len(cols):
        return (0, 0, 1, 1)
    return (cols[0] / w, rows[0] / h, (cols[-1] + 1) / w, (rows[-1] + 1) / h)


_OCR = [None]


def ocr(img):
    if _OCR[0] is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR[0] = RapidOCR()
    h, w = img.shape[:2]
    res, _ = _OCR[0](img)
    out = []
    for box, txt, conf in res or []:
        if conf < 0.6 or len(txt.strip()) < 2:
            continue
        xs = [p[0] / w for p in box]
        ys = [p[1] / h for p in box]
        out.append((txt.strip(), [min(xs), min(ys), max(xs), max(ys)]))
    return out


HANDLE_RE = re.compile(r'@\w|tik ?tok|instagram|\bIG\b|\byt\b|\.com|subscribe|edit(s|z)?\s*by|\bcc\s*[:@]',
                       re.I)


def text_scan(ana, cbox):
    """OCR every 0.5 s: persistent overlays (same box in many frames / several shots), handles,
    subtitle lines (bottom band, sentence-like, changing), per-time text coverage."""
    ts = sorted(ana['frames'])
    cuts = np.array(ana['cuts'] + [1e9])
    per_t, groups, subs, handles = {}, [], [], []
    for t in ts:
        boxes = ocr(ana['frames'][t])
        per_t[t] = boxes
        shot = int(np.searchsorted(cuts, t))
        for txt, b in boxes:
            if HANDLE_RE.search(txt):
                handles.append((t, txt))
            for g in groups:
                if L.iou(g['box'], b) > 0.4:
                    g['n'] += 1
                    g['shots'].add(shot)
                    g['texts'].add(txt.lower())
                    break
            else:
                groups.append({'box': b, 'n': 1, 'shots': {shot}, 'texts': {txt.lower()}})
            # subtitles: bottom part of the picture, centred, wide-ish, sentence-like
            cy0, cy1 = cbox[1], cbox[3]
            rel_y = (b[1] - cy0) / max(1e-3, cy1 - cy0)
            if rel_y > 0.72 and abs((b[0] + b[2]) / 2 - 0.5) < 0.12 and (b[2] - b[0]) > 0.18 and ' ' in txt and len(txt) >= 8:
                subs.append(t)
    n = max(1, len(ts))
    def mark_sized(b):              # a watermark / handle is small; lyric typography is big
        return (b[2] - b[0]) * (b[3] - b[1]) <= 0.03 and (b[3] - b[1]) <= 0.08

    persistent = [g for g in groups if mark_sized(g['box']) and max(len(t) for t in g['texts']) >= 3 and (
                  (g['n'] >= 0.3 * n and len(g['shots']) >= 3)
                  or (len(g['shots']) >= 3 and len(g['texts']) <= 2 and g['n'] >= 4))]
    return {'per_t': per_t, 'persistent': [{'box': [round(v, 3) for v in g['box']], 'n': g['n'],
                                            'shots': len(g['shots']), 'text': sorted(g['texts'])[:3]} for g in persistent],
            'handles': handles[:10], 'subs': subs}


def logo_scan(ana, cbox):
    """Non-text logos: edge pixels present in >= 75 % of shots, logo-sized, inside the picture."""
    cuts = np.array(ana['cuts'] + [1e9])
    by_shot = {}
    for t, f in ana['frames'].items():
        by_shot.setdefault(int(np.searchsorted(cuts, t)), []).append(f)
    if len(by_shot) < 5:
        return []
    acc = None
    for fr in by_shot.values():
        g = cv2.cvtColor(fr[len(fr) // 2], cv2.COLOR_BGR2GRAY)
        e = (cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 40).astype(np.float32)
        acc = e if acc is None else acc + e
    P = acc / len(by_shot)
    var = np.stack([cv2.cvtColor(fr[len(fr) // 2], cv2.COLOR_BGR2GRAY).astype(np.float32)
                    for fr in by_shot.values()]).std(0)
    h, w = P.shape
    mask = (P >= 0.75).astype(np.uint8)
    x0, y0, x1, y1 = cbox
    pad = 0.02
    mask[:, :int((x0 + pad) * w)] = 0
    mask[:, int((x1 - pad) * w):] = 0
    mask[:int((y0 + pad) * h), :] = 0
    mask[int((y1 - pad) * h):, :] = 0
    mask = cv2.dilate(mask, np.ones((5, 9), np.uint8))
    num, _, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, num):
        bx, by, bw, bh, area = st[i]
        if bw >= 0.025 * w and bh >= 0.012 * h and bw * bh <= 0.08 * w * h and area >= 40:
            # a logo stays put while the picture around it changes; a repeated still (the same
            # close-up used in several shots, manga panels) changes neither -> not a logo
            r0, r1 = max(0, by - bh), min(h, by + 2 * bh)
            c0, c1 = max(0, bx - bw // 2), min(w, bx + bw + bw // 2)
            ring = var[r0:r1, c0:c1].copy()
            inner = var[by:by + bh, bx:bx + bw].copy()
            ring[by - r0:by - r0 + bh, bx - c0:bx - c0 + bw] = np.nan
            if not (np.nanmedian(ring) >= 25 and np.median(inner) < 0.6 * np.nanmedian(ring)):
                continue
            out.append([round(bx / w, 3), round(by / h, 3), round((bx + bw) / w, 3), round((by + bh) / h, 3)])
    return out


def audio_curves(path):
    import librosa
    wav = tempfile.mktemp(suffix='.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', path, '-vn', '-ac', '1', '-ar', '22050', wav], check=True)
    y, sr = librosa.load(wav, sr=22050)
    os.remove(wav)
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    on = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    _, beats = librosa.beat.beat_track(onset_envelope=on, sr=sr, hop_length=hop)
    tt = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    return {'t': tt, 'rms': rms, 'onset': on[:len(rms)], 'beats': librosa.frames_to_time(beats, sr=sr, hop_length=hop)}


def pick_windows(ana, au, txt, seg_len, k=6):
    """Candidate windows ranked by drop + energy + motion + cut rate - text cover - subtitles."""
    dur = ana['dur']
    L_ = min(seg_len, MAX_SHARE * dur)
    tt, rms, on = au['t'], au['rms'], au['onset']
    db = 20 * np.log10(rms + 1e-5)
    mt = np.array([m[0] for m in ana['motion']])
    mv = np.array([m[1] for m in ana['motion']])
    cuts = np.array(ana['cuts'])
    txt_t = np.array(sorted(txt['per_t']))
    txt_cov = np.array([sum((b[2] - b[0]) * (b[3] - b[1]) for _, b in txt['per_t'][t]) for t in txt_t]) if len(txt_t) else np.zeros(0)
    subs = np.array(txt['subs'])
    lo, hi = min(1.5, 0.08 * dur), dur - max(2.0, 0.1 * dur)      # skip the editor's intro / end card

    def mean_in(x, y, a, b):
        m = (x >= a) & (x < b)
        return float(y[m].mean()) if m.any() else 0.0

    # drops: beats where the next 3 s are much louder than the previous 3 s
    drops = []
    for b in au['beats']:
        if lo + 0.5 < b < hi - 2:
            jump = mean_in(tt, db, b, b + 3) - mean_in(tt, db, b - 3, b)
            drops.append((jump + 0.02 * mean_in(tt, on, b - 0.1, b + 0.2), b))
    drops.sort(reverse=True)
    starts = set()
    for _, b in drops[:8]:
        for pre in (0.0, 0.5 * L_ / 4, 1.0):            # start at / just before the drop
            starts.add(round(max(lo, b - pre), 2))
    for s in np.arange(lo, max(lo, hi - L_) + 0.01, 1.0):
        starts.add(round(float(s), 2))
    cands = []
    m_all = float(np.percentile(mv, 90)) if len(mv) else 1.0
    for s in sorted(starts):
        e = s + L_
        if e > hi + 0.01:
            continue
        energy = mean_in(tt, db, s, e)
        motion = mean_in(mt, mv, s, e) / max(1e-3, m_all)
        cutrate = float(((cuts >= s) & (cuts < e)).sum()) / L_
        drop = max([j for j, b in drops if s <= b <= s + 0.35 * L_] + [0.0])
        tcov = mean_in(txt_t, txt_cov, s, e) if len(txt_t) else 0.0
        sub = float(((subs >= s) & (subs < e)).sum()) / max(1, L_ * 2) if len(subs) else 0.0
        if any(s - 0.6 <= b <= e + 0.6 for b in txt.get('brand_t', [])):
            continue                                 # the editor's name / tag is on screen here
        # clean entry / exit: a cut near the start and the end reads as a deliberate excerpt
        snap = float((np.abs(cuts - s) < 0.3).any()) + float((np.abs(cuts - e) < 0.3).any()) if len(cuts) else 0.0
        score = (0.35 * np.clip(drop / 8, 0, 1) + 0.2 * np.clip((energy + 30) / 20, 0, 1) + 0.2 * np.clip(motion, 0, 1)
                 + 0.15 * np.clip(cutrate / 1.5, 0, 1) + 0.05 * snap - 1.5 * tcov - 1.0 * sub)
        cands.append({'s': round(s, 2), 'e': round(e, 2), 'score': round(float(score), 3), 'drop_db': round(drop, 1),
                      'energy_db': round(energy, 1), 'motion': round(motion, 2), 'cuts_per_s': round(cutrate, 2),
                      'text_cover': round(tcov, 3), 'subs': round(sub, 2)})
    cands.sort(key=lambda c: -c['score'])
    out = []
    for c in cands:                                  # distinct windows only
        if all(abs(c['s'] - o['s']) >= 0.5 * L_ for o in out):
            out.append(c)
        if len(out) >= k:
            break
    return out


# ---------------------------------------------------------------- character match + safety

class Tagger:
    """WD ViT tagger v3 (SmilingWolf, Apache-2.0, ONNX, ~380 MB): danbooru character tags. A far stronger
    "is this character on screen" check than SigLIP for the characters it knows (Gojo, Yuji, Sukuna, Megumi,
    Frieren, Kurisu, Robin, Yor, C.C., Marin, Makima, Zero Two ...). Characters it does not know (Toji,
    Yuta, most romance leads) fall back to the SigLIP + AniList-art check. Also gives the rating tags."""
    URL = 'https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/main/'

    def __init__(self):
        import csv
        import onnxruntime as ort
        d = os.path.join(os.path.expanduser('~'), '.cache', 'wd_tagger')
        os.makedirs(d, exist_ok=True)
        for f in ('model.onnx', 'selected_tags.csv'):
            if not os.path.exists(os.path.join(d, f)):
                urllib.request.urlretrieve(self.URL + f, os.path.join(d, f))
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(os.path.join(d, 'model.onnx'), so, providers=['CPUExecutionProvider'])
        self.inp = self.sess.get_inputs()[0]
        self.size = self.inp.shape[1] if isinstance(self.inp.shape[1], int) else 448
        rows = list(csv.reader(open(os.path.join(d, 'selected_tags.csv'), encoding='utf-8')))[1:]
        self.names = [r[1] for r in rows]
        self.cat = np.array([int(r[2]) for r in rows])
        self.char_idx = np.where(self.cat == 4)[0]
        self.rating_idx = np.where(self.cat == 9)[0]

    def has(self, tag):
        return tag in self.names

    def __call__(self, img):
        h, w = img.shape[:2]
        m = max(h, w)
        sq = np.full((m, m, 3), 255, np.uint8)
        sq[(m - h) // 2:(m - h) // 2 + h, (m - w) // 2:(m - w) // 2 + w] = img
        x = cv2.resize(sq, (self.size, self.size), interpolation=cv2.INTER_AREA).astype(np.float32)[None]
        return self.sess.run(None, {self.inp.name: x})[0][0]

    def chars(self, probs, thr=0.5):
        return {self.names[i]: float(probs[i]) for i in self.char_idx if probs[i] >= thr}

    def rating(self, probs):
        return {self.names[i]: float(probs[i]) for i in self.rating_idx}


class Judge:
    def __init__(self, cache_dir):
        self.cache = L.Cache(cache_dir, 2000)
        self.sig = L.Siglip(self.cache)
        cf.load_models()
        p = L.model_path('anime_face')
        self.faces = L.AnimeFaces(p) if p else None
        chars = json.load(open(os.path.join(ROOT, 'core', 'quiz', 'assets', 'anime-characters.json'),
                               encoding='utf-8'))['characters']
        self.by_name = {}
        for c in chars:
            self.by_name.setdefault(c['name'].lower(), c)
        self.chars = chars
        self.safe_t = self.sig.text(cj.UNSAFE + cj.SAFE)
        self.refs = {}

    def char(self, name):
        return self.by_name.get(name.lower())

    def ref_set(self, names):
        """Face-crop embeddings of the AniList art of these characters + name prompt embeddings."""
        key = tuple(names)
        if key in self.refs:
            return self.refs[key]
        crops, owners = [], []
        for i, nm in enumerate(names):
            c = self.char(nm)
            if not c:
                continue
            im = L.fetch_image(c['image'], self.cache)
            if im is None:
                continue
            for cr in (L.face_crops(im, self.faces, 1) or [im]):
                crops.append(cr)
                owners.append(i)
        emb = self.sig.images(crops)
        prompts = [f'an anime screenshot of {nm} from {(self.char(nm) or {}).get("anime", "anime")}' for nm in names]
        self.refs[key] = (emb, np.array(owners), self.sig.text(prompts))
        return self.refs[key]

    def tagger(self):
        if not hasattr(self, '_tagger'):
            try:
                self._tagger = Tagger()
            except Exception as e:      # noqa: BLE001 - no tagger -> SigLIP only
                log(f'WD tagger unavailable: {e}')
                self._tagger = None
        return self._tagger

    def match_tags(self, frames, target_tags):
        """Tagger vote: frames where any known character is tagged (>= 0.5); the target wins a frame when
        its tag is the strongest character tag there. -> (conf, details) or None if no target tag is known."""
        tg = self.tagger()
        tags = [t for t in (target_tags or []) if tg and tg.has(t)]
        if not tags:
            return None
        wins, votes, others, explicit = 0, 0, {}, 0
        for f in frames:
            pr = tg(f)
            r = tg.rating(pr)
            if r.get('explicit', 0) >= 0.5 or r.get('questionable', 0) >= 0.6:
                explicit += 1
            ch = tg.chars(pr)
            if not ch:
                continue
            votes += 1
            best = max(ch, key=ch.get)
            if best in tags:
                wins += 1
            else:
                others[best] = others.get(best, 0) + 1
        conf = wins / votes if votes else 0.0
        return conf, {'method': 'wd_tagger', 'votes': votes, 'wins': wins, 'others': others,
                      'frames': len(frames), 'rating_flags': explicit}

    def match(self, frames, target_names, other_names):
        """Share of character frames where a target character beats every other candidate.
        frames: list of BGR images. Returns (confidence 0-1, details)."""
        names = list(target_names) + [n for n in other_names if n not in target_names]
        emb, owners, temb = self.ref_set(names)
        nt = len(target_names)
        wins, votes, other_win = 0, 0, {}
        face_frames = 0
        imgs = []
        for f in frames:
            fc = L.face_crops(f, self.faces, 2) if self.faces else []
            imgs.append((f, fc))
        allimgs = [f for f, _ in imgs]
        fe = self.sig.images(allimgs)
        crop_list = [(i, c) for i, (_, fc) in enumerate(imgs) for c in fc]
        ce = self.sig.images([c for _, c in crop_list]) if crop_list else np.zeros((0, 768), np.float32)
        for i in range(len(imgs)):
            # zero-shot name probability over the whole frame
            lg = self.sig.logits(fe[i:i + 1], temb)[0]
            p = np.exp(lg - lg.max())
            p /= p.sum()
            # face crops vs the art (best crop of the frame)
            face_s = np.zeros(len(names))
            mine = [ce[j] for j, (fi, _) in enumerate(crop_list) if fi == i]
            if mine and len(emb):
                sims = np.stack(mine) @ emb.T                       # crops x refs
                for k in range(len(names)):
                    sel = owners == k
                    if sel.any():
                        face_s[k] = sims[:, sel].max()
                face_frames += 1
            if not mine:
                continue                                            # no face: no vote (scenery, effects)
            fs = (face_s - face_s.mean()) / (face_s.std() + 1e-6)
            score = 0.6 * p / max(1e-6, p.max()) + 0.4 * (fs - fs.min()) / max(1e-6, fs.max() - fs.min())
            best = int(np.argmax(score))
            votes += 1
            if best < nt:
                wins += 1
            else:
                other_win[names[best]] = other_win.get(names[best], 0) + 1
        conf = wins / votes if votes else 0.0
        return conf, {'votes': votes, 'wins': wins, 'face_frames': face_frames, 'others': other_win}

    def safe(self, frames):
        """(ok, reason): NudeNet per frame + SigLIP suggestive check (gore allowed since 2026-10-12)."""
        for f in frames:
            if cf.nude(f):
                return False, 'nudenet'
        e = self.sig.images(frames)
        lg = self.sig.logits(e, self.safe_t)
        nu = len(cj.UNSAFE)
        p_unsafe = 1 / (1 + np.exp(-lg[:, :nu])).max(1)
        if (p_unsafe > 0.5).mean() > 0.15:
            return False, f'siglip_unsafe_{float((p_unsafe > 0.5).mean()):.2f}'
        return True, ''


# ---------------------------------------------------------------- source finding per entry

def find_source(entry, others, get_judge, work, seg_len, used, report, max_dl=4, pre=None):
    """Best source edit + window for one entry, or None. Downloads up to `max_dl` candidates (most
    viewed first), runs the cheap watermark / text gates on all of them (that is the measured
    watermark-free rate), then the models (safety + character match + windows) on the clean ones."""
    if pre is not None:
        clean = pre
        for res, row in clean:
            res = evaluate(res, entry, others, get_judge(), seg_len)
            row.update({k: res.get(k) for k in ('verdict', 'why', 'conf', 'window', 'match')})
            if res['verdict'] == 'ok':
                return res
        return None
    seen, cands = set(), []
    for q in entry['q']:
        for e in (search(q) or []):
            if e.get('id') in seen or e.get('id') in used:
                continue
            seen.add(e.get('id'))
            ok, why = prefilter(e, entry)
            report['searched'] += 1
            if not ok:
                report['reject'][why] = report['reject'].get(why, 0) + 1
                continue
            cands.append(e)
    cands.sort(key=lambda e: -(e.get('view_count') or 0))       # popularity as a proxy for "good edit"
    # rights + priority on the infos first (old edits / inactive editors / permitted first; vetoed and
    # blocklisted editors skipped), then download in that order
    ranked = []
    for e in cands[:max(8, 2 * max_dl)]:
        info = full_info(e['id'])
        if info is None and OFFLINE[0]:
            continue                                  # not fetched yet; offline run
        ok, why = info_gate(info)
        if not ok:
            report['reject'][why] = report['reject'].get(why, 0) + 1
            continue
        rt = rights(info, fetch_extra=not OFFLINE[0])
        rt['entry'] = entry['name']
        save_proof(rt, work)
        if not rt['usable']:
            why = 'blocklisted' if rt['blocked'] else 'editor_forbids_repost'
            report['reject'][why] = report['reject'].get(why, 0) + 1
            continue
        ranked.append((rt['priority'], info, rt))
    ranked.sort(key=lambda x: -x[0])
    clean = []
    for _, info, rt in ranked:
        if len(clean) >= 2 or report['_dl'].get(entry['name'], 0) >= max_dl:
            break
        report['_dl'][entry['name']] = report['_dl'].get(entry['name'], 0) + 1
        path = download(info, os.path.join(work, 'src'))
        if not path and OFFLINE[0]:
            report['_dl'][entry['name']] -= 1
            continue
        if not path:
            report['reject']['download'] = report['reject'].get('download', 0) + 1
            continue
        report['downloaded'] += 1
        res = text_gate(path, info)
        res['rights'] = {k: rt.get(k) for k in ('permitted', 'type', 'age_days', 'channel_idle_days', 'priority',
                                                 'onscreen_credit', 'reason')}
        row = {k: res.get(k) for k in ('id', 'title', 'channel', 'handle', 'views', 'verdict', 'why', 'persistent', 'marks', 'logos',
                                       'handles', 'subs_n')}
        row['entry'] = entry['name']
        row['rights'] = res['rights']
        report['evaluated'].append(row)
        row['brand_n'] = res.get('brand_n')
        if res['verdict'] == 'reject':
            continue
        report['watermark_free'] += 1
        clean.append((res, row))
    if get_judge is None:
        return clean                          # prefetch phase: network + cheap gates only
    for res, row in clean:
        res = evaluate(res, entry, others, get_judge(), seg_len)
        row.update({k: res.get(k) for k in ('verdict', 'why', 'conf', 'window', 'match')})
        if res['verdict'] == 'ok':
            return res
    return None


def mark_scan(path, step=0.5, width=960, ncc_min=0.35, share_min=0.5):
    """Semi-transparent / small watermarks ("SANCHEZ" in a corner, "nsnlxd" across the picture) that
    per-frame OCR misses: average the gradient over the whole edit (moving content washes out, a mark
    that never moves stays crisp), OCR that average, then keep only text whose pattern is really
    present in >= `share_min` of the frames (long lyric / caption lines are there far less often).
    The editor's end card (last 10 %) is skipped: the excerpt never comes from there."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    every = max(1, int(round(fps * step)))
    gacc, macc, small, i = None, None, [], 0
    while True:
        if not cap.grab():
            break
        if i % every == 0 and i < 0.9 * n:
            ok, f = cap.retrieve()
            if not ok:
                break
            f = cv2.resize(f, (width, int(f.shape[0] * width / f.shape[1])))
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            gm = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
            gacc = gm.astype(np.float32) if gacc is None else gacc + gm
            macc = f.astype(np.float32) if macc is None else macc + f
            small.append(gm)
        i += 1
    if not small:
        return []
    k = len(small)
    gmean = gacc / k
    gimg = 255 - np.clip(gmean / max(1e-3, np.percentile(gmean, 99.7)) * 255, 0, 255).astype(np.uint8)
    H, W_ = gimg.shape
    hits = []
    for img in (cv2.cvtColor(gimg, cv2.COLOR_GRAY2BGR), np.clip(macc / k, 0, 255).astype(np.uint8)):
        for txt, b in ocr(img):
            if len(re.sub(r'[^A-Za-z0-9]', '', txt)) < 3:
                continue
            if all(L.iou(b, h['box']) < 0.3 for h in hits):
                hits.append({'text': txt, 'box': b})
    out = []
    for h in hits:
        x0, y0, x1, y1 = [int(round(v)) for v in (h['box'][0] * W_, h['box'][1] * H, h['box'][2] * W_, h['box'][3] * H)]
        x0, y0, x1, y1 = max(0, x0 - 2), max(0, y0 - 2), min(W_, x1 + 2), min(H, y1 + 2)
        if x1 - x0 < 8 or y1 - y0 < 6:
            continue
        tpl = gmean[y0:y1, x0:x1]
        tpl = (tpl - tpl.mean()) / (tpl.std() + 1e-6)
        pres = 0
        for gm in small:
            p_ = gm[y0:y1, x0:x1].astype(np.float32)
            p_ = (p_ - p_.mean()) / (p_.std() + 1e-6)
            if float((p_ * tpl).mean()) >= ncc_min:
                pres += 1
        share = pres / k
        tag_like = len(h['text'].split()) <= 2 and len(h['text']) <= 16       # a name / handle, not a lyric line
        if share >= share_min or (tag_like and share >= 0.25):
            out.append({'text': h['text'], 'box': [round(v, 3) for v in h['box']], 'share': round(share, 2)})
    return out


def text_gate(path, info):
    """Watermark / handle / logo / persistent overlay gate (no models)."""
    res = {'id': info['id'], 'title': info.get('title'), 'channel': info.get('channel') or info.get('uploader'),
           'handle': info.get('uploader_id'), 'path': path, 'views': info.get('view_count'),
           'likes': info.get('like_count'), 'height': info.get('height'), 'dur': info.get('duration'), 'verdict': 'clean'}
    ana = decode(path)
    cbox = content_box(ana['frames'])
    txt = text_scan(ana, cbox)
    logos = logo_scan(ana, cbox)
    # the editor's own name burned in (a name card, a corner tag): OCR text matching the channel
    # name / handle. Those seconds can never be in our excerpt (pick_windows skips them).
    keys = {k for k in re.split(r'[^a-z0-9]+', ((res['channel'] or '') + ' ' + (res['handle'] or '')).lower())
            if len(k) >= 4 and k not in ('edit', 'edits', 'editz', 'amvs', 'anime', 'official', 'channel')}
    brand = []
    for t, boxes in txt['per_t'].items():
        for tx, _ in boxes:
            norm = re.sub(r'[^a-z0-9]', '', tx.lower())
            if any(k in norm for k in keys):
                brand.append(t)
    txt['brand_t'] = sorted(set(brand))
    res['brand_n'] = len(txt['brand_t'])
    res.update(persistent=txt['persistent'], logos=logos, handles=txt['handles'], subs_n=len(txt['subs']), cbox=cbox,
               _ana=ana, _txt=txt)
    marks = mark_scan(path)
    res['marks'] = marks
    if txt['handles']:
        res.update(verdict='reject', why='handle')
    elif marks:
        res.update(verdict='reject', why='watermark_mean')
    elif txt['persistent']:
        res.update(verdict='reject', why='watermark')
    elif logos:
        res.update(verdict='reject', why='logo')
    return res


def evaluate(res, entry, others, judge, seg_len):
    ana, txt, path = res['_ana'], res['_txt'], res['path']
    au = audio_curves(path)
    wins = pick_windows(ana, au, txt, seg_len)
    tgt = entry['chars']
    oth = [c for o in others for c in o['chars']]
    # same-show distractors: the most popular characters of the same anime that are not targets
    show = (judge.char(tgt[0]) or {}).get('anime')
    if show:
        same = sorted([c for c in judge.chars if c.get('anime') == show and c['name'] not in tgt],
                      key=lambda c: -(c.get('favourites') or 0))[:6]
        oth += [c['name'] for c in same if c['name'] not in oth]
    fr_t = sorted(ana['frames'])
    for w in wins:
        if w['subs'] > 0.15:
            w['reject'] = 'subtitles'
            continue
        wait_ram('the next window')
        ts = [t for t in fr_t if w['s'] <= t < w['e']]                     # 2 fps
        frames = [ana['frames'][t] for t in ts]
        if not frames:
            continue
        ok, why = judge.safe(frames)
        if not ok:
            w['reject'] = why
            continue
        mt = judge.match_tags(frames, entry.get('tags'))
        if mt is not None:
            conf, det = mt
            if det['rating_flags'] > 0.1 * len(frames):
                w['reject'] = 'rating'
                continue
        else:
            conf, det = judge.match(frames, tgt, oth)
            det['method'] = 'siglip_art'
        w['conf'], w['match'] = round(conf, 2), det
        top_other = max(det['others'].values()) if det['others'] else 0
        # the entry's character must win the window; reject when another character dominates
        # the entry's character must clearly own the window: >= 60 % of the voting frames and at least twice
        # any other single character (0.5 / near-ties let Yuta-heavy windows pass as Toji in the preview)
        if (det['votes'] >= (6 if det.get('method') == 'wd_tagger' else 4) and conf >= CHAR_MIN
                and det['wins'] >= 2 * top_other):
            res.update(verdict='ok', why='', window=w, conf=round(conf, 2), match=det, windows=wins)
            return res
        w['reject'] = 'character'
    res.update(verdict='reject', why='no_window', windows=wins,
               conf=max([w.get('conf', 0) for w in wins] + [0]), match=[w.get('match') for w in wins][:3])
    return res


# ---------------------------------------------------------------- render

def font(size, weight=800):
    from PIL import ImageFont
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def overlay_png(path, title, names, shown, credit, label=''):
    """The list overlay: 2-line title, 5 numbered slots (filled when shown), small credit."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(im), ImageDraw.Draw(sh)
    ft, fn, fe, fc = font(72, 800), font(52, 800), font(38, 800), font(27, 700)

    def put(xy, s, f, anchor='la', fill=(255, 255, 255, 255)):
        ds.text((xy[0] + 3, xy[1] + 4), s, font=f, anchor=anchor, fill=(0, 0, 0, 200))
        d.text(xy, s, font=f, anchor=anchor, fill=fill)

    put((W // 2, 32), title[0], ft, 'ma')
    put((W // 2, 128), title[1], ft, 'ma')
    y0, dy = 250, 66
    for i in range(len(names)):
        y = y0 + i * dy
        put((182, y), f'{i + 1}.', fn)
        if i in shown:
            put((250, y + 8), names[i], fe)
    if credit:                     # small, in the band's lower-right corner: readable, unobtrusive
        put((W - 22, BAND_Y + 608 - 14), credit, fc, 'rd', fill=(255, 255, 255, 215))
    if label:                      # e.g. PREVIEW - not permission-checked, never upload
        put((W // 2, H - 70), label, fc, 'ma', fill=(255, 60, 60, 255))
    sh = sh.filter(ImageFilter.GaussianBlur(5))
    Image.alpha_composite(sh, im).save(path)


def render_entry(src, win, png, out, threads=4):
    s, dur = win['s'], win['e'] - win['s']
    fg_h = 608
    fc = (f'[0:v]fps={FPS},split=2[a][b];'
          f'[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=22:2,eq=brightness=-0.04[bg];'
          f'[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];'
          f'[bg][fg]overlay=0:{BAND_Y},fade=t=in:st=0:d=0.2,fade=t=out:st={dur - 0.15:.3f}:d=0.15[c];[c][1:v]overlay=0:0,format=yuv420p[v];'
          f'[0:a]aresample=48000,afade=t=in:d=0.04,afade=t=out:st={dur - 0.12:.3f}:d=0.12,'
          f'loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a2]')
    cmd = ['ffmpeg', '-v', 'error', '-y', '-threads', str(threads), '-ss', f'{s:.3f}', '-t', f'{dur:.3f}', '-i', src,
           '-loop', '1', '-t', f'{dur:.3f}', '-i', png, '-filter_complex', fc, '-map', '[v]', '-map', '[a2]',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-threads', str(threads), '-r', str(FPS),
           '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', f'{dur:.3f}', out]
    subprocess.run(cmd, check=True)


def concat(parts, out):
    lst = out + '.txt'
    with open(lst, 'w', encoding='utf-8') as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                    '-movflags', '+faststart', out], check=True)
    os.remove(lst)


LINK_RE = re.compile(r'https?://|www\.|\.(com|net|org|io|tv|gg|ly|me)(/|\s|$)', re.I)


def describe(title, entries):
    """Plain-text description (owner 2026-10-24): one line per entry, the original editor's channel
    handle as PLAIN TEXT (no URL; rule no-external-links), countdown order as in the video."""
    lines = [f'{" ".join(title).title()} (our ranking)', '']
    for e in sorted([e for e in entries if e.get('source')], key=lambda e: -e['rank']):
        who = e.get('handle') or e.get('channel') or 'unknown editor'
        if not str(who).startswith('@'):
            who = e.get('channel') or who
        lines.append(f'#{e["rank"]} {e["name"].title()} — edit by {who}')
    lines += ['', 'All edits belong to their original editors; clips and songs to their rights holders.']
    text = '\n'.join(lines)
    if LINK_RE.search(text):
        raise ValueError('description contains a link')
    return text


def plan_lengths(rng, n):
    first = [rng.uniform(*r) for r in (SEG_3 if n == 3 else SEG_2)]
    return first + [max(12.0, TOTAL - sum(first))]


def build(topic, out, work, n_show=None, seed=None, onscreen_credit=True, label=''):
    T = TOPICS[topic]
    rng = random.Random(seed)
    entries = T['entries'][:5]
    n_show = n_show or rng.choice(REVEAL)
    lengths = plan_lengths(rng, n_show)
    names = [e['name'] for e in entries]
    idle()
    YT_CACHE[0] = os.path.join(work, 'cache', 'yt')
    jh = {}

    def get_judge():
        if 'j' not in jh:
            wait_ram('the SigLIP / analysis models')
            jh['j'] = Judge(os.path.join(work, 'cache'))
        return jh['j']
    report = {'topic': topic, 'searched': 0, 'downloaded': 0, 'watermark_free': 0, 'reject': {}, 'evaluated': [],
              'entries': [], '_dl': {}}
    picks, used = {}, set()
    # countdown: the lowest rank plays first, #1 last
    order = list(range(len(entries)))[::-1]      # #5 first; at most n_show revealed, never #1
    cand_order = order[:n_show + 1]               # one spare rank (#2) if an entry finds no source
    pre = {}
    for i in cand_order:                        # phase 1: search, download, watermark gate (no models, low RAM)
        e = entries[i]
        log(f'#{i + 1} {e["name"]}: searching')
        pre[i] = find_source(e, None, None, work, 0, used, report)
        log(f'#{i + 1} {e["name"]}: {len(pre[i])} clean candidates')
    for i in cand_order:                   # phase 2: models (waits for free RAM)
        if len(picks) >= n_show:
            break
        e = entries[i]
        seg_len = lengths[len(picks)]
        others = [o for o in entries if o is not e]
        r = find_source(e, others, get_judge, work, seg_len, used, report, pre=pre[i])
        if not r:
            log(f'#{i + 1} {e["name"]}: NO SOURCE passed the gates')
            report['entries'].append({'rank': i + 1, 'name': e['name'], 'source': None})
            continue
        used.add(r['id'])
        picks[i] = r
        log(f'#{i + 1} {e["name"]}: {r["id"]} by {r["channel"]} window {r["window"]["s"]}-{r["window"]["e"]} conf {r["conf"]}')
        report['entries'].append({'rank': i + 1, 'name': e['name'], 'source': r['id'], 'channel': r['channel'],
                                  'handle': r.get('handle'), 'title': r['title'], 'views': r['views'], 'window': r['window'],
                                  'conf': r['conf'], 'match': r['match'], 'source_dur': r['dur'],
                                  'rights': r.get('rights')})
        json.dump(report, open(os.path.splitext(out)[0] + '.report.json', 'w', encoding='utf-8'), indent=1,
                  ensure_ascii=False, default=str)
    parts, shown = [], set()
    for i in cand_order:
        if i not in picks:
            continue
        r = picks[i]
        shown.add(i)
        png = os.path.join(work, f'ov_{i}.png')
        # on-screen credit: off by default as in the references (owner: credit in the description)
        overlay_png(png, T['title'], names, shown, f'edit: {r.get("handle") or r["channel"]}' if onscreen_credit else '',
                    label)
        part = os.path.join(work, f'part_{i}.mp4')
        wait_ram('rendering')
        render_entry(r['path'], r['window'], png, part)
        parts.append(part)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    concat(parts, out)
    report['description'] = describe(T['title'], report['entries'])
    json.dump(report, open(os.path.splitext(out)[0] + '.report.json', 'w', encoding='utf-8'), indent=1,
              ensure_ascii=False, default=str)
    log('wrote', out)
    return report


# ---------------------------------------------------------------- permission (owner 2026-10-24: permitted edits ONLY)
# "use only edits whose editors gave permission ... even if they allow permission the quality must still be up there".
# A source enters the pool only with a STORED proof (exact text / licence + date). Types:
#   cc_by      YouTube licence "Creative Commons Attribution licence (reuse allowed)" (yt-dlp `license`)
#   explicit   the editor's own description / pinned comment clearly allows reposting / using the edit
#   allowlist  the editor granted permission directly (core/edit/refs/ranking-editor-allowlist.json, owner-kept)
# NOT permission (recorded with the reason, never used):
#   "no copyright intended" / fair-use disclaimers (a disclaimer, not a grant);
#   paid project files / shops (Payhip, Boosty, Gumroad...): buying a project lets YOU make an edit, it does not
#   let you repost the editor's render;
#   conditional wording ("do not reupload unless transformative", "credit me if you use clips"): only clips /
#   only transformative use, a ranking re-post is not clearly covered;
#   any "do not reupload / repost / steal" anywhere in the text vetoes the explicit grant.
ALLOWLIST = os.path.join(HERE, 'refs', 'ranking-editor-allowlist.json')
PERMIT_MIN_VIEWS = 5_000          # permitted edits are rare: a lower bar here, quality is still gated by res /
                                  # watermark / character / safety / by-eye review
CC_SEARCH_SP = 'EgIwAQ%253D%253D'      # YouTube search filter "Creative Commons"
GRANT_RE = re.compile(
    r"(free to (use|repost|re-?upload|post)|feel free to (use|repost|re-?upload|post|share)|"
    r"you (can|may|are allowed to) (use|repost|re-?upload|post) (this|my|these|it)|"
    r"(repost|re-?upload)s? (are |is )?(allowed|welcome|ok|fine)|"
    r"(use|repost|re-?upload) (it |this |my edits? )?with credit|"
    r"allowed to (use|repost|re-?upload)|copyright[- ]free edit|free edits? to use|"
    r"credit me if you (use|repost|re-?upload|post) (this|it|my edit))", re.I)
VETO_RE = re.compile(r"(do ?n[o']?t|please don.?t|no) (re-?upload|repost|steal|use)|"
                     r"without (my )?permission|re-?uploads? (are )?not allowed|unless (it.?s )?transformative", re.I)
DISCLAIMER_RE = re.compile(r"no copyright intended|no copyright infringement|fair use|section 107|"
                           r"all rights (to|belong)|don.?t own", re.I)
SHOP_RE = re.compile(r"payhip|boosty|gumroad|ko-?fi|patreon|project files?|\bpf\b|\.aep|editing shop", re.I)


def search_cc(q, n=20):
    """YouTube search restricted to Creative Commons uploads."""
    def run():
        pace()
        url = f'https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}&sp={CC_SEARCH_SP}'
        out = ytdlp(['--flat-playlist', '-J', '--playlist-end', str(n), url])
        try:
            return json.loads(out).get('entries') or []
        except ValueError:
            return []
    return _cached(f'cc_{q}_{n}', run)


def find_permitted(topic, ranks=(5, 4, 3, 2), per_entry_info=8, pinned_top=4):
    """Permission survey + proofs for one topic: CC-licensed edits and edits whose editor publicly allows
    reuse. Writes every proof (permitted or not, with the reason) to $EDIT_POOL_DIR/ranking/_permissions/.
    Returns {rank: [permitted proofs]} and a summary."""
    T = TOPICS[topic]
    out, summary = {}, {'checked': 0, 'permitted': 0, 'types': {}, 'not_permitted_reasons': {}}
    for rank in ranks:
        e = T['entries'][rank - 1]
        name = e['name'].lower()
        cands, seen = [], set()
        lists = [search_cc(f'{name} edit'), search_cc(f'{name} amv')]
        for q in (f'{name} edit free to use', f'{name} edit no copyright free to use'):
            lists.append(search(q) or [])
        for lst in lists:
            for c in lst or []:
                if c.get('id') in seen:
                    continue
                seen.add(c.get('id'))
                ok, why = prefilter(c, e, min_views=PERMIT_MIN_VIEWS)
                if ok:
                    cands.append(c)
        cands.sort(key=lambda c: -(c.get('view_count') or 0))
        got = []
        for k, c in enumerate(cands[:per_entry_info]):
            info = full_info(c['id'])
            if not info:
                continue
            ok, why = info_gate(info)
            if not ok:
                continue
            proof = permission(info, fetch_pinned=k < pinned_top)
            proof['entry'] = e['name']
            proof['topic'] = topic
            proof['title'] = info.get('title')
            save_proof(proof, None)
            summary['checked'] += 1
            if proof['permitted']:
                got.append(proof)
                summary['permitted'] += 1
                summary['types'][proof['type']] = summary['types'].get(proof['type'], 0) + 1
            else:
                r = proof['reason'][:50]
                summary['not_permitted_reasons'][r] = summary['not_permitted_reasons'].get(r, 0) + 1
        out[rank] = got
        log(f'{topic} #{rank} {e["name"]}: {len(got)} permitted of {min(len(cands), per_entry_info)} checked')
    return out, summary


# ---------------------------------------------------------------- source rules (owner 2026-10-24, final)
# "instead lets do we credit them and prioritise old ones like 1 year old" (the owner heard the risk and decided):
# * any edit that passes the quality bar may be used WITH credit (@handle in the description + on-screen);
# * edits >= 1 year old first, then editors whose channel looks inactive (no upload for ~6 months);
# * HARD SKIP editors who say "don't repost / reupload / use my edits" (description, pinned comment, channel
#   about) and editors on the takedown blocklist; the checked text is stored as a snapshot per source;
# * CC / free-to-use edits rank above the rest (permission() above, now a bonus).
BLOCKLIST = os.path.join(HERE, 'refs', 'ranking-editor-blocklist.json')
OLD_DAYS, INACTIVE_DAYS = 365, 182


def load_blocklist():
    try:
        return {k.lower() for k in json.load(open(BLOCKLIST, encoding='utf-8')).get('editors', {})}
    except Exception:
        return set()


def channel_meta(info):
    """Channel 'about' text + the newest upload's (approximate) date: one small request, cached."""
    cid = info.get('channel_id')
    if not cid:
        return {}

    def run():
        pace()
        out = ytdlp(['--flat-playlist', '-J', '--playlist-end', '2', '--extractor-args', 'youtubetab:approximate_date',
                     f'https://www.youtube.com/channel/{cid}/videos'])
        try:
            d = json.loads(out)
        except ValueError:
            return None
        ts = [e.get('timestamp') for e in (d.get('entries') or []) if e.get('timestamp')]
        return {'about': d.get('description') or '', 'latest_ts': max(ts) if ts else None,
                'followers': d.get('channel_follower_count')}
    return _cached(f'ch_{cid}', run) or {}


def rights(info, fetch_extra=True):
    """Snapshot of what the editor says + the priority signals. -> dict (stored per source)."""
    proof = permission(info, fetch_pinned=fetch_extra)          # CC / explicit grant / allow-list / veto
    ch = channel_meta(info) if fetch_extra else {}
    about = ch.get('about') or ''
    veto = VETO_RE.search(about)
    if veto and not proof.get('permitted'):
        proof['reason'] = f'channel about forbids it: "{veto.group(0)}"'
        proof['evidence'] = {'where': 'channel_about', 'text': _snip(about, veto)}
    handle = (info.get('uploader_id') or '').lower()
    now = time.time()
    up = info.get('upload_date')
    age_days = (now - time.mktime(time.strptime(up, '%Y%m%d'))) / 86400 if up else None
    last = ch.get('latest_ts')
    idle_days = (now - last) / 86400 if last else None
    forbids = bool(re.search(r'forbids', proof.get('reason') or ''))
    proof.update({
        'blocked': handle in load_blocklist(),
        'forbids_repost': forbids,
        'age_days': round(age_days) if age_days is not None else None,
        'channel_idle_days': round(idle_days) if idle_days is not None else None,
        'snapshot': {'description': (info.get('description') or '')[:4000], 'about': about[:2000],
                     'pinned': (_cached(f'c_{info.get("id")}', lambda: None) or {}).get('pinned')},
    })
    proof['usable'] = not proof['blocked'] and not forbids
    # priority: permission bonus > old (>= 1 year) > inactive channel > popularity
    proof['priority'] = (3.0 * bool(proof.get('permitted')) + 2.0 * bool(age_days and age_days >= OLD_DAYS)
                         + 1.0 * bool(idle_days and idle_days >= INACTIVE_DAYS)
                         + 0.1 * np.log10(1 + (info.get('view_count') or 0)))
    return proof


def survey(topic, ranks=(5, 4, 3, 2), per_entry=12):
    """Pool-size survey under the final source rules (no downloads): per rank, search candidates that pass
    the title / length / views gates, then the info gates (landscape, >= 720p, like ratio) and the rights
    check (veto / blocklist, CC / free-to-use bonus, age, channel idle). Proof snapshots are stored."""
    T = TOPICS[topic]
    out = {'topic': topic, 'ranks': {}}
    for rank in ranks:
        e = T['entries'][rank - 1]
        seen, cands = set(), []
        lists = [search(q) or [] for q in e['q']]
        lists.append(search_cc(f'{e["name"].lower()} edit') or [])
        for lst in lists:
            for c in lst:
                if c.get('id') in seen:
                    continue
                seen.add(c.get('id'))
                if prefilter(c, e)[0]:
                    cands.append(c)
        cands.sort(key=lambda c: -(c.get('view_count') or 0))
        row = {'name': e['name'], 'search_pass': len(cands), 'info_pass': 0, 'usable': 0, 'old_1y': 0,
               'idle_6m': 0, 'permitted': 0, 'vetoed': 0, 'blocked': 0, 'ids': []}
        for c in cands[:per_entry]:
            info = full_info(c['id'])
            if not info or not info_gate(info)[0]:
                continue
            row['info_pass'] += 1
            rt = rights(info, fetch_extra=not OFFLINE[0])
            rt['entry'], rt['topic'] = e['name'], topic
            save_proof(rt, None)
            row['vetoed'] += bool(rt['forbids_repost'])
            row['blocked'] += bool(rt['blocked'])
            if rt['usable']:
                row['usable'] += 1
                row['old_1y'] += bool(rt['age_days'] and rt['age_days'] >= OLD_DAYS)
                row['idle_6m'] += bool(rt['channel_idle_days'] and rt['channel_idle_days'] >= INACTIVE_DAYS)
                row['permitted'] += bool(rt.get('permitted'))
                row['ids'].append((info['id'], info.get('uploader_id'), rt['age_days'], round(rt['priority'], 2)))
        out['ranks'][rank] = row
        log(f'{topic} #{rank} {e["name"]}: search {row["search_pass"]}, info {row["info_pass"]}, usable '
            f'{row["usable"]} (>=1y {row["old_1y"]}, idle {row["idle_6m"]}, permitted {row["permitted"]}, vetoed {row["vetoed"]})')
    return out


def load_allowlist():
    try:
        return json.load(open(ALLOWLIST, encoding='utf-8')).get('editors', {})
    except Exception:
        return {}


def pinned_comment(vid):
    """The uploader's pinned comment text (one small request), cached like the infos."""
    def run():
        pace()
        out = ytdlp(['-J', '--skip-download', '--write-comments', '--extractor-args',
                     'youtube:max_comments=10,all,0,0;comment_sort=top', f'https://www.youtube.com/watch?v={vid}'])
        try:
            cs = json.loads(out).get('comments') or []
        except ValueError:
            return {'pinned': None}
        pin = [c for c in cs if c.get('is_pinned') and c.get('author_is_uploader')]
        return {'pinned': pin[0].get('text') if pin else ''}
    r = _cached(f'c_{vid}', run)
    return (r or {}).get('pinned')


def _snip(text, m, pad=140):
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return text[a:b].replace('\n', ' | ')


def permission(info, fetch_pinned=True):
    """-> proof dict {permitted, type, evidence, reason, checked_at, ...}. Never raises."""
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    base = {'source_id': info.get('id'), 'editor_handle': info.get('uploader_id'), 'channel': info.get('channel'),
            'license': info.get('license'), 'checked_at': now, 'permitted': False, 'type': None,
            'evidence': None, 'reason': ''}
    allow = load_allowlist()
    h = (info.get('uploader_id') or '').lower()
    for k, v in allow.items():
        if k.lower() == h:
            return dict(base, permitted=True, type='allowlist', evidence=v,
                        reason='editor granted permission directly (owner allow-list)',
                        onscreen_credit=bool(v.get('credit_onscreen')))
    lic = info.get('license') or ''
    if 'creative commons' in lic.lower():
        return dict(base, permitted=True, type='cc_by', evidence=lic,
                    reason='YouTube CC BY licence: reuse allowed with attribution', onscreen_credit=False)
    texts = [('description', info.get('description') or '')]
    if fetch_pinned:
        pc = pinned_comment(info['id'])
        if pc:
            texts.append(('pinned_comment', pc))
    for where, t in texts:
        veto = VETO_RE.search(t)
        g = GRANT_RE.search(t)
        if g and not veto:
            return dict(base, permitted=True, type='explicit', evidence={'where': where, 'text': _snip(t, g)},
                        reason=f'the editor\'s {where} allows reuse: "{g.group(0)}"',
                        onscreen_credit=bool(re.search(r'credit', _snip(t, g), re.I)))
        if veto:
            return dict(base, reason=f'{where} forbids it: "{veto.group(0)}"',
                        evidence={'where': where, 'text': _snip(t, veto)})
    for where, t in texts:
        if DISCLAIMER_RE.search(t):
            m = DISCLAIMER_RE.search(t)
            return dict(base, reason='only a copyright disclaimer (not a grant)', evidence={'where': where, 'text': _snip(t, m)})
        if SHOP_RE.search(t):
            m = SHOP_RE.search(t)
            return dict(base, reason='sells project files / edits (a paid project is not a repost licence)',
                        evidence={'where': where, 'text': _snip(t, m)})
    return dict(base, reason='no permission statement found')


def save_proof(proof, work):
    d = os.path.join(pool_dir(False), '_permissions')
    os.makedirs(d, exist_ok=True)
    json.dump(proof, open(os.path.join(d, f'{proof["source_id"]}.json'), 'w', encoding='utf-8'), indent=1,
              ensure_ascii=False, default=str)


# ---------------------------------------------------------------- pool / backlog (production design, 2026-10-24)
# The daily video never downloads. Mining (the nightly GitHub rotation or the local idle miner, one job at
# a time, spare account) STAGES candidates. Claude reviews them by eye (pull-staged -> sheets -> import).
# The daily job only ASSEMBLES from the reviewed pool. NOT wired into any rotation yet.
#
# $EDIT_POOL_DIR/ranking/<topic>/manifest.json      (staged: $EDIT_POOL_DIR/_staged/ranking/<topic>/)
# {"version": 1, "topic": "jjk_strongest", "title": [...], "built": ISO,
#  "entries": [{"rank": 5, "name": "TOJI", "chars": [...],
#     "items": [{"id": "r_<source_id>_<start_ms>", "file": "clips/r_<...>.mp4",      # the cut excerpt (<= 35 s)
#                "source_id": "...", "source_title": "...", "source_channel": "...", "editor_handle": "@...",
#                "source_views": 123, "source_dur": 61.0, "start": 12.3, "end": 47.3,
#                "char_conf": 0.83, "char_match": {...}, "safety": "pass", "watermark": "none",
#                "status": "staged|reviewed|rejected", "added": "YYYY-MM-DD"}]}]}
# Used ledger: memory/edit-pool-used.json items["ranking/<topic>/clip/<id>"] = {"used_at", "video"}, plus
# "ranking_recent": the source ids of the last uploads, newest last -> REUSE_GAP.
POOL_MAX_SEG = 35.0
REUSE_GAP = 8               # an edit is not reused within the next 8 ranking uploads (as the quiz pools)
NEEDED_RANKS = (5, 4, 3, 2)  # revealed (#5 #4 #3) + one spare; #1 never shows


def pool_dir(staged=False):
    root = os.path.abspath(os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))
    return os.path.join(root, '_staged', 'ranking') if staged else os.path.join(root, 'ranking')


def _manifest(d, topic):
    p = os.path.join(d, topic, 'manifest.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    T = TOPICS[topic]
    return {'version': 1, 'topic': topic, 'title': T['title'], 'built': None,
            'entries': [{'rank': i + 1, 'name': e['name'], 'chars': e['chars'], 'items': []}
                        for i, e in enumerate(T['entries'])]}


def _save(d, topic, man):
    os.makedirs(os.path.join(d, topic), exist_ok=True)
    man['built'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    json.dump(man, open(os.path.join(d, topic, 'manifest.json'), 'w', encoding='utf-8'), indent=1, ensure_ascii=False)


def cut_clip(src, s, e, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-threads', '4', '-ss', f'{s:.3f}', '-t', f'{e - s:.3f}', '-i', src,
                    '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-threads', '4', '-c:a', 'aac', '-b:a', '256k',
                    out], check=True)


def stage(topic, work, per_entry=2):
    """Mining step. Search / download / gates / models for the needed ranks, then cut the best <= 35 s
    window of each passing edit into the staged pool. Review + import is a separate step."""
    T = TOPICS[topic]
    idle()
    YT_CACHE[0] = os.path.join(work, 'cache', 'yt')
    entries = T['entries']
    jh = {}

    def get_judge():
        if 'j' not in jh:
            wait_ram('the SigLIP / analysis models')
            jh['j'] = Judge(os.path.join(work, 'cache'))
        return jh['j']
    report = {'topic': topic, 'searched': 0, 'downloaded': 0, 'watermark_free': 0, 'reject': {}, 'evaluated': [],
              'entries': [], '_dl': {}}
    sd = pool_dir(staged=True)
    man = _manifest(sd, topic)
    for rank in NEEDED_RANKS:
        i = rank - 1
        e = entries[i]
        others = [o for o in entries if o is not e]
        pre = find_source(e, None, None, work, 0, set(), report, max_dl=2 + 2 * per_entry)
        ent = man['entries'][i]
        for res, row in pre:
            res = evaluate(res, e, others, get_judge(), POOL_MAX_SEG)
            row.update({k: res.get(k) for k in ('verdict', 'why', 'conf', 'window', 'match')})
            if res['verdict'] != 'ok':
                continue
            w = res['window']
            iid = f'r_{res["id"]}_{int(w["s"] * 1000)}'
            if any(it['id'] == iid for it in ent['items']):
                continue
            cut_clip(res['path'], w['s'], w['e'], os.path.join(sd, topic, 'clips', iid + '.mp4'))
            ent['items'].append({'id': iid, 'file': f'clips/{iid}.mp4', 'source_id': res['id'],
                                 'source_title': res['title'], 'source_channel': res['channel'],
                                 'editor_handle': res.get('handle'), 'source_views': res.get('views'),
                                 'source_dur': res.get('dur'), 'start': w['s'], 'end': w['e'],
                                 'char_conf': res['conf'], 'char_match': res['match'],
                                 'safety': 'pass', 'watermark': 'none', 'rights': res.get('rights'),
                                 'status': 'staged',
                                 'added': time.strftime('%Y-%m-%d')})
            if sum(1 for it in ent['items'] if it['status'] == 'staged') >= per_entry:
                break
        _save(sd, topic, man)
    report.pop('_dl', None)
    json.dump(report, open(os.path.join(sd, topic, 'stage-report.json'), 'w', encoding='utf-8'), indent=1,
              ensure_ascii=False, default=str)
    return man


def import_reviewed(topic, ids, reject=()):
    """Move reviewed staged items (by id) into the pool. `reject` ids are marked rejected in staging."""
    import shutil
    sd, pd = pool_dir(True), pool_dir(False)
    sm, pm = _manifest(sd, topic), _manifest(pd, topic)
    n = 0
    for se, pe in zip(sm['entries'], pm['entries']):
        for it in se['items']:
            if it['id'] in reject:
                it['status'] = 'rejected'
            elif it['id'] in ids and it['status'] == 'staged':
                os.makedirs(os.path.join(pd, topic, 'clips'), exist_ok=True)
                shutil.copy2(os.path.join(sd, topic, it['file']), os.path.join(pd, topic, it['file']))
                it['status'] = 'reviewed'
                pe['items'] = [x for x in pe['items'] if x['id'] != it['id']] + [dict(it)]
                n += 1
    _save(sd, topic, sm)
    _save(pd, topic, pm)
    return n


def _ledger():
    p = os.path.join(os.environ.get('MEMORY_DIR') or os.path.join(ROOT, 'memory'), 'edit-pool-used.json')
    try:
        return p, json.load(open(p, encoding='utf-8'))
    except Exception:
        return p, {'version': 1, 'items': {}, 'moments': {}}


def usable_items(entry, ledger):
    recent = ledger.get('ranking_recent', [])[-REUSE_GAP:]
    blocked = load_blocklist()
    out = [it for it in entry['items'] if it['status'] == 'reviewed' and it['source_id'] not in recent
           and (it.get('editor_handle') or '').lower() not in blocked]
    return sorted(out, key=lambda it: (-((it.get('rights') or {}).get('priority') or 0), -(it.get('char_conf') or 0)))


def eligible(topic, n_show=3):
    """A topic is eligible only when every revealed rank has a usable reviewed edit."""
    _, led = _ledger()
    man = _manifest(pool_dir(False), topic)
    ranks = sorted(range(1, len(man['entries']) + 1), reverse=True)[:n_show]
    return all(usable_items(man['entries'][r - 1], led) for r in ranks)


def assemble(topic, out, work, n_show=None, seed=None, onscreen_credit=True, mark_used=False):
    """The daily step: assemble only from the reviewed pool (no network)."""
    T = TOPICS[topic]
    rng = random.Random(seed)
    n_show = n_show or rng.choice(REVEAL)
    if not eligible(topic, n_show):
        raise SystemExit(f'topic {topic} not eligible: a revealed rank has no usable reviewed edit')
    lengths = plan_lengths(rng, n_show)
    lp, led = _ledger()
    pd = pool_dir(False)
    man = _manifest(pd, topic)
    names = [e['name'] for e in man['entries']]
    parts, shown, entries = [], set(), []
    idle()
    os.makedirs(work, exist_ok=True)
    for k, rank in enumerate(sorted(range(1, len(names) + 1), reverse=True)[:n_show]):
        it = usable_items(man['entries'][rank - 1], led)[0]
        shown.add(rank - 1)
        clip = os.path.join(pd, topic, it['file'])
        win = {'s': 0.0, 'e': min(lengths[k], it['end'] - it['start'])}
        png = os.path.join(work, f'ov_pool_{rank}.png')
        credit = f'edit: {it.get("editor_handle") or it["source_channel"]}' if onscreen_credit else ''
        overlay_png(png, T['title'], names, shown, credit)
        part = os.path.join(work, f'part_pool_{rank}.mp4')
        wait_ram('rendering')
        render_entry(clip, win, png, part)
        parts.append(part)
        entries.append({'rank': rank, 'name': names[rank - 1], 'source': it['source_id'],
                        'handle': it.get('editor_handle'), 'channel': it['source_channel'], 'item': it['id']})
    concat(parts, out)
    desc = describe(T['title'], entries)
    if mark_used:
        for e in entries:
            led.setdefault('items', {})[f'ranking/{topic}/clip/{e["item"]}'] = {
                'used_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'video': os.path.basename(out)}
            led.setdefault('ranking_recent', []).append(e['source'])
        json.dump(led, open(lp, 'w', encoding='utf-8'), indent=1)
    return {'out': out, 'entries': entries, 'description': desc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--topic', default='jjk_strongest', choices=sorted(TOPICS))
    ap.add_argument('--out', help='output mp4 (build / assemble)')
    ap.add_argument('--work', default=os.path.join(ROOT, 'output', 'ranking-proto'))
    ap.add_argument('--show', type=int, choices=(2, 3), help='entries revealed (default: random as the references)')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--offline', action='store_true', help='cached searches / downloads only (e.g. while GitHub mining runs)')
    ap.add_argument('--no-onscreen-credit', action='store_true', help='drop the small "edit: @handle" line (default on)')
    ap.add_argument('--describe', help='print the description from an existing .report.json and exit')
    ap.add_argument('--mode', default='build', choices=('build', 'stage', 'import', 'eligible', 'assemble', 'permits', 'survey'),
                    help='build = one-off prototype (search + render); stage / import / eligible / assemble = pool path')
    ap.add_argument('--label', default='', help='burned-in label, e.g. "PREVIEW - not permission-checked, never upload"')
    ap.add_argument('--ids', default='', help='import: comma-separated staged item ids that passed review')
    ap.add_argument('--reject', default='', help='import: comma-separated staged item ids that failed review')
    a = ap.parse_args()
    if a.mode == 'survey':
        YT_CACHE[0] = os.path.join(a.work, 'cache', 'yt')
        OFFLINE[0] = a.offline
        idle()
        print(json.dumps(survey(a.topic), ensure_ascii=False, indent=1, default=str))
        return
    if a.mode == 'permits':
        YT_CACHE[0] = os.path.join(a.work, 'cache', 'yt')
        OFFLINE[0] = a.offline
        idle()
        res, summ = find_permitted(a.topic)
        print(json.dumps({'topic': a.topic, 'summary': summ,
                          'permitted': {r: [(p['source_id'], p['editor_handle'], p['type']) for p in v] for r, v in res.items()}},
                         ensure_ascii=False, indent=1))
        return
    if a.mode == 'stage':
        OFFLINE[0] = a.offline
        stage(a.topic, a.work)
        return
    if a.mode == 'import':
        print(import_reviewed(a.topic, set(filter(None, a.ids.split(','))), set(filter(None, a.reject.split(',')))))
        return
    if a.mode == 'eligible':
        print(eligible(a.topic, a.show or 3))
        return
    if a.mode == 'assemble':
        r = assemble(a.topic, a.out, a.work, a.show, a.seed, not a.no_onscreen_credit)
        print(r['description'])
        return
    if a.describe:
        rep = json.load(open(a.describe, encoding='utf-8'))
        print(describe(TOPICS[rep['topic']]['title'], rep['entries']))
        return
    os.makedirs(a.work, exist_ok=True)
    OFFLINE[0] = a.offline
    build(a.topic, a.out, a.work, a.show, a.seed, not a.no_onscreen_credit, a.label)


if __name__ == '__main__':
    main()
