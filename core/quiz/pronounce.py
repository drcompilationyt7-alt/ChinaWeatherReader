"""Phonetic respelling for the narrator (TTS text ONLY; on-screen text keeps the real spelling).

  respell(text)  ->  text with known names respelled ("Gojo" -> "Go-joe", "Kaguya" -> "Kah-goo-yah")
  priority per name token:  assets/pronounce.json overrides  >  verified best spelling (pronounce-cache.json, from the round trip)
                            >  auto rules (Japanese romaji morae / Korean syllables)  >  unchanged
  English names and words pass through unchanged: only tokens that occur in the character / show / idol / vtuber names AND never
  appear as a lowercase English word in the narrator lines or trivia text are touched.
  Enabled with POP_PRONOUNCE=1 (sound.tts_lines); `python pronounce.py "Satoru Gojo"` prints a respelling.
"""
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')
CACHE = os.path.join(os.path.expanduser('~'), '.cache', 'pop_quiz_models', 'pronounce-cache.json')

# ---- Japanese (Hepburn) -------------------------------------------------------
V = {'a': 'ah', 'i': 'ee', 'u': 'oo', 'e': 'eh', 'o': 'oh'}
# (romaji mora -> English-friendly syllable). Consonant + vowel; special spellings first.
MORA = {}
for c, en in (('', ''), ('k', 'k'), ('g', 'g'), ('s', 's'), ('z', 'z'), ('t', 't'), ('d', 'd'), ('n', 'n'), ('h', 'h'), ('b', 'b'),
              ('p', 'p'), ('m', 'm'), ('r', 'r'), ('w', 'w'), ('y', 'y')):
    for v, vv in V.items():
        MORA[c + v] = en + vv
for k in ('yi', 'ye', 'wi', 'wu', 'we', 'si', 'zi', 'ti', 'tu', 'di', 'du', 'hu'):
    MORA.pop(k, None)
MORA.update({'wo': 'oh', 'shi': 'shee', 'chi': 'chee', 'tsu': 'tsoo', 'fu': 'foo', 'ji': 'jee',
             'sha': 'shah', 'shu': 'shoo', 'sho': 'shoh', 'she': 'sheh', 'cha': 'chah', 'chu': 'choo', 'cho': 'choh', 'che': 'cheh',
             'ja': 'jah', 'ju': 'joo', 'jo': 'joh', 'je': 'jeh', 'fa': 'fah', 'fi': 'fee', 'fe': 'feh', 'fo': 'foh',
             'ryo': 'ree-oh', 'rya': 'ree-ah', 'ryu': 'ree-oo',
             'kya': 'kyah', 'kyu': 'kyoo', 'kyo': 'kyoh', 'gya': 'gyah', 'gyu': 'gyoo', 'gyo': 'gyoh',
             'nya': 'nyah', 'nyu': 'nyoo', 'nyo': 'nyoh', 'hya': 'hyah', 'hyu': 'hyoo', 'hyo': 'hyoh',
             'bya': 'byah', 'byu': 'byoo', 'byo': 'byoh', 'pya': 'pyah', 'pyu': 'pyoo', 'pyo': 'pyoh',
             'mya': 'myah', 'myu': 'myoo', 'myo': 'myoh', 'dzu': 'zoo', 'tsa': 'tsah', 'ti': 'tee', 'di': 'dee'})
_MORAE = sorted(MORA, key=len, reverse=True)
_LONG = str.maketrans({'ō': 'ou', 'ū': 'uu', 'ā': 'aa', 'ē': 'ei', 'ī': 'ii', 'Ō': 'Ou', 'Ū': 'Uu', 'Ā': 'Aa'})


def _parse(word):
    """romaji -> list of morae, or None when the word is not Japanese romaji (English names stay untouched)."""
    w = word.lower().translate(_LONG)
    if not re.fullmatch(r"[a-z']+", w) or len(w) < 2:
        return None
    out, i = [], 0
    while i < len(w):
        if w[i] == "'":
            i += 1
            continue
        # doubled consonant (small tsu): kk ss tt pp, and tch
        if i + 1 < len(w) and w[i] == w[i + 1] and w[i] in 'kstpgdbzjfh':
            out.append(('xtsu', w[i]))
            i += 1
            continue
        if w.startswith('tch', i):
            out.append(('xtsu', 't'))
            i += 1
            continue
        for m in _MORAE:
            if w.startswith(m, i):
                # 'n' + vowel is na / ni ...; a lone n (end / before a consonant) is handled below
                out.append(('m', m))
                i += len(m)
                break
        else:
            if w[i] == 'n' and (i + 1 == len(w) or w[i + 1] not in 'aiueoy'):
                out.append(('n', 'n'))
                i += 1
            elif w[i] == 'm' and i + 1 < len(w) and w[i + 1] in 'bpm':
                out.append(('n', 'n'))
                i += 1
            else:
                return None
    return out


