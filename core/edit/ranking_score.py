#!/usr/bin/env python3
"""Eventful-edit + song scoring and phrase-aligned windows for the ranking compilation (2026-10-25).

Owner feedback on PREVIEW2: Toji (@noctryx7) and Gojo (@XenozEdit) were GOOD, Yuji (@Dr1zzy.aep.) was "not
eventful enough"; Toji felt a bit long. So every candidate window is scored on what made Toji / Gojo work:
  * cut density (cuts / s) and how many cuts land on a beat / onset (beat-synced editing),
  * impact / motion energy (frame difference), flashes (luma spikes), shakes (fast camera-shift reversals),
    zooms (radial optical-flow bursts),
  * the song: a drop inside the window (dB jump on a beat), loudness, punch (onset strength), bass, pulse
    clarity, and popularity (the same song used by several editors in our search cache + the edit's views).
Windows start and end on the edit song's phrase boundaries (bar grid from the beat track, phrase ends = bar
boundaries with high structural novelty, an energy dip or an editor cut) and are 18-24 s long, so no entry
drags or stops mid-phrase.

Calibration (refs + PREVIEW2 sources): see CAL below and core/edit/refs/ranking-compilation-study.md.

    python core/edit/ranking_score.py <edit.mp4> [...]       # print the top windows per file
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import cv2
import numpy as np

SEG_MIN, SEG_MAX, SEG_TARGET = 18.0, 24.0, 20.0

# normalisation ranges (value -> 0..1 between lo and hi), set from the calibration set:
# refs v1tnk8GwWD8 / XSHZhNzRovw / XQRx0mqFxbc / BnsZETrlnFA segments, Toji lynJABr10N4 + Gojo thMXb5r792s
# (owner: good) and Yuji IryZxcTZgzE (owner: not eventful).
CAL = {
    'cuts_per_s': (0.5, 1.8), 'beat_sync': (0.3, 0.7), 'motion': (20.0, 55.0), 'flashes_per_s': (0.15, 0.7),
    'shakes_per_s': (0.1, 0.8), 'zooms_per_s': (0.6, 1.6), 'drop_db': (3.0, 12.0), 'energy_db': (-24.0, -9.0),
    'punch': (0.9, 1.6), 'bass': (0.05, 0.3), 'pulse': (0.1, 0.45),
}
W_EVENT = {'cuts_per_s': 0.25, 'beat_sync': 0.15, 'motion': 0.2, 'flashes_per_s': 0.15, 'shakes_per_s': 0.15,
           'zooms_per_s': 0.1}
W_SONG = {'drop_db': 0.25, 'energy_db': 0.25, 'punch': 0.1, 'bass': 0.1, 'pulse': 0.3}


def _n(k, v):
    lo, hi = CAL[k]
    return float(np.clip((v - lo) / (hi - lo), 0, 1))


# ------------------------------------------------------------------ curves (cached per source)

def video_curves(path, size=(160, 90)):
    """Per frame (every frame, up to 30 fps): time, luma, mean abs diff, hist cut flag, global shift (phase
    correlation) and radial flow (zoom) on a small grey copy."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(fps / 30)))
    t, luma, diff, cut, sx, sy, zoom = [], [], [], [], [], [], []
    prev, prev_h, i = None, None, 0
    win = cv2.createHanningWindow(size, cv2.CV_32F) if hasattr(cv2, 'createHanningWindow') else None
    yy, xx = np.mgrid[0:45, 0:80].astype(np.float32)
    rx, ry = xx - 39.5, yy - 22
    rn = np.sqrt(rx ** 2 + ry ** 2) + 1e-3
    rx, ry = rx / rn, ry / rn
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step:
            i += 1
            continue
        ok, f = cap.retrieve()
        if not ok:
            break
        s = cv2.resize(f, size, interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hh = cv2.normalize(cv2.calcHist([cv2.cvtColor(s, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8],
                                        [0, 180, 0, 256]), None).flatten()
        t.append(i / fps)
        luma.append(float(g.mean()))
        if prev is None:
            diff.append(0.0); cut.append(0); sx.append(0.0); sy.append(0.0); zoom.append(0.0)
        else:
            diff.append(float(np.abs(g - prev).mean()))
            c = 1 - cv2.compareHist(prev_h, hh, cv2.HISTCMP_CORREL) > 0.45
            cut.append(int(c))
            if c:
                sx.append(0.0); sy.append(0.0); zoom.append(0.0)
            else:
                (dx, dy), _ = cv2.phaseCorrelate(prev, g, win)
                sx.append(float(dx)); sy.append(float(dy))
                a = cv2.resize(prev, (80, 45)); b = cv2.resize(g, (80, 45))
                fl = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 2, 9, 2, 5, 1.1, 0)
                zoom.append(float((fl[..., 0] * rx + fl[..., 1] * ry).mean()))
        prev, prev_h = g, hh
        i += 1
    cap.release()
    return {k: np.array(v, np.float32) for k, v in
            dict(t=t, luma=luma, diff=diff, cut=cut, sx=sx, sy=sy, zoom=zoom).items()} | {'fps': fps / step}


def audio_feats(path):
    import librosa
    wav = tempfile.mktemp(suffix='.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', path, '-vn', '-ac', '1', '-ar', '22050', wav], check=True)
    y, sr = librosa.load(wav, sr=22050)
    os.remove(wav)
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    on = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)[:len(rms)]
    tempo, beats = librosa.beat.beat_track(onset_envelope=on, sr=sr, hop_length=hop)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))[:, :len(rms)]
    fr = librosa.fft_frequencies(sr=sr, n_fft=2048)
    bass = S[fr < 150].sum(0) / (S.sum(0) + 1e-6)
    mf = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)[:, :len(rms)]
    ch = librosa.feature.chroma_stft(S=S ** 2, sr=sr, hop_length=hop)[:, :len(rms)]
    onsets = librosa.onset.onset_detect(onset_envelope=on, sr=sr, hop_length=hop, units='time')
    tt = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    return {'t': tt, 'rms': rms, 'db': 20 * np.log10(rms + 1e-5), 'onset': on, 'bass': bass,
            'beats': librosa.frames_to_time(beats, sr=sr, hop_length=hop), 'onsets': onsets,
            'tempo': float(np.atleast_1d(tempo)[0]), 'feat': np.vstack([mf / 50.0, ch]), 'dur': len(y) / sr}


