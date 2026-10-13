"""
Second opinion on sung-word onsets (cinematic_slowmo remake round 3).

The CTC aligner in lyrics.py can land a word 0.4-0.7 s late on a long held syllable (the previous word's melisma
swallows the next onset: 'blue' @0.95, word 4 aligned at 4.30 s while the stem's harmonic onset is 3.60 and
faster-whisper medium says 3.72). This module cross-checks every aligned word against faster-whisper word
timestamps on the same vocal stem and snaps a disagreeing word to the strongest harmonic-band onset (HPSS
harmonic part, 250-3500 Hz log flux) just before / at the recogniser's time. Words the two agree on are kept.
Word text is never logged (content rule): words are referred to by index and time.

    ws2, log = crosscheck(ws, audio_path, a, b)      # ws = lyrics.words(...) output (times of audio_path)
"""
import hashlib
import json
import os

import numpy as np

CACHE = os.path.join(os.environ.get('TEMP') or '/tmp', 'edit-cache', 'lyrics', 'xcheck')


def _norm(s):
    return ''.join(ch for ch in str(s).lower() if ch.isalnum())


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def harmonic_onsets(v, sr, hop=80):
    """(times, prominences) of harmonic-band onsets of a vocal stem (s from the stem's start)."""
    import librosa
    from scipy.signal import find_peaks
    h = librosa.effects.harmonic(v, margin=2.0)
    S = np.abs(librosa.stft(h, n_fft=1024, hop_length=hop))
    f = librosa.fft_frequencies(sr=sr, n_fft=1024)
    band = (f >= 250) & (f <= 3500)
    L = np.log1p(10 * S[band])
    flux = np.maximum(0, np.diff(L, axis=1)).sum(0)
    flux /= np.percentile(flux, 99) + 1e-9
    pk, pr = find_peaks(flux, distance=max(1, int(0.08 * sr / hop)), prominence=0.15)
    return pk * hop / sr, pr['prominences']


def _whisper(v, sr, key, model='medium', threads=4):
    p = os.path.join(CACHE, f'{key}-{model}.json')
    nostore = os.environ.get('LYRICS_NO_STORE', '0') == '1'     # lyrics / heard words never touch the disk
    if os.path.exists(p) and not nostore:
        try:
            return json.load(open(p, encoding='utf-8'))
        except (OSError, ValueError):
            pass
    from faster_whisper import WhisperModel
    if sr != 16000:
        from scipy.signal import resample_poly
        v = resample_poly(v, 16000, sr).astype(np.float32)
    m = WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=threads)
    segs, _ = m.transcribe(v, language='en', word_timestamps=True, vad_filter=False)
    out = [dict(t0=float(w.start), t1=float(w.end), text=w.word.strip(), p=float(w.probability))
           for s in segs for w in (s.words or [])]
    if nostore:
        return out
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    return out


def crosscheck(ws, audio_path, a, b, threads=4, model='medium', tol=0.22, back=0.35, fwd=0.10, min_sim=0.5):
    """ws: words with t_start / t_end in audio_path seconds. Returns (new words, log). A word moves only when the
    recogniser's matching word starts more than `tol` s away AND a harmonic onset lies in [fw - back, fw + fwd];
    the order of words is kept (each start >= previous + 0.12 s)."""
    import lyrics as LY
    if not ws:
        return ws, []
    pad = 1.0
    a0 = max(0.0, float(a) - pad)
    v = LY.vocal_stem(audio_path, a0, float(b) + pad, threads)
    sr = LY.SR
    st = os.stat(audio_path)
    key = hashlib.sha1(f'{os.path.abspath(audio_path)}|{st.st_size}|{a0:.3f}|{float(b):.3f}'.encode()).hexdigest()[:20]
    try:
        fw = _whisper(v, sr, key, model, threads)
    except Exception as e:  # noqa: BLE001
        return ws, [dict(error=f'whisper: {e}')]
    on, prom = harmonic_onsets(v, sr)
    on = on + a0
    fw = [dict(h, t0=h['t0'] + a0, t1=h['t1'] + a0) for h in fw]
    out, log, used = [dict(w) for w in ws], [], set()
    for i, w in enumerate(out):
        t = float(w['t_start'])
        cands = [(j, _sim(w['text'], h['text'])) for j, h in enumerate(fw) if j not in used and abs(h['t0'] - t) < 1.2]
        cands = [c for c in cands if c[1] >= min_sim]
        if not cands:
            log.append(dict(i=i, t=round(t, 3), fw=None))
            continue
        j = max(cands, key=lambda c: (c[1], -abs(fw[c[0]]['t0'] - t)))[0]
        used.add(j)
        ft = fw[j]['t0']
        rec = dict(i=i, t=round(t, 3), fw=round(ft, 3), diff_ms=round((ft - t) * 1000))
        if abs(ft - t) > tol:
            m = (on >= ft - back) & (on <= ft + fwd)
            if m.any():
                k = int(np.argmax(np.where(m, prom, -1)))
                nt = float(on[k])
                lo = float(out[i - 1]['t_start']) + 0.12 if i else -1e9
                if nt > lo:
                    shift = nt - t
                    w['t_start'] = round(nt, 4)
                    w['t_end'] = round(max(nt + 0.08, float(w['t_end']) + max(0.0, shift)), 4)
                    if i:
                        out[i - 1]['t_end'] = round(min(float(out[i - 1]['t_end']), nt - 0.01), 4)
                    rec['moved_to'] = round(nt, 3)
        log.append(rec)
    for i in range(1, len(out)):                  # keep the order
        if out[i]['t_start'] < out[i - 1]['t_start'] + 0.12:
            out[i]['t_start'] = round(out[i - 1]['t_start'] + 0.12, 4)
    return out, log