def jp_respell(word, variant=0):
    """variant 0 = default (hyphenated, "ai" -> eye); 1 = joined (no hyphens); 2 = CAPITAL stress on the first syllable;
    3 = alternative vowels (ai -> "eye" kept off: "ie", ei -> "ey", u -> "u" as in food = "ooh")."""
    ms = _parse(word)
    if not ms:
        return None
    syl = []   # list of (syllable text, final vowel letter or '')
    pend = ''
    for kind, m in ms:
        if kind == 'xtsu':
            pend = m
            continue
        if kind == 'n':
            if syl:
                syl[-1] = (syl[-1][0] + 'n', '')
            else:
                syl.append(('n', ''))
            continue
        s = MORA[m]
        if pend:
            if syl:
                syl[-1] = (syl[-1][0] + pend, '')
            pend = ''
        last = m[-1] if m[-1] in 'aiueo' else ''
        pv = syl[-1][1] if syl else ''
        pm = ms[ms.index((kind, m)) - 1] if False else None
        if syl and pv and m in ('a', 'i', 'u', 'e', 'o', 'wo'):   # a vowel mora right after a vowel: long vowel / diphthong
            prev, ps = pv, syl[-1][0]
            base = ps[:-len(V[pv])] if ps.endswith(V[pv]) else None
            if base is not None:
                if (prev, m) in (('o', 'u'), ('o', 'o'), ('u', 'u'), ('i', 'i'), ('a', 'a'), ('e', 'e')):
                    continue   # long vowel: one syllable
                if (prev, m) == ('e', 'i'):
                    syl[-1] = (base + ('ey' if variant == 3 else 'ay'), '')
                    continue
                if (prev, m) == ('a', 'i'):
                    syl[-1] = (base + ('ie' if variant == 3 else 'y' if variant == 4 else 'eye'), '')
                    continue
                if (prev, m) == ('o', 'i'):
                    syl[-1] = (base + 'oy', '')
                    continue
        syl.append((s, last))
    parts = [s for s, _ in syl]
    if len(parts) == 1 and parts[0] == 'ah' and False:
        pass
    if variant == 1:
        txt = ''.join(parts)
    else:
        if variant == 2 and parts:
            parts[0] = parts[0].upper()
        txt = '-'.join(parts)
    if variant == 2:
        return txt
    return txt[:1].upper() + txt[1:] if txt else txt


# ---- Korean (revised romanisation) -------------------------------------------
KR = {'jeong': 'jung', 'jung': 'jung', 'yeon': 'yun', 'yeo': 'yuh', 'hyun': 'hyun', 'seung': 'sung', 'sung': 'sung', 'chae': 'chay',
      'young': 'yung', 'won': 'wun', 'woo': 'woo', 'min': 'min', 'eun': 'un', 'jin': 'jin', 'ji': 'jee', 'hyo': 'hyoh', 'dae': 'day',
      'tae': 'tay', 'kyu': 'kyoo', 'gyu': 'gyoo', 'bin': 'bin', 'hee': 'hee', 'ho': 'hoh', 'soo': 'soo', 'seo': 'suh', 'kim': 'kim',
      'lee': 'lee', 'park': 'park', 'choi': 'chwee', 'kang': 'kahng', 'bang': 'bahng', 'han': 'han', 'huh': 'huh', 'hong': 'hohng',
      'yu': 'yoo', 'yun': 'yoon', 'ha': 'hah', 'na': 'nah', 'mi': 'mee', 'ri': 'ree', 'sa': 'sah', 'ra': 'rah', 'do': 'doh',
      'gi': 'gee', 'ki': 'kee', 'sun': 'sun', 'su': 'soo', 'ye': 'yeh', 'hae': 'heh', 'rin': 'rin', 'an': 'ahn', 'ahn': 'ahn'}
_KRS = sorted(KR, key=len, reverse=True)


def kr_respell(word):
    w = word.lower()
    out, i = [], 0
    while i < len(w):
        for k in _KRS:
            if w.startswith(k, i):
                out.append(KR[k])
                i += len(k)
                break
        else:
            return None
    return '-'.join(out).capitalize()



# ---- Chinese (pinyin) ---------------------------------------------------------
PY_INIT = {'zh': 'j', 'ch': 'ch', 'sh': 'sh', 'b': 'b', 'p': 'p', 'm': 'm', 'f': 'f', 'd': 'd', 't': 't', 'n': 'n', 'l': 'l', 'g': 'g',
           'k': 'k', 'h': 'h', 'j': 'j', 'q': 'ch', 'x': 'sh', 'r': 'r', 'z': 'dz', 'c': 'ts', 's': 's', 'y': 'y', 'w': 'w', '': ''}
