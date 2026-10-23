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



# ------------------------------------------------------------------------------------------------ intro ensemble
# 2026-10-23 (owner on blue v2: "the lyrics is still a bit off"). Measured per word on the rendered file (Demucs vocals,
# wav2vec2 base + lv60k CTC on the exact lyric words, energy / timbre / brightness evidence): the words on black showed
# -0.07, 0.00, +0.84, +0.41, +0.81 s from their sung onsets. Root cause: on a held, legato line the CTC aligner (and the
# whisper cross-check above, whose times sit on the vowel opening) put a word on a LATER note of the melisma - the
# blank state absorbs the held vowel and the letters are emitted at its end. The two CTC models fail on different words
# (base: a word at its final consonant; lv60k: the last word crammed at the window end), and never early. The fix: per
# intro word, the candidates (current time, base CTC, lv60k CTC on the exact words) and the stem's acoustic word-start
# events (energy onset after a dip, timbre change); a monotone DP picks one event per word, cost = distance to the
# nearest candidate + LATE_W x (event - earliest candidate) (the failure mode is late) + a little for weak events.

LATE_W = 0.30
ENS_MIN_GAP = 0.15
ENS_FAR = 0.35


def _events(v, sr):
    """sorted [(time s from the stem's start, strength 0..1)] of word-start evidence on a vocal stem."""
    import librosa
    from scipy.signal import find_peaks
    import lyrics as LY
    on, prom, env, rms, hop = LY.vocal_onsets(v)
    ev = [(float(t), min(1.0, float(p) / 0.6)) for t, p in zip(on, prom) if p >= 0.12]
    M = librosa.feature.mfcc(y=v, sr=sr, n_mfcc=13, n_fft=640, hop_length=160)[1:]
    M = (M - M.mean(1, keepdims=True)) / (M.std(1, keepdims=True) + 1e-9)
    k = 6
    d = np.zeros(M.shape[1])
    for i in range(k, M.shape[1] - k):
        d[i] = np.linalg.norm(M[:, i:i + k].mean(1) - M[:, i - k:i].mean(1))
    lv = librosa.feature.rms(y=v, frame_length=640, hop_length=160)[0]
    lv = 20 * np.log10(lv / (np.percentile(lv, 95) + 1e-9) + 1e-9)
    pk, _ = find_peaks(d, distance=8, prominence=0.5)
    for p in pk:
        if d[p] >= 2.0 and lv[min(len(lv) - 1, p + 3)] > -30:
            ev.append((p * 0.01, min(1.0, (float(d[p]) - 1.5) / 2.5)))

    def _db(t):
        return float(lv[int(np.clip(round(t / 0.01), 0, len(lv) - 1))])
    # a word starts where the voice rises or holds, never inside a decay (a final consonant / the fall into a gap)
    ev = [(t, s_) for t, s_ in ev if _db(t + 0.06) - _db(t - 0.06) >= -2.5 and _db(t + 0.04) > -30]
    # ... and not in the middle of one held note (same pitch on both sides, no dip before): golden hour 3j, a held word's
    # CTC 'end' 0.5 s before the note ended pulled the next word into the hold. A strong consonant (timbre) still counts.
    try:
        f0, _, _ = librosa.pyin(v, fmin=70, fmax=1000, sr=sr, frame_length=1024, hop_length=320)
        st = 12 * np.log2(f0 / 440.0) + 69

        def _med(a_, b_):
            q = st[max(0, int(a_ / 0.02)):max(0, int(b_ / 0.02))]
            q = q[~np.isnan(q)]
            return float(np.median(q)) if len(q) >= 3 else None

        keep = []
        for t, s_ in ev:
            pa, pb = _med(t - 0.14, t - 0.02), _med(t + 0.02, t + 0.14)
            dip = _db(t + 0.05) - min(_db(t - 0.12 + 0.02 * j) for j in range(7))
            held = pa is not None and pb is not None and abs(pa - pb) < 0.5 and dip < 2.0
            if not held or s_ >= 0.7:
                keep.append((t, s_))
        ev = keep
    except Exception:  # noqa: BLE001
        pass
    # the first voiced event after a gap (>= 60 ms of the stem 20 dB under its p95 within 0.15 s before): the voice onset
    # after silence IS the word's onset (blue v2, the last intro word: onset 3.19, base CTC 3.29, lv60k 4.61)
    _GAPS.clear()
    for t, s_ in sorted(ev):
        w_ = [_db(t - 0.15 + 0.01 * j) for j in range(14)]
        if sum(x < -15 for x in w_) >= 5 and _db(t + 0.04) > -13 and not any(abs(t - g) < 0.1 for g in _GAPS):
            _GAPS.append(t)
    return sorted(ev)


