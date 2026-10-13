#!/usr/bin/env python3
"""
Timing study of an "anime sound edit": how the clips' own sounds and the cuts
sit on the song.

Inputs (per reference id):
  <ana>/<id>.json        frame analysis from sound_analyze.py (per-frame series, fps)
  <ana>/<id>_audio.json  alignment of the edit audio with the ORIGINAL song
                         (song, speed, song_offset_s, fine_lag_ms) - optional
  songs/<song>.m4a       the original song
  CUTS[id]               editor cuts verified frame by frame (first frame of the new shot)

What it measures
  - the song's beat grid (constant tempo fitted on the original song's
    percussive onsets) and downbeats, mapped into edit time
  - the song's own onsets (strength-ranked) in edit time
  - the CLIP-SOUND layer: edit audio minus the aligned original song
    (complex STFT least squares per band per 0.25 s), and its onsets
  - per shot: pre-roll = strongest clip-sound transient after the cut, and the
    visual action frame (largest picture change inside the shot); how each
    lands on the song onsets / 16th grid; shot length in 16ths; the cut's
    place in the bar
  - mix: clip sound vs music level, music ducking at cuts

    python sound_timing.py ID [ID ...] --ref DIR/edit-refs/sound
Writes <ana>/<id>_timing.json and prints a summary.
"""
import argparse
import json
import subprocess

import librosa
import numpy as np
from scipy.ndimage import uniform_filter1d

SR = 22050
HOP = 128

# editor cuts, verified by eye on the frame sheets (first frame of each new shot).
# dissolve: cut frame -> length of the blend in frames
CUTS = {
    'FB15TNFAHVU': dict(cuts=[11, 23, 31, 42, 50, 59, 70, 81, 90, 99, 111, 119, 129, 141, 151, 162, 171, 181, 190, 201, 209],
                        dissolve={11: 2, 90: 3}),
    'N1gQySyUGXk': dict(cuts=[11, 23, 31, 42, 50, 59, 70, 81, 90, 99, 111, 119, 129, 141, 151, 162, 171, 181, 190, 201, 209],
                        dissolve={11: 2, 90: 3}),
    'jKSxAkOS3JY': dict(cuts=[24, 47, 67, 79, 96, 111, 126, 154, 186, 200, 216, 243, 257, 277, 303, 320, 334, 354, 364, 377,
                              394, 410, 425, 438, 469, 484, 497], dissolve={}),
    'n_NvC1IQUMQ': dict(cuts=[9, 24, 31, 41, 50, 59, 68, 79, 90, 98, 113, 126, 135, 143, 153, 162, 170, 174, 179, 185, 194,
                              203, 212, 238, 243, 253, 260], dissolve={}),
    'qgzBYo7gvhE': dict(cuts=[30, 46, 53, 62, 70, 78, 88, 95, 101, 108, 119, 129, 141, 150, 152, 160, 169, 176, 189, 192, 199],
                        dissolve={}),
    'h0HNYocSDE8': dict(cuts=[15, 27, 44, 59, 71, 89, 104, 115, 135, 146, 175, 183, 196, 208, 219, 226, 242, 252],
                        dissolve={}),
}
BPM_HINT = {'shape_of_you': 96.0, 'time_back': 120.0, 'fake': 85.0, 'handclap': 140.0, 'wellerman': 96.0}


def load(path, sr=SR):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'],
                       capture_output=True)
    return np.frombuffer(r.stdout, np.float32).copy()


def grid_fit(y, bpm0, lo=0.985, hi=1.015):
    """Constant beat grid on the percussive onset envelope: period and phase (s)."""
    oe = librosa.onset.onset_strength(y=librosa.effects.percussive(y), sr=SR, hop_length=HOP)
    ot = np.arange(len(oe)) * HOP / SR
    e = oe / (oe.max() + 1e-9)
    best = (-1, 60 / bpm0, 0)
    for P in (60 / bpm0) * np.linspace(lo, hi, 61):
        ph = np.arange(0, P, 0.001)
        k = np.arange(int(ot[-1] / P))
        sc = np.interp(ph[:, None] + k[None] * P, ot, e).mean(axis=1)
        i = int(np.argmax(sc))
        if sc[i] > best[0]:
            best = (sc[i], P, ph[i])
    return best[1], best[2]