PY_FIN = {'a': 'ah', 'o': 'oh', 'e': 'uh', 'i': 'ee', 'u': 'oo', 'v': 'yoo', 'ai': 'eye', 'ei': 'ay', 'ao': 'ow', 'ou': 'oh', 'an': 'ahn',
          'en': 'un', 'ang': 'ahng', 'eng': 'ung', 'ong': 'ohng', 'er': 'er', 'ia': 'yah', 'ie': 'yeh', 'iao': 'yow', 'iu': 'yoh',
          'ian': 'yen', 'in': 'een', 'iang': 'yahng', 'ing': 'eeng', 'iong': 'yohng', 'ua': 'wah', 'uo': 'woh', 'uai': 'why', 'ui': 'way',
          'uan': 'wahn', 'un': 'wun', 'uang': 'wahng', 've': 'yweh', 'van': 'ywen', 'vn': 'yoon', 'ue': 'weh'}
_PYI = sorted((k for k in PY_INIT if k), key=len, reverse=True)
_PYF = sorted(PY_FIN, key=len, reverse=True)


def pinyin_respell(word):
    """pinyin (with ' separators) -> respelling, or None when it does not parse as pinyin."""
    out = []
    for part in word.lower().replace('’', "'").split("'"):
        i = 0
        while i < len(part):
            ini = next((k for k in _PYI if part.startswith(k, i)), '')
            j = i + len(ini)
            fin = next((f for f in _PYF if part.startswith(f, j) and (j + len(f) == len(part) or part[j + len(f)] in 'bpmfdtnlgkhjqxzcsrywz' or True)), None)
            if fin is None:
                return None
            # try the longest final that leaves a parsable rest (greedy with one-step lookahead on n / ng)
            for f in _PYF:
                if part.startswith(f, j) and f in PY_FIN:
                    rest = part[j + len(f):]
                    if not rest or any(rest.startswith(k) for k in _PYI) or any(rest.startswith(x) for x in ('a', 'e', 'o')):
                        fin = f
                        break
            if ini in ('j', 'q', 'x', 'y') and fin in ('u', 'un', 'uan', 'ue'):
                fin = {'u': 'v', 'un': 'vn', 'uan': 'van', 'ue': 've'}[fin]
            if ini in ('zh', 'ch', 'sh', 'r', 'z', 'c', 's') and fin == 'i':
                syl = PY_INIT[ini] + ('r' if ini in ('zh', 'ch', 'sh', 'r') else 'uh')
            elif ini == 'y' and fin in ('i', 'in', 'ing'):
                syl = {'i': 'ee', 'in': 'een', 'ing': 'eeng'}[fin]
            elif ini == 'w' and fin == 'u':
                syl = 'woo'
            elif ini in ('y', 'w') and fin in ('ia', 'ie', 'iao', 'iu', 'ian', 'iang', 'iong', 'ua', 'uo', 'uai', 'ui', 'uan', 'uang'):
                syl = PY_FIN[fin]
            else:
                syl = PY_INIT[ini] + PY_FIN[fin]
            out.append(syl)
            i = j + len(fin)
    return '-'.join(out).capitalize() if out else None


# ---- name corpora / state -----------------------------------------------------
_STATE = {}


def _load_json(p, d=None):
    try:
        return json.load(open(p, encoding='utf-8'))
    except (OSError, ValueError):
        return d if d is not None else {}


def _words(s):
    return re.findall(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’]*", s)


def bank_cities():
    return _load_json(os.path.join(ASSETS, 'pop-bank.json'), {}).get('cities')