_GAPS = []


_ENS_M = {}


def _ctc_starts(v, words, model, ends=False):
    """forced-alignment start (s) of each word (letters A-Z') on stem v (16 kHz); model 'base' | 'large'.
    ends=True: (starts, ends of each word's last letter)"""
    import torch
    import torchaudio.functional as F
    import torchaudio.pipelines as tp
    B = tp.WAV2VEC2_ASR_LARGE_LV60K_960H if model == 'large' else tp.WAV2VEC2_ASR_BASE_960H
    if model not in _ENS_M:
        _ENS_M[model] = B.get_model().eval()
    m = _ENS_M[model]
    lab = B.get_labels()
    x = torch.from_numpy(np.asarray(v, np.float32))[None]
    if model == 'large':
        x = torch.nn.functional.layer_norm(x, x.shape)
    with torch.inference_mode():
        em, _ = m(x)
    lp = torch.log_softmax(em, -1)
    toks, wid = [], []
    for k, w in enumerate(words):
        if k:
            toks.append(lab.index('|'))
            wid.append(-1)
        for c in w:
            toks.append(lab.index(c))
            wid.append(k)
    if not toks or len(toks) >= lp.shape[1]:
        return ([None] * len(words), [None] * len(words)) if ends else [None] * len(words)
    ali, sc = F.forced_align(lp, torch.tensor([toks], dtype=torch.int32), blank=0)
    spans = F.merge_tokens(ali[0], sc[0].exp())
    ratio = x.shape[1] / lp.shape[1] / 16000.0
    st, en = {}, {}
    for s, w in zip(spans, wid):
        if w >= 0:
            if w not in st:
                st[w] = float(s.start * ratio)
            en[w] = float(s.end * ratio)
    if ends:
        return [st.get(k) for k in range(len(words))], [en.get(k) for k in range(len(words))]
    return [st.get(k) for k in range(len(words))]


def ctc_candidates(v, letters, cands, log, models=('base', 'large')):
    """add each model's CTC start of every word to cands[k] (anchors). A vowel-initial word (after the first) also gets
    ('after', end of the previous word's last letter + 0.02): a legato vowel after a vowel has no sharp cue, both models
    emit its letter at the most confident frame, LATE (blue v2: the one-letter word at 1.00-1.04, sung from 0.84-0.87 right after the
    previous word's final letter); a consonant-initial word on which both models agree is trusted as it is."""
    st_all, en_all = {}, {}
    for mdl in models:
        try:
            st_all[mdl], en_all[mdl] = _ctc_starts(v, letters, mdl, ends=True)
        except Exception as e:  # noqa: BLE001  (no lv60k weights / no network: the other model + the current times)
            log[mdl] = f'failed: {type(e).__name__}'
            continue
        log[mdl] = [None if x is None else round(x, 3) for x in st_all[mdl]]
    for k in range(len(letters)):
        for mdl in st_all:
            if st_all[mdl][k] is not None:
                cands[k].append(st_all[mdl][k])
        if k and letters[k][:1] in ('A', 'E', 'I', 'O', 'U'):
            ends = [en_all[mdl][k - 1] for mdl in en_all if en_all[mdl][k - 1] is not None]
            if ends:
                cands[k].append(('after', max(ends) + 0.02))
    return cands