def downbeat_phase(y, P, ph):
    """Which beat of 4 is the downbeat: low-end attack + harmonic change."""
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    fr = librosa.fft_frequencies(sr=SR, n_fft=2048)
    st = np.arange(S.shape[1]) * 512 / SR
    low = np.concatenate([[0], np.maximum(0, np.diff(np.log1p(S[fr < 120]), axis=1)).sum(axis=0)])
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=SR)
    grid = ph + np.arange(int(st[-1] / P)) * P
    idx = np.searchsorted(st, grid)
    idx = idx[idx < S.shape[1] - 4]
    hc = np.zeros(len(idx))
    for i in range(2, len(idx) - 2):
        a = chroma[:, idx[i - 2]:idx[i]].mean(axis=1)
        b = chroma[:, idx[i]:idx[i + 2]].mean(axis=1)
        hc[i] = 1 - a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    lowb = np.array([low[j:j + 3].max() for j in idx])
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    feat = z(hc) + 0.7 * z(lowb)
    return int(np.argmax([feat[p::4].mean() for p in range(4)]))


def split_song(ry, seg):
    """Clip-sound layer = edit - g(band, t) * aligned song (complex least squares, 0.25 s, 6 bands)."""
    n = min(len(seg), len(ry))
    Rx = librosa.stft(ry[:n], n_fft=1024, hop_length=HOP)
    Sx = librosa.stft(seg[:n], n_fft=1024, hop_length=HOP)
    frq = librosa.fft_frequencies(sr=SR, n_fft=1024)
    edges = [0, 150, 400, 1000, 2500, 6000, 11025]
    Res = Rx.copy()
    gains = np.zeros((len(edges) - 1, Rx.shape[1]))
    win = int(0.25 * SR / HOP)
    for bi in range(len(edges) - 1):
        m = (frq >= edges[bi]) & (frq < edges[bi + 1])
        num = np.real((np.conj(Sx[m]) * Rx[m]).sum(axis=0))
        den = (np.abs(Sx[m]) ** 2).sum(axis=0)
        g = np.clip(uniform_filter1d(num, win) / (uniform_filter1d(den, win) + 1e-9), 0, 10)
        gains[bi] = g
        Res[m] = Rx[m] - g[None] * Sx[m]
    res_y = librosa.istft(Res, hop_length=HOP, length=n)
    song_y = librosa.istft(Rx - Res, hop_length=HOP, length=n)
    removed_db = float(10 * np.log10((np.abs(Rx) ** 2).sum() / ((np.abs(Res) ** 2).sum() + 1e-9)))
    return res_y, song_y, gains, removed_db


def onsets_with_strength(y, delta=0.12, wait_s=0.06):
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    fr = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=HOP, backtrack=False, delta=delta,
                                    wait=max(1, int(wait_s * SR / HOP)))
    t = fr * HOP / SR
    s = env[fr] / (np.percentile(env, 95) + 1e-9)
    return t, s, env


def near(t, arr):
    arr = np.asarray(arr, float)
    if not len(arr):
        return None, None
    j = int(np.argmin(np.abs(arr - t)))
    return float(t - arr[j]), j


