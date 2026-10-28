#!/usr/bin/env python3
"""Slow-mo source title filter (2026-10-28): women-beauty anime slow-mo only.

The 10-27 CI night downloaded 144 sources of which ~70 % were off target: the old anime_title() passed any title with
"twixtor" / "scenepack" (K-pop fancams, Hollywood, sports, stock video, My Little Pony, male anime leads) and matched single
name words inside other words ("Fruits 4K Stock Video" via "Fruits Basket", "Hancock (2008)" via "Boa Hancock").

A title passes when it names a FEMALE anime character (whole-word / phrase match: AniList characters with gender Female,
core/quiz/assets/anime-characters.json; full names in both name orders + distinctive name words) or a female-led SHOW
(the curated query shows + the AniList shows whose character favourites are >= 60 % female) or says "anime girl" / "waifu" /
"beautiful anime" ..., and is not:
  - a hard off-target pattern (stock / wallpaper / overlay / movieclips / sports / MLP / western cartoon / manga / k-drama ...)
  - K-pop (fancam / idol / concert / an idol or group name from core/quiz/assets/kpop-idols.json) without a female full name /
    show phrase
  - male-lead-only: a male character name (full or distinctive word) and no female character name (a show alone does not
    outweigh a male name: "Sakuta Bunny Girl Senpai twixtor" is a Sakuta pack)
Single-word show / name matches also need an anime signal (twixtor / scene pack / anime / episode / clips for edit ...).

    title_check(title, shows=()) -> (ok, reasons)        reasons: why it failed / which signal passed it
"""
import json
import os
import re
import unicodedata

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
ASSETS = os.path.join(ROOT, 'core', 'quiz', 'assets')

# always off target (checked on the normalized title)
HARD_OFF = re.compile(
    r'\bstock (?:video|footage|clips?)\b|\bcopyright free\b|\broyalty free\b|\bno copyright\b|\bwallpaper\b|\boverlays?\b|'
    r'\bmovie ?clips\b|\btime ?lapse\b|\bgopro\b|\bpov\b|\bon ride\b|\bdrone\b|\bnba\b|\bnfl\b|\bufc\b|\bfifa\b|\bfootball\b|'
    r'\bsoccer\b|\bbasketball\b|\bf1\b|\bformula 1\b|\bcar clips\b|\bmlp\b|\bmy little pony\b|\bpony\b|\bequestria\b|'
    r'\bmanga\b|\bmanhwa\b|\bwebtoon\b|\bk ?drama\b|\bc ?drama\b|\bthai ?drama\b|\bdrama\b|\bdisney\b|\bpixar\b|\bmarvel\b|'
    r'\bavengers\b|\bspider ?man\b|\bbatman\b|\btransformers\b|\bever after high\b|\btotal drama\b|\bavatar\b|\bbarbie\b|'
    r'\bwinx\b|\bmeme templ\w*|\bmoveset\b|\bgameplay\b|\bshowcase\b|\blive action\b|\breal life\b|\bcosplay\b|\bvlog\b|\bcredits\b|'
    r'\btrailer reaction\b|\bofficial (?:trailer|teaser)\b|\bteaser\b|\bsneak peek\b|\bstar wars\b|\bnetflix series\b|\bsitcom\b|\bseries scenepack\b')
KPOP_RE = re.compile(r'\bk ?pop\b|\bfan ?cam\b|\bface ?cam\b|\bconcert\b|\bidols?\b|\bmama awards\b|\bgayo\w*\b|\binkigayo\b|'
                     r'\bm countdown\b|\bmusic bank\b|\bshow champion\b|\bcomeback stage\b|\bsbs\b|\bkbs\b|\bmbc\b|\bmnet\b')
KPOP_GROUPS = ['bts', 'blackpink', 'twice', 'enhypen', 'babymonster', 'newjeans', 'aespa', 'ive', 'le sserafim', 'itzy', 'nmixx',
               'stray kids', 'seventeen', 'txt', 'tomorrow x together', 'ateez', 'exo', 'red velvet', 'nct', 'gidle', 'g idle',
               'kiss of life', 'illit', 'riize', 'zerobaseone', 'treasure', 'mamamoo', 'kep1er', 'fromis 9', 'loona',
               'oh my girl', 'stayc', 'everglow', 'weeekly', 'xinyu', 'taekook', 'jikook', 'ot7', 'bangtan', 'army']