def assign(cands, E, Sg, late_w=LATE_W, min_gap=ENS_MIN_GAP, far=ENS_FAR, gaps=None):
    """monotone DP: one event TIME per word (or None when no assignment exists). cands: per word a list of times
    (CTC anchors), ('weak', t) (near only) and ('after', t) (only for events at / after t)."""
    E = list(map(float, E))
    Sg = list(map(float, Sg))
    for c_ in cands:
        # two CTC models agreeing within 80 ms with no stem event near them (a re-articulation on the same note): their
        # time is a weak virtual event (as lyrics.refine keeps the CTC time)
        pl = [x for x in c_ if not isinstance(x, tuple)]
        if len(pl) >= 2 and max(pl) - min(pl) <= 0.10 and not any(abs(e_ - float(np.mean(pl))) <= 0.06 for e_ in E):
            E.append(float(np.mean(pl)))
            Sg.append(0.3)
        elif len(pl) >= 2 and not any(abs(e_ - min(pl)) <= 0.06 for e_ in E):
            # the models disagree and nothing on the stem marks the earlier one (a soft consonant: golden hour 3g word 5):
            # the earlier model's time is a weak virtual event (the failure mode is late)
            E.append(float(min(pl)))
            Sg.append(0.2)
    o_ = np.argsort(E, kind='stable')
    E = np.asarray(E, float)[o_]
    Sg = np.asarray(Sg, float)[o_]
    n, m = len(cands), len(E)
    INF = 1e9
    if not n or not m:
        return None
    cost = np.full((n, m), INF)
    for k in range(n):
        # plain candidates count everywhere; ('after', t) = the end of the previous word's last letter: a candidate only
        # for events at / after it (a legato word starts right after it, never before)
        c = np.asarray([x for x in cands[k] if not isinstance(x, tuple)], float)
        ca = np.asarray([x[1] for x in cands[k] if isinstance(x, tuple) and x[0] == 'after'], float)
        cw = np.asarray([x[1] for x in cands[k] if isinstance(x, tuple) and x[0] == 'weak'], float)
        agree = len(c) >= 2 and float(c.max() - c.min()) <= 0.10
        near = np.abs(E[:, None] - c[None, :]).min(1) if len(c) else np.full(m, INF)
        if len(cw) and not agree:
            # a weak candidate (whisper / the aligner): near only, never the late anchor, only when the models disagree and
            # never for an event before the earliest model (golden hour 3g: whisper 2.06 pulled a word 0.2 s early)
            nw = np.abs(E[:, None] - cw[None, :]).min(1)
            if len(c):
                nw = np.where(E >= c.min() - 0.1, nw, INF)
            near = np.minimum(near, nw)
        cmin = np.full(m, c.min() if len(c) else INF)
        for t_ in ca:
            ok_ = E >= t_ - 0.03
            near = np.where(ok_, np.minimum(near, np.abs(E - t_)), near)
            cmin = np.where(ok_, np.minimum(cmin, t_), cmin)
        cost[k] = near + late_w * np.maximum(0.0, E - cmin) + 0.06 * (1.0 - Sg)
        for g in gaps or []:
            # an anchor up to 0.2 s after a voice onset that follows silence: the word starts AT that onset, not later in it
            if len(c) and np.any((c >= g - 0.02) & (c <= g + 0.2)):
                cost[k] = np.where((E > g + 0.02) & (E <= g + 0.2), cost[k] + 0.15, cost[k])
        cost[k][near > far] = INF                      # an event far from every candidate is not this word
    D = np.full((n, m), INF)
    P = np.full((n, m), -1, int)
    D[0] = cost[0]
    for k in range(1, n):
        arg = np.zeros(m, int)                         # running argmin of D[k-1] over events 0..j
        cur, cj = INF, -1
        for j in range(m):
            if D[k - 1][j] < cur:
                cur, cj = D[k - 1][j], j
            arg[j] = cj
        for j in range(m):
            lim = int(np.searchsorted(E, E[j] - min_gap, side='right')) - 1
            if lim < 0 or arg[lim] < 0 or D[k - 1][arg[lim]] >= INF or cost[k, j] >= INF:
                continue
            D[k, j] = D[k - 1][arg[lim]] + cost[k, j]
            P[k, j] = arg[lim]
    j = int(np.argmin(D[-1]))
    if D[-1][j] >= INF:
        return None
    pick = [0] * n
    for k in range(n - 1, -1, -1):
        pick[k] = j
        j = int(P[k, j])
    return [float(E[p_]) for p_ in pick]


