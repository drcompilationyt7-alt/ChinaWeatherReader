"""
Audio for the quiz shorts, all synthesised here (no third-party music, so no
Content ID claims): timer ticks, reveal ding, whoosh, pop, outro chime and a
light 4-bar backing loop. Voice lines come from edge-tts and are mixed in
with the music ducked underneath.
"""
import asyncio
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

SR = 44100


def _t(dur):
    return np.arange(int(SR * dur)) / SR


def _env(n, attack=0.004, decay=None):
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    if decay:
        a = a * np.exp(-t / decay)
    return a


def tick(high=False):
    t = _t(0.05)
    f = 2600 if high else 1900
    s = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.012)
    s += 0.35 * np.random.default_rng(1).standard_normal(len(t)) * np.exp(-t / 0.004)
    return 0.55 * s


def ding():
    t = _t(0.9)
    s = np.sin(2 * np.pi * 1318.5 * t) * np.exp(-t / 0.28)
    s += 0.6 * np.sin(2 * np.pi * 1975.5 * t) * np.exp(-t / 0.18)
    s += 0.25 * np.sin(2 * np.pi * 2637 * t) * np.exp(-t / 0.10)
    return 0.45 * s * _env(len(t), 0.002)


def whoosh(dur=0.32):
    n = int(SR * dur)
    noise = np.random.default_rng(2).standard_normal(n)
    # crude band-pass sweep: moving-average lowpass of varying width
    out = np.zeros(n)
    t = np.arange(n) / n
    k = (3 + 30 * (1 - t) ** 2).astype(int)
    c = np.cumsum(np.concatenate([[0], noise]))
    for i in range(n):
        lo = max(0, i - k[i])
        out[i] = (c[i + 1] - c[lo]) / (i + 1 - lo)
    env = np.sin(np.pi * t) ** 1.5
    return 0.9 * out * env


def pop():
    t = _t(0.09)
    f = 700 * np.exp(-t / 0.03) + 180
    ph = 2 * np.pi * np.cumsum(f) / SR
    return 0.5 * np.sin(ph) * np.exp(-t / 0.03)


def chime():
    out = np.zeros(int(SR * 1.2))
    for i, f in enumerate([523.25, 659.25, 783.99, 1046.5]):
        t = _t(0.7)
        s = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * np.exp(-t / 0.25)
        start = int(SR * 0.09 * i)
        out[start:start + len(s)] += 0.28 * s
    return out


def _note(f, dur, kind='pluck'):
    t = _t(dur)
    if kind == 'bass':
        tri = 2 * np.abs(2 * ((f * t) % 1) - 1) - 1
        return tri * np.exp(-t / 0.35) * _env(len(t), 0.01)
    if kind == 'pad':
        s = sum(np.sin(2 * np.pi * f * m * t) / m for m in (1, 2, 3))
        return s * np.minimum(1, t / 0.08) * np.exp(-t / 0.6)
    s = np.sin(2 * np.pi * f * t) + 0.5 * np.sin(4 * np.pi * f * t) + 0.2 * np.sin(6 * np.pi * f * t)
    return s * np.exp(-t / 0.16) * _env(len(t), 0.003)


def _kick():
    t = _t(0.3)
    f = 110 * np.exp(-t / 0.05) + 45
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12)


def _hat(seed):
    t = _t(0.05)
    n = np.random.default_rng(seed).standard_normal(len(t))
    n = n - np.convolve(n, np.ones(4) / 4, mode='same')  # high-pass-ish
    return n * np.exp(-t / 0.012)


def _midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


PROGRESSIONS = [
    # (root, chord intervals) per bar; keys chosen per seed
    [(0, (0, 4, 7)), (7, (0, 4, 7)), (9, (0, 3, 7)), (5, (0, 4, 7))],   # I V vi IV
    [(9, (0, 3, 7)), (5, (0, 4, 7)), (0, (0, 4, 7)), (7, (0, 4, 7))],   # vi IV I V
    [(0, (0, 4, 7)), (9, (0, 3, 7)), (5, (0, 4, 7)), (7, (0, 4, 7))],   # I vi IV V
]


