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


def tts_lines(lines, voices=None, rate='+8%', workdir=None):
    """Synthesise each line with edge-tts. Returns list of arrays (None on failure)."""
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


def mix(duration, events, voice, music_track=None, music_gain=0.55):
    """
    events: [(time_sec, samples, gain)] sound effects
    voice:  [(time_sec, samples)] speech; music ducks under it
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
