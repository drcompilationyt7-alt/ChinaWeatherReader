"""
Audio alignment for the meme edit: find where a window of our song plays inside another
recording (a K-pop stage, fancam or dance practice of the song, an anime's creditless
opening when the song is its OP).

    ref = SongRef('song.mp3', start=50, length=14)
    hit = ref.locate(decode('stage.webm'))    # None, or
    # {'t0': 61.23,        recording time where song time `start` plays (can be < 0: partial)
    #  'score': 0.71,      normalised correlation at the peak (onset + chroma), up to 1
    #  'z': 14.2,          peak height over the curve's noise floor (robust z-score)
    #  'ratio': 2.3,       peak over the best peak that is not a repeat of the same music
    #  'sub': [61.22, 61.25], 'sub_score': [0.7, 0.66],   two separate sub-windows
    #  'song_from': 50.0, 'song_to': 64.0}      part of the window the recording covers

Method: log-mel onset flux in 4 bands plus 12-bin chroma at 23 ms frames, each locally
whitened so loud passages don't dominate. Coarse: normalised cross-correlation of the song
window at every lag of the recording (partial overlap allowed at both ends). Fine: two
separate sub-windows are searched again within +-0.4 s of the best lag, with parabolic
interpolation; their offsets must agree within 80 ms, which rejects remixes, tempo-changed
live versions and chance matches. Repeats of the same music (a second chorus) are allowed,
the context before and after the window picks between them. numpy only (ffmpeg decodes).

Also sound hits for "anime sound edits" (a clip's own impacts landing on the beat):
sfx_frames(y) -> per-frame measures; sfx_hits(fr, t0, t1) -> [(time, strength)] of short,
broadband, loud transients (not speech); sfx_score(fr, t0, t1) -> 0-1 usability, pulled down
by talking and by a song's regular beat.
"""
import subprocess

import numpy as np

SR, HOP, NFFT = 11025, 256, 1024
FPS = SR / HOP                          # ~43 frames/s, 23 ms
MIN_SCORE = 0.30                        # peak correlation (onset + chroma average)
MIN_Z = 7.0                             # peak over the noise floor
MIN_RATIO = 1.35                        # peak over the best unrelated peak
MIN_SUB = 0.22                          # each sub-window's correlation
MAX_SKEW = 0.08                         # sub-window offsets agree within this (s)
MIN_COVER = 6.0                         # seconds of the window the recording must contain


