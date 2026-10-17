#!/usr/bin/env python3
"""
Independent judge for SOUND edits (sound_hit_sync): compares a candidate render with a reference
sound edit (default FB15TNFAHVU, Shape of You) and writes evidence + a 0-10 scorecard.

    python core/edit/judge_sound.py CANDIDATE REFERENCE OUT_DIR [--plan plan.json] [--label sound_6]
           [--cand-song S --cand-song-offset X --cand-bpm B] [--ref-song S --ref-song-offset X --ref-bpm B]
           [--per-sec 24] [--seed 1]

REFERENCE may be a file or a YouTube URL (downloaded with python -m yt_dlp into OUT_DIR/ref/).
If the reference sits in edit-refs/sound/, its aligned song (analysis/<id>_song_aligned.wav) is used.
With --plan, the candidate's song / offset / bpm come from the plan (music, music_start, sound_edit.bpm)
and the plan's hits are drawn on the sheets (P = planned hit, p = planned picture-only hit).

Outputs in OUT_DIR:
  sheets/sec_XX.jpg      per second: 20-30 random frames of ours (left of each pair) next to the reference
                         frame at the same BEAT position (beat index from the downbeat, looped over the
                         reference's full bars). Red frame = an audible clip hit within half a frame
                         (24 fps-equivalent) of that frame, yellow tick = cut.
  hits_cand.jpg / hits_ref.jpg   every strong hit: frames -2..+2 (24 fps steps) with the motion box
                         (green) and the 9:16 centre zone (blue), plus the measured sound class and level.
  timeline.png           music level, clip-layer level, hits (audible / clearly above), voice, cuts.
  cand_cliplayer.wav / ref_cliplayer.wav   the clip-sound layer (edit minus aligned song) to listen to.
  report.json / report.md   all metrics, both videos, the scorecard with evidence timestamps.

Method (all measured the same way on both videos, so the reference calibrates the bar):
  audio  - the song is aligned (waveform xcorr) and removed by complex least squares per band per 0.25 s
           (sound_timing.split_song). Clip events = onsets of that residual. A hit is AUDIBLE when its 20 ms
           peak is >= -6 dB re the music RMS (+-0.3 s), CLEARLY ABOVE when >= +3 dB re the music RMS and
           >= 0 dB re the song at the same instant. Types are a heuristic from centroid / flatness / decay /
           low share / sub-transients / pitch (pyin). Onset lock = distance to the nearest song onset.
           Loudness from ffmpeg ebur128.
  picture- streamed decode at 640 px height: cuts (frame difference + histogram), per-shot luma / saturation /
           Laplacian sharpness, motion map around each hit (cause = motion peak within -2..+3 frames at 24 fps
           steps, its centroid / area / prominence), near-duplicate shots, persistent edges (overlays / text).
Light by design: IDLE priority, 2 threads, frames are streamed.
"""
import argparse
import io
import json
import os
import subprocess
import sys

for _k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_k, '2')

import numpy as np  # noqa: E402

for _a, _t in (('float', float), ('complex', complex), ('int', int)):  # librosa 0.9 on numpy >= 1.24
    if _a not in np.__dict__:
        setattr(np, _a, _t)

try:
    import psutil  # noqa: E402
    _p = psutil.Process()
    _p.nice(psutil.IDLE_PRIORITY_CLASS if os.name == 'nt' else 19)
except Exception:
    pass

import librosa  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scipy.ndimage import uniform_filter, uniform_filter1d  # noqa: E402
from scipy.signal import fftconvolve  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'refs'))
from sound_timing import SR, HOP, CUTS as REF_CUTS, grid_fit, downbeat_phase, split_song  # noqa: E402

THREADS = '2'
# cut -> hit pre-roll band of criterion 1: the refs' 60-240 ms; the owner's 2026-10-13 / 10-15 rule (a build-up of ~0.4-0.8 s before
# each hit, "short build-up, hit on the beat, quick cut") is scored with JUDGE_PRE_HI_MS=800 (sound_11 on)
PRE_HI = float(os.environ.get('JUDGE_PRE_HI_MS', '240'))
# owner 2026-10-16 (hard rule): no frozen clips / holds, every shot keeps moving until the cut. A STILL RUN = consecutive frames whose
# mean grey difference (90 px frames) from the run's first frame stays under FREEZE_THR; anime drawings are held 2-4 frames@24
# (FB15 itself has 0.125-0.167 s still runs), so a FROZEN run is one longer than JUDGE_FREEZE_MAX_S (0.2 s = more than a 4-frame hold)
FREEZE_THR = float(os.environ.get('JUDGE_FREEZE_THR', '0.5'))
FREEZE_MAX = float(os.environ.get('JUDGE_FREEZE_MAX_S', '0.2'))
OWNER_TYPES = {'gun', 'reload/click', 'crack/glass', 'blade/metal'}
HALF_TYPES = {'thud/punch'}


# ----------------------------------------------------------------------------------------------- io
def ffprobe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate:format=duration', '-of', 'json', path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    st = d['streams'][0]
    a, b = st['r_frame_rate'].split('/')
    return int(st['width']), int(st['height']), float(a) / float(b), float(d['format']['duration'])


def load_audio(path, sr=SR):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-threads', THREADS, '-i', path, '-vn', '-ac', '1', '-ar', str(sr),
                        '-f', 'f32le', '-'], capture_output=True)
    return np.frombuffer(r.stdout, np.float32).copy()


def loudness(path):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-threads', THREADS, '-i', path, '-vn', '-af',
                        'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True)
    out = {'lufs_i': None, 'true_peak_dbtp': None, 'lra': None}
    txt = r.stderr[r.stderr.rfind('Summary'):]
    for line in txt.splitlines():
        s = line.strip()
        try:
            if s.startswith('I:'):
                out['lufs_i'] = float(s.split()[1])
            elif s.startswith('Peak:'):
                out['true_peak_dbtp'] = float(s.split()[1])
            elif s.startswith('LRA:'):
                out['lra'] = float(s.split()[1])
        except ValueError:
            pass
    return out