def intro_ensemble(ws, audio_path, a, b, threads=4, models=('base', 'large')):
    """ws: aligned words (audio_path seconds); re-time the lyric words starting in [a, b) (the words-on-black intro).
    Returns (words, log); the log holds indices / times only (no words)."""
    import re
    import lyrics as LY
    idx = [i for i, w in enumerate(ws) if a - 0.15 <= float(w['t_start']) < b and w.get('lyric', True)]
    if not idx:
        return ws, dict(skip='no intro words')
    letters = [re.sub(r"[^A-Z']", '', LY.romanise(str(ws[i]['text']))) for i in idx]
    if any(not x for x in letters):
        return ws, dict(skip='a word the CTC model cannot spell')
    lo = max(0.0, float(ws[idx[0]]['t_start']) - 0.6)
    if idx[0] > 0:
        lo = max(lo, float(ws[idx[0] - 1]['t_start']) + 0.1, float(ws[idx[0] - 1]['t_end']) - 0.05)
    hi = float(ws[idx[-1]]['t_end']) + 0.4
    if idx[-1] + 1 < len(ws):
        hi = min(hi, float(ws[idx[-1] + 1]['t_start']) - 0.02)
    hi = max(hi, float(ws[idx[-1]]['t_start']) + 0.4)
    import torch
    torch.set_num_threads(int(threads))
    v = LY.vocal_stem(audio_path, lo, hi, threads)
    cands = [[('weak', float(ws[i]['t_start']) - lo)] for i in idx]   # the aligner's time: near only
    log = dict(n=len(idx), lo=round(lo, 3), hi=round(hi, 3), ctc={})
    ctc_candidates(v, letters, cands, log['ctc'], models)
    for mdl, x in list(log['ctc'].items()):
        if isinstance(x, list):
            log['ctc'][mdl] = [None if y is None else round(y + lo, 3) for y in x]
    ev = _events(v, LY.SR)
    if os.environ.get("JUDGE_LW_DEBUG"):
        print("[ens] cands", [[x if not isinstance(x, tuple) else (x[0][0], round(x[1], 2)) for x in c_] for c_ in cands], "events", [(round(t, 2), round(x, 2)) for t, x in ev], "gaps", [round(g, 2) for g in _GAPS])
    pick = assign(cands, [t for t, _ in ev], [s for _, s in ev], gaps=list(_GAPS)) if ev else None
    if pick is None:
        log['skip'] = 'no monotone assignment'
        return ws, log
    out = [dict(w) for w in ws]
    moves = []
    for k, i in enumerate(idx):
        nt = round(float(pick[k]) + lo, 4)
        moves.append(dict(i=i, was=round(float(ws[i]['t_start']), 3), now=nt))
        out[i]['t_start'] = nt
    for i in idx:                                       # ends: never past the next word's start
        if i + 1 < len(out):
            nx = float(out[i + 1]['t_start'])
            out[i]['t_end'] = round(min(max(float(out[i]['t_end']), float(out[i]['t_start']) + 0.08), nx - 0.01), 4)
        if i > 0 and float(out[i - 1]['t_end']) > float(out[i]['t_start']) - 0.01:
            out[i - 1]['t_end'] = round(float(out[i]['t_start']) - 0.01, 4)
    log['moves'] = moves
    return out, log