def curves(path, cache_dir=None):
    """Video + audio curves, cached as .npz next to the cache dir (keyed by file name + size)."""
    key = f'{os.path.splitext(os.path.basename(path))[0]}_{os.path.getsize(path)}'
    p = os.path.join(cache_dir, f'ev_{key}.npz') if cache_dir else None
    if p and os.path.exists(p):
        z = np.load(p, allow_pickle=True)
        v, a = z['v'].item(), z['a'].item()
    else:
        v, a = video_curves(path), audio_feats(path)
        if p:
            os.makedirs(cache_dir, exist_ok=True)
            np.savez_compressed(p, v=np.array(v, dtype=object), a=np.array(a, dtype=object))
    a['db'] = np.maximum(a['db'], -40.0)      # floor: an editor's silent gap is not a 60 dB "drop"
    return v, a


# ------------------------------------------------------------------ phrases

def phrase_bounds(a, cuts_t=()):
    """Bar grid (4 beats; downbeat phase = the strongest onset phase) and a phrase-end strength per bar
    boundary: structural novelty (MFCC + chroma checkerboard over 2 bars) + an energy dip right before it
    + an editor cut on it + a bonus every 4 bars from the strongest bar. -> list of (t, strength 0..1)."""
    b = a['beats']
    if len(b) < 8:
        return [(float(x), 0.5) for x in np.arange(0, a['dur'], 2.0)]
    on_at = np.interp(b, a['t'], a['onset'])
    ph = int(np.argmax([on_at[k::4].sum() for k in range(4)]))
    bars = b[ph::4]
    # bar-level features
    fi = np.searchsorted(a['t'], bars)
    F = np.stack([a['feat'][:, fi[k]:fi[k + 1]].mean(1) if fi[k + 1] > fi[k] else a['feat'][:, fi[k]]
                  for k in range(len(bars) - 1)] + [a['feat'][:, fi[-1]:].mean(1)])
    F = (F - F.mean(0)) / (F.std(0) + 1e-6)
    nov = np.zeros(len(bars))
    for k in range(2, len(bars) - 2):
        pre, post = F[k - 2:k].mean(0), F[k:k + 2].mean(0)
        nov[k] = np.linalg.norm(pre - post)
    nov = nov / (nov.max() + 1e-6)
    db = a['db']
    out = []
    cuts_t = np.asarray(cuts_t, np.float32)
    strong = int(np.argmax(nov)) if len(nov) else 0
    for k, t in enumerate(bars):
        dip_pre = db[(a['t'] >= t - 0.4) & (a['t'] < t)]
        around = db[(a['t'] >= t - 2.0) & (a['t'] < t + 2.0)]
        dip = float(np.clip((np.median(around) - dip_pre.min()) / 12, 0, 1)) if len(dip_pre) and len(around) else 0
        cut = float((np.abs(cuts_t - t) < 0.12).any()) if len(cuts_t) else 0.0
        grid = 1.0 if (k - strong) % 4 == 0 else 0.0
        out.append((float(t), round(float(0.45 * nov[k] + 0.2 * dip + 0.15 * cut + 0.2 * grid), 3)))
    return out