ANIME_SIG = re.compile(r'\banime\b|\btwixtor\b|\btwixter\b|\btwitxor\b|\bscene ?pack|\bscenes?\b|\bepisode|\bep ?\d|\bseason\b|'
                       r'\bs\d\b|\bclips? for edit|\bkimi no na wa\b|\bshinkai\b|\braw clips\b|\bamv\b|\bpv\b|アニメ')
GENERIC_FEMALE = re.compile(r'\banime girls?\b|\bwaifus?\b|\bheroines?\b|\b(?:beautiful|pretty|cute|kawaii|aesthetic|soft|dreamy|'
                            r'gorgeous|elegant) anime\b|\banime (?:beauty|beauties|mix|women|woman|ladies|lady)\b|美少女|女の子')

# name words that are English words / western names / brands / too common to stand alone (full names still match)
STOP = set('''aqua alpha beta gamma delta epsilon darkness finger glass healer index jewelry lemon march mercury ship shooter shower
silence silky star titan turbo void white ward wendy rose ruby lisa laura mary maria anna anne annie april aurora diana emma julie
juliet victoria taylor thompson jones campbell carol catherine chris oscar carr garland scarlet vanilla rice power frost felt flora
luna iris sophia olivia rachel rebecca lucy monica kate crown coco lion sara sasha shirley silva tanya reaper hancock honda
watanabe nakamura inoue shimizu yoshikawa komatsu kawamoto sengoku zero nana momo mina rumi tomo nico miko misa gabi toga kaga
inda mako mami maka nava nota ueno wong lain lenz krul ymir vivi teio yona sora hina kana miki mira rena reina riyo riza rory
seiko tepes olivier smith clara claudia cecilia elizabeth isabella jeanne mira noelle diane holo hawkeye brightness blanc chain
chariot charmy fran gran grimm hermes hydra juno karen kiki lada lola lotte louise mabel mavis melty moore oscar paprika
persia pino ponyo sade selena serena sicily sophie tifa trish ursula valli wynn yoru yume yuzu robin
allen arthur bruno caesar char chrome ciel david death diego eater evans fifth foster funny garo gray guts haku joseph johnny
jonathan julius karma light luck lynx meme miya norman pain pierre sato sensei shin simon soul spike stark tony twenty victor
walker william wolfgang yang sung tobi kira kyan tatsu meguru shun lian guang cheng isaac martinez brook chopper mash migi toru
eiji sano nara saki rain king wolf howl sonic shadow happy will blood cell break swing park report watch weather adam alex
mike michael paul louis lucas daniel steven edgar gilbert albert bang crown dandy dutch hacker hawk kong long nate river roger
ruth scar seven twelve vincent walter wang xiang xiao zhang ying anime girl girls twixtor clips scene pack edit edits season
episode''' .split())
# male leads the AniList list misses or shares with a female name
MALE_EXTRA = ['levi', 'levi ackerman', 'deku', 'gojo', 'todo', 'aoi todo', 'cid kagenou', 'cid', 'ichigo kurosaki', 'obito', 'kurama',
              'akaza', 'kageyama', 'sakuta', 'sakuta azusagawa', 'denji', 'aki hayakawa', 'yuji', 'itadori', 'sukuna', 'naruto', 'sasuke',
              'tanjiro', 'zenitsu', 'inosuke', 'giyu', 'rengoku', 'luffy', 'zoro', 'sanji', 'ace', 'eren', 'armin', 'bakugo', 'todoroki',
              'asta', 'yuno', 'gon', 'killua', 'ban', 'meliodas', 'escanor', 'kaneki', 'light yagami', 'lelouch', 'saitama', 'goku',
              'vegeta', 'gohan', 'jotaro', 'giorno', 'rudo', 'sae itoshi', 'rin itoshi', 'isagi', 'nagi', 'bachira', 'haruka sakura',
              'sakura haruka', 'albafica', 'yuri ayato', 'qifrey', 'zen wistaria', 'taki', 'tachibana taki', 'kousei', 'kaoru tsumugi',
              'sung jinwoo', 'jinwoo', 'ayanokoji', 'anos', 'rimuru', 'subaru natsuki', 'natsuki subaru', 'kirito', 'rudeus',
              'loid', 'loid forger', 'twilight']