def state():
    if _STATE:
        return _STATE
    over = _load_json(os.path.join(ASSETS, 'pronounce.json'), {'names': {}}).get('names', {})
    cache = _load_json(CACHE, {})
    for k, v in cache.items():   # verified phrases (places / full names) act like overrides unless the hand file has the key
        if re.search(r'\s', k) and v.get('best') and k not in over:
            over[k] = v['best']
    jp, kr, names = {}, {}, set()
    chars = _load_json(os.path.join(ASSETS, 'anime-characters.json'), {'characters': []})['characters']
    for c in chars:
        for t in _words(c['name'] + ' ' + c.get('anime', '')):
            jp[t.lower()] = jp.get(t.lower(), 0) + 1
        names.add(c['name'])
    for v in _load_json(os.path.join(ASSETS, 'vtubers.json'), {'vtubers': []})['vtubers']:
        for t in _words(v['name']):
            jp.setdefault(t.lower(), 1)
        names.add(v['name'])
    for v in _load_json(os.path.join(ASSETS, 'kpop-idols.json'), {'idols': []})['idols']:
        for t in _words(v['name'].replace('-', ' ')):
            kr[t.lower()] = 1
        names.add(v['name'])
    pl = {'cn': {}, 'jp': {}, 'kr': {}}
    cs = _load_json(os.path.join(ASSETS, 'countries.json'), {'countries': []})['countries']
    cities = (_load_json(os.path.join(ASSETS, 'city-photos.json'), {'cities': []}).get('cities') or []) + (bank_cities() or [])
    for it in cs:
        names.add(it['name'])
        if it.get('capital'):
            names.add(it['capital'])
    cat = {'CN': 'cn', 'HK': 'cn', 'TW': 'cn', 'MO': 'cn', 'JP': 'jp', 'KR': 'kr'}
    for it in cities:
        names.add(it['name'])
        c = cat.get(it.get('iso2'))
        if c:
            for t in _words(it['name']):
                pl[c][t.lower()] = 1
    for it in cs:
        c = cat.get(it.get('iso2'))
        for t in _words(it.get('capital') or ''):
            if c:
                pl[c][t.lower()] = 1
    # tokens that are ordinary English words (lowercase in our own narrator / trivia text) are never respelled automatically
    eng = set()
    bank = _load_json(os.path.join(ASSETS, 'pop-bank.json'), {})
    for it in (bank.get('trivia') or []) + (bank.get('wyr') or []) + (bank.get('emoji') or []):
        for x in [it.get('question', '')] + list(it.get('options', []) or []):
            eng.update(w for w in _words(str(x)) if w.islower())
    eng.update(EN_WORDS)
    _STATE.update(pl=pl, over={k.lower(): v for k, v in over.items()}, cache=cache, jp=jp, kr=kr, eng=eng, names=names)
    return _STATE


EN_WORDS = set('''a an the and or of to in on at is it who what which this that these those next up round level easy hard medium
answer correct score quiz guess character name anime show from with your you how many best pick would rather first second third last
final bonus ready time out for new every day comment follow like more less all not yes no one two three four five six seven eight nine ten
rose love rain fire ice sun moon star light dark night day red blue green white black gold silver king queen prince princess lord lady
sir miss mister old young big small little great dragon tiger wolf fox cat dog bird fish bear lion man woman boy girl hero villain
alice anna emma lisa mary kate eve ray max sam tom jack jim joe ben dan don ian leo neo ash kai rex zero one lucky happy sweet snow
wind storm shadow blade sword knight angel devil demon god ghost spirit soul mind heart eye hand hair blood bone iron steel stone
sky sea ocean river lake mountain forest garden flower blossom cherry tree leaf wood
'''.split())


def _case(orig, new):
    if orig.isupper() and len(orig) > 1:
        return new   # all caps in the data (UI style): respelled is lower-case-initial capitalised
    return new


def token(tok):
    """Respelling of ONE name token, or None."""
    st = state()
    k = tok.lower().replace('’', "'")
    if k in st['over']:
        return st['over'][k]
    c = st['cache'].get(k)
    if c and c.get('best'):
        return c['best']
    if k in st['eng']:
        return None
    if k in st['pl']['cn'] and k not in st['jp']:
        r = pinyin_respell(k)
        if r:
            return r
    if k in st['pl']['jp']:
        r = jp_respell(k)
        if r:
            return r
    if k in st['pl']['kr']:
        r = kr_respell(k)
        if r:
            return r
    if k in st['kr'] and k not in st['jp']:
        r = kr_respell(k)
        if r:
            return r
    if k in st['jp']:
        return jp_respell(k)
    return None


def auto(tok, variant=0):
    """Auto-rule respelling ignoring overrides / cache (used by the verifier)."""
    return jp_respell(tok, variant)


def respell(text):
    """TTS text with name tokens respelled. Multi-word / punctuated overrides (assets/pronounce.json "names") match first."""
    st = state()
    out = text
    multi = sorted((k for k in st['over'] if re.search(r"[\s.\-]", k)), key=len, reverse=True)
    holes = {}
    for k in multi:
        rx = re.compile(r'(?<![A-Za-z])' + re.escape(k) + r'(?![A-Za-z])', re.I)
        def rep(m, k=k):
            key = f'\u0001{len(holes)}\u0001'
            holes[key] = st['over'][k]
            return key
        out = rx.sub(rep, out)

    def one(m):
        w = m.group(0)
        if '-' in w:   # hyphenated names (Neuro-sama): each part
            parts = w.split('-')
            return '-'.join(token(p) or p for p in parts) if all(p for p in parts) else w
        return token(w) or w
    out = re.sub(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’\-]*[A-Za-zÀ-ɏ]|[A-Za-zÀ-ɏ]", one, out)
    for k, v in holes.items():
        out = out.replace(k, v)
    return out


if __name__ == '__main__':
    import sys
    for a in sys.argv[1:]:
        print(a, '->', respell(a))