def fetch_reference(url, out_dir):
    d = os.path.join(out_dir, 'ref')
    os.makedirs(d, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'yt_dlp', '-f', 'mp4', '-o', os.path.join(d, '%(id)s.%(ext)s'), url],
                   check=True)
    return [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.mp4')][0]


# -------------------------------------------------------------------------------------- audio layer
def db(x):
    return 20 * np.log10(np.maximum(x, 1e-7))


def rms_env(y, win_s, hop=HOP):
    w = max(1, int(win_s * SR / hop))
    fr = librosa.util.frame(np.pad(y, (0, hop)), frame_length=hop, hop_length=hop)
    p = (fr ** 2).mean(axis=0)
    return np.sqrt(uniform_filter1d(p, w))


def align_song(edit, song, hint=None):
    """Offset (s) of the song at edit t=0: coarse onset-envelope xcorr (if no hint), then waveform xcorr."""
    if hint is None:
        h = 512
        ee = librosa.onset.onset_strength(y=edit, sr=SR, hop_length=h)
        se = librosa.onset.onset_strength(y=song, sr=SR, hop_length=h)
        ee = (ee - ee.mean()) / (ee.std() + 1e-9)
        se = (se - se.mean()) / (se.std() + 1e-9)
        c = fftconvolve(se, ee[::-1], mode='valid')
        hint = float(np.argmax(c)) * h / SR
    n = min(len(edit), int(15 * SR))
    e = edit[:n]
    pad = int(0.35 * SR)
    a = max(0, int(hint * SR) - pad)
    s = song[a:a + n + 2 * pad]
    if len(s) < n + 1:
        return hint
    num = fftconvolve(s, e[::-1], mode='valid')
    en = np.sqrt(np.convolve(s ** 2, np.ones(n), mode='valid')) + 1e-9
    k = int(np.argmax(num / en))
    return (a + k) / SR


def song_segment(song, off, n):
    i = int(round(off * SR))
    seg = np.zeros(n, np.float32)
    src = song[max(0, i):max(0, i) + n]
    seg[max(0, -i):max(0, -i) + len(src)] = src[:n - max(0, -i)]
    return seg


def beat_grid(seg, bpm_hint):
    if not bpm_hint:
        bpm_hint = float(librosa.beat.tempo(y=seg, sr=SR)[0])
    P, ph = grid_fit(seg, bpm_hint)
    dbp = downbeat_phase(seg, P, ph)
    db0 = ph + dbp * P
    while db0 - 4 * P >= -0.05:
        db0 -= 4 * P
    return dict(period=float(P), bpm=float(60 / P), phase=float(ph), downbeat0=float(db0))


def classify(f):
    if f['voiced']:
        return 'voice'
    if f['attack_db'] > -8 and f['flat'] > 0.25:
        return 'whoosh'
    if f['n_sub'] >= 2 and f['centroid'] >= 1800 and f['decay12_ms'] <= 140:
        return 'reload/click'
    if f['decay12_ms'] <= 60 and f['centroid'] >= 2500:
        return 'reload/click'
    if f['low_db'] >= -12 and f['flat'] >= 0.2 and f['decay12_ms'] >= 70:
        return 'gun'
    if f['flat'] < 0.12 and f['centroid'] >= 1500 and f['decay12_ms'] >= 120:
        return 'blade/metal'
    if f['centroid'] >= 2800 and f['flat'] >= 0.25:
        return 'crack/glass'
    if f['centroid'] >= 1800 and f['flat'] < 0.25:
        return 'blade/metal'
    if f['centroid'] < 1300:
        return 'thud/punch'
    return 'other'


def analyse_audio(path, song_path, song_off, bpm_hint, aligned=False):
    y = load_audio(path)
    out = {'song_path': song_path}
    if song_path:
        song = load_audio(song_path)
        if aligned:
            off = align_song(y, np.concatenate([np.zeros(int(0.35 * SR), np.float32), song]), 0.35) - 0.35
        else:
            off = align_song(y, song, song_off)
        seg = song_segment(song, off, len(y))
        res, songl, gains, removed = split_song(y, seg)
        out.update(song_offset_s=round(float(off), 4), song_removed_db=round(float(removed), 2))
        # lock per 2 s block: fraction of the edit energy explained by the song
        blk = int(2 * SR)
        locks = []
        for i in range(0, len(y) - blk // 2, blk):
            a, b = y[i:i + blk], seg[i:i + blk]
            locks.append(float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)))
        out['lock_corr_per_2s'] = [round(v, 2) for v in locks]
    else:
        seg = None
        res, songl = y.copy(), np.zeros_like(y)
        out.update(song_offset_s=None, song_removed_db=None, warning='no song: clip-layer metrics = whole mix')
    n = min(len(y), len(res))
    y, res, songl = y[:n], res[:n], songl[:n]
    grid = beat_grid(seg[:n] if seg is not None else y, bpm_hint)
    out['grid'] = grid

    t_env = np.arange(len(rms_env(res, 0.02))) * HOP / SR
    r20 = rms_env(res, 0.02)
    s20 = rms_env(songl, 0.02)
    s600 = rms_env(songl, 0.6)
    m = min(len(r20), len(s20), len(s600))
    r20, s20, s600, t_env = r20[:m], s20[:m], s600[:m], t_env[:m]
    r20d, s20d, s600d = db(r20), db(s20), db(s600)

    # song onsets (strong half) on the song layer
    senv = librosa.onset.onset_strength(y=songl, sr=SR, hop_length=HOP)
    sfr = librosa.onset.onset_detect(onset_envelope=senv, sr=SR, hop_length=HOP, delta=0.08,
                                     wait=int(0.06 * SR / HOP))
    s_str = senv[sfr]
    s_on = sfr * HOP / SR
    strong_song = s_on[s_str >= np.median(s_str)] if len(s_str) else s_on

    # clip-layer onsets
    renv = librosa.onset.onset_strength(y=res, sr=SR, hop_length=HOP)
    rfr = librosa.onset.onset_detect(onset_envelope=renv, sr=SR, hop_length=HOP, delta=0.07,
                                     wait=int(0.06 * SR / HOP), backtrack=False)
    r_on = rfr * HOP / SR

    # pitch (voice) on the clip layer
    y11 = librosa.resample(res, orig_sr=SR, target_sr=11025)
    f0, vflag, vprob = librosa.pyin(y11, fmin=80, fmax=600, sr=11025, frame_length=1024, hop_length=256)
    vt = np.arange(len(vflag)) * 256 / 11025
    act = np.interp(vt, t_env, r20d - s20d) > -6
    lvl = np.interp(vt, t_env, r20d) > -50
    active = act & lvl
    voiced_active = active & (vprob > 0.5)
    out['voice_share_of_clip_time'] = round(float(voiced_active.sum() / max(1, active.sum())), 3)

    S = np.abs(librosa.stft(res, n_fft=1024, hop_length=HOP))
    frq = librosa.fft_frequencies(sr=SR, n_fft=1024)
    hits = []
    for t in r_on:
        i = int(t * SR / HOP)
        j0, j1 = max(0, i - 1), min(m, i + int(0.06 * SR / HOP) + 1)
        if j1 <= j0:
            continue
        k = j0 + int(np.argmax(r20[j0:j1]))
        pk = r20d[k]
        mus = s600d[k]
        inst = pk - s20d[k]
        rel = pk - mus
        kpre = max(0, i - int(0.03 * SR / HOP))
        attack = r20d[kpre] - pk
        rise = pk - r20d[max(0, i - int(0.06 * SR / HOP))]
        kk = k
        while kk < m - 1 and r20d[kk] > pk - 12 and (kk - k) * HOP / SR < 0.6:
            kk += 1
        decay = (kk - k) * HOP / SR * 1000
        c0, c1 = i, min(S.shape[1], i + int(0.08 * SR / HOP) + 1)
        spec = S[:, c0:c1].mean(axis=1) ** 2 + 1e-12
        tot = spec.sum()
        cen = float((frq * spec).sum() / tot)
        band = (frq >= 2000) & (frq <= 10000)
        flat = float(np.exp(np.log(spec[band]).mean()) / spec[band].mean())
        low = float(10 * np.log10(spec[frq < 250].sum() / tot))
        nsub = int(((r_on > t + 0.03) & (r_on < t + 0.3)).sum())
        vi = (vt >= t) & (vt < t + 0.2)
        voiced = bool(vi.any() and (vprob[vi] > 0.5).mean() >= 0.5 and np.nanmedian(np.where(vi, f0, np.nan)) < 600)
        dts = s_on - t
        song_dt = float(dts[np.argmin(np.abs(dts))] * 1000) if len(dts) else None
        sdt = strong_song - t
        strong_dt = float(sdt[np.argmin(np.abs(sdt))] * 1000) if len(sdt) else None
        f = dict(t=round(float(t), 3), peak_db_vs_music=round(float(rel), 1), peak_db_vs_song_inst=round(float(inst), 1),
                 attack_db=round(float(attack), 1), rise_db=round(float(rise), 1), decay12_ms=round(decay),
                 centroid=round(cen), flat=round(flat, 3), low_db=round(low, 1), n_sub=nsub, voiced=voiced,
                 song_onset_dt_ms=None if song_dt is None else round(song_dt),
                 strong_song_onset_dt_ms=None if strong_dt is None else round(strong_dt))
        f['audible'] = bool(rel >= -6 and rise >= 4.5)
        f['clear'] = bool(rel >= 3 and inst >= 0 and rise >= 4.5)
        f['cls'] = classify(f)
        hits.append(f)
    # merge: within 90 ms keep the loudest (a hit's own sub-transients)
    merged = []
    for h in hits:
        if h['audible'] and merged and h['t'] - merged[-1]['t'] < 0.09:
            if h['peak_db_vs_music'] > merged[-1]['peak_db_vs_music']:
                merged[-1] = h
            continue
        if h['audible']:
            merged.append(h)
    aud = merged
    clear = [h for h in aud if h['clear']]
    dur = n / SR
    out['duration_s'] = round(dur, 2)
    out['events'] = aud
    out['n_audible'] = len(aud)
    out['n_clear'] = len(clear)
    out['audible_per_s'] = round(len(aud) / dur, 2)
    out['clear_per_s'] = round(len(clear) / dur, 2)
    out['clear_share_of_audible'] = round(len(clear) / max(1, len(aud)), 3)

    def gaps(ts):
        ts = [0.0] + sorted(ts) + [dur]
        g = np.diff(ts)
        i = int(np.argmax(g))
        return round(float(g[i]), 2), round(ts[i], 2), round(ts[i + 1], 2)
    out['longest_gap_audible'] = gaps([h['t'] for h in aud])
    out['longest_gap_clear'] = gaps([h['t'] for h in clear])
    dead = []
    for s in range(int(dur)):
        if not any(s <= h['t'] < s + 1 for h in clear):
            dead.append(s)
    out['seconds_without_clear_hit'] = dead
    out['dead_second_share'] = round(len(dead) / max(1, int(dur)), 3)
    out['clip_layer_present_pct'] = round(float((r20d - s600d > -20).mean() * 100), 1)
    # noise floor between hits: residual level where no audible event within +-0.15 s
    mask = np.ones(m, bool)
    for h in aud:
        a = int((h['t'] - 0.05) * SR / HOP)
        b = int((h['t'] + 0.35) * SR / HOP)
        mask[max(0, a):max(0, b)] = False
    out['between_hits_clip_db_vs_music'] = round(float(np.median((r20d - s600d)[mask])), 1) if mask.any() else None
    lk = [abs(h['song_onset_dt_ms']) for h in clear if h['song_onset_dt_ms'] is not None]
    out['lock_abs_median_ms'] = round(float(np.median(lk))) if lk else None
    out['lock_within40_pct'] = round(100 * float(np.mean(np.array(lk) <= 40)), 1) if lk else None
    cov = [bool(np.any(np.abs(np.array([h['t'] for h in clear]) - s) <= 0.04)) for s in strong_song] if clear else []
    out['strong_song_onsets_with_clear_hit_pct'] = round(100 * float(np.mean(cov)), 1) if cov else 0.0
    pk = [h['peak_db_vs_music'] for h in clear]
    out['clear_peak_db_vs_music_p25_50_75'] = [round(float(v), 1) for v in np.percentile(pk, [25, 50, 75])] if pk else None
    at = [h['attack_db'] for h in aud]
    out['attack_db_median'] = round(float(np.median(at)), 1) if at else None
    dc = [h['decay12_ms'] for h in clear]
    out['decay12_ms_median'] = round(float(np.median(dc))) if dc else None
    cls = {}
    for h in aud:
        cls[h['cls']] = cls.get(h['cls'], 0) + 1
    out['classes_audible'] = cls
    clsc = {}
    for h in clear:
        clsc[h['cls']] = clsc.get(h['cls'], 0) + 1
    out['classes_clear'] = clsc
    out['voiced_hit_share'] = round(sum(h['voiced'] for h in aud) / max(1, len(aud)), 3)
    out['song_onsets'] = [round(float(v), 3) for v in s_on]
    # owner 2026-10-15 ("remake the beat with the clip sounds", ref FB15TNFAHVU): (15) EVERY audible clip transient sits within
    # +-30 ms of a song onset, (16) the share of the song's drum onsets (kick / snare / hat: onsets of the percussive part of the
    # song layer, strong half) that a clip transient re-creates within +-30 ms
    try:
        perc = librosa.effects.percussive(songl, margin=2.0)
        penv = librosa.onset.onset_strength(y=perc, sr=SR, hop_length=HOP)
        pfr = librosa.onset.onset_detect(onset_envelope=penv, sr=SR, hop_length=HOP, delta=0.1, wait=int(0.08 * SR / HOP))
        p_str = penv[pfr]
        d_on = (pfr * HOP / SR)[p_str >= np.median(p_str)] if len(pfr) else np.zeros(0)
    except Exception:  # noqa: BLE001
        d_on = strong_song
    d_on = np.asarray(d_on, float)
    all_on = np.sort(np.concatenate([s_on, d_on])) if len(d_on) else np.asarray(s_on, float)
    tr_t = np.array([h['t'] for h in aud], float)
    offb = []
    for h in aud:
        dt = all_on - h['t']
        h['onset_dt30_ms'] = round(float(dt[np.argmin(np.abs(dt))] * 1000)) if len(dt) else None
        if h['onset_dt30_ms'] is None or abs(h['onset_dt30_ms']) > 30:
            offb.append(h)
    out['transients_on_onset_share'] = round(1 - len(offb) / max(1, len(aud)), 3)
    out['transients_off_onset'] = [(h['t'], h['onset_dt30_ms'], h['peak_db_vs_music']) for h in offb]
    rec = [bool(len(tr_t) and np.min(np.abs(tr_t - t)) <= 0.03) for t in d_on]
    out['drum_onsets'] = [round(float(v), 3) for v in d_on]
    out['drum_onsets_recreated_share'] = round(float(np.mean(rec)), 3) if rec else 0.0
    out['drum_onsets_per_s'] = round(len(d_on) / max(0.1, n / SR), 2)
    out['_series'] = dict(t=t_env, clip_db=r20d, music_db=s600d, vt=vt, voiced=voiced_active)
    out['_res'] = res
    out['loudness'] = loudness(path)
    return out