def music(duration, seed=0, bpm=112):
    """Light upbeat loop: kick, hats, bass, plucked chords."""
    rng = np.random.default_rng(seed)
    key = int(rng.integers(0, 7))
    prog = PROGRESSIONS[int(rng.integers(0, len(PROGRESSIONS)))]
    beat = 60.0 / bpm
    bar = beat * 4
    n = int(SR * (duration + 1))
    out = np.zeros(n)

    def put(sig, at, gain):
        i = int(at * SR)
        if i >= n:
            return
        j = min(n, i + len(sig))
        out[i:j] += gain * sig[:j - i]

    kick = _kick()
    hats = [_hat(s) for s in range(4)]
    t = 0.0
    b = 0
    while t < duration + bar:
        root, chord = prog[b % 4]
        base = 48 + key + root
        for q in range(4):
            bt = t + q * beat
            if q in (0, 2):
                put(kick, bt, 0.55)
            put(_note(_midi(base - 12), beat * 0.9, 'bass'), bt, 0.30)
            for h in range(2):
                put(hats[(q * 2 + h) % 4], bt + h * beat / 2, 0.10 if h else 0.06)
            # plucked chord on the off-beat
            for k, iv in enumerate(chord):
                put(_note(_midi(base + 12 + iv), beat * 0.8), bt + beat / 2 + 0.008 * k, 0.07)
        put(_note(_midi(base + 12), bar, 'pad'), t, 0.05)
        t += bar
        b += 1
    return out[:int(SR * duration)]