# ------------------------------------------------------------------ window scoring

def window_metrics(v, a, s, e):
    m = (v['t'] >= s) & (v['t'] < e)
    L = max(1e-3, e - s)
    tv = v['t'][m]
    cuts = tv[v['cut'][m] > 0]
    ons = np.concatenate([a['beats'], a['onsets']])
    sync = float(np.mean([np.abs(ons - c).min() < 0.07 for c in cuts])) if len(cuts) and len(ons) else 0.0
    luma = v['luma'][m]
    # flashes: luma jumps >= 28 / 255 above the previous 0.2 s median and the frame is bright
    k = max(1, int(round(v['fps'] * 0.2)))
    fl = 0
    last = -9
    for i in range(k, len(luma)):
        if luma[i] - np.median(luma[i - k:i]) >= 28 and luma[i] >= 90 and i - last > k:
            fl += 1
            last = i
    # shakes: camera shift > 1.8 px (of 160) with a direction reversal within 3 frames, outside cuts
    sh = 0
    mag = np.hypot(v['sx'][m], v['sy'][m])
    sxm, sym = v['sx'][m], v['sy'][m]
    last = -9
    for i in range(1, len(mag) - 3):
        if mag[i] > 1.8 and i - last > 3:
            j = i + 1 + int(np.argmax(mag[i + 1:i + 4]))
            if mag[j] > 1.8 and (sxm[i] * sxm[j] + sym[i] * sym[j]) < 0:
                sh += 1
                last = j
    # zooms: radial flow bursts (punch-ins / outs), |mean radial flow| > 0.35 px for >= 2 frames
    z = np.abs(v['zoom'][m]) > 0.35
    zo = int(np.sum(z[1:] & ~z[:-1])) if len(z) > 1 else 0
    am = (a['t'] >= s) & (a['t'] < e)
    db = a['db']
    drops = []
    for b in a['beats']:
        if s - 0.3 <= b <= s + 0.6 * L:
            drops.append(np.mean(db[(a['t'] >= b) & (a['t'] < b + 3)]) - np.mean(db[(a['t'] >= b - 3) & (a['t'] < b)]))
    ac = a['onset'][am]
    if len(ac) > 64:
        x = ac - ac.mean()
        r = np.correlate(x, x, 'full')[len(x) - 1:]
        r = r / (r[0] + 1e-6)
        lo, hi = int(0.25 * 22050 / 512), int(1.0 * 22050 / 512)
        pulse = float(r[lo:hi].max()) if hi < len(r) else 0.0
    else:
        pulse = 0.0
    return {'cuts_per_s': len(cuts) / L, 'beat_sync': sync, 'motion': float(v['diff'][m].mean()) if m.any() else 0.0,
            'flashes_per_s': fl / L, 'shakes_per_s': sh / L, 'zooms_per_s': zo / L,
            'drop_db': float(max(drops + [0.0])), 'energy_db': float(np.mean(db[am])) if am.any() else -60.0,
            'punch': float(np.mean(ac) / (np.median(a['onset']) + 1e-6)) if am.any() else 0.0,
            'bass': float(np.mean(a['bass'][am])) if am.any() else 0.0, 'pulse': pulse}


def scores(mx):
    ev = sum(w * _n(k, mx[k]) for k, w in W_EVENT.items())
    so = sum(w * _n(k, mx[k]) for k, w in W_SONG.items())
    return round(10 * ev, 2), round(10 * so, 2)