# show phrases too generic to stand alone or shows that are male-led despite female favourites
SHOW_STOP = {'another', 'school', 'bottom', 'gate', 'akane', 'act', 'tomo', 'nana', 'charlotte', 'k', 're', 'rent', 'laid', 'my dress',
              'cyberpunk', 'attack on titan', 'attack on titan final', 'jojo no kimyou na bouken', 'tokyo ghoul', 'pokemon the series',
              'genshin comic anthology', 'genshin ten', 'colorful stage the movie', 'aho', 'can a boy', 'your throne', 'rokka',
              'tamen de gushi', 'blend', 'bna', 'free', 'given', 'orange', 'aria', 'wave', 'hello world', 'kyoto animation',
              'free kyoto animation', 'free iwatobi swim club', 'tsurune', 'given anime', 'orange anime', 'medalist', 'tomo chan',
              'ponyo', 'paprika', 'school days', 'acs', 'nichijou', 'toonami', 'the intruder'}
# shows that need an anime signal (common English phrases)
SHOW_NEEDS_SIG = {'your name', 'a silent voice', 'blue box', 'weathering with you', 'suzume', 'look back', 'another', 'golden time',
                  'plastic memories', 'charlotte', 'freezing', 'gosick', 'chobits', 'another', 'love and lies', 'gamers', 'wolf children', 'castle in the sky', 'skip beat',
                  'bloom into you', 'school days', 'vivy', 'maquia', 'princess tutu', 'kiki s delivery service'}
# extra aliases of the female-led shows (titles use them)
SHOW_ALIAS = ['kimi no na wa', 'kaguya sama', 'love is war', 'bunny girl senpai', 'seishun buta yarou', 'oshi no ko', 'frieren',
              'beyond journey s end', 'sousou no frieren', 'kanojo okarishimasu', 'rent a girlfriend', 'gotoubun no hanayome',
              '5 toubun no hanayome', 'quintessential quintuplets', 'sono bisque doll', 'dress up darling', 'my dress up darling',
              'kaoru hana wa rin to saku', 'fragrant flower blooms with dignity', 'roshidere', 'alya sometimes hides her feelings',
              'majo no tabitabi', 'wandering witch', 'kimi ni todoke', 'koe no katachi', 'shigatsu wa kimi no uso', 'your lie in april',
              'boku no kokoro no yabai yatsu', 'dangers in my heart', 'ao no hako', 'blue box', 'kusuriya no hitorigoto',
              'apothecary diaries', 'lycoris recoil', 'bocchi the rock', 'date a live', 'violet evergarden', 'darling in the franxx',
              'spy x family', 'spy family', 'kobayashi san chi no maid dragon', 'maid dragon', 'love live', 'sound euphonium',
              'hibike euphonium', 'tamako market', 'tamako love story', 'k on', 'yuru camp', 'laid back camp', 'akebi chan',
              'yagate kimi ni naru', 'tenshi sama', 'angel next door', 'kubo san', 'komi san', 'komi can t communicate', 'horimiya',
              'toradora', 'tonikaku kawaii', 'tonikawa', 'shikimori san', 'nagatoro', 'makeine', 'too many losing heroines',
              'i want to eat your pancreas', 'kimi no suizou wo tabetai', 'garden of words', 'kotonoha no niwa', '5 centimeters per second',
              'weathering with you', 'tenki no ko', 'suzume no tojimari', 'hyouka', 'clannad', 'anohana', 'fruits basket', 'snow white with the red hair',
              'akagami no shirayukihime', 'kamisama kiss', 'kamisama hajimemashita', 'yamada kun to lv999', 'my love story with yamada',
              'yamada kun lv999', '100 kanojo', '100 girlfriends', 'magical revolution of the reincarnated princess', 'tensei oujo',
              'love after world domination', 'koi wa sekai seifuku no ato de', 'freezing', 'blood c', 'vivy fluorite eye',
              'love live superstar', 'idolmaster', 'monogatari', 'chainsaw man reze', 'reze arc', 'kakegurui', 're zero', 'rezero',
              'konosuba', 'steins gate', 'madoka magica', 'cardcaptor sakura', 'sailor moon', 'akame ga kill', 'kill la kill',
              'call of the night', 'yofukashi no uta', 'dandadan', 'dan da dan', 'skip and loafer', 'girls band cry', 'yuru yuri',
              'gochiusa', 'is the order a rabbit', 'non non biyori', 'amanchu', 'hanasaku iroha', 'aharen san', 'kimizero', 'kimi ni todoke',
              'spirited away', 'howl s moving castle', 'whisper of the heart', 'mysterious girlfriend x', 'nazo no kanojo x']