def main(vid, ref, song_override=None):
    ana = ref + '/analysis'
    F = json.load(open(f'{ana}/{vid}.json', encoding='utf-8'))
    try:
        A = json.load(open(f'{ana}/{vid}_audio.json', encoding='utf-8'))
    except FileNotFoundError:
        A = {}
    song = song_override or A.get('song')
    fps = F['fps']
    V = CUTS[vid]
    cuts = V['cuts']
    ry = load(f'{ref}/{vid}.mp4')
    L = len(ry) / SR
    sy = load(f'{ref}/songs/{song}.m4a')
    aligned = A.get('aligned', True) and 'song_offset_s' in A
    if aligned:
        r, s0, lag = A['speed'], A['song_offset_s'], A['fine_lag_ms'] / 1000
    else:
        r, s0, lag = 1.0, A.get('song_offset_s', 0.0), 0.0
    to_edit = lambda ts: (np.asarray(ts) - s0) / r + lag
    # ---- grid
    if A.get('waveform_match', True):
        P, ph = grid_fit(sy, BPM_HINT.get(song, A.get('song_tempo_bpm', 120)))
        dph = downbeat_phase(sy, P, ph)
        grid = ph + np.arange(int(len(sy) / SR / P)) * P
        beats_e = to_edit(grid)
        downs_e = to_edit(grid[dph::4])
        Pe = P / r
    else:
        # different master of the song: grid from the edit's own audio (low band = kick)
        P, ph = grid_fit(ry, BPM_HINT.get(song, 120), 0.99, 1.01)
        dph = downbeat_phase(ry, P, ph)
        grid = ph + np.arange(-4, int(L / P) + 4) * P
        beats_e = grid
        downs_e = grid[(dph + 4) % 4::4]
        Pe = P
    keep = (beats_e > -Pe) & (beats_e < L + Pe)
    beats_e = beats_e[keep]
    downs_e = downs_e[(downs_e > -4 * Pe) & (downs_e < L + 4 * Pe)]
    q16 = np.sort(np.concatenate([beats_e + k * Pe / 4 for k in range(4)]))
    # ---- song onsets (edit time) and clip-sound layer
    if A.get('waveform_match', True):
        son_t, son_s, _ = onsets_with_strength(sy)
        son_e = to_edit(son_t)
        m = (son_e >= -0.05) & (son_e <= L + 0.05)
        son_e, son_s = son_e[m], son_s[m]
        seg = load(f'{ana}/{vid}_song_aligned.wav')
        res_y, song_y, gains, removed_db = split_song(ry, seg)
    else:
        son_e, son_s = np.zeros(0), np.zeros(0)
        res_y, song_y, gains, removed_db = ry.copy(), np.zeros_like(ry), None, 0.0
    strong = son_e[son_s >= np.percentile(son_s, 50)] if len(son_s) else np.zeros(0)
    ev_t, ev_s, _ = onsets_with_strength(res_y, delta=0.2, wait_s=0.05)
    rms_res = librosa.feature.rms(y=res_y, frame_length=1024, hop_length=HOP)[0]
    rms_song = librosa.feature.rms(y=song_y, frame_length=1024, hop_length=HOP)[0] if len(song_y) else np.zeros_like(rms_res)
    fi = lambda t: int(np.clip(round(t * SR / HOP), 0, len(rms_res) - 1))
    db = lambda x: 20 * np.log10(x + 1e-6)
    # ---- visual action frame per shot
    diff = np.array(F['series']['diff'])
    luma = np.array(F['series']['luma'])
    n = len(diff)
    fr_ms = 1000 / fps
    bounds = [0] + cuts + [n]
    shots = []
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        t0 = a / fps
        row = {'i': i, 'start': a, 'end': b, 'frames': b - a, 't': round(t0, 3), 'dur_s': round((b - a) / fps, 3),
               'len_16ths': round((b - a) / fps / (Pe / 4), 2)}
        if i > 0:
            ob, _ = near(t0, beats_e)
            o16, _ = near(t0, q16)
            row.update({'cut_to_beat_ms': round(ob * 1000), 'cut_to_16th_ms': round(o16 * 1000),
                        'pos16_in_bar': int(round((t0 - downs_e[0]) / (Pe / 4))) % 16})
            if len(son_e):
                oso, j = near(t0, son_e)
                row['cut_to_song_onset_ms'] = round(oso * 1000)
                os_, _ = near(t0, strong)
                row['cut_to_strong_onset_ms'] = round(os_ * 1000) if os_ is not None else None
        # visual action: biggest frame change inside the shot (skip the cut frame itself), first 0.6 s
        lo, hi = a + 1, min(b, a + 1 + int(0.6 * fps))
        if hi > lo:
            k = lo + int(np.argmax(diff[lo:hi]))
            row['action_frame_offset'] = k - a
            row['action_diff'] = round(float(diff[k]), 1)
            row['action_diff_over_shot_median'] = round(float(diff[k] / (np.median(diff[lo:b]) + 1e-3)), 2)
            row['action_to_16th_ms'] = round(near(k / fps, q16)[0] * 1000)
            if len(son_e):
                row['action_to_song_onset_ms'] = round(near(k / fps, son_e)[0] * 1000)
        # clip-sound transient: strongest clip-sound onset from 2 frames before the cut to 0.45 s after (inside shot)
        w0, w1 = t0 - 2 / fps, min(b / fps, t0 + 0.45)
        sel = np.where((ev_t >= w0) & (ev_t < w1))[0]
        if len(sel):
            j = sel[int(np.argmax(ev_s[sel]))]
            first = sel[0]
            te = float(ev_t[j])
            row['sfx_t'] = round(te, 3)
            row['sfx_preroll_ms'] = round((te - t0) * 1000)
            row['first_sfx_preroll_ms'] = round((float(ev_t[first]) - t0) * 1000)
            row['sfx_strength'] = round(float(ev_s[j]), 2)
            row['sfx_to_16th_ms'] = round(near(te, q16)[0] * 1000)
            row['sfx_to_beat_ms'] = round(near(te, beats_e)[0] * 1000)
            if len(son_e):
                row['sfx_to_song_onset_ms'] = round(near(te, son_e)[0] * 1000)
                row['sfx_to_strong_onset_ms'] = round(near(te, strong)[0] * 1000) if len(strong) else None
            i0 = fi(te)
            pk = rms_res[i0:i0 + int(0.12 * SR / HOP)].max()
            mu = rms_song[max(0, i0 - int(0.3 * SR / HOP)):i0 + int(0.3 * SR / HOP)].mean()
            row['sfx_peak_db_vs_music'] = round(float(db(pk) - db(mu)), 1) if mu > 0 else None
            # decay: time until the clip sound falls 12 dB below its peak
            tail = rms_res[i0:i0 + int(1.0 * SR / HOP)]
            kk = np.where(tail < pk * 0.25)[0]
            row['sfx_decay_12db_ms'] = round(float(kk[kk > np.argmax(tail)][0] * HOP / SR * 1000)) if len(kk[kk > np.argmax(tail)]) else None
        # clip sound spectral centre in the shot
        a_s, b_s = int(t0 * SR), int(b / fps * SR)
        if b_s - a_s > 512:
            cen = librosa.feature.spectral_centroid(y=res_y[a_s:b_s], sr=SR, hop_length=512)[0]
            row['clip_sound_centroid_hz'] = round(float(np.median(cen)))
            row['clip_sound_db_vs_music'] = round(float(db(rms_res[fi(t0):fi(b / fps)].mean()) -
                                                       db(rms_song[fi(t0):fi(b / fps)].mean() + 1e-9)), 1) if rms_song.any() else None
        row['luma'] = round(float(luma[a:b].mean()), 3)
        shots.append(row)
    # ---- audio continuity at cuts: does the clip sound start / stop with the picture?
    cont = []
    for c in cuts:
        t = c / fps
        i0 = fi(t)
        w = int(0.06 * SR / HOP)
        before = rms_res[max(0, i0 - w):i0].mean()
        after = rms_res[i0:i0 + w].mean()
        cont.append(db(after) - db(before))
    # ---- music ducking under the clip sounds
    if gains is not None:
        gm = np.sqrt((gains[:4] ** 2).mean(axis=0))
        gdb = db(gm) - db(np.median(gm))
        duck = []
        for c in cuts:
            i0 = fi(c / fps)
            duck.append(float(np.mean(gdb[i0:i0 + 20]) - np.mean(gdb[max(0, i0 - 40):max(1, i0 - 5)])))
        # correlation of music gain with clip-sound level (negative = ducking)
        lvl = db(uniform_filter1d(rms_res[:len(gdb)], 20))
        ok = np.isfinite(lvl) & np.isfinite(gdb[:len(lvl)])
        duck_corr = float(np.corrcoef(lvl[ok], gdb[:len(lvl)][ok])[0, 1])
    else:
        duck, duck_corr, gdb = [], None, np.zeros(1)
    col = lambda k: np.array([s[k] for s in shots if s.get(k) is not None], float)
    inner = shots[1:-1]
    L16 = np.array([s['len_16ths'] for s in inner])
    summ = {
        'id': vid, 'song': song, 'waveform_match': A.get('waveform_match', True), 'song_removed_db': round(removed_db, 2),
        'bpm': round(60 / Pe, 2), 'beat_s': round(Pe, 4), 'sixteenth_ms': round(Pe / 4 * 1000, 1), 'fps': fps,
        'edit_len_s': round(L, 3), 'edit_len_bars': round(L / (4 * Pe), 2), 'song_offset_s': round(float(s0), 3),
        'first_downbeat_s': round(float(downs_e[downs_e >= -0.05][0]), 3),
        'n_cuts': len(cuts), 'cuts_per_s': round(len(cuts) / L, 2), 'cuts_per_bar': round(len(cuts) / (L / (4 * Pe)), 2),
        'first_shot_s': shots[0]['dur_s'], 'last_shot_s': shots[-1]['dur_s'],
        'shot_frames_median': float(np.median([s['frames'] for s in inner])),
        'shot_s_median': round(float(np.median([s['dur_s'] for s in inner])), 3),
        'shot_16ths_median': round(float(np.median(L16)), 2),
        'shot_16ths_hist': {int(k): int(v) for k, v in zip(*np.unique(np.round(L16).astype(int), return_counts=True))},
        'pos16_hist': {int(k): int(v) for k, v in zip(*np.unique([s['pos16_in_bar'] for s in shots[1:]], return_counts=True))},
        'cut_to_16th_abs_ms_median': round(float(np.median(np.abs(col('cut_to_16th_ms'))))),
        'cut_to_16th_ms_median': round(float(np.median(col('cut_to_16th_ms')))),
        'cuts_within_1frame_of_16th_pct': round(float(np.mean(np.abs(col('cut_to_16th_ms')) <= fr_ms * 1.01) * 100)),
        'action_frame_offset_median': float(np.median(col('action_frame_offset'))),
        'action_frame_offset_hist': {int(k): int(v) for k, v in zip(*np.unique(col('action_frame_offset').astype(int), return_counts=True))},
        'action_to_16th_abs_ms_median': round(float(np.median(np.abs(col('action_to_16th_ms'))))),
        'shots_with_sfx_pct': round(100 * np.mean(['sfx_t' in s for s in shots])),
        'sfx_preroll_ms_median': round(float(np.median(col('sfx_preroll_ms')))) if len(col('sfx_preroll_ms')) else None,
        'sfx_preroll_ms_p25_p75': [round(float(np.percentile(col('sfx_preroll_ms'), q))) for q in (25, 75)] if len(col('sfx_preroll_ms')) else None,
        'first_sfx_preroll_ms_median': round(float(np.median(col('first_sfx_preroll_ms')))) if len(col('first_sfx_preroll_ms')) else None,
        'sfx_to_16th_abs_ms_median': round(float(np.median(np.abs(col('sfx_to_16th_ms'))))) if len(col('sfx_to_16th_ms')) else None,
        'sfx_to_16th_ms_median': round(float(np.median(col('sfx_to_16th_ms')))) if len(col('sfx_to_16th_ms')) else None,
        'sfx_within_30ms_of_16th_pct': round(float(np.mean(np.abs(col('sfx_to_16th_ms')) <= 30) * 100)) if len(col('sfx_to_16th_ms')) else None,
        'sfx_peak_db_vs_music_median': round(float(np.median(col('sfx_peak_db_vs_music'))), 1) if len(col('sfx_peak_db_vs_music')) else None,
        'sfx_decay_12db_ms_median': round(float(np.median(col('sfx_decay_12db_ms')))) if len(col('sfx_decay_12db_ms')) else None,
        'clip_sound_db_vs_music_median': round(float(np.median(col('clip_sound_db_vs_music'))), 1) if len(col('clip_sound_db_vs_music')) else None,
        'clip_sound_centroid_hz_median': round(float(np.median(col('clip_sound_centroid_hz')))) if len(col('clip_sound_centroid_hz')) else None,
        'clip_sound_level_jump_at_cut_db_median': round(float(np.median(cont)), 1),
        'music_gain_change_after_cut_db_median': round(float(np.median(duck)), 2) if duck else None,
        'music_gain_vs_clip_sound_corr': round(duck_corr, 3) if duck_corr is not None else None,
        'music_gain_db_p10_p90': [round(float(np.percentile(gdb, q)), 1) for q in (10, 90)],
        'n_clip_sound_onsets': int(len(ev_t)), 'clip_sound_onsets_per_s': round(len(ev_t) / L, 2),
        'clip_onsets_within_30ms_of_16th_pct': round(float(np.mean([abs(near(t, q16)[0]) <= 0.03 for t in ev_t]) * 100)) if len(ev_t) else None,
    }
    if len(son_e):
        summ.update({
            'cut_to_song_onset_ms_median': round(float(np.median(col('cut_to_song_onset_ms')))),
            'cut_to_song_onset_abs_ms_median': round(float(np.median(np.abs(col('cut_to_song_onset_ms'))))),
            'action_to_song_onset_abs_ms_median': round(float(np.median(np.abs(col('action_to_song_onset_ms'))))),
            'sfx_to_song_onset_ms_median': round(float(np.median(col('sfx_to_song_onset_ms')))) if len(col('sfx_to_song_onset_ms')) else None,
            'sfx_to_song_onset_abs_ms_median': round(float(np.median(np.abs(col('sfx_to_song_onset_ms'))))) if len(col('sfx_to_song_onset_ms')) else None,
            'sfx_within_40ms_of_song_onset_pct': round(float(np.mean(np.abs(col('sfx_to_song_onset_ms')) <= 40) * 100)) if len(col('sfx_to_song_onset_ms')) else None,
            'cut_within_40ms_of_song_onset_pct': round(float(np.mean(np.abs(col('cut_to_song_onset_ms')) <= 40) * 100)),
            'strong_song_onsets_per_s': round(len(strong) / L, 2),
            'strong_song_onsets_covered_by_sfx_pct': round(float(np.mean([np.min(np.abs(ev_t - s)) <= 0.04 for s in strong]) * 100)) if len(ev_t) and len(strong) else None,
            'strong_song_onsets_covered_by_cut_pct': round(float(np.mean([np.min(np.abs(np.array(cuts) / fps - s)) <= 0.13 for s in strong]) * 100)),
        })
    out = {'summary': summ, 'shots': shots, 'beats': np.round(beats_e, 4).tolist(), 'downbeats': np.round(downs_e, 4).tolist(),
           'song_onsets': np.round(son_e, 3).tolist(), 'song_onset_strength': np.round(son_s, 2).tolist(),
           'clip_sound_onsets': np.round(ev_t, 3).tolist(), 'clip_sound_onset_strength': np.round(ev_s, 2).tolist()}
    json.dump(out, open(f'{ana}/{vid}_timing.json', 'w', encoding='utf-8'), indent=1)
    import soundfile as sf
    sf.write(f'{ana}/{vid}_clipsounds.wav', res_y, SR)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('ids', nargs='+')
    ap.add_argument('--ref', default='C:/Users/Home/AppData/Local/Temp/edit-refs/sound')
    a = ap.parse_args()
    for v in a.ids:
        o = main(v, a.ref)
        print(json.dumps(o['summary']))