def decode(path, start=0.0, dur=None, runner=None, timeout=90, sr=SR, pad=False):
    """Mono float32 audio at sr, or None (no audio stream / under 1 s). pad: pad or trim the
    start so sample 0 is at the file's (or the seek point's) time 0 even when the audio
    stream starts later than the video, as in section downloads cut on a keyframe."""
    cmd = ['ffmpeg', '-v', 'error', '-nostdin']
    if start and start > 0:
        cmd += ['-ss', f'{start:.3f}']
    cmd += ['-i', path]
    if dur:
        cmd += ['-t', f'{dur:.3f}']
    cmd += ['-map', '0:a:0', '-ac', '1', '-ar', str(sr)]
    if pad:
        cmd += ['-af', 'aresample=async=1:first_pts=0']
    cmd += ['-f', 's16le', '-']
    if runner:
        _, out, _ = runner(cmd, timeout)
    else:
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=timeout).stdout
        except subprocess.TimeoutExpired:
            return None
    if not out or len(out) < 2 * sr:
        return None
    return np.frombuffer(out[:len(out) // 2 * 2], np.int16).astype(np.float32) / 32768


# ---------------------------------------------------------------- features

def _hz_mel(f):
    return 2595 * np.log10(1 + np.asarray(f, float) / 700)


def _mel_hz(m):
    return 700 * (10 ** (np.asarray(m, float) / 2595) - 1)


_FREQS = np.fft.rfftfreq(NFFT, 1 / SR)
_WIN = np.hanning(NFFT + 1)[:-1].astype(np.float32)


def _mel_bank(n=48, fmin=40, fmax=5000, freqs=_FREQS):
    edges = _mel_hz(np.linspace(_hz_mel(fmin), _hz_mel(fmax), n + 2))
    fb = np.zeros((n, len(freqs)), np.float32)
    for k in range(n):
        lo, c, hi = edges[k:k + 3]
        up = (freqs - lo) / max(c - lo, 1e-6)
        down = (hi - freqs) / max(hi - c, 1e-6)
        fb[k] = np.maximum(0, np.minimum(up, down))
    return fb / np.maximum(fb.sum(1, keepdims=True), 1e-6)


def _chroma_bank(fmin=110, fmax=4200):
    fb = np.zeros((12, len(_FREQS)), np.float32)
    ok = (_FREQS >= fmin) & (_FREQS <= fmax)
    pitch = 12 * np.log2(np.maximum(_FREQS[ok], 1) / 440) + 69
    near = np.round(pitch)
    w = np.exp(-0.5 * ((pitch - near) / 0.3) ** 2)
    idx = np.nonzero(ok)[0]
    fb[(near.astype(int) % 12), idx] = w
    return fb


MEL, CHROMA = _mel_bank(), _chroma_bank()


def _power(y, nfft=NFFT, hop=HOP):
    """Power spectrogram (frames, nfft/2 + 1); frame k is centred on sample k * hop."""
    if len(y) < nfft:
        y = np.pad(y, (0, nfft - len(y)))
    y = np.pad(y, (nfft // 2, nfft // 2), mode='reflect').astype(np.float32)
    n = 1 + (len(y) - nfft) // hop
    fr = np.lib.stride_tricks.as_strided(y, (n, nfft), (y.strides[0] * hop, y.strides[0]))
    win = _WIN if nfft == NFFT else np.hanning(nfft + 1)[:-1].astype(np.float32)
    out = np.empty((n, nfft // 2 + 1), np.float32)
    for i in range(0, n, 4096):         # chunks keep the complex buffer small
        out[i:i + 4096] = np.abs(np.fft.rfft(fr[i:i + 4096] * win, axis=1)) ** 2
    return out


def _moving(x, k):
    """Centred moving average along axis 0 (edge-padded)."""
    k = max(1, int(k)) | 1
    xp = np.pad(x, [(k // 2, k // 2)] + [(0, 0)] * (x.ndim - 1), mode='edge')
    c = np.cumsum(np.concatenate([np.zeros((1,) + x.shape[1:]), xp]), axis=0)
    return (c[k:] - c[:-k]) / k


def _whiten(x, mean_s, std_s):
    x = x - _moving(x, mean_s * FPS)
    return x / (np.sqrt(_moving(x ** 2, std_s * FPS)) + 1e-3)


def features(y):
    """{'on': (n, 4) onset flux, 'ch': (n, 12) chroma}, whitened; frame k is at k / FPS s."""
    p = _power(y)
    mel = 10 * np.log10(p @ MEL.T + 1e-10)
    mel = np.maximum(mel, mel.max() - 80)
    flux = np.maximum(0, np.diff(mel, axis=0, prepend=mel[:1]))
    on = np.stack([b.mean(1) for b in np.array_split(flux, 4, axis=1)], 1)
    on = _whiten(on, 1.0, 4.0)
    ch = np.sqrt(p) @ CHROMA.T
    ch = ch / (np.linalg.norm(ch, axis=1, keepdims=True) + 1e-3 * (ch.max() + 1e-9))
    ch = _moving(_whiten(ch, 4.0, 8.0), 3)
    return {'on': on.astype(np.float32), 'ch': ch.astype(np.float32)}


# ---------------------------------------------------------------- correlation

def _fft_xcorr(v, q):
    """sum_d sum_i q[i, d] * v[p + i, d] for p = -(m-1) .. n-1 (index p + m - 1)."""
    n, m = len(v), len(q)
    size = 1 << int(np.ceil(np.log2(n + m)))
    # float64: numpy 2 keeps float32 FFTs in single precision, too noisy for quiet overlaps
    fv = np.fft.rfft(v.astype(np.float64), size, axis=0)
    fq = np.fft.rfft(q[::-1].astype(np.float64), size, axis=0)
    return np.fft.irfft((fv * fq).sum(1), size)[:n + m - 1]


def ncc(q, v, min_overlap):
    """Cosine similarity of q (m, d) against v (n, d) at every lag, over the overlap only.
    Index j is the lag p = j - (m - 1): query frame 0 at recording frame p. Lags with an
    overlap shorter than min_overlap frames are -1."""
    m, n = len(q), len(v)
    num = _fft_xcorr(v, q)
    cq = np.concatenate([[0.0], np.cumsum((q.astype(np.float64) ** 2).sum(1))])
    cv = np.concatenate([[0.0], np.cumsum((v.astype(np.float64) ** 2).sum(1))])
    p = np.arange(-(m - 1), n)
    i0, i1 = np.maximum(0, -p), np.minimum(m, n - p)
    qq, vv = cq[i1] - cq[i0], cv[p + i1] - cv[p + i0]
    s = num / np.sqrt(qq * vv + 1e-9)
    ov = np.maximum(1, i1 - i0)
    # overlaps that are (nearly) silence on either side carry no evidence
    quiet = (vv / ov < 0.05 * cv[-1] / max(n, 1)) | (qq / ov < 0.05 * cq[-1] / max(m, 1))
    s[((i1 - i0) < min_overlap) | quiet] = -1.0
    return s


def _cos_at(q, v, p):
    """Cosine of q against v at integer lag p (overlap only), or None when under half overlaps."""
    m, n = len(q), len(v)
    i0, i1 = max(0, -p), min(m, n - p)
    if i1 - i0 < max(8, m // 2):
        return None
    a, b = q[i0:i1].ravel(), v[p + i0:p + i1].ravel()
    return float(a @ b / np.sqrt((a @ a) * (b @ b) + 1e-9))


def _parabolic(s, j):
    if 0 < j < len(s) - 1:
        a, b, c = s[j - 1], s[j], s[j + 1]
        den = a - 2 * b + c
        if den < 0:
            return j + float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return float(j)


def _peaks(s, gap):
    """Local maxima of s, strongest first, at least gap apart."""
    idx = np.nonzero((s[1:-1] >= s[:-2]) & (s[1:-1] >= s[2:]) & (s[1:-1] > 0))[0] + 1
    out = []
    for j in idx[np.argsort(-s[idx])]:
        if all(abs(j - k) >= gap for k in out):
            out.append(int(j))
        if len(out) >= 12:
            break
    return out


class SongRef:
    """Features of the whole song, and the window [start, start + length] to find."""

    def __init__(self, path, start, length, runner=None):
        y = decode(path, runner=runner, timeout=120)
        if y is None:
            raise ValueError(f'cannot decode {path}')
        self.dur = len(y) / SR
        self.start = float(max(0.0, min(start, self.dur - 4)))
        self.length = float(max(2.0, min(length, self.dur - self.start)))
        self.f = features(y)
        self.a = int(round(self.start * FPS))
        self.b = min(len(self.f['on']), int(round((self.start + self.length) * FPS)))

    def _q(self, a, b):
        return self.f['on'][a:b], self.f['ch'][a:b]

    def _curve(self, v, a, b, min_ov):
        q_on, q_ch = self._q(a, b)
        return 0.5 * (ncc(q_on, v['on'], min_ov) + ncc(q_ch, v['ch'], min_ov))

    def _sub(self, v, a, b, p_guess, search):
        """Best lag (float frames) of song frames a..b near p_guess, and its score."""
        m = b - a
        s = self._curve(v, a, b, max(8, int(0.8 * m)))
        lo = max(0, int(p_guess - search) + m - 1)
        hi = min(len(s), int(p_guess + search) + m)
        if hi - lo < 3:
            return None, -1.0
        j = lo + int(np.argmax(s[lo:hi]))
        return _parabolic(s, j) - (m - 1), float(s[j])

    def _validate(self, v, p):
        """Two separate sub-windows re-searched near lag p (window start at recording frame p).
        Returns (sub_lags, sub_scores, covered song frames (a, b)) or None."""
        n = len(v['on'])
        a = max(self.a, self.a - p)                  # song frames the recording covers
        b = min(self.b, self.a + (n - p))
        if b - a < MIN_COVER * FPS:
            return None
        k = int(0.45 * (b - a))
        lags, scores = [], []
        for sa, sb in ((a, a + k), (b - k, b)):
            lag, sc = self._sub(v, sa, sb, p + (sa - self.a), 0.4 * FPS)
            if lag is None:
                return None
            lags.append(lag - (sa - self.a))         # back to "window start" lags
            scores.append(sc)
        return lags, scores, (a, b)

    def _context(self, v, p):
        """Mean correlation of the song just before / after the window at lag p (repeat tie-break)."""
        out = []
        for a, b in ((self.a - int(10 * FPS), self.a), (self.b, self.b + int(6 * FPS))):
            a, b = max(0, a), min(len(self.f['on']), b)
            if b - a < 2 * FPS:
                continue
            pp = p + (a - self.a)
            vals = [x for x in (_cos_at(q, v[k], pp) for q, k in zip(self._q(a, b), ('on', 'ch'))) if x is not None]
            if len(vals) == 2:
                out.append(sum(vals) / 2)
        return float(np.mean(out)) if out else 0.0

    def locate(self, y, around=None, search=None):
        """Where the window plays in audio y (see module doc), or None when not confident.
        around/search (s): only look within around +- search (a verification of a known cut);
        the noise-floor tests are skipped then, the sub-window agreement is not."""
        if y is None or len(y) < SR * MIN_COVER:
            return None
        v = features(y)
        n, m = len(v['on']), self.b - self.a
        s = self._curve(v, self.a, self.b, int(min(m, max(MIN_COVER * FPS, 0.5 * m))))
        if around is not None:
            lo = max(0, int((around - search) * FPS) + m - 1)
            hi = min(len(s), int((around + search) * FPS) + m)
            if hi - lo < 3:
                return None
            cand = [lo + int(np.argmax(s[lo:hi]))]
        else:
            cand = _peaks(s, int(1.5 * FPS))
        if not cand or s[cand[0]] < MIN_SCORE:
            return None
        top = s[cand[0]]
        ok, bad = [], []
        for j in cand[:5]:
            if s[j] < max(MIN_SCORE, 0.75 * top):
                bad.append(s[j])
                continue
            p = _parabolic(s, j) - (m - 1)
            val = self._validate(v, int(round(p)))
            if val is None:
                bad.append(s[j])
                continue
            lags, scores, cover = val
            if min(scores) < MIN_SUB or max(abs(lags[0] - lags[1]), abs(lags[0] - p), abs(lags[1] - p)) > MAX_SKEW * FPS:
                bad.append(s[j])
                continue
            ok.append((float(s[j]) + 0.5 * self._context(v, int(round(p))), j, p, lags, scores, cover))
        if not ok:
            return None
        _, j, p, lags, scores, cover = max(ok)
        score = float(s[j])
        rest = [x for k, x in zip(cand[5:], s[cand[5:]])] + bad
        second = max([x for x in rest if x > 0] + [0.05])
        valid = s[s > -1]
        med = float(np.median(valid))
        mad = float(np.median(np.abs(valid - med))) * 1.4826 + 1e-3
        res = {'t0': round(p / FPS, 3), 'score': round(score, 3), 'z': round((score - med) / mad, 1),
               'ratio': round(float(score / second), 2), 'sub': [round(float(x) / FPS, 3) for x in lags],
               'sub_score': [round(x, 3) for x in scores],
               'song_from': round(cover[0] / FPS, 2), 'song_to': round(cover[1] / FPS, 2),
               'dur': round(n / FPS, 2)}
        if around is None and (res['z'] < MIN_Z or res['ratio'] < MIN_RATIO):
            res['weak'] = True
        return res


# ---------------------------------------------------------------- sound effects

SR_FX, NFFT_FX, HOP_FX = 22050, 1024, 256
FPS_FX = SR_FX / HOP_FX                 # ~86 frames/s, 11.6 ms
_FREQS_FX = np.fft.rfftfreq(NFFT_FX, 1 / SR_FX)
MEL_FX = _mel_bank(40, 60, 10000, _FREQS_FX)
_BROAD = (_FREQS_FX >= 400) & (_FREQS_FX <= 9000)
_LOW = (_FREQS_FX >= 100) & (_FREQS_FX <= 2500)


def _flatness(p):
    p = p.astype(np.float64) + 1e-10
    return np.exp(np.log(p).mean(1)) / p.mean(1)


def sfx_frames(y):
    """Per-frame measures of y (frame k at k / FPS_FX s): log-mel flux, level (dB), spectral
    flatness of 0.4-9 kHz (broadband noise is flat) and of 0.1-2.5 kHz (voices and music are
    tonal there, so it is low)."""
    p = _power(y, NFFT_FX, HOP_FX)
    mel = 10 * np.log10(p @ MEL_FX.T + 1e-10)
    mel = np.maximum(mel, mel.max() - 80)
    flux = np.maximum(0, np.diff(mel, axis=0, prepend=mel[:1])).mean(1)
    return {'flux': flux, 'db': 10 * np.log10(p.mean(1) + 1e-10),
            'flat': _flatness(p[:, _BROAD]), 'tonal': _flatness(p[:, _LOW])}


def sfx_hits(fr, t0=0.0, t1=None, max_hits=5, gap=0.25):
    """[(time s, strength 0-1)] of strong, short, broadband transients between t0 and t1:
    impacts, clashes, gunshots, whooshes; not speech (it turns tonal right after its onset)
    or quiet background. Strongest first."""
    flux, db, flat, tonal = fr['flux'], fr['db'], fr['flat'], fr['tonal']
    n = len(flux)
    i0, i1 = max(0, int(t0 * FPS_FX)), min(n, int((t1 if t1 is not None else n / FPS_FX) * FPS_FX))
    if i1 - i0 < FPS_FX:
        return []
    seg = flux[i0:i1]
    med = float(np.median(seg))
    thr = max(float(np.percentile(seg, 97)), med + 6 * float(np.median(np.abs(seg - med))), 3.0)
    med_db = float(np.median(db[i0:i1]))
    out = []
    for k in range(max(i0, 1), min(i1, n - 1)):
        if flux[k] < thr or flux[k] < flux[k - 1] or flux[k] < flux[k + 1]:
            continue
        pk = float(db[k:k + 6].max())
        over, rise = pk - med_db, pk - float(np.median(db[max(0, k - 30):max(1, k - 2)]))
        fl, ton = float(flat[k:k + 6].max()), float(np.median(tonal[k:k + 15]))
        if over < 4 or rise < 6 or fl < 0.18 or ton < 0.12:
            continue
        st = (0.35 * min(1, flux[k] / (2 * thr)) + 0.25 * min(1, over / 15) + 0.2 * min(1, rise / 20)
              + 0.2 * min(1, fl / 0.4))
        out.append((k / FPS_FX, round(st, 3)))
    picked = []
    for t, s in sorted(out, key=lambda x: -x[1]):
        if all(abs(t - u) >= gap for u, _ in picked):
            picked.append((round(t, 3), s))
        if len(picked) >= max_hits:
            break
    return picked


def sfx_score(fr, t0, t1, hits=None):
    """0-1: how usable the sound of t0..t1 is for a sound edit. Clean strong impacts score high;
    talking (tonal low band) or a song (a regular beat) over them pull it down; no hits, 0."""
    hits = sfx_hits(fr, t0, t1) if hits is None else hits
    i0, i1 = max(0, int(t0 * FPS_FX)), min(len(fr['flux']), int(t1 * FPS_FX))
    if not hits or i1 - i0 < FPS_FX:
        return 0.0
    db, tonal, flux = fr['db'][i0:i1], fr['tonal'][i0:i1], fr['flux'][i0:i1]
    loud = db > np.median(db) - 10
    talk = float((tonal[loud] < 0.06).mean()) if loud.any() else 0.0
    x = np.convolve(flux, np.ones(3) / 3, 'same')
    x = x - x.mean()
    ac = np.correlate(x, x, 'full')[len(x) - 1:]
    lo, hi = int(0.3 * FPS_FX), min(len(ac) - 1, int(1.0 * FPS_FX))
    beat = float(ac[lo:hi].max() / (ac[0] + 1e-9)) if hi > lo else 0.0     # a song: ~0.45-0.7
    impact = min(1.0, sum(s for _, s in sorted(hits, key=lambda h: -h[1])[:3]) / 1.5)
    return round(impact * (1 - 0.6 * min(1, talk / 0.5)) * (1 - 0.8 * min(1, max(0, beat - 0.2) / 0.3)), 3)