_C = {}
STOP_R = None


def norm(t):
    """NFKC (fancy unicode letters -> ascii), lower, punctuation -> space; padded with spaces for phrase matching"""
    t = unicodedata.normalize('NFKC', str(t or '')).lower()
    t = t.replace('’', "'").replace('‘', "'").replace('・', ' ')
    t = re.sub(r"[^\w぀-ヿ一-鿿]+", ' ', t).replace('_', ' ')
    return ' ' + ' '.join(t.split()) + ' '


def _ro(t):
    """romanization variants collapsed for name matching: Kyouko / Kyoko, Shouko / Shoko, Yuuki / Yuki"""
    return t.replace('ou', 'o').replace('uu', 'u').replace('oo', 'o')


def _words(s):
    return _ro(norm(s)).split()


def _phrase(s):
    return ' '.join(_words(s))


def _load(shows=(), females=()):
    key = (tuple(shows), tuple(females))
    if key in _C:
        return _C[key]
    try:
        with open(os.path.join(ASSETS, 'anime-characters.json'), encoding='utf-8') as f:
            chars = json.load(f).get('characters') or []
    except (OSError, ValueError):
        chars = []
    try:
        with open(os.path.join(ASSETS, 'kpop-idols.json'), encoding='utf-8') as f:
            idols = json.load(f).get('idols') or []
    except (OSError, ValueError):
        idols = []
    global STOP_R
    STOP_R = {_ro(w) for w in STOP}
    fem = [c for c in chars if c.get('gender') == 'Female']
    mal = [c for c in chars if c.get('gender') == 'Male']
    ftok_all = {w for c in fem for w in _words(c['name'])}
    mtok_all = {w for c in mal for w in _words(c['name'])}

    def full_names(cs):
        out = set()
        for c in cs:
            ws = _words(c['name'])
            if len(ws) >= 2:
                out.add(' '.join(ws))
                out.add(' '.join(reversed(ws)))               # Japanese order: "Haruno Sakura", "Waguri Kaoruko"
            elif len(ws) == 1 and len(ws[0]) >= 4 and ws[0] not in STOP_R:
                out.add(ws[0])
        return out

    def distinct(cs, other, top):
        cs = sorted(cs, key=lambda c: -(c.get('favourites') or 0))[:top]
        return {w for c in cs for w in _words(c['name']) if len(w) >= 4 and w not in other and w not in STOP_R and not w.isdigit()}

    f_full = full_names(fem) | {_phrase(x) for x in females if ' ' in x.strip()}
    m_full = full_names(mal) | {_phrase(x) for x in MALE_EXTRA if ' ' in x}
    f_tok = distinct(fem, mtok_all, 800)
    for x in females:                    # the curated query characters: "Komi Shouko" -> komi / shouko, "Alya Roshidere" -> alya
        ws = _words(x)
        for w in ws[:1] + ws[1:2] * (len(ws) == 2):
            if len(w) >= 4 and w not in mtok_all and w not in STOP_R:
                f_tok.add(w)
    # a one-word / common female name next to its show: "Robin ... One Piece", "Rin (Naruto)", "Power | Chainsaw Man"
    pairs = set()
    for c in sorted(fem, key=lambda c: -(c.get('favourites') or 0))[:800]:
        sh = _phrase(re.split(r'\s*[:：(]\s*|\s+[-–]\s+', str(c.get('anime') or ''))[0])
        for w in _words(c['name']):
            if len(w) >= 3 and len(sh) >= 4 and w not in mtok_all:      # "Kamado + Demon Slayer" is Tanjiro too
                pairs.add((w, sh))
    m_tok = distinct(mal, ftok_all, 450) | {_phrase(x) for x in MALE_EXTRA if ' ' not in x}
    f_full -= {x for x in f_full if ' ' not in x and x in mtok_all}      # one-word female names shared with a male name
    # female-led shows: favourites >= 60 % female among the AniList characters of that show
    fav = {}
    for c in chars:
        a = str(c.get('anime') or '')
        g = 0 if c.get('gender') == 'Female' else 1 if c.get('gender') == 'Male' else None
        if g is None:
            continue
        for form in {a, re.split(r'\s*[:：]\s*|\s+[-–]\s+', a)[0]}:
            p = _phrase(form)
            if p:
                fav.setdefault(p, [0, 0])[g] += c.get('favourites') or 0
    show = {p for p, (f, m) in fav.items() if f >= 0.6 * (f + m) and f > 0}
    show |= {_phrase(x) for x in list(shows) + SHOW_ALIAS}
    stop_sh = {_phrase(x) for x in SHOW_STOP}
    show = {s for s in show if s and s not in stop_sh and (' ' in s or (len(s) >= 5 and s not in STOP_R and s not in mtok_all))}
    kpop = {_phrase(i['name']) for i in idols if len(_phrase(i['name'])) >= 3} | {_phrase(i.get('group') or '') for i in idols}
    kpop |= {_phrase(x) for x in KPOP_GROUPS}
    kpop.discard('')
    _C[key] = dict(needs_sig={_phrase(x) for x in SHOW_NEEDS_SIG}, pairs=pairs, f_full=f_full, m_full=m_full, f_tok=f_tok, m_tok=m_tok, show=show, kpop=kpop)
    return _C[key]