def decode_audio(path):
    """Any audio file -> mono float32 at SR via ffmpeg."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0


DEFAULT_VOICES = ['en-US-AndrewNeural', 'en-US-GuyNeural', 'en-US-ChristopherNeural']
KOKORO_URL = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'
_kokoro = None


def _kokoro_model():
    """Kokoro-82M (Apache-2.0) via kokoro-onnx; model files are downloaded once to KOKORO_DIR."""
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        import urllib.request
        d = os.environ.get('KOKORO_DIR') or os.path.join(os.path.expanduser('~'), '.cache', 'kokoro')
        os.makedirs(d, exist_ok=True)
        for f in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'):
            p = os.path.join(d, f)
            if not os.path.exists(p) or os.path.getsize(p) < 1_000_000:
                urllib.request.urlretrieve(KOKORO_URL + f, p)
        _kokoro = Kokoro(os.path.join(d, 'kokoro-v1.0.onnx'), os.path.join(d, 'voices-v1.0.bin'))
    return _kokoro


def kokoro_lines(lines, voice=None, speed=None, pitch=None):
    """Natural-sounding narration with Kokoro. KOKORO_PITCH (semitones) deepens the voice."""
    k = _kokoro_model()
    voice = voice or os.environ.get('KOKORO_VOICE', 'am_onyx')
    speed = float(speed or os.environ.get('KOKORO_SPEED', '1.0'))
    semis = float(pitch if pitch is not None else os.environ.get('KOKORO_PITCH', '0'))
    lang = 'en-gb' if voice.startswith('b') else 'en-us'
    out = []
    for text in lines:
        try:
            a, sr = k.create(text, voice=voice, speed=speed, lang=lang)
            a = np.asarray(a, dtype=np.float64)
            f = 2 ** (semis / 12.0)
            # resample to SR and apply the pitch shift in one go, then restore the tempo
            n_out = int(len(a) * SR / sr / f)
            a = np.interp(np.linspace(0, len(a) - 1, n_out), np.arange(len(a)), a)
            if abs(f - 1) > 1e-3:
                a = _stretch(a, f)
            nz = np.where(np.abs(a) > 0.01)[0]
            out.append(a[max(0, nz[0] - 400):nz[-1] + 1600] if len(nz) else a)
        except Exception:
            out.append(None)
    return out


_chatterbox = None
VOICE_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'voice', 'narrator-ref.wav')


def chatterbox_lines(lines):
    """
    Most natural narration: Chatterbox (Resemble AI, MIT) on CPU, voiced after
    assets/voice/narrator-ref.wav (a synthetic Kokoro voice, not a real person).
    CHATTERBOX_EXAGGERATION / CHATTERBOX_CFG tune the delivery: 0.3 / 0.6 is calm and cool;
    0.7 / 0.3 ("hype") sounds menacing and garbles short lines.
    """
    global _chatterbox
    import torch
    from chatterbox.tts import ChatterboxTTS
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    if _chatterbox is None:
        _chatterbox = ChatterboxTTS.from_pretrained(device='cpu')
    ref = os.environ.get('CHATTERBOX_REF') or VOICE_REF
    exag = float(os.environ.get('CHATTERBOX_EXAGGERATION', '0.3'))
    cfg = float(os.environ.get('CHATTERBOX_CFG', '0.6'))
    out = []
    for text in lines:
        try:
            with torch.inference_mode():
                wav = _chatterbox.generate(text, audio_prompt_path=ref, exaggeration=exag, cfg_weight=cfg)
            a = wav.squeeze().cpu().numpy().astype(np.float64)
            sr = _chatterbox.sr
            a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * SR / sr)), np.arange(len(a)), a)
            nz = np.where(np.abs(a) > 0.01)[0]
            out.append(a[max(0, nz[0] - 400):nz[-1] + 1600] if len(nz) else a)
        except Exception as e:
            print(f'chatterbox line failed: {str(e)[:80]}', file=sys.stderr)
            out.append(None)
    return out


def _stretch(a, rate):
    """Overlap-add time stretch (keeps pitch): rate > 1 lengthens."""
    win = int(0.04 * SR)
    hop_out = win // 2
    hop_in = hop_out / rate
    w = np.hanning(win)
    n = int((len(a) - win) / hop_in)
    out = np.zeros(n * hop_out + win)
    norm = np.zeros_like(out)
    for i in range(max(0, n)):
        s = int(i * hop_in)
        out[i * hop_out:i * hop_out + win] += a[s:s + win] * w
        norm[i * hop_out:i * hop_out + win] += w
    return out / np.maximum(norm, 1e-3)


def tts_lines(lines, voices=None, rate='+8%', workdir=None):
    """Synthesise each line: TTS_ENGINE=chatterbox | kokoro | edge (default), falling back in that order."""
    engine = os.environ.get('TTS_ENGINE', 'edge').lower()
    if engine == 'chatterbox':
        try:
            got = chatterbox_lines(lines)
            if all(g is not None for g in got):
                return got
            # patch the lines Chatterbox missed with Kokoro so the voice stays close
            try:
                fill = kokoro_lines([l for l, g in zip(lines, got) if g is None])
                it = iter(fill)
                return [g if g is not None else next(it) for g in got]
            except Exception:
                if any(g is not None for g in got):
                    return got
        except Exception as e:
            print(f'chatterbox unavailable ({str(e)[:80]}), trying kokoro', file=sys.stderr)
        engine = 'kokoro'
    if engine == 'kokoro':
        try:
            got = kokoro_lines(lines)
            if any(g is not None for g in got):
                return got
        except Exception as e:
            print(f'kokoro unavailable ({str(e)[:80]}), using edge-tts', file=sys.stderr)
    voices = [v for v in (voices or []) if v] + DEFAULT_VOICES
    try:
        import edge_tts
    except Exception:
        return [None] * len(lines)
    workdir = workdir or tempfile.mkdtemp(prefix='quiz-tts-')

    async def one(i, text):
        p = os.path.join(workdir, f'line{i}.mp3')
        for attempt in range(4):
            voice = voices[min(attempt // 2, len(voices) - 1)]
            try:
                await edge_tts.Communicate(text, voice, rate=rate).save(p)
                if os.path.getsize(p) > 1000:
                    return p
            except Exception:
                await asyncio.sleep(1.5 * (attempt + 1))
        return None

    async def run():
        return await asyncio.gather(*[one(i, t) for i, t in enumerate(lines)])

    if sys.platform == 'win32':  # avoids "Event loop is closed" noise from the proactor loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        paths = asyncio.run(run())
    except Exception:
        return [None] * len(lines)
    out = []
    for p in paths:
        try:
            a = decode_audio(p) if p else None
            if a is not None:
                # trim leading/trailing silence edge-tts adds
                nz = np.where(np.abs(a) > 0.01)[0]
                a = a[max(0, nz[0] - 400):nz[-1] + 1600] if len(nz) else a
            out.append(a)
        except Exception:
            out.append(None)
    return out


def _k_weight_gain(n, sr=SR):
    """|H(f)| of the ITU-R BS.1770 K-weighting (high shelf + high pass) at the rfft bins of an n-sample signal."""
    f = np.fft.rfftfreq(n, 1 / sr)
    z = np.exp(-2j * np.pi * f / sr)

    def biquad(b, a):
        return np.abs((b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2))
    # stage 1: high shelf (+4 dB above ~1.7 kHz)
    A, w0, q = 10 ** (3.999843853973347 / 40), 2 * np.pi * 1681.974450955533 / sr, 0.7071752369554196
    al, c = np.sin(w0) / (2 * q), np.cos(w0)
    shelf = biquad((A * ((A + 1) + (A - 1) * c + 2 * np.sqrt(A) * al), -2 * A * ((A - 1) + (A + 1) * c),
                    A * ((A + 1) + (A - 1) * c - 2 * np.sqrt(A) * al)),
                   ((A + 1) - (A - 1) * c + 2 * np.sqrt(A) * al, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - 2 * np.sqrt(A) * al))
    # stage 2: high pass at ~38 Hz
    w0, q = 2 * np.pi * 38.13547087602444 / sr, 0.5003270373238773
    al, c = np.sin(w0) / (2 * q), np.cos(w0)
    hp = biquad(((1 + c) / 2, -(1 + c), (1 + c) / 2), (1 + al, -2 * c, 1 - al))
    return shelf * hp


def lufs(x, sr=SR):
    """Integrated loudness of a mono signal (BS.1770: K-weighting, 400 ms blocks, -70 LUFS and -10 LU gates)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < int(0.4 * sr):
        return -70.0
    y = np.fft.irfft(np.fft.rfft(x) * _k_weight_gain(len(x), sr), n=len(x))  # zero-phase: loudness only needs power
    blk, hop = int(0.4 * sr), int(0.1 * sr)
    c = np.concatenate([[0.0], np.cumsum(y ** 2)])
    ms = (c[blk::hop][:(len(y) - blk) // hop + 1] - c[:-blk:hop][:(len(y) - blk) // hop + 1]) / blk
    ld = -0.691 + 10 * np.log10(np.maximum(ms, 1e-12))
    ms = ms[ld > -70]
    if not len(ms):
        return -70.0
    rel = -0.691 + 10 * np.log10(ms.mean()) - 10
    ms = ms[-0.691 + 10 * np.log10(ms) > rel]
    return float(-0.691 + 10 * np.log10(ms.mean()))


def _soft_limit(x, peak, knee=0.6):
    """Leaves everything under knee * peak alone and bends the rest smoothly towards `peak`."""
    t = peak * knee
    a = np.abs(x)
    over = a > t
    y = x.copy()
    y[over] = np.sign(x[over]) * (t + (peak - t) * np.tanh((a[over] - t) / (peak - t)))
    return y


def _true_peak_gain(y, ceiling, over=4):
    """Per-sample gain that keeps the 4x-oversampled (inter-sample) peaks under `ceiling`: bright
    transients (ticks, hats) overshoot their sample peaks, and AAC adds a little more."""
    n = len(y)
    up = np.fft.irfft(np.fft.rfft(y), n=n * over) * over
    env = np.abs(up).reshape(n, over).max(axis=1)
    g = np.minimum(1.0, ceiling / np.maximum(env, 1e-9))
    w = int(0.004 * SR)  # 4 ms: hold the lowest gain around each peak, then smooth the edges
    pad = np.pad(g, (w, w), mode='edge')
    held = np.min(np.lib.stride_tricks.sliding_window_view(pad, 2 * w + 1), axis=1)
    c = np.concatenate([[0.0], np.cumsum(np.pad(held, (w, w), mode='edge'))])
    return (c[2 * w + 1:] - c[:-(2 * w + 1)]) / (2 * w + 1)


def true_peak_db(y, over=4):
    up = np.fft.irfft(np.fft.rfft(y), n=len(y) * over) * over
    return 20 * np.log10(np.abs(up).max() or 1e-9)


def measure_file(path):
    """(integrated LUFS, true peak dBTP) of an encoded file, as ffmpeg's EBU R128 meter reports them."""
    import re
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, text=True)
    s = (r.stderr or '')[(r.stderr or '').rfind('Summary:'):]
    i, tp = re.search(r'I:\s+(-?[\d.]+) LUFS', s), re.search(r'Peak:\s+(-?[\d.]+) dBFS', s)
    return (float(i.group(1)) if i else None), (float(tp.group(1)) if tp else None)


def master(out, target=-14.0, peak=0.84, tp_ceiling=-3.0):
    """Loudness to `target` LUFS through a soft limiter, with the true (inter-sample) peak held under
    `tp_ceiling` dBTP, which leaves room for AAC so the upload stays under -1 dBTP."""
    ceil = 10 ** (tp_ceiling / 20)
    g = 10 ** ((target - lufs(out)) / 20)
    y = out
    for _ in range(5):
        y = _soft_limit(out * g, peak)
        y = y * _true_peak_gain(y, ceil)
        now = lufs(y)
        if abs(now - target) < 0.15:
            break
        g *= 10 ** ((target - now) / 20)
    return y


def mix(duration, events, voice, music_track=None, music_gain=0.55, target_lufs=None):
    """
    events: [(time_sec, samples, gain)] sound effects
    voice:  [(time_sec, samples)] speech; music ducks under it
    target_lufs: master to this loudness (EBU R128) instead of the older RMS heuristic
    """
    n = int(SR * duration)
    fx = np.zeros(n)
    vo = np.zeros(n)
    for at, sig, gain in events:
        i = int(at * SR)
        if sig is None or i >= n:
            continue
        j = min(n, i + len(sig))
        fx[i:j] += gain * sig[:j - i]
    for at, sig in voice:
        i = int(at * SR)
        if sig is None or i >= n:
            continue
        j = min(n, i + len(sig))
        vo[i:j] += sig[:j - i]
    # voice level: peak-normalise speech to a consistent loudness
    if np.abs(vo).max() > 0:
        vo *= 0.9 / np.abs(vo).max()
    out = fx + vo
    if music_track is not None:
        m = np.zeros(n)
        m[:min(n, len(music_track))] = music_track[:n]
        # sidechain-style duck: smooth envelope of the voice
        env = np.abs(vo)
        win = int(SR * 0.12)
        env = np.convolve(env, np.ones(win) / win, mode='same')
        duck = 1 - 0.6 * np.clip(env / 0.08, 0, 1)
        fade = np.ones(n)
        f = int(SR * 0.6)
        fade[-f:] = np.linspace(1, 0.2, f)
        out += music_gain * m * duck * fade
    if target_lufs is not None:
        return master(out, target_lufs)
    # gentle limiter + loudness target (about -14 LUFS for dense speech+music)
    rms = math.sqrt(float(np.mean(out ** 2))) or 1.0
    out *= 0.2 / rms
    out = np.tanh(out * 1.1) / math.tanh(1.1)
    peak = np.abs(out).max() or 1.0
    if peak > 0.89:  # keeps true peak under -1 dBTP after AAC encoding
        out *= 0.89 / peak
    return out


def write_wav(path, samples):
    import wave
    data = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
