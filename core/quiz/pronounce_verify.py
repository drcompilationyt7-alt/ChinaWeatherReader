"""Round-trip check for pronounce.py: TTS the respelled name with the channel narrator voice (Chatterbox), transcribe it with the
local Whisper (faster-whisper base.en), compare with the expected name, try alternative respellings on a failure and keep the best
in ~/.cache/pop_quiz_models/pronounce-cache.json.  Resumable; one name once; pauses while free RAM is low (edit renders).

  %TEMP%/ttsvenv/Scripts/python.exe core/quiz/pronounce_verify.py [--minutes N] [--limit N] [--only places|names|idols] [--report]
Cache entry per lower-case token or phrase: {"best": respelling, "score": 0..1, "status": ok|fail, "tried": {spelling: score}}.
Names that stay "fail" are listed by --report for a manual fix in assets/pronounce.json.
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pronounce as pr  # noqa: E402

OK = 0.72
MIN_FREE_GB = float(os.environ.get('POP_MIN_FREE_GB', '4'))   # Chatterbox needs ~2.5 GB


def free_gb():
    try:
        out = subprocess.run(['wmic', 'OS', 'get', 'FreePhysicalMemory'], capture_output=True, text=True).stdout
        return int(re.findall(r'\d+', out)[0]) / 1048576
    except Exception:  # noqa: BLE001
        return 99.0


def load_cache():
    try:
        return json.load(open(pr.CACHE, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_cache(c):
    os.makedirs(os.path.dirname(pr.CACHE), exist_ok=True)
    json.dump(c, open(pr.CACHE + '.tmp', 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    os.replace(pr.CACHE + '.tmp', pr.CACHE)


# --- phonetic comparison (consonant skeleton + vowel count; whisper spells English, the expected side is romaji) -------------
def _plain(s):
    s = unicodedata.normalize('NFKD', s.lower())
    return re.sub(r'[^a-z]', '', ''.join(ch for ch in s if not unicodedata.combining(ch)))


SUB = [('tsu', 'ts'), ('sch', 'sh'), ('tch', 'ch'), ('ph', 'f'), ('ck', 'k'), ('q', 'k'), ('x', 'ks'), ('zh', 'j'), ('dg', 'j'),
       ('kh', 'k'), ('wh', 'w'), ('c', 'k'), ('ll', 'l'), ('y', 'i')]


def skeleton(s):
    s = _plain(s)
    for a, b in SUB:
        s = s.replace(a, b)
    s = re.sub(r'[aeiou]+', 'V', s)
    s = re.sub(r'(.)\1+', r'\1', s)
    return s


def key_cons(s):
    return skeleton(s).replace('V', '')


def score(expected, heard):
    """expected: the real spelling (romaji / pinyin / English); heard: Whisper's words for the name."""
    e, h = key_cons(expected), key_cons(heard)
    if not e or not h:
        return 0.0
    cons = difflib.SequenceMatcher(None, e, h).ratio()
    sk = difflib.SequenceMatcher(None, skeleton(expected), skeleton(heard)).ratio()
    return round(0.6 * cons + 0.4 * sk, 3)


def variants(token, cur):
    """Alternative respellings, best guess first (the current one is tried first)."""
    out = []
    if cur:
        out.append(cur)
    k = token.lower()
    if re.fullmatch(r"[a-z']+", k):
        for v in range(0, 5):
            r = pr.jp_respell(k, v)
            if r:
                out.append(r)
        r = pr.pinyin_respell(k)
        if r:
            out.append(r)
        r = pr.kr_respell(k)
        if r:
            out.append(r)
    out.append(token)   # the plain spelling as the last resort
    seen, res = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return res


class Rig:
    def __init__(self):
        import numpy as np  # noqa: F401
        import sound
        from faster_whisper import WhisperModel
        self.sound = sound
        self.wh = WhisperModel('base.en', device='cpu', compute_type='int8', cpu_threads=2)

    def say(self, spelling):
        a = self.sound.chatterbox_lines([f'Next up. {spelling}.'])[0]
        return a

    def hear(self, a):
        import numpy as np
        from scipy.signal import resample_poly
        y = resample_poly(np.asarray(a, dtype=np.float32), 16000, self.sound.SR)
        segs, _ = self.wh.transcribe(y, beam_size=3, vad_filter=False, language='en')
        words = ' '.join(s.text for s in segs)
        words = re.sub(r'(?i)^\W*next\W*up\W*', '', words.strip())
        return words.strip(' .!?,')


