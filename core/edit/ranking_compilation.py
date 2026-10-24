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
            {'name': 'SUKUNA', 'chars': ['Sukuna'], 'anime': 'JUJUTSU KAISEN',
             'q': ['sukuna edit 4k', 'ryomen sukuna amv edit'], 'alias': [r'sukuna']},
            {'name': 'GOJO', 'chars': ['Satoru Gojo'], 'anime': 'JUJUTSU KAISEN',
             'q': ['gojo edit 4k', 'gojo satoru amv edit'], 'alias': [r'gojo']},
            {'name': 'YUTA', 'chars': ['Yuta Okkotsu'], 'anime': 'JUJUTSU KAISEN',
             'q': ['yuta okkotsu edit 4k', 'yuta amv edit'], 'alias': [r'yuta', r'okkotsu']},
            {'name': 'YUJI', 'chars': ['Yuji Itadori'], 'anime': 'JUJUTSU KAISEN',
             'q': ['yuji itadori edit 4k', 'itadori amv edit'], 'alias': [r'yuji', r'itadori']},
            {'name': 'TOJI', 'chars': ['Toji Fushiguro'], 'anime': 'JUJUTSU KAISEN',
             'q': ['toji fushiguro edit 4k', 'toji amv edit'], 'alias': [r'toji']},
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
            {'name': 'MY DRESS-UP DARLING', 'chars': ['Marin Kitagawa', 'Wakana Gojo'], 'anime': 'Sono Bisque Doll',
             'q': ['marin kitagawa edit 4k'], 'alias': [r'marin', r'dress.?up darling', r'bisque']},
            {'name': 'THE FRAGRANT FLOWER', 'chars': ['Kaoruko Waguri', 'Rintarou Tsumugi'], 'anime': 'Kaoru Hana',
             'q': ['fragrant flower blooms with dignity edit 4k'], 'alias': [r'fragrant flower', r'kaoru hana', r'waguri']},
        ]},
}

# ---------------------------------------------------------------- helpers


MIN_FREE_GB = 5.2


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
                'like_count', 'width', 'height', 'duration', 'upload_date')
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


def prefilter(e, entry):
    """Flat search entry -> (ok, reason)."""
    t = (e.get('title') or '')
    if L.EXPLICIT_RE.search(t):
        return False, 'explicit'
    if not any(re.search(a, t, re.I) for a in entry['alias']):
        return False, 'title_no_name'
    d = e.get('duration') or 0
    if not 12 <= d <= MAX_SRC_DUR:
        return False, f'dur_{d}'
    if (e.get('view_count') or 0) < MIN_VIEWS:
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
    clean = []
    for e in cands:
        if len(clean) >= 2 or report['_dl'].get(entry['name'], 0) >= max_dl:
            break
        info = full_info(e['id'])
        if info is None and OFFLINE[0]:
            continue                                  # not fetched yet; offline run
        ok, why = info_gate(info)
        if not ok:
            report['reject'][why] = report['reject'].get(why, 0) + 1
            continue
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
        row = {k: res.get(k) for k in ('id', 'title', 'channel', 'handle', 'views', 'verdict', 'why', 'persistent', 'marks', 'logos',
                                       'handles', 'subs_n')}
        row['entry'] = entry['name']
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
        ts = [t for t in fr_t if w['s'] <= t < w['e']]                     # 2 fps
        frames = [ana['frames'][t] for t in ts]
        if not frames:
            continue
        ok, why = judge.safe(frames)
        if not ok:
            w['reject'] = why
            continue
        conf, det = judge.match(frames, tgt, oth)
        w['conf'], w['match'] = round(conf, 2), det
        top_other = max(det['others'].values()) if det['others'] else 0
        # the entry's character must win the window; reject when another character dominates
        if det['votes'] >= 4 and conf >= 0.5 and top_other < det['wins']:
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


def overlay_png(path, title, names, shown, credit):
    """The list overlay: 2-line title, 5 numbered slots (filled when shown), small credit."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(im), ImageDraw.Draw(sh)
    ft, fn, fe, fc = font(72, 800), font(52, 800), font(38, 800), font(30, 600)

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
    if credit:
        put((W // 2, BAND_Y + 608 + 22), credit, fc, 'ma', fill=(255, 255, 255, 230))
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


def build(topic, out, work, n_show=None, seed=None, onscreen_credit=False):
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
                                  'conf': r['conf'], 'match': r['match'], 'source_dur': r['dur']})
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
        overlay_png(png, T['title'], names, shown, f'edit: {r.get("handle") or r["channel"]}' if onscreen_credit else '')
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--topic', default='jjk_strongest', choices=sorted(TOPICS))
    ap.add_argument('--out', required=True)
    ap.add_argument('--work', default=os.path.join(ROOT, 'output', 'ranking-proto'))
    ap.add_argument('--show', type=int, choices=(2, 3), help='entries revealed (default: random as the references)')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--offline', action='store_true', help='cached searches / downloads only (e.g. while GitHub mining runs)')
    ap.add_argument('--onscreen-credit', action='store_true', help='small "edit: @handle" under the clip (refs: none)')
    ap.add_argument('--describe', help='print the description from an existing .report.json and exit')
    a = ap.parse_args()
    if a.describe:
        rep = json.load(open(a.describe, encoding='utf-8'))
        print(describe(TOPICS[rep['topic']]['title'], rep['entries']))
        return
    os.makedirs(a.work, exist_ok=True)
    OFFLINE[0] = a.offline
    build(a.topic, a.out, a.work, a.show, a.seed, a.onscreen_credit)


if __name__ == '__main__':
    main()