def _hits(t, phrases):
    return sorted(p for p in phrases if f' {p} ' in t)


def title_check(title, shows=(), females=()):
    """(ok, reasons) - see the module doc. shows / females: extra (curated) show and female character names"""
    L = _load(shows, females)
    t0 = norm(title)
    m = HARD_OFF.search(t0)
    if m:
        return False, [f'off: {m.group(0).strip()}']
    sig = bool(ANIME_SIG.search(t0))
    t = _ro(t0)                                        # names / shows match on the collapsed romanization
    f_full = _hits(t, L['f_full'])
    sh = _hits(t, L['show'])
    sh_ok = [s for s in sh if (s not in L['needs_sig'] and (' ' in s or len(s) >= 6)) or sig]
    rest = t
    for s in sh + f_full:                              # names inside a matched show / female name are not male hits
        rest = rest.replace(f' {s} ', ' # ')
    m_hit = _hits(rest, L['m_full']) + [w for w in rest.split() if w in L['m_tok']]
    f_tok = [w for w in rest.split() if w in L['f_tok']]
    generic = bool(GENERIC_FEMALE.search(t0))
    kp = KPOP_RE.search(t0)
    kp_names = _hits(t, L['kpop'])
    if (kp or kp_names) and not (f_full or sh_ok):
        return False, ['kpop: ' + (kp.group(0).strip() if kp else kp_names[0])]
    female = f_full + (f_tok if sig else []) + [f'{w} + {p}' for w, p in L['pairs'] if f' {w} ' in t and f' {p} ' in t]
    if female:
        return True, ['female: ' + female[0]]
    if m_hit:
        return False, ['male lead: ' + m_hit[0]]
    if sh_ok:
        return True, ['show: ' + sh_ok[0]]
    if generic and sig:
        return True, ['generic female anime']
    return False, ['no female character / show']


def title_ok(title, shows=(), females=()):
    return title_check(title, shows, females)[0]


if __name__ == '__main__':
    import sys
    for a in sys.argv[1:]:
        print(title_check(a), a)