def targets(only=None):
    st = pr.state()
    jp = json.load(open(os.path.join(pr.ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    out, seen = [], set()

    def add(label, exp, kind):
        k = label.lower()
        if k not in seen:
            seen.add(k)
            out.append((label, exp, kind))
    if only in (None, 'places'):
        for f, key in (('countries.json', 'countries'), ('city-photos.json', 'cities')):
            for it in json.load(open(os.path.join(pr.ASSETS, f), encoding='utf-8'))[key]:
                add(it['name'], it['name'], 'place')
                if it.get('capital'):
                    add(it['capital'], it['capital'], 'place')
        for it in (pr.bank_cities() or []):
            add(it['name'], it['name'], 'place')
    if only in (None, 'idols'):
        for it in json.load(open(os.path.join(pr.ASSETS, 'kpop-idols.json'), encoding='utf-8'))['idols']:
            add(it['name'], it['name'], 'idol')
        for it in json.load(open(os.path.join(pr.ASSETS, 'vtubers.json'), encoding='utf-8'))['vtubers']:
            add(it['name'], it['name'], 'vtuber')
    if only in (None, 'names'):
        for c in sorted(jp, key=lambda c: -c['favourites']):
            for t in pr._words(c['name']):   # one token at a time: the same token is shared by many characters
                if t.lower() in st['eng'] or len(t) < 3:
                    continue
                add(t, t, 'name')
    return out


def run(minutes=60, limit=None, only=None):
    cache = load_cache()
    todo = [t for t in targets(only) if t[0].lower() not in cache]
    if limit:
        todo = todo[:limit]
    print(f'{len(todo)} names to verify', flush=True)
    rig = None
    t_end = time.time() + minutes * 60
    n = 0
    for label, exp, kind in todo:
        if time.time() > t_end:
            break
        while free_gb() < MIN_FREE_GB and time.time() < t_end:
            print(f'paused: free RAM {free_gb():.1f} GB', flush=True)
            time.sleep(60)
        if time.time() > t_end:
            break
        rig = rig or Rig()
        k = label.lower()
        cur = pr.state()['over'].get(k) or pr.auto(label) if ' ' not in label else pr.state()['over'].get(k) or pr.respell(label)
        tried, best, bs = {}, None, -1
        for sp in variants(label, cur)[:5]:
            try:
                a = rig.say(sp)
                heard = rig.hear(a) if a is not None else ''
            except Exception as e:  # noqa: BLE001
                tried[sp] = {'err': str(e)[:40]}
                continue
            sc = score(exp, heard)
            tried[sp] = {'score': sc, 'heard': heard}
            if sc > bs:
                best, bs = sp, sc
            if sc >= OK + 0.12:   # clearly good: stop trying alternatives
                break
        cache[k] = {'best': best, 'score': bs, 'status': 'ok' if bs >= OK else 'fail', 'kind': kind, 'tried': tried}
        n += 1
        if n % 10 == 0:
            save_cache(cache)
            print(f'{n}/{len(todo)} {label}: {best} {bs}', flush=True)
    save_cache(cache)
    print(f'done {n}', flush=True)


def report():
    c = load_cache()
    bad = {k: v for k, v in c.items() if v.get('status') == 'fail'}
    print(f'{len(c)} verified, {len(bad)} still failing')
    for k, v in sorted(bad.items(), key=lambda kv: kv[1].get('score', 0)):
        print(f"  {k:28s} best {v['best']!r} {v['score']}  heard: " + ' | '.join(f"{s}->{d.get('heard')}" for s, d in list(v['tried'].items())[:3]))


if __name__ == '__main__':
    a = sys.argv
    arg = lambda k, d=None: a[a.index(k) + 1] if k in a else d  # noqa: E731
    if '--report' in a:
        report()
    else:
        run(float(arg('--minutes', 60)), int(arg('--limit', 0)) or None, arg('--only'))
