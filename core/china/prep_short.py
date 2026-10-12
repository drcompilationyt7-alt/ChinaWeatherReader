"""
Mainland Unpacked: turn a Short's script JSON into a Remotion-ready folder.

  python core/china/prep_short.py content/china/week-2026-10-13/1-byd-flash-charge.json

Writes remotion/public/china/<slug>/ (gitignored):
  props.json          everything the Remotion composition needs (frames, words, cues, sfx, music, credits)
  vo-<n>.wav          the narration per scene (loudness-matched)
  img-<n>.jpg         the photos (Wikimedia Commons, licence-checked; credits collected)
  music.mp3           the music bed (Kevin MacLeod, CC BY 4.0: credited in the description)

Timing model: every animation and sound effect hangs off a *cue*, a word in the narration.
Faster-whisper gives each spoken word's time (ElevenLabs gives exact character times when that engine
is used). So when the narrator says "five minutes", the "5 MIN" card lands on that frame with its sound.

Voice: Kokoro am_puck (American male) for EVERY video, Shorts and long-form (owner 2026-10-12).
CHINA_TTS=elevenlabs / chatterbox exist but are only used when set explicitly.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'core', 'quiz'))
sys.path.insert(0, HERE)
import sound  # noqa: E402
import commons  # noqa: E402
import sfx as sfxlib  # noqa: E402
import stock  # noqa: E402

FPS = 30
SR = sound.SR
PUB = os.path.join(ROOT, 'remotion', 'public')
CACHE = os.environ.get('CHINA_WORK') or os.path.join(os.path.expanduser('~'), '.cache', 'china-work')
GAP = 0.32          # silence between scenes (s): the transition whoosh sits here
LEAD = 0.25         # first line starts after the hook's slam
CHANNEL_VOICE = 'am_puck'     # the channel's narrator (Kokoro v1.0, en-us); owner picked it 2026-10-12 over bm_george
CHANNEL_SPEED = 1.05

MUSIC = {   # mood -> Kevin MacLeod tracks (incompetech.com), CC BY 4.0
    'tech': ['Voxel Revolution', 'Digital Lemonade', 'EDM Detection Mode'],
    'epic': ['Rising Tide', 'Stay the Course', 'Undaunted'],
    'serious': ['Lightless Dawn', 'Deep Haze', 'Interloper'],
    'tense': ['Industrial Cinematic', 'The Descent', 'Darkling'],
    'calm': ['Clean Soul', 'Dreamer', 'Inspired'],
}
LEGACY_MOOD = {'edm': 'tech', 'dark': 'tense', 'lofi': 'serious', 'regular': 'tech'}


def load_env():
    p = os.path.join(ROOT, '.env')
    if os.path.exists(p):
        for ln in open(p, encoding='utf-8', errors='ignore'):
            m = re.match(r'\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$', ln)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


# ---------------- audio helpers ----------------
def decode(path):
    out = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).astype(np.float64)


def norm_voice(a, target=-16.0):
    a = a - np.mean(a)
    return np.clip(a * 10 ** ((target - sound.lufs(a)) / 20), -0.98, 0.98)


def sfx_peak(path):
    """Seconds from the file start to its loudest moment: the sfx is placed so the peak lands on the cue."""
    a = np.abs(decode(path))
    if not len(a):
        return 0.0
    win = int(SR * 0.01)
    env = np.convolve(a, np.ones(win) / win, mode='same')
    return float(np.argmax(env) / SR)


# ---------------- voice engines ----------------
def tts_elevenlabs(text, voice_id):
    key = os.environ['ELEVENLABS_API_KEY']
    body = json.dumps({'text': text, 'model_id': os.environ.get('ELEVENLABS_MODEL', 'eleven_multilingual_v2'),
                       'voice_settings': {'stability': 0.45, 'similarity_boost': 0.8, 'style': 0.25, 'use_speaker_boost': True}}).encode()
    req = urllib.request.Request(f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128',
                                 data=body, headers={'xi-api-key': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.load(r)
    import base64
    tmp = os.path.join(CACHE, 'el-tmp.mp3')
    open(tmp, 'wb').write(base64.b64decode(j['audio_base64']))
    a = decode(tmp)
    al = j.get('alignment') or {}
    chars, starts, ends = al.get('characters', []), al.get('character_start_times_seconds', []), al.get('character_end_times_seconds', [])
    words, cur, t0 = [], '', None
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                words.append((cur, t0, prev_e))
            cur, t0 = '', None
            continue
        if t0 is None:
            t0 = s
        cur += c
        prev_e = e
    if cur:
        words.append((cur, t0, prev_e))
    return a, words


def elevenlabs_ok():
    key = os.environ.get('ELEVENLABS_API_KEY')
    if not key:
        return False
    try:
        with urllib.request.urlopen(urllib.request.Request('https://api.elevenlabs.io/v1/user', headers={'xi-api-key': key}), timeout=20):
            return True
    except Exception:
        return False


def synth(lines, engine, voice):
    """[(audio, words-or-None)] per line, cached on disk by engine/voice/text."""
    os.makedirs(CACHE, exist_ok=True)
    out, todo = [None] * len(lines), []
    for i, s in enumerate(lines):
        key = hashlib.sha1(f'{engine}|{voice}|{os.environ.get("CHATTERBOX_EXAGGERATION")}|{os.environ.get("CHATTERBOX_CFG")}|{s}'.encode()).hexdigest()[:16]
        p = os.path.join(CACHE, f'vo-{key}.npz')
        if os.path.exists(p):
            z = np.load(p, allow_pickle=True)
            out[i] = (z['a'], list(z['w']) if z['w'].size else None)
        else:
            todo.append((i, s, p))
    if todo and engine == 'elevenlabs':
        for i, s, p in todo:
            a, w = tts_elevenlabs(s, voice)
            np.savez(p, a=a, w=np.array(w, dtype=object))
            out[i] = (a, w)
    elif todo:
        fn = sound.chatterbox_lines if engine == 'chatterbox' else (lambda ls: sound.kokoro_lines(ls, voice=voice, speed=CHANNEL_SPEED, pitch=0))
        res = fn([s for _, s, _ in todo])
        for (i, s, p), a in zip(todo, res):
            if a is None:
                raise RuntimeError(f'TTS failed: {s}')
            np.savez(p, a=a, w=np.array([], dtype=object))
            out[i] = (a, None)
    return out


# ---------------- numbers for the voice ----------------
_ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
_TENS = 'zero ten twenty thirty forty fifty sixty seventy eighty ninety'.split()


def _words(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ('' if n % 10 == 0 else '-' + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' and ' + _words(n % 100))
    for div, name in ((10 ** 9, 'billion'), (10 ** 6, 'million'), (1000, 'thousand')):
        if n >= div:
            return _words(n // div) + ' ' + name + ('' if n % div == 0 else (' and ' if n % div < 100 else ' ') + _words(n % div))
    return str(n)


def speakable(text):
    """Digits -> words for local TTS models, which misread them ('520' came out as '510')."""
    def num(m):
        raw, pct = m.group(1), m.group(2)
        v = raw.replace(',', '')
        if '.' in v:
            a, b = v.split('.')
            out = _words(int(a)) + ' point ' + ' '.join(_ONES[int(c)] for c in b)
        else:
            n = int(v)
            if 1900 <= n <= 2099 and ',' not in raw and not pct:      # years
                hi, lo = divmod(n, 100)
                out = _words(hi) + ' ' + ('hundred' if lo == 0 else ('oh ' + _ONES[lo] if lo < 10 else _words(lo)))
                if 2000 <= n <= 2009:
                    out = 'two thousand' + ('' if lo == 0 else ' and ' + _ONES[lo])
            else:
                out = _words(n)
        return out + (' percent' if pct else '')
    return re.sub(r'(?<![\w.])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(%)?', num, text)


# Chinese names the English voice mangles ("Guizhou" came out as "Gijhu"): respelled for the voice only.
SAY = {   # each respelling checked against Kokoro's phonemizer (en-gb), not guessed
    'Guizhou': 'Gway-joe', 'Huajiang': 'Whah-jahng', 'Beipanjiang': 'Bay-pahn-jahng', 'Shenzhen': 'Shen-jen',
    'Changsha': 'Chahng-shah', 'gaokao': 'gow-kow', 'Zhongguancun': 'Jong-gwan-tsoon', 'Huawei': 'Wah-way',
    'Yuanbao': 'Yuen-bow', 'Xiaomi': 'Shao-mee', 'Yiwu': 'Ee-woo', 'Hubei': 'Hoo-bay', 'Pudong': 'Poo-dong',
    'Chongqing': 'Chong-ching', 'Hangzhou': 'Hahng-joe', 'Guangzhou': 'Gwahng-joe', 'Suzhou': 'Soo-joe',
    'Xinjiang': 'Shin-jahng', 'Zhejiang': 'Juh-jahng',
    'SMIC': 'S M I C', 'EUV': 'E U V', 'TSMC': 'T S M C', 'BYD': 'B Y D', 'DJI': 'D J I',
}


def respell(text):
    for k, v in sorted(SAY.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r"(?<![\w-])" + re.escape(k) + r"(?![\w])", v, text, flags=re.I)
    return text


# ---------------- word timing ----------------
_whisper = None


def whisper_words(a):
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel('base.en', device='cpu', compute_type='int8')
    a16 = np.interp(np.arange(0, len(a), SR / 16000), np.arange(len(a)), a).astype(np.float32)
    segs, _ = _whisper.transcribe(a16, word_timestamps=True, language='en', beam_size=5)
    return [(w.word.strip(), w.start, w.end) for s in segs for w in s.words]


NUM = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10'}


def tok(w):
    w = re.sub(r"[^a-z0-9%]", '', w.lower().replace(',', ''))
    return NUM.get(w, w)


def align(caption, heard, dur):
    """Times for each caption word: matched against the recognised words, gaps interpolated."""
    cw = caption.split()
    ct, ht = [tok(w) for w in cw], [tok(w) for w, _, _ in heard]
    times = [None] * len(cw)
    for blk in difflib.SequenceMatcher(None, ct, ht, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            times[blk.a + k] = (heard[blk.b + k][1], heard[blk.b + k][2])
    # interpolate the unmatched runs between their known neighbours, weighted by word length
    i = 0
    while i < len(cw):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(cw) and times[j] is None:
            j += 1
        t0 = times[i - 1][1] if i > 0 else 0.05
        t1 = times[j][0] if j < len(cw) else dur - 0.05
        wts = [len(cw[k]) + 2 for k in range(i, j)]
        acc = 0
        for k in range(i, j):
            a0 = t0 + (t1 - t0) * acc / sum(wts)
            acc += wts[k - i]
            times[k] = (a0, t0 + (t1 - t0) * acc / sum(wts))
        i = j
    return [(cw[k], times[k][0], times[k][1]) for k in range(len(cw))]


def find_cue(words, cue, after=0):
    """Index of the first caption word at/after `after` where the cue phrase starts (case/punctuation-insensitive)."""
    if cue is None:
        return None
    ct = [tok(w) for w in str(cue).split()]
    wt = [tok(w[0]) for w in words]
    for i in range(after, len(wt) - len(ct) + 1):
        if wt[i:i + len(ct)] == ct:
            return i
    for i in range(after, len(wt)):    # prefix match ("kilometers" for "kilometer")
        if ct and wt[i].startswith(ct[0]):
            return i
    return None


# ---------------- main ----------------
def prep(spec_path):
    load_env()
    spec = json.load(open(spec_path, encoding='utf-8'))
    slug = spec.get('slug') or os.path.splitext(os.path.basename(spec_path))[0]
    out = os.path.join(PUB, 'china', slug)
    os.makedirs(out, exist_ok=True)
    scenes = spec['scenes']

    # Owner 2026-10-12: ONE channel voice for every video (Shorts and long-form): Kokoro am_puck (American male). Other engines only when asked for explicitly (CHINA_TTS), never automatically.
    engine = os.environ.get('CHINA_TTS', 'kokoro')
    voice = (os.environ.get('ELEVENLABS_VOICE_EXPLAINER') if engine == 'elevenlabs'
             else os.environ.get('CHINA_VOICE', CHANNEL_VOICE) if engine == 'kokoro' else 'narrator-ref')
    print(f'{slug}: voice engine {engine}', file=sys.stderr)
    lines = [sc.get('say', sc['text']) for sc in scenes]
    if engine != 'elevenlabs':
        lines = [respell(speakable(x)) for x in lines]
    vo = synth(lines, engine, voice)

    # timeline (seconds) + per-scene words
    t = LEAD
    sc_out, all_words, credits = [], [], []
    for i, (sc, (a, ew)) in enumerate(zip(scenes, vo)):
        a = norm_voice(np.asarray(a, np.float64))
        dur = len(a) / SR
        heard = ew if ew else whisper_words(a)
        words = align(sc['text'], heard, dur)
        start = t
        scene_len = dur + GAP + sc.get('hold', 0.0)
        if i == len(scenes) - 1:
            scene_len += 1.6        # outro breathes: logo + subscribe
        sound.write_wav(os.path.join(out, f'vo-{i}.wav'), a)
        # cues -> absolute frames
        def cue_frame(c, after=0):
            k = find_cue(words, c, after)
            return (int(round((start + words[k][1]) * FPS)), k) if k is not None else (None, after)
        s2 = {k: v for k, v in sc.items() if not k.startswith('_')}
        if 'from' in s2:                      # a counter's start value; 'from' is the scene's start frame below
            s2['countFrom'] = s2.pop('from')
        for key in ('items', 'events'):
            if key in s2:
                after = 0
                for it in s2[key]:
                    f, after = cue_frame(it.get('cue'), after)
                    it['cueFrame'] = f
        for key in ('before', 'after'):
            if key in s2:
                s2[key]['cueFrame'], _ = cue_frame(s2[key].get('cue'))
        if 'cue' in s2:
            s2['cueFrame'], _ = cue_frame(s2['cue'])
        if 'cueEnd' in s2:
            f, k = cue_frame(s2['cueEnd'])
            s2['cueEndFrame'] = int(round((start + words[k][2]) * FPS)) if f is not None else None
        if sc.get('image'):
            path, info = commons.fetch(sc['image'], width=2000)
            fn = f'img-{i}.jpg'
            shutil.copyfile(path, os.path.join(out, fn))
            from PIL import Image
            with Image.open(path) as im:
                s2['imageAspect'] = im.width / im.height
            s2['imageFile'] = f'china/{slug}/{fn}'
            if info['credit'] not in credits:
                credits.append(info['credit'])
        if sc.get('video'):
            # real footage (Pexels / Pixabay / YouTube Creative Commons), cut to this scene and cropped to 9:16
            v0 = float(sc.get('videoStart', 0))
            need = scene_len + 1.0
            vpath, vinfo = stock.fetch(sc['video'], v0, need)
            fn = f'vid-{i}.mp4'
            stock.prepare(vpath, os.path.join(out, fn), v0 - float(vinfo.get('sectionStart', 0)), need)
            s2['videoFile'] = f'china/{slug}/{fn}'
            if vinfo['credit'] not in credits:
                credits.append(vinfo['credit'])
        s2.update({'from': int(round(start * FPS)), 'durationInFrames': int(round(scene_len * FPS)),
                   'voice': f'china/{slug}/vo-{i}.wav', 'voiceFrom': int(round(start * FPS))})
        sc_out.append(s2)
        all_words += [{'text': w, 'from': int(round((start + a0) * FPS)), 'to': int(round((start + a1) * FPS))} for w, a0, a1 in words]
        t += scene_len
    total = int(round(t * FPS)) + 6

    # sound effects with their peak offsets
    lib = sfxlib.ensure()
    peaks = {}
    for role, rel in lib.items():
        cache = os.path.join(PUB, rel) + '.peak'
        if not os.path.exists(cache):
            open(cache, 'w').write(str(sfx_peak(os.path.join(PUB, rel))))
        peaks[role] = float(open(cache).read())

    # music
    mood = LEGACY_MOOD.get(spec.get('music', 'tech'), spec.get('music', 'tech'))
    tracks = MUSIC.get(mood, MUSIC['tech'])
    track = spec.get('track') or tracks[int(hashlib.sha1(slug.encode()).hexdigest(), 16) % len(tracks)]
    mdir = os.path.join(PUB, 'music')
    os.makedirs(mdir, exist_ok=True)
    mpath = os.path.join(mdir, track.replace(' ', '_') + '.mp3')
    if not os.path.exists(mpath):
        url = 'https://incompetech.com/music/royalty-free/mp3-royaltyfree/' + urllib.request.quote(track) + '.mp3'
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120) as r:
            open(mpath, 'wb').write(r.read())
    m = decode(mpath)
    music_gain = float(10 ** ((-27.0 - sound.lufs(m[:SR * 60])) / 20))    # bed sits ~11 dB under the voice
    music_credit = f'Music: "{track}" by Kevin MacLeod (incompetech.com), licensed under Creative Commons: By Attribution 4.0 (http://creativecommons.org/licenses/by/4.0/)'

    props = {
        'slug': slug, 'fps': FPS, 'durationInFrames': total, 'scenes': sc_out, 'words': all_words,
        'sfx': {r: {'file': f, 'peak': int(round(peaks[r] * FPS))} for r, f in lib.items()},
        'music': {'file': f'music/{os.path.basename(mpath)}', 'gain': music_gain, 'track': track},
        'credits': credits + [music_credit], 'engine': engine,
    }
    json.dump(props, open(os.path.join(out, 'props.json'), 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(f'{slug}: {total / FPS:.1f} s, {len(sc_out)} scenes, music "{track}"', file=sys.stderr)
    return os.path.join(out, 'props.json'), props


if __name__ == '__main__':
    p, _ = prep(sys.argv[1])
    print(p)