def phrase_windows(v, a, lo, hi, seg=(SEG_MIN, SEG_MAX), target=SEG_TARGET, k=8, skip=()):
    """Candidate windows between phrase boundaries, 18-24 s, inside [lo, hi] (the editor's intro / end card
    stay out), not overlapping `skip` times (editor name on screen). Ranked by
    0.5 eventful + 0.3 song + 0.1 phrase-end strength + 0.1 start-on-drop - length deviation."""
    cuts_t = v['t'][v['cut'] > 0]
    pb = [x for x in phrase_bounds(a, cuts_t) if lo - 0.01 <= x[0] <= hi + 0.01]
    cands = []
    for i, (s, ss) in enumerate(pb):
        for e, es in pb[i + 1:]:
            L = e - s
            if L < seg[0]:
                continue
            if L > seg[1]:
                break
            if any(s - 0.6 <= b <= e + 0.6 for b in skip):
                continue
            mx = window_metrics(v, a, s, e)
            ev, so = scores(mx)
            dev = abs(L - target) / (seg[1] - seg[0])
            tot = 0.5 * ev + 0.3 * so + 1.0 * es + 1.0 * min(1.0, mx['drop_db'] / 8) - 1.2 * dev
            cands.append({'s': round(s, 2), 'e': round(e, 2), 'len': round(L, 2), 'eventful': ev, 'song': so,
                          'phrase_end': es, 'phrase_start': ss, 'total': round(tot, 2),
                          **{kk: round(vv, 3) for kk, vv in mx.items()}})
    cands.sort(key=lambda c: -c['total'])
    out = []
    for c in cands:
        # distinct regions, plus up to 2 length variants of the same region (>= 1.5 s apart) so the builder
        # can balance the entry lengths on phrase ends
        same = [o for o in out if min(c['e'], o['e']) - max(c['s'], o['s']) >= 0.5 * c['len']]
        if not same or (len(same) < 3 and all(abs(c['len'] - o['len']) >= 1.5 for o in same)):
            out.append(c)
        if len(out) >= k:
            break
    return out


# ------------------------------------------------------------------ song popularity (metadata)

BAD_SONG = re.compile(r'copyright|fair use|rights|owner|belong|disclaim|https?://|youtu|subscribe|section 107|'
                      r'instagram|tiktok|discord|payhip|boosty|ib|edit audio', re.I)
HEAD_RE = re.compile(r'^\W*(music|song|audio|track|soundtrack)\W*$', re.I)
INLINE_RE = re.compile(r'(?:music|song|audio|track)\s*[:\-–—]+\s*(.{3,80})', re.I)


def song_name(info):
    """Best-effort song name: the line after a 'MUSIC' header, an inline 'Song: X', else the title
    ('Gojo Satoru - Starboy [Edit/AMV]' -> 'Starboy'). Disclaimer / link lines are skipped."""
    lines = [re.sub(r"[^\w\s\-–.,'&()]", ' ', x).strip() for x in (info.get('description') or '').splitlines()]
    lines = [x for x in lines if x and not re.fullmatch(r'[-_=—\s]+', x)]
    for k, x in enumerate(lines):
        if HEAD_RE.match(x):
            for y in lines[k + 1:k + 3]:
                if not BAD_SONG.search(y) and len(re.sub(r'\W', '', y)) >= 3:
                    return y[:80]
        m = INLINE_RE.search(x)
        if m and not BAD_SONG.search(m.group(1)):
            return m.group(1).strip()
    t = info.get('title') or ''
    m = re.search(r'[-–|]\s*([^\[\(「|\-–]{3,40})\s*[\[\(「]', t)
    return m.group(1).strip() if m else ''


def song_key(name):
    n = re.sub(r'\(.*?\)|\[.*?\]|slowed|reverb|remix|sped ?up|ultra|edit audio.*', ' ', name.lower())
    n = n.split(' - ')[-1] if ' - ' in n else n
    return re.sub(r'[^a-z0-9]+', ' ', n).strip()


def song_popularity(info, all_infos):
    """0..1: how many OTHER editors in the search cache use the same song (a trending edit song) + the
    edit's own views (log)."""
    k = song_key(song_name(info))
    if re.search(r'(anime|jujutsu|kaisen|jjk|4k|amv|edit|itadori|yuji|sukuna|gojo|toji|yuta|forger|family|'
                 r'frieren|robin|kurisu|geass|horimiya|marin|music)', k):
        k = ''                                       # an anime / character name, not a song
    same = 0
    if len(k) >= 4:
        eds = {i.get('uploader_id') for i in all_infos if i and i.get('id') != info.get('id')
               and song_key(song_name(i)) == k}
        same = len(eds - {info.get('uploader_id')})
    views = float(np.clip((np.log10(1 + (info.get('view_count') or 0)) - 4.5) / 2.5, 0, 1))
    return round(0.5 * min(1.0, same / 3) + 0.5 * views, 3), k


def main():
    for p in sys.argv[1:]:
        v, a = curves(p)
        dur = a['dur']
        ws = phrase_windows(v, a, min(1.5, 0.08 * dur), dur - max(2.0, 0.1 * dur))
        print(os.path.basename(p), json.dumps(ws[:3], indent=None))


if __name__ == '__main__':
    main()