# -------------------------------------------------------------------------------------- picture
def scan_video(path, keep_idx, thumb_w=150):
    W, H, fps, dur = ffprobe(path)
    ah = 640
    aw = int(round(W * ah / H / 2)) * 2
    cmd = ['ffmpeg', '-v', 'error', '-threads', THREADS, '-i', path, '-vf', f'scale={aw}:{ah}:flags=area',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    fb = aw * ah * 3
    sw, sh = 90, int(round(90 * ah / aw))
    th = int(round(thumb_w * ah / aw))
    small, tiny, luma, sat, sharp, keep, edge_acc = [], [], [], [], [], {}, None
    i = 0
    while True:
        buf = p.stdout.read(fb)
        if len(buf) < fb:
            break
        arr = np.frombuffer(buf, np.uint8).reshape(ah, aw, 3)
        f = arr.astype(np.float32)
        g = f @ np.array([0.299, 0.587, 0.114], np.float32)
        lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        sharp.append(float(lap.var()))
        luma.append(float(g.mean() / 255))
        mx, mn = f.max(axis=2), f.min(axis=2)
        sat.append(float(((mx - mn) / (mx + 1)).mean()))
        im = Image.fromarray(arr)
        sm = np.asarray(im.convert('L').resize((sw, sh), Image.BILINEAR), np.uint8)
        small.append(sm)
        tiny.append(np.asarray(im.resize((12, int(round(12 * ah / aw))), Image.BILINEAR), np.uint8))
        e = (np.abs(np.diff(sm.astype(np.int16), axis=0))[:, :-1] + np.abs(np.diff(sm.astype(np.int16), axis=1))[:-1]) > 40
        edge_acc = e.astype(np.uint16) if edge_acc is None else edge_acc + e
        if i in keep_idx:
            b = io.BytesIO()
            im.resize((thumb_w, th), Image.BILINEAR).save(b, 'JPEG', quality=85)
            keep[i] = b.getvalue()
        i += 1
    p.wait()
    small = np.stack(small)
    return dict(path=path, W=W, H=H, fps=fps, dur=dur, n=i, small=small, tiny=np.stack(tiny), luma=np.array(luma),
                sat=np.array(sat), sharp=np.array(sharp), thumbs=keep, edge_persist=edge_acc / max(1, i),
                thumb_size=(thumb_w, th))


def detect_cuts(v):
    """Hard cuts: a large frame difference + histogram change that PERSISTS (the frames one 24 fps step either
    side are also unlike), so flashes / impact frames inside a shot are not counted."""
    s = v['small'].astype(np.int16)
    n = len(s)
    d = np.abs(np.diff(s, axis=0)).mean(axis=(1, 2))
    hb = np.stack([np.histogram(x, bins=16, range=(0, 256))[0] for x in v['small']]).astype(np.float32)
    hb /= hb.sum(axis=1, keepdims=True)
    hd = np.abs(np.diff(hb, axis=0)).sum(axis=1)
    ti = v['tiny'].reshape(n, -1).astype(np.float32)
    ti = (ti - ti.mean(axis=1, keepdims=True)) / (ti.std(axis=1, keepdims=True) + 1e-6)
    k = max(1, int(round(v['fps'] / 24)))
    cuts = []
    gap = max(2, int(round(v['fps'] / 8)))
    for i in range(len(d)):
        lo = max(0, i - 30)
        med = np.median(d[lo:i + 30]) + 1.0
        if not ((d[i] > 5 * med and d[i] > 8 and hd[i] > 0.15) or d[i] > 35):
            continue
        a, b = max(0, i - k), min(n - 1, i + 1 + k)
        if float(np.dot(ti[a], ti[b]) / ti.shape[1]) > 0.55:
            continue
        if cuts and i + 1 - cuts[-1] < gap:
            if d[i] > d[cuts[-1] - 1]:
                cuts[-1] = i + 1
            continue
        cuts.append(i + 1)
    return cuts, d


def motion_base(v, cuts):
    """Global median motion per 24 fps step (grey levels on the 90 px frames), cut transitions excluded."""
    if 'motion_base' in v:
        return v['motion_base']
    st = max(1, int(round(v['fps'] / 24)))
    sm = v['small']
    cs = set()
    for c in cuts:
        cs |= set(range(c - st, c + st + 1))
    idx = [k for k in range(st, len(sm), 2) if k not in cs]
    vals = [np.abs(sm[k].astype(np.int16) - sm[k - st]).mean() for k in idx]
    v['motion_base'] = round(float(np.median(vals)), 2) if vals else 2.0
    return v['motion_base']


FG_BAND = [False]     # set from the plan: a 'blurfit' vertical (wide window + blurred fill): the cause is judged in the sharp middle band only


def cause_at(v, cuts, t):
    """Visible cause at time t: motion (24 fps steps) from -1 to +3 steps around the hit inside its shot, against
    the video's global median motion; spark = brightness rise in the motion region; centroid / area in the frame."""
    fps = v['fps']
    st = max(1, int(round(fps / 24)))
    fi = int(round(t * fps))
    sm = v['small'].astype(np.float32)
    n = len(sm)
    if fi >= n - 1:
        return None
    prev_cut = max([c for c in cuts if c <= fi], default=0)
    next_cut = min([c for c in cuts if c > fi], default=n)
    a = max(prev_cut + 1, fi - st)
    b = min(next_cut - 1, fi + 3 * st)
    if b < a:
        b = a
    M = np.zeros_like(sm[0])
    cnt = 0
    for k in range(a, b + 1, max(1, st // 2)):
        j = max(prev_cut, k - st)
        if j >= k:
            continue
        M += np.abs(sm[k] - sm[j])
        cnt += 1
    M /= max(1, cnt)
    if FG_BAND[0]:
        # blurfit layout (owner 2026-10-13): the sharp wide window sits in the middle band (1:1 .. 16:9 = 56 %..32 % of the height); the blurred
        # fill above / below is context, not the cause. The cause must still be large and centred AT THE HIT (after the punch-in).
        h_ = M.shape[0]
        M[:int(0.25 * h_)] = 0
        M[int(0.75 * h_):] = 0
    base = motion_base(v, cuts)
    prom = float(M.mean() / (base + 0.3))
    Mb = uniform_filter(M, 5)
    hot = Mb >= 0.35 * Mb.max() if Mb.max() > 0 else np.zeros(Mb.shape, bool)
    area = float(hot.mean()) * (2.0 if FG_BAND[0] else 1.0)
    yy, xx = np.mgrid[0:Mb.shape[0], 0:Mb.shape[1]]
    w = np.where(hot, Mb, 0)
    sw = w.sum() + 1e-9
    cx = float((w * xx).sum() / sw / Mb.shape[1]) if hot.any() else 0.5
    cy = float((w * yy).sum() / sw / Mb.shape[0]) if hot.any() else 0.5
    ys, xs = np.nonzero(hot)
    box = [float(np.percentile(xs, 10) / Mb.shape[1]), float(np.percentile(ys, 10) / Mb.shape[0]),
           float(np.percentile(xs, 90) / Mb.shape[1]), float(np.percentile(ys, 90) / Mb.shape[0])] if len(xs) else [0, 0, 1, 1]
    pre_f = max(prev_cut, fi - st)
    post = np.maximum(sm[min(n - 1, fi + st)], sm[min(n - 1, b)])
    spark = float(np.percentile((post - sm[pre_f])[hot], 90) / 255) if hot.any() else 0.0
    lum_reg = float(sm[fi][hot].mean() / 255) if hot.any() else float(sm[fi].mean() / 255)
    preroll = (fi - prev_cut) / fps
    centred = abs(cx - 0.5) <= 0.22 and abs(cy - 0.5) <= 0.25
    lit = lum_reg >= 0.16 or spark >= 0.12
    readable = (0.015 <= area <= 0.6) and lit and v['sharp'][fi] > 0.25 * np.median(v['sharp'])
    moving = prom >= 1.2 or (spark >= 0.3 and prom >= 0.4)  # calibrated by eye on FB15 + sound_6 strips
    visible = moving and readable and preroll >= 0.035
    return dict(frame=fi, prom=round(prom, 2), spark=round(spark, 2), area=round(area, 3), cx=round(cx, 2),
                cy=round(cy, 2), box=[round(x, 3) for x in box], lum=round(lum_reg, 2), preroll_ms=round(preroll * 1000),
                centred=bool(centred), readable=bool(readable), moving=bool(moving),
                visible=bool(visible), visible_centred=bool(visible and centred))


def why_not(c):
    if c['visible_centred']:
        return 'OK'
    if c['preroll_ms'] < 35:
        return 'cut-on-hit'
    if not c['moving']:
        return 'nothing moves'
    if not c['readable']:
        return 'unreadable (dark/tiny/soft)'
    return 'off-centre x%.2f y%.2f' % (c['cx'], c['cy'])


def still_runs(v, thr=None):
    """(start s, duration s) of every run of >= 2 frames whose mean grey difference from the run's first frame stays < thr."""
    thr = FREEZE_THR if thr is None else thr
    f = v['small'].astype(np.int16)
    fps, n, out, k = v['fps'], len(f), [], 0
    while k < n - 1:
        m = k + 1
        while m < n and np.abs(f[m] - f[k]).mean() < thr:
            m += 1
        if m - k > 1:
            out.append((round(k / fps, 3), round((m - k) / fps, 3)))
        k = m
    return out


def picture_metrics(v, cuts):
    fps, n = v['fps'], v['n']
    bounds = [0] + cuts + [n]
    shots = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < 1:
            continue
        mid = (a + b) // 2
        shots.append(dict(start=round(a / fps, 3), dur=round((b - a) / fps, 3), luma=round(float(np.median(v['luma'][a:b])), 3),
                          sat=round(float(np.median(v['sat'][a:b])), 3), sharp=round(float(np.median(v['sharp'][a:b])), 1),
                          mid=mid))
    durs = np.array([s['dur'] for s in shots])
    inner = durs[1:-1] if len(durs) > 2 else durs
    # near duplicates (non-adjacent shots)
    sig = []
    for s in shots:
        t = v['tiny'][s['mid']].astype(np.float32).ravel()
        sig.append((t - t.mean()) / (t.std() + 1e-6))
    dups = []
    for i in range(len(shots)):
        for j in range(i + 2, len(shots)):
            c = float(np.dot(sig[i], sig[j]) / len(sig[i]))
            if c > 0.9:
                dups.append([shots[i]['start'], shots[j]['start'], round(c, 2)])
    col = np.array([v['tiny'][s['mid']].reshape(-1, 3).mean(axis=0) for s in shots])
    ep = v['edge_persist']
    persist = ep > 0.6
    rows = np.nonzero(persist.any(axis=1))[0]
    cols = np.nonzero(persist.any(axis=0))[0]
    overlay = dict(area_pct=round(float(persist.mean() * 100), 2),
                   box=[round(cols.min() / ep.shape[1], 2), round(rows.min() / ep.shape[0], 2),
                        round(cols.max() / ep.shape[1], 2), round(rows.max() / ep.shape[0], 2)] if len(rows) else None)
    # letterbox bars: rows that stay near-black in > 90 % of frames
    dark_rows = (v['small'].mean(axis=2) < 12).mean(axis=0) > 0.9
    sr = still_runs(v)
    still = dict(still_runs_over_0_1s=sum(1 for _, d in sr if d > 0.1), still_max_s=max([d for _, d in sr], default=0.0),
                 frozen=[[t, d] for t, d in sorted(sr, key=lambda x: -x[1]) if d > FREEZE_MAX])
    return dict(n_shots=len(shots), cuts_per_s=round(len(cuts) / (n / fps), 2), still=still,
                shot_s_p10_50_90=[round(float(x), 3) for x in np.percentile(inner, [10, 50, 90])],
                shot_cv=round(float(inner.std() / (inner.mean() + 1e-9)), 2),
                luma_median=round(float(np.median([s['luma'] for s in shots])), 3),
                luma_p10=round(float(np.percentile([s['luma'] for s in shots], 10)), 3),
                sat_median=round(float(np.median([s['sat'] for s in shots])), 3),
                sharp_median=round(float(np.median([s['sharp'] for s in shots])), 1),
                sharp_p10=round(float(np.percentile([s['sharp'] for s in shots], 10)), 1),
                soft_shots=[s['start'] for s in shots if s['sharp'] < 0.4 * np.median([x['sharp'] for x in shots])],
                dark_shots=[s['start'] for s in shots if s['luma'] < 0.13],
                near_duplicate_pairs=dups, colour_spread=round(float(col.std(axis=0).mean()), 1),
                overlay=overlay, black_bar_rows_pct=round(float(dark_rows.mean() * 100), 1),
                shots=[{k: s[k] for k in ('start', 'dur', 'luma', 'sat', 'sharp')} for s in shots])


# ------------------------------------------------------------------------------------- scorecard
def lin(x, bad, good):
    """10 at good, 0 at bad, linear between (works for either direction)."""
    if x is None:
        return 0.0
    if bad == good:
        return 10.0
    return float(np.clip(10 * (x - bad) / (good - bad), 0, 10))


def band(x, lo, hi, lo0, hi0):
    if x is None:
        return 0.0
    if lo <= x <= hi:
        return 10.0
    return lin(x, lo0, lo) if x < lo else lin(x, hi0, hi)


VERBS = {'gun': 'shoot', 'reload': 'reload', 'sword': 'clash', 'sheath': 'sheathe', 'fist': 'punch', 'kick': 'kick', 'clap': 'clap / knock / slap',
         'object': 'slam / click', 'impact': 'slam', 'glass': 'shatter', 'mask': 'crack', 'crack': 'crack', 'chain': 'rattle', 'metal': 'clang',
         'stomp': 'stomp'}


def scorecard(C, R, plan_len=None):
    ca, ra, cv, rv = C['audio'], R['audio'], C['picture'], R['picture']
    sc, ev = {}, {}
    hits_c = C['hits']
    # (1) sound-to-music feel
    pre = [h['cause']['preroll_ms'] for h in hits_c if h.get('cause')]
    pre_med = float(np.median(pre)) if pre else None
    cov_ref = max(1.0, ra['strong_song_onsets_with_clear_hit_pct'])
    s1 = (0.3 * lin(ca['lock_abs_median_ms'], 60, 15) + 0.3 * lin(ca['lock_within40_pct'], 30, 75)
          + 0.2 * band(pre_med, 60, PRE_HI, 15, PRE_HI + 300) + 0.2 * lin(ca['strong_song_onsets_with_clear_hit_pct'], 0.2 * cov_ref, 0.9 * cov_ref))
    off = [h for h in hits_c if h['clear'] and h['song_onset_dt_ms'] is not None and abs(h['song_onset_dt_ms']) > 40]
    sc['1_sound_to_music'] = s1
    ev['1_sound_to_music'] = (f"lock |median| {ca['lock_abs_median_ms']} ms (ref {ra['lock_abs_median_ms']}), within 40 ms "
                              f"{ca['lock_within40_pct']} % (ref {ra['lock_within40_pct']}), strong song onsets carrying a clear hit "
                              f"{ca['strong_song_onsets_with_clear_hit_pct']} % (ref {ra['strong_song_onsets_with_clear_hit_pct']}), "
                              f"cut-to-hit pre-roll median {pre_med} ms; off-beat clear hits at "
                              + ', '.join(f"{h['t']}s({h['song_onset_dt_ms']:+d}ms)" for h in off[:8]))
    # (2) audible + punchy
    pk = ca['clear_peak_db_vs_music_p25_50_75'][1] if ca['clear_peak_db_vs_music_p25_50_75'] else None
    rshare = max(0.05, ra['clear_share_of_audible'])
    nf_ref = ra['between_hits_clip_db_vs_music'] if ra['between_hits_clip_db_vs_music'] is not None else -15
    # the between-hits level depends on how well the song subtracts (ref 2.4 dB removed vs ours ~6), so it is
    # reported but not scored
    del nf_ref
    s2 = (0.5 * lin(ca['clear_share_of_audible'], 0.35 * rshare, 0.95 * rshare) + 0.25 * band(pk, 3, 8, -2, 14)
          + 0.25 * lin(ca['attack_db_median'], -4, -13))
    weak = [h for h in hits_c if not h['clear']]
    sc['2_clip_sound_audible_punchy'] = s2
    ev['2_clip_sound_audible_punchy'] = (f"clear-above-music {ca['n_clear']}/{ca['n_audible']} audible hits = {ca['clear_share_of_audible']} "
                                         f"(ref {ra['n_clear']}/{ra['n_audible']} = {ra['clear_share_of_audible']}); clear peak p25/50/75 "
                                         f"{ca['clear_peak_db_vs_music_p25_50_75']} dB (ref {ra['clear_peak_db_vs_music_p25_50_75']}); attack "
                                         f"{ca['attack_db_median']} dB (ref {ra['attack_db_median']}); clip layer between hits "
                                         f"{ca['between_hits_clip_db_vs_music']} dB re music (ref {ra['between_hits_clip_db_vs_music']}); "
                                         f"buried hits at " + ', '.join(f"{h['t']}s({h['peak_db_vs_music']:+.1f})" for h in weak[:10]))
    # (3) visible cause, centred
    ch = [h for h in hits_c if h['clear'] and h.get('cause')]
    vc = float(np.mean([h['cause']['visible_centred'] for h in ch])) if ch else 0.0
    vis = float(np.mean([h['cause']['visible'] for h in ch])) if ch else 0.0
    rh = [h for h in R['hits'] if h['clear'] and h.get('cause')]
    rvc = float(np.mean([h['cause']['visible_centred'] for h in rh])) if rh else 0.8
    s3 = 0.7 * lin(vc, 0.3 * max(rvc, 0.5), 0.95 * max(rvc, 0.5)) + 0.3 * lin(vis, 0.3, 0.85)
    bad = [h for h in ch if not h['cause']['visible_centred']]
    sc['3_visible_centred_cause'] = s3
    ev['3_visible_centred_cause'] = (f"clear hits with a visible + centred cause {vc:.2f} (ref {rvc:.2f}); visible anywhere {vis:.2f}; "
                                     "misses: " + ', '.join(
                                         f"{h['t']}s({why_not(h['cause'])})"
                                         for h in bad[:10]))
    # (4) sound types
    clear = [h for h in hits_c if h['clear']]
    wts = [1.0 if h['cls'] in OWNER_TYPES else (0.6 if h['cls'] in HALF_TYPES else 0.0) for h in clear]
    tfit = float(np.mean(wts)) if wts else 0.0
    rclear = [h for h in R['hits'] if h['clear']]
    rfit = float(np.mean([1.0 if h['cls'] in OWNER_TYPES else (0.6 if h['cls'] in HALF_TYPES else 0.0) for h in rclear])) if rclear else 0.6
    # the spectral class heuristic is crude (it calls most crisp broadband hits 'gun'), so it is scored against the
    # reference's own share; the judge confirms types by eye / ear and from the plan's source tags
    s4 = lin(tfit, 0.3 * rfit, 0.95 * rfit) - max(0.0, ca['voiced_hit_share'] - 0.15) * 20
    sc['4_sound_types_fit'] = max(0.0, s4)
    ev['4_sound_types_fit'] = (f"clear-hit classes {ca['classes_clear']} (ref {ra['classes_clear']}); owner-type weighted share {tfit:.2f}; "
                               f"voiced hits {ca['voiced_hit_share']} (ref {ra['voiced_hit_share']}); voice share of clip time "
                               f"{ca['voice_share_of_clip_time']} (ref {ra['voice_share_of_clip_time']}); voiced/other hits at "
                               + ', '.join(f"{h['t']}s({h['cls']})" for h in clear if h['cls'] in ('voice', 'other', 'whoosh'))[:400])
    # (5) density
    g = ca['longest_gap_clear'][0]
    s5 = (0.3 * band(ca['clear_per_s'], 1.2, 2.8, 0.3, 4.5) + 0.3 * lin(g, 4.0, 1.2) + 0.2 * lin(ca['dead_second_share'], 0.3, 0.0)
          + 0.2 * lin(ca['clip_layer_present_pct'], 50, 85))
    sc['5_density_no_dead'] = s5
    ev['5_density_no_dead'] = (f"clear hits {ca['clear_per_s']}/s (ref {ra['clear_per_s']}), audible {ca['audible_per_s']}/s (ref "
                               f"{ra['audible_per_s']}); longest gap without a clear hit {ca['longest_gap_clear']} (ref {ra['longest_gap_clear']}); "
                               f"seconds without a clear hit {ca['seconds_without_clear_hit']}; clip layer present {ca['clip_layer_present_pct']} % "
                               f"(ref {ra['clip_layer_present_pct']})")
    # (6) framing / picture quality
    allc = [h['cause'] for h in hits_c if h.get('cause')]
    cen = float(np.mean([c['centred'] for c in allc])) if allc else 0
    sr = cv['sharp_median'] / max(1.0, rv['sharp_median'])
    s6 = (0.35 * lin(sr, 0.25, 0.8) + 0.15 * band(cv['luma_median'], 0.25, 0.6, 0.1, 0.85)
          + 0.15 * lin(cv['sat_median'] / max(0.01, rv['sat_median']), 0.4, 0.8) + 0.2 * lin(cen, 0.3, 0.85)
          + 0.15 * lin(len(cv['soft_shots']) / max(1, cv['n_shots']), 0.3, 0.0))
    sc['6_vertical_framing_quality'] = s6
    ev['6_vertical_framing_quality'] = (f"sharpness median {cv['sharp_median']} vs ref {rv['sharp_median']} (x{sr:.2f}); luma {cv['luma_median']} "
                                        f"(ref {rv['luma_median']}); saturation {cv['sat_median']} (ref {rv['sat_median']}); hit motion centred "
                                        f"{cen:.2f}; soft shots at {cv['soft_shots'][:8]}; dark shots at {cv['dark_shots'][:8]}; black-bar rows "
                                        f"{cv['black_bar_rows_pct']} %; overlay {cv['overlay']}")
    # (7) variety
    nd = len(cv['near_duplicate_pairs'])
    srcs = C.get('plan_sources')
    s7 = (0.4 * lin(nd, 6, 0) + 0.3 * lin(cv['colour_spread'] / max(1.0, rv['colour_spread']), 0.4, 0.85)
          + 0.3 * (lin(srcs[0] / max(1, srcs[1]), 0.5, 0.9) if srcs else 7.0))
    sc['7_variety'] = s7
    ev['7_variety'] = (f"near-duplicate shot pairs {nd} {cv['near_duplicate_pairs'][:6]}; colour spread {cv['colour_spread']} (ref "
                       f"{rv['colour_spread']}); plan distinct sources / shots {srcs}; shot length p10/50/90 {cv['shot_s_p10_50_90']} s "
                       f"(ref {rv['shot_s_p10_50_90']}), cv {cv['shot_cv']} (ref {rv['shot_cv']}), cuts/s {cv['cuts_per_s']} (ref {rv['cuts_per_s']})")
    # (8) length
    L = ca['duration_s']
    s8 = 10.0 if 30 <= L <= 50 else max(0.0, 10 - 0.6 * (30 - L if L < 30 else L - 50))
    sc['8_length_30_50s'] = s8
    ev['8_length_30_50s'] = f"{L} s (target 30-50 s; ref {ra['duration_s']} s is a loop)"
    # (10) lead-in, (11) whole action on screen, (12) cut rate (owner 2026-10-13, frame by frame against FB15: every ref hit shot opens on the INTACT
    # object 2-4 frames before the event, the whole object / action is in the frame, and the cut rate is the ref's, not faster)
    def share(X, f):
        hs = [h for h in X['hits'] if h.get('clear') and h.get('cause')]
        return (sum(1 for h in hs if f(h['cause'])) / len(hs)) if hs else 0.0
    lead = lambda c: c.get('preroll_ms', 0) >= 90 and c.get('visible')                     # noqa: E731
    whole = lambda c: c.get('visible') and 0.02 <= c['box'][0] and c['box'][2] <= 0.98 and c.get('area', 0) <= 0.6   # noqa: E731
    cl, rl, cw, rw = share(C, lead), share(R, lead), share(C, whole), share(R, whole)
    sc['10_lead_in_build_up'] = lin(cl / max(0.05, rl), 0.45, 0.9)
    ev['10_lead_in_build_up'] = (f"clear hits whose shot opens >= 90 ms before the hit on a readable moving object: {cl:.2f} (ref {rl:.2f}); "
                                 f"cut -> hit median {C['cause_summary'].get('preroll_ms_median')} ms (ref {R['cause_summary'].get('preroll_ms_median')} ms)")
    sc['11_whole_action_in_frame'] = lin(cw / max(0.05, rw), 0.45, 0.9)
    ev['11_whole_action_in_frame'] = f"clear hits whose cause box is whole inside the frame (not clipped at the edges, <= 60 % of it): {cw:.2f} (ref {rw:.2f})"
    # owner 2026-10-13: slower than both references (FB15 2.3 cuts/s, jKSxAkOS3JY 3.2): target 1.2-1.6 cuts/s, each hit shot ~0.6-1.2 s
    cps = cv['cuts_per_s']
    sc['12_cut_rate_vs_ref'] = 10.0 if 1.1 <= cps <= 1.7 else max(0.0, 10 - 8 * (1.1 - cps if cps < 1.1 else cps - 1.7))
    ev['12_cut_rate_vs_ref'] = f"cuts/s {cps} (target 1.1-1.7 by the owner's eye; ref FB15 {rv['cuts_per_s']}, jKSxAkOS3JY 3.2): shot length p10/50/90 {cv['shot_s_p10_50_90']} s"
    # (13) every hit's action can be named in ONE word that implies its sound (shoot, reload, slam, stomp, clash, shatter, knock, snap ...), (14) no filler shots
    ph = C.get('plan_vis') or []
    aud = [h for h in ph if not h[1]]
    named = [h for h in aud if VERBS.get(h[2])]
    if ph:
        sc['13_action_nameable_in_one_word'] = lin(len(named) / max(1, len(aud)), 0.6, 0.98)
        miss = [f"{h[0]:.2f}s({h[2]})" for h in aud if not VERBS.get(h[2])]
        ev['13_action_nameable_in_one_word'] = (f"{len(named)} of {len(aud)} audible hits carry a sound-implying action tag "
                                                f"({', '.join(sorted({VERBS[h[2]] for h in named}))}); not nameable: {miss[:10]} (still check hits_cand.jpg by eye)")
        sc['14_every_shot_a_clear_cause'] = lin(len(aud) / max(1, len(ph)), 0.7, 1.0)
        ev['14_every_shot_a_clear_cause'] = f"{len(aud)} of {len(ph)} shots are cause-and-sound hits ({len(ph) - len(aud)} picture-only filler shots)"
    # (15) / (16) owner 2026-10-15 "remake the beat with the clip sounds": every audible clip transient on a song onset (+-30 ms; FB15
    # 0.71, jKSxAkOS3JY 0.46, sound_10b 0.74: 10 at 0.97, 8 at 0.876), and the share of the song's drum onsets the clip transients
    # re-create (FB15 0.71, jKSxAkOS3JY 0.32, sound_10b 0.42: 10 at 0.6, 8 at 0.51). Absolute bars (the owner asked for more than sound_10b)
    if ca.get('transients_on_onset_share') is not None:
        sc['15_transients_on_onsets'] = lin(ca['transients_on_onset_share'], 0.5, 0.97)
        ev['15_transients_on_onsets'] = (f"{ca['transients_on_onset_share']:.3f} of {ca['n_audible']} audible clip transients within +-30 ms of a song onset "
                                         f"(ref {ra.get('transients_on_onset_share')}); off-onset: "
                                         + ', '.join(f"{t}s({d if d is None else f'{d:+d}'}ms, {p:+.1f}dB)" for t, d, p in ca['transients_off_onset'][:12]))
        sc['16_drum_onsets_recreated'] = lin(ca['drum_onsets_recreated_share'], 0.15, 0.6)
        ev['16_drum_onsets_recreated'] = (f"{ca['drum_onsets_recreated_share']:.3f} of {len(ca['drum_onsets'])} song drum onsets ({ca['drum_onsets_per_s']}/s) carry a clip "
                                          f"transient within +-30 ms (ref {ra.get('drum_onsets_recreated_share')})")
    # (17) owner 2026-10-16 hard rule: zero frozen frames / holds (still run > FREEZE_MAX); any frozen run fails the edit
    st = cv.get('still') or {}
    if st:
        fz = st.get('frozen') or []
        sc['17_no_frozen_frames'] = 10.0 if not fz else max(0.0, 5.0 - 1.0 * (len(fz) - 1))
        ev['17_no_frozen_frames'] = (f"{len(fz)} still runs > {FREEZE_MAX:.2f} s (diff < {FREEZE_THR} grey): {fz[:10]}; longest still {st.get('still_max_s')} s, "
                                     f"{st.get('still_runs_over_0_1s')} runs > 0.1 s (anime 2-4 frame holds; ref {(rv.get('still') or {}).get('still_runs_over_0_1s')})")
    core = [sc[k] for k in sc]
    s9 = min(float(np.mean(core)), min(core) + 2.5)
    sc['9_overall_vs_ref'] = s9
    ev['9_overall_vs_ref'] = f"auto = min(mean of 1-8 = {np.mean(core):.1f}, worst + 2.5 = {min(core) + 2.5:.1f}); the judge's eye may override"
    sc = {k: round(float(v), 1) for k, v in sc.items()}
    passed = all(v >= 8 for k, v in sc.items() if k != '9_overall_vs_ref') and sc['9_overall_vs_ref'] >= 8.5
    return dict(scores=sc, evidence=ev, pass_bar='>= 8 on every criterion and >= 8.5 overall', PASS=bool(passed))


# ----------------------------------------------------------------------------------------- sheets
def _font(sz):
    for f in ('arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(f, sz)
        except OSError:
            pass
    return ImageFont.load_default()


def make_sheets(out_dir, C, R, sel, mapping, per_row=4):
    os.makedirs(os.path.join(out_dir, 'sheets'), exist_ok=True)
    cvid, rvid = C['_v'], R['_v']
    tw, th = cvid['thumb_size']
    rtw, rth = rvid['thumb_size']
    rth2 = int(round(rth * tw / rtw))
    cell_w, cell_h = 2 * tw + 14, max(th, rth2) + 34
    f = _font(13)
    chits = [h['t'] for h in C['hits']]
    rhits = [h['t'] for h in R['hits']]
    phits = C.get('plan_hits') or []
    paths = []
    for sec, idxs in sorted(sel.items()):
        rows = (len(idxs) + per_row - 1) // per_row
        sheet = Image.new('RGB', (per_row * cell_w + 10, rows * cell_h + 40), (18, 18, 18))
        d = ImageDraw.Draw(sheet)
        d.text((8, 8), f"{C['label']}  second {sec}-{sec + 1}   pairs: OURS (left) | REF at the same beat (right)   red = clip hit, "
                       f"yellow = cut, P/p = planned audible / picture-only hit", fill=(230, 230, 230), font=f)
        for k, fi in enumerate(idxs):
            x0 = 8 + (k % per_row) * cell_w
            y0 = 36 + (k // per_row) * cell_h
            t = fi / cvid['fps']
            rt, rfi, bpos = mapping(t)
            im = Image.open(io.BytesIO(cvid['thumbs'][fi]))
            sheet.paste(im, (x0, y0))
            rim = Image.open(io.BytesIO(rvid['thumbs'][rfi])).resize((tw, rth2))
            sheet.paste(rim, (x0 + tw + 4, y0))
            half = 0.5 / 24
            if any(abs(t - h) <= half for h in chits):
                d.rectangle([x0, y0, x0 + tw - 1, y0 + th - 1], outline=(255, 40, 40), width=4)
            if any(abs(rt - h) <= half for h in rhits):
                d.rectangle([x0 + tw + 4, y0, x0 + 2 * tw + 3, y0 + rth2 - 1], outline=(255, 40, 40), width=4)
            if fi in C['_cutset']:
                d.rectangle([x0, y0, x0 + 12, y0 + 12], fill=(255, 220, 0))
            if rfi in R['_cutset']:
                d.rectangle([x0 + tw + 4, y0, x0 + tw + 16, y0 + 12], fill=(255, 220, 0))
            tag = ''
            for (ph, silent) in phits:
                if abs(t - ph) <= half:
                    tag = ' p' if silent else ' P'
            d.text((x0, y0 + max(th, rth2) + 2), f"{t:5.2f}s b{bpos:5.2f}{tag}", fill=(240, 240, 240), font=f)
            d.text((x0 + tw + 4, y0 + max(th, rth2) + 2), f"ref {rt:4.2f}s", fill=(170, 170, 170), font=f)
        pth = os.path.join(out_dir, 'sheets', f'sec_{sec:02d}.jpg')
        sheet.save(pth, quality=80)
        paths.append(pth)
    return paths


def hit_strips(out_path, X, title, max_hits=60):
    v = X['_v']
    fps = v['fps']
    st = max(1, int(round(fps / 24)))
    tw, th = v['thumb_size']
    hs = [h for h in X['hits'] if h['clear'] and h.get('cause')][:max_hits]
    if not hs:
        return None
    f = _font(13)
    rowh = th + 22
    sheet = Image.new('RGB', (5 * (tw + 4) + 330, len(hs) * rowh + 30), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    d.text((8, 6), title + "   frames -2..+2 (24 fps steps) around each clear hit; green = motion box, blue = centre zone",
           fill=(230, 230, 230), font=f)
    for r, h in enumerate(hs):
        y0 = 28 + r * rowh
        fi = h['cause']['frame']
        for k, o in enumerate(range(-2, 3)):
            j = min(max(0, fi + o * st), v['n'] - 1)
            if j not in v['thumbs']:
                continue
            im = Image.open(io.BytesIO(v['thumbs'][j]))
            x0 = 8 + k * (tw + 4)
            sheet.paste(im, (x0, y0))
            if o == 0:
                b = h['cause']['box']
                d.rectangle([x0 + b[0] * tw, y0 + b[1] * th, x0 + b[2] * tw, y0 + b[3] * th], outline=(60, 255, 60), width=2)
                d.rectangle([x0 + 0.28 * tw, y0 + 0.25 * th, x0 + 0.72 * tw, y0 + 0.75 * th], outline=(80, 140, 255), width=1)
                d.rectangle([x0, y0, x0 + tw - 1, y0 + th - 1], outline=(255, 40, 40), width=2)
        c = h['cause']
        verdict = why_not(c)
        txt = (f"{h['t']:.2f}s  {h['cls']}  {h['peak_db_vs_music']:+.1f} dB\nlock {h['song_onset_dt_ms']} ms  pre {c['preroll_ms']} ms\n"
               f"motion x{c['prom']} spark {c['spark']} area {c['area']}\ncx {c['cx']} cy {c['cy']}  {verdict}")
        if h.get('plan'):
            txt += f"\nplan: {h['plan']}"
        d.multiline_text((8 + 5 * (tw + 4) + 6, y0 + 4), txt, fill=(240, 240, 240) if verdict == 'OK' else (255, 150, 120), font=f)
    sheet.save(out_path, quality=80)
    return out_path


def timeline_png(out_path, C, R):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(18, 7), sharex=False)
    for ax, X, name in ((axes[0], C, C['label']), (axes[1], R, 'reference')):
        s = X['audio']['_series']
        ax.plot(s['t'], s['music_db'], color='0.6', lw=1, label='music RMS (0.6 s)')
        ax.plot(s['t'], s['clip_db'], color='tab:blue', lw=0.6, label='clip layer (20 ms)')
        for h in X['hits']:
            ax.axvline(h['t'], color='red' if h['clear'] else 'orange', lw=1.2 if h['clear'] else 0.7, alpha=0.9)
        for c in X['_cuts_s']:
            ax.plot([c, c], [-75, -68], color='k', lw=1.5)
        vt = s['vt'][s['voiced']]
        ax.plot(vt, np.full(len(vt), -72), '|', color='purple', ms=6)
        for on in X['audio']['song_onsets']:
            ax.plot([on], [-66], marker='v', color='green', ms=3)
        ax.set_ylim(-78, 0)
        ax.set_title(f"{name}: red = clip hit clearly above music, orange = audible, black = cut, green = song onset, purple = voice")
        ax.set_xlabel('s')
    axes[0].legend(loc='upper right', fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=90)
    plt.close(fig)


# ------------------------------------------------------------------------------------------- main
def plan_info(plan_path):
    p = json.load(open(plan_path))
    hits, srcs = [], set()
    for s in p.get('shots', []):
        srcs.add(s.get('source_id') or s.get('path'))
        h = s.get('hit') or {}
        sfx = s.get('sfx') or []
        t = sfx[0].get('hit_at') if sfx and sfx[0].get('hit_at') is not None else h.get('at')
        if t is not None:
            hits.append(dict(t=float(t), silent=bool(h.get('silent')), cls=h.get('cls'), vis_src=s.get('vis_src'),
                             match=(h.get('music_match') or {}).get('clip_sound'), voiced=h.get('voiced'),
                             src=(s.get('source_title') or '')[:40]))
    se = p.get('sound_edit') or {}
    return dict(music=p.get('music'), music_start=p.get('music_start'), bpm=se.get('bpm'), hits=hits,
                cuts=[float(s['t0']) for s in p.get('shots', [])], sources=(len(srcs), len(p.get('shots', []))))


def reference_song(ref_path):
    stem = os.path.splitext(os.path.basename(ref_path))[0]
    cand = os.path.join(os.path.dirname(ref_path), 'analysis', f'{stem}_song_aligned.wav')
    return cand if os.path.exists(cand) else None


def run(args):
    os.makedirs(args.out_dir, exist_ok=True)
    ref = args.reference
    if ref.startswith('http'):
        ref = fetch_reference(ref, args.out_dir)
    plan = plan_info(args.plan) if args.plan else None
    try:
        FG_BAND[0] = bool(args.plan and json.load(open(args.plan)).get('vertical_style') == 'blurfit')
    except Exception:  # noqa: BLE001
        pass
    cand_song = args.cand_song or (plan and plan['music'])
    cand_off = args.cand_song_offset if args.cand_song_offset is not None else (plan and plan['music_start'])
    cand_bpm = args.cand_bpm or (plan and plan['bpm'])
    ref_song = args.ref_song or reference_song(ref)
    ref_aligned = args.ref_song is None and ref_song is not None
    print('[judge] audio: candidate', flush=True)
    ca = analyse_audio(args.candidate, cand_song, cand_off, cand_bpm)
    print('[judge] audio: reference', flush=True)
    ra = analyse_audio(ref, ref_song, args.ref_song_offset, args.ref_bpm, aligned=ref_aligned)

    _, _, cfps, cdur = ffprobe(args.candidate)
    _, _, rfps, rdur = ffprobe(ref)
    cg, rg = ca['grid'], ra['grid']
    nb_ref = max(4, int((rdur - rg['downbeat0']) / (4 * rg['period'])) * 4)

    def mapping(t):
        b = (t - cg['downbeat0']) / cg['period']
        rb = b % nb_ref
        rt = rg['downbeat0'] + rb * rg['period']
        rfi = int(min(max(0, round(rt * rfps)), int(rdur * rfps) - 1))
        return rt, rfi, b

    rng = np.random.default_rng(args.seed)
    ncf = int(cdur * cfps)
    sel = {}
    clear_t = [h['t'] for h in ca['events'] if h['clear']]
    for sec in range(int(np.ceil(cdur))):
        lo, hi = int(sec * cfps), min(ncf, int((sec + 1) * cfps))
        if hi - lo < 4:
            continue
        forced = {int(round(t * cfps)) for t in clear_t if lo <= int(round(t * cfps)) < hi}
        pool = [i for i in range(lo, hi) if i not in forced]
        k = max(0, min(len(pool), args.per_sec - len(forced)))
        sel[sec] = sorted(set(rng.choice(pool, size=k, replace=False).tolist()) | forced)
    st_c = max(1, int(round(cfps / 24)))
    keep_c = set(i for v in sel.values() for i in v)
    for t in clear_t:
        fi = int(round(t * cfps))
        keep_c |= {fi + o * st_c for o in range(-2, 3)}
    print('[judge] picture: reference', flush=True)
    rv = scan_video(ref, set(range(int(rdur * rfps) + 2)))
    print('[judge] picture: candidate', flush=True)
    cvid = scan_video(args.candidate, keep_c)

    out = {}
    ref_id = os.path.splitext(os.path.basename(ref))[0]
    for X, a, v, lab in ((None, ca, cvid, args.label), (None, ra, rv, 'reference')):
        cuts, _ = detect_cuts(v)
        v['cut_source'] = f'detected ({len(cuts)})'
        if lab == 'reference' and ref_id in REF_CUTS:
            cuts = [int(round(c * v['fps'] / 24)) for c in REF_CUTS[ref_id]['cuts']]
            v['cut_source'] = f"verified sound_timing.CUTS ({len(cuts)}); detector {v['cut_source']}"
        elif lab != 'reference' and plan and plan.get('cuts'):
            pc = [int(round(t * v['fps'])) for t in plan['cuts'] if t > 0]
            v['cut_source'] = f"plan shot starts ({len(pc)}); detector {v['cut_source']}"
            cuts = pc
        motion_base(v, cuts)
        pm = picture_metrics(v, cuts)
        hits = []
        for h in a['events']:
            hh = dict(h)
            hh['cause'] = cause_at(v, cuts, h['t'])
            hits.append(hh)
        out[lab] = dict(label=lab, audio=a, picture=pm, hits=hits, _v=v, _cutset=set(cuts),
                        _cuts_s=[c / v['fps'] for c in cuts])
    C, R = out[args.label], out['reference']
    if plan:
        C['plan_sources'] = plan['sources']
        C['plan_hits'] = [(h['t'], h['silent']) for h in plan['hits']]
        C['plan_vis'] = [(h['t'], h['silent'], h.get('vis_src')) for h in plan['hits']]
        for h in C['hits']:
            near = [p for p in plan['hits'] if abs(p['t'] - h['t']) <= 0.05]
            if near:
                p = near[0]
                h['plan'] = f"{'MUTED ' if p['silent'] else ''}{p['vis_src']}/{p['match']} {p['src'][:22]}"
        planned_aud = [p for p in plan['hits'] if not p['silent']]
        found = sum(any(abs(h['t'] - p['t']) <= 0.05 and h['clear'] for h in C['hits']) for p in planned_aud)
        C['plan_check'] = dict(planned_audible=len(planned_aud), planned_picture_only=len(plan['hits']) - len(planned_aud),
                               planned_audible_found_clear=found)
    # visible-cause stats for both
    for X in (C, R):
        ch = [h for h in X['hits'] if h['clear'] and h.get('cause')]
        X['cause_summary'] = dict(n=len(ch), visible=round(float(np.mean([h['cause']['visible'] for h in ch])), 2) if ch else None,
                                  visible_centred=round(float(np.mean([h['cause']['visible_centred'] for h in ch])), 2) if ch else None,
                                  preroll_ms_median=float(np.median([h['cause']['preroll_ms'] for h in ch])) if ch else None)
    card = scorecard(C, R)
    rcard = scorecard(R, R)
    card2 = None
    if getattr(args, 'ref2', None):
        # SECOND reference (owner 2026-10-13: jKSxAkOS3JY, 'Time Back'): scored the same way; the final scores are the MEAN of both cards
        print('[judge] second reference', flush=True)
        ra2 = analyse_audio(args.ref2, args.ref2_song, None, args.ref2_bpm)
        _, _, f2, d2 = ffprobe(args.ref2)
        rv2 = scan_video(args.ref2, set(range(int(d2 * f2) + 2)))
        cuts2, _ = detect_cuts(rv2)
        motion_base(rv2, cuts2)
        R2 = dict(label='reference2', audio=ra2, picture=picture_metrics(rv2, cuts2), _v=rv2, _cuts_s=[c / rv2['fps'] for c in cuts2],
                  hits=[dict(h, cause=cause_at(rv2, cuts2, h['t'])) for h in ra2['events']])
        ch2 = [h for h in R2['hits'] if h['clear'] and h.get('cause')]
        R2['cause_summary'] = dict(n=len(ch2), visible=round(float(np.mean([h['cause']['visible'] for h in ch2])), 2) if ch2 else None,
                                   visible_centred=round(float(np.mean([h['cause']['visible_centred'] for h in ch2])), 2) if ch2 else None,
                                   preroll_ms_median=float(np.median([h['cause']['preroll_ms'] for h in ch2])) if ch2 else None)
        card2 = scorecard(C, R2)
        mean_sc = {k: round(float((card['scores'][k] + card2['scores'][k]) / 2), 1) for k in card['scores']}
        card2['mean_scores'] = mean_sc
        card2['mean_PASS'] = bool(all(v >= 8 for k, v in mean_sc.items() if k != '9_overall_vs_ref') and mean_sc['9_overall_vs_ref'] >= 8.5)
    print('[judge] sheets', flush=True)
    try:
        sheets = make_sheets(args.out_dir, C, R, sel, mapping)
    except Exception as e:  # noqa: BLE001  (a sheet problem must not lose the metrics)
        print('[judge] sheets failed:', e, flush=True)
        sheets = []
    hit_strips(os.path.join(args.out_dir, 'hits_cand.jpg'), C, f"{args.label}: clear hits")
    hit_strips(os.path.join(args.out_dir, 'hits_ref.jpg'), R, "reference: clear hits")
    timeline_png(os.path.join(args.out_dir, 'timeline.png'), C, R)
    import soundfile as sf
    sf.write(os.path.join(args.out_dir, 'cand_cliplayer.wav'), C['audio']['_res'], SR)
    sf.write(os.path.join(args.out_dir, 'ref_cliplayer.wav'), R['audio']['_res'], SR)

    def strip(X):
        a = {k: v for k, v in X['audio'].items() if not k.startswith('_')}
        return dict(audio=a, picture=X['picture'], hits=X['hits'], cause_summary=X['cause_summary'],
                    plan_check=X.get('plan_check'), cuts_s=[round(c, 3) for c in X['_cuts_s']],
                    cut_source=X['_v'].get('cut_source'), motion_base=X['_v'].get('motion_base'))
    rep = dict(candidate=args.candidate, reference=ref, label=args.label, grid_candidate=cg, grid_reference=rg,
               ref_loop_beats=nb_ref, scorecard=card, reference_self_scorecard=rcard,
               cand=strip(C), ref=strip(R), sheets=sheets, scorecard_ref2=card2)
    with open(os.path.join(args.out_dir, 'report.json'), 'w') as fh:
        json.dump(rep, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o))
    write_md(os.path.join(args.out_dir, 'report.md'), rep)
    print(json.dumps(card['scores'], indent=1), 'PASS' if card['PASS'] else 'FAIL')
    if card2:
        print('[judge] vs reference 2:', json.dumps(card2['scores']))
        print('[judge] MEAN of both:', json.dumps(card2['mean_scores']), 'PASS' if card2['mean_PASS'] else 'FAIL')
    print('[judge] reference self-score', json.dumps(rcard['scores']))
    return rep


def write_md(path, rep):
    c, r = rep['cand'], rep['ref']
    L = [f"# Sound-edit judge: {rep['label']}", '', f"candidate `{rep['candidate']}`  ", f"reference `{rep['reference']}`", '',
         f"**{'PASS' if rep['scorecard']['PASS'] else 'FAIL'}** (bar: {rep['scorecard']['pass_bar']})", '',
         '| criterion | ours | ref (self) | evidence |', '|---|---|---|---|']
    for k, v in rep['scorecard']['scores'].items():
        L.append(f"| {k} | {v} | {rep['reference_self_scorecard']['scores'].get(k, 'n/a')} | {rep['scorecard']['evidence'].get(k, '')} |")
    L += ['', '## Key numbers (ours / ref)', '', '| measure | ours | ref |', '|---|---|---|']
    for k in ('duration_s', 'n_audible', 'n_clear', 'audible_per_s', 'clear_per_s', 'clear_share_of_audible', 'longest_gap_clear',
              'clip_layer_present_pct', 'between_hits_clip_db_vs_music', 'lock_abs_median_ms', 'lock_within40_pct',
              'strong_song_onsets_with_clear_hit_pct', 'clear_peak_db_vs_music_p25_50_75', 'attack_db_median', 'decay12_ms_median',
              'voiced_hit_share', 'voice_share_of_clip_time', 'classes_clear', 'loudness', 'song_removed_db',
              'transients_on_onset_share', 'drum_onsets_per_s', 'drum_onsets_recreated_share'):
        L.append(f"| {k} | {c['audio'].get(k)} | {r['audio'].get(k)} |")
    for k in ('n_shots', 'cuts_per_s', 'still', 'shot_s_p10_50_90', 'shot_cv', 'luma_median', 'sat_median', 'sharp_median', 'colour_spread',
              'overlay', 'black_bar_rows_pct'):
        L.append(f"| {k} | {c['picture'].get(k)} | {r['picture'].get(k)} |")
    L.append(f"| visible cause (clear hits) | {c['cause_summary']} | {r['cause_summary']} |")
    if c.get('plan_check'):
        L.append(f"| plan check | {c['plan_check']} | |")
    L += ['', '## Clear / audible hits (ours)', '', '| t | clear | dB vs music | class | lock ms | pre-roll ms | cause | plan |', '|---|---|---|---|---|---|---|---|']
    for h in c['hits']:
        cz = h.get('cause') or {}
        L.append(f"| {h['t']} | {'Y' if h['clear'] else '-'} | {h['peak_db_vs_music']} | {h['cls']} | {h['song_onset_dt_ms']} | "
                 f"{cz.get('preroll_ms')} | {'OK' if cz.get('visible_centred') else 'x'} cx{cz.get('cx')} m{cz.get('prom')} | {h.get('plan', '')} |")
    L += ['', 'Evidence: `sheets/sec_XX.jpg`, `hits_cand.jpg`, `hits_ref.jpg`, `timeline.png`, `cand_cliplayer.wav`, `ref_cliplayer.wav`.']
    open(path, 'w', encoding='utf-8').write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('candidate')
    ap.add_argument('reference')
    ap.add_argument('out_dir')
    ap.add_argument('--plan')
    ap.add_argument('--label', default='candidate')
    ap.add_argument('--cand-song')
    ap.add_argument('--cand-song-offset', type=float)
    ap.add_argument('--cand-bpm', type=float)
    ap.add_argument('--ref-song')
    ap.add_argument('--ref-song-offset', type=float)
    ap.add_argument('--ref-bpm', type=float, default=None)
    ap.add_argument('--ref2', default=None, help='second reference video (e.g. edit-refs/sound/jKSxAkOS3JY.mp4); scored the same way, mean of both')
    ap.add_argument('--ref2-song', default=None)
    ap.add_argument('--ref2-bpm', type=float, default=None)
    ap.add_argument('--per-sec', type=int, default=24)
    ap.add_argument('--seed', type=int, default=1)
    run(ap.parse_args())


if __name__ == '__main__':
    main()
