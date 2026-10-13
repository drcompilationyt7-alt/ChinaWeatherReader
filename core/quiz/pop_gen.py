"""AI-generated art for the thin pools (owner 2026-10-10/11): our own generation with Illustrious-XL v1.0 (SDXL anime model,
CreativeML OpenRAIL++-M licence = commercial use of the outputs allowed; NoobAI-XL was rejected: its licence prohibits commercial use).

One full-body standing picture per (scope, character) serves character / silhouette / outfit. Two stages per batch, one model at a time:
  1. generate (GPU, only while >= 7 GB VRAM is free and no edit render runs)  -> ~/.cache/pop_quiz_models/gen/out/<scope>/<id>_<seed>.png
  2. score (existing pipeline: WD14 gate + age class, ISNet, SigLIP on-model with STRICTER thresholds, artefact tags, theme tag)
     -> ai-cache.json (own file: never races the extras cache) + staged pictures in gen/stage/<scope>/<fmt>/<id>.jpg
pop_extras.finish_theme / finish_v2 merge an AI item only where NO real item exists for that character (real art wins), copy the
staged picture into assets/pop2 and mark the item source kind 'ai' / srctype 'AI'.
Rules: adults only for swimwear / suggestive prompts (theme age rule + overrides), strict-class characters get general prompts only,
same WD14 gate / censor / child-bodied rules as every other source.
"""
import json
import os
import random
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pop_vision as pv  # noqa: E402

GEN = os.path.join(pv.MODELS, 'gen')
MODEL_FILE = os.environ.get('POP_GEN_MODEL') or os.path.join(GEN, 'Illustrious-XL-v1.0.safetensors')
OUT = os.path.join(GEN, 'out')
STAGE = os.path.join(GEN, 'stage')
AI_CACHE = os.path.join(pv.MODELS, 'ai-cache.json')
DB_CACHE = os.path.join(pv.MODELS, 'ai-dbcache.json')   # Danbooru answers found by this module (the shared extras cache is never written)
MODEL_NAME = 'Illustrious-XL v1.0'
W, H, STEPS, CFG = 768, 1152, 24, 5.5
MIN_VRAM_GB = 7.0

# stricter than fan art (owner: AI only if GOOD and VERY close to the character)
AI_MARGIN, AI_HAIR, AI_EYE = 0.10, 0.55, 0.35
ARTEFACT_TAGS = ('bad_anatomy', 'bad_hands', 'bad_feet', 'extra_arms', 'extra_eyes', 'extra_legs', 'extra_digits', 'fewer_digits', 'deformed',
                 'missing_limb', 'disembodied_limb', 'disembodied_head', 'mutated_hands', 'text', 'english_text', 'signature', 'watermark', 'artist_name',
                 'username', 'speech_bubble', 'twitter_username', 'web_address', 'multiple_views', 'comic', 'chibi', 'cropped_legs')
ART_THRESH = 0.30

BASE_Q = 'masterpiece, best quality, amazing quality, very aesthetic, absurdres, newest'
NEG = ('lowres, worst quality, bad quality, bad anatomy, bad hands, extra digits, fewer digits, missing fingers, extra arms, extra legs, '
       'jpeg artifacts, signature, watermark, text, username, artist name, multiple views, chibi, monochrome, greyscale, cropped, out of frame, '
       'nsfw, explicit, nude, nipples, underwear, panties, bra, pantyshot, see-through, topless, child, loli, shota, aged down, '
       'flat chest, petite, blurry, sketch, 3d, realistic, photo, ai-generated')
FEMALE_TAGS = 'adult, mature female, 1girl, solo'

# theme prompt parts: {adult: [...], general: [...]} per sex. adult = confirmed adults without a strict override; general = everyone else
STYLE = {
    'beach': {'f': {'adult': ['bikini', 'frilled bikini', 'one-piece swimsuit', 'side-tie bikini, sarong', 'bikini, open shirt']},
              'm': {'adult': ['swim trunks, hawaiian shirt', 'swim trunks, open jacket']}},
    'halloween': {'f': {'adult': ['witch hat, witch costume, black dress', 'vampire costume, cape', 'halloween costume, devil horns, devil tail, pumpkin',
                                  'halloween costume, cat ears, tail, pumpkin'],
                        'general': ['witch hat, witch costume, black dress, long sleeves', 'vampire costume, cape', 'halloween costume, pumpkin']},
                  'm': {'adult': ['vampire costume, cape', 'witch hat, robe, halloween costume', 'halloween costume, wolf ears, tail'],
                        'general': ['vampire costume, cape', 'halloween costume, pumpkin']}},
    'festival': {'f': {'adult': ['yukata, floral print, obi, hair ornament, uchiwa', 'kimono, obi, hair ornament', 'yukata, paper lantern, fireworks'],
                       'general': ['yukata, floral print, obi, hair ornament', 'kimono, obi']},
                 'm': {'adult': ['yukata, obi, uchiwa', 'jinbei, uchiwa'], 'general': ['yukata, obi', 'jinbei']}},
    'winter': {'f': {'adult': ['santa costume, santa hat', 'winter clothes, scarf, coat, snow', 'christmas, scarf, sweater, mittens'],
                     'general': ['santa costume, santa hat, long sleeves', 'winter clothes, scarf, coat, snow']},
               'm': {'adult': ['winter clothes, scarf, coat, snow', 'santa costume, santa hat'], 'general': ['winter clothes, scarf, coat, snow']}},
    'valentine': {'f': {'adult': ['valentine, holding gift box, red ribbon, heart', 'valentine, heart-shaped box, chocolate, dress'],
                        'general': ['valentine, holding gift box, red ribbon, heart', 'chocolate, heart, dress']},
                  'm': {'adult': ['valentine, holding gift box, red ribbon, suit'], 'general': ['holding gift box, red ribbon, heart']}},
    'maid': {'f': {'adult': ['maid, maid headdress, maid apron, frills', 'maid, maid headdress, cafe, tray'],
                   'general': ['maid, maid headdress, maid apron, long sleeves, frills']}},
    'gym': {'f': {'adult': ['gym uniform, shorts, sneakers, sportswear', 'track jacket, track pants, sneakers', 'volleyball uniform, knee pads, sneakers'],
                  'general': ['gym uniform, shorts, sneakers, short sleeves', 'track jacket, track pants, sneakers']},
            'm': {'adult': ['gym uniform, shorts, sneakers, sportswear', 'track jacket, track pants, sneakers'],
                  'general': ['gym uniform, shorts, sneakers, short sleeves', 'track jacket, track pants, sneakers']}},
}
APPEAR_RX = re.compile(r'^(.+_hair|.+_eyes|ahoge|glasses|semi-rimless_eyewear|rimless_eyewear|round_eyewear|animal_ears|cat_ears|fox_ears|horns|pointy_ears|'
                       r'tail|mole_under_eye|mole_under_mouth|scar|scar_on_face|eyepatch|facial_mark|hair_ornament|hairband|hair_ribbon|headband|'
                       r'twintails|ponytail|side_ponytail|braid|twin_braids|drill_hair|hair_bun|double_bun|bob_cut|hime_cut|hair_over_one_eye|'
                       r'heterochromia|fang|hair_between_eyes|sidelocks|crossed_bangs|swept_bangs|blunt_bangs|forehead)$')
BAD_APPEAR = re.compile(r'(messy|wet|bangs|between|sidelocks|multicolored|gradient|streaked|colored_inner|two-tone|hair_over)')


class AICacheView:
    """Read-only view of the shared extras cache (this process NEVER writes it: other builds own that file) with an overlay for the keys
    this module adds (Danbooru answers: dbtag / dbai1), persisted in ai-dbcache.json."""

    def __init__(self, pe):
        self.d = dict(pe.Cache().d)
        try:
            self.extra = json.load(open(DB_CACHE, encoding='utf-8'))
        except (OSError, ValueError):
            self.extra = {}
        self.d.update(self.extra)

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v
        self.extra[k] = v

    def save(self):
        json.dump(self.extra, open(DB_CACHE + '.tmp', 'w', encoding='utf-8'))
        os.replace(DB_CACHE + '.tmp', DB_CACHE)


def gpu_free_gb():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=20).stdout
        return int(out.strip().splitlines()[0]) / 1024.0
    except Exception:  # noqa: BLE001
        return 0.0


def edit_render_running():
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'compose_blocks|aep_replay|edit-pipeline' } "
           "| Measure-Object | Select-Object -ExpandProperty Count")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, text=True, timeout=30)
        return int((r.stdout or '0').strip() or 0) > 0
    except Exception:  # noqa: BLE001
        return False


def gpu_ok():
    return gpu_free_gb() >= MIN_VRAM_GB and not edit_render_running()


def load_ai_cache():
    try:
        return json.load(open(AI_CACHE, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_ai_cache(d):
    os.makedirs(os.path.dirname(AI_CACHE), exist_ok=True)
    json.dump(d, open(AI_CACHE + '.tmp', 'w', encoding='utf-8'))
    os.replace(AI_CACHE + '.tmp', AI_CACHE)


def appearance_tags(c, cache, ai):
    """Hair / eye colour + hairstyle + accessories from WD14 on the official portrait (cached in the ai cache)."""
    k = f"look:{c['id']}"
    if k in ai:
        return ai[k]
    import pop_extras as pe
    im = pe.load_img(c['image'])
    t = pv.tag(pe.flatten(im))['tags']
    sel = [(n, p) for n, p in t.items() if p >= 0.5 and APPEAR_RX.match(n) and not BAD_APPEAR.search(n)]
    hair = sorted([x for x in sel if x[0].endswith('_hair')], key=lambda x: -x[1])[:2]
    eyes = sorted([x for x in sel if x[0].endswith('_eyes')], key=lambda x: -x[1])[:1]
    rest = sorted([x for x in sel if not x[0].endswith('_hair') and not x[0].endswith('_eyes')], key=lambda x: -x[1])[:6]
    tags = [n.replace('_', ' ') for n, _ in hair + eyes + rest]
    ai[k] = tags
    return tags


def make_prompt(c, scope, cls, tag, look, rng):
    """(prompt, negative, style_index). Adults get the adult variants of the theme; anyone else only the general ones."""
    male = c.get('gender') == 'Male'
    st = STYLE.get(scope)
    who = 'm' if male else 'f'
    if st and who in st:
        pool = st[who]
        adult_ok = cls in pv_adult()
        variants = pool.get('adult' if adult_ok else 'general') or pool.get('adult' if not adult_ok else 'general')
        if not adult_ok and not pool.get('general'):
            return None
        theme_part = rng.choice(variants)
    elif st:
        return None
    else:
        theme_part = ''
    name = re.sub(r'_\(.*\)$', '', tag).replace('_', ' ')
    series = re.findall(r'_\((.*)\)$', tag)
    who_tag = ('1boy, solo, adult male' if male else FEMALE_TAGS) if cls in pv_adult() else ('1boy, solo' if male else '1girl, solo')
    parts = [who_tag, name] + ([series[0].replace('_', ' ')] if series else []) + look + ['full body', 'standing', 'looking at viewer', 'simple background',
                                                                                            'white background', 'sfw']
    if theme_part:
        parts.insert(3, theme_part)
    return BASE_Q + ', ' + ', '.join(parts), NEG


def pv_adult():
    return pv.ADULT_CLASSES


def load_pipe():
    import torch
    from diffusers import StableDiffusionXLPipeline, EulerAncestralDiscreteScheduler
    try:   # straight to the GPU: the 6.9 GB checkpoint never sits in RAM (the edit agent uses most of it)
        pipe = StableDiffusionXLPipeline.from_single_file(MODEL_FILE, torch_dtype=torch.float16, use_safetensors=True, device_map='cuda')
    except Exception as e:  # noqa: BLE001
        print('device_map load failed, using the CPU path:', str(e)[:80], flush=True)
        pipe = StableDiffusionXLPipeline.from_single_file(MODEL_FILE, torch_dtype=torch.float16, use_safetensors=True)
        pipe.to('cuda')
    pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
    pipe.set_progress_bar_config(disable=True)
    return pipe


def free_pipe(pipe):
    import gc
    import torch
    del pipe
    gc.collect()
    torch.cuda.empty_cache()


def jobs_for(scope, pool_chars, pe, cache, ai, anime, th, n_chars, only_ids=None):
    """Next characters that need AI art in this scope: popular first (theme_order: ~75 % women), with a Danbooru tag, rule class allowed,
    no real item for ALL of the formats we could add yet, not tried before."""
    import pop_fanart as pf
    out = []
    for c in pe.theme_order(pool_chars):
        if len(out) >= n_chars:
            break
        k = f"ai:{scope}:{c['id']}"
        if k in ai:
            continue
        cls = pe.theme_class(c, th, cache, anime) if th else pe.rule_class(c, cache, anime)
        if cls is None:
            continue
        if scope == 'base':   # normal pools (silhouette / outfit); only characters a selector theme (villains, glasses) picked, plus the rest later
            if only_ids is not None and c['id'] not in only_ids:
                continue
            real = cache.get(f"so:{c['id']}:v2")
            if isinstance(real, dict) and isinstance(real.get('silhouette'), dict) and 'dropped' not in real['silhouette']:
                continue
        else:
            real = cache.get(f"theme:{scope}:{c['id']}") if th else None
            if isinstance(real, dict) and all(isinstance(real.get(f), dict) and 'dropped' not in real[f] for f in ('character', 'silhouette')):
                continue
        tag = cache.get(f"dbtag:{c['id']}")
        if tag is None:
            try:
                tag = pf.find_tag(c, pe.show_titles(c, anime), cache)
            except Exception:  # noqa: BLE001
                tag = ''
        if not tag:
            ai[k] = {'skip': 'no danbooru tag (the model does not know them)'}
            continue
        out.append((c, cls, tag))
    return out


def generate(jobs, scope, seeds, ai, minutes_left, pe, cache):
    """Stage 1: generate images (GPU). Returns the paths per character id."""
    import torch
    os.makedirs(os.path.join(OUT, scope), exist_ok=True)
    pipe = None
    made = {}
    try:
        for c, cls, tag in jobs:
            if time.time() > minutes_left:
                break
            while time.time() < minutes_left and not gpu_ok():
                print(f'gen: waiting (free VRAM {gpu_free_gb():.1f} GB / edit render)', flush=True)
                if pipe is not None:
                    free_pipe(pipe)
                    pipe = None
                time.sleep(120)
            if not gpu_ok():
                break
            if pipe is None:
                pipe = load_pipe()
            look = appearance_tags(c, cache, ai)
            rng = random.Random(c['id'] * 7919 + 13)
            files = []
            for s in range(seeds):
                p = make_prompt(c, scope, cls, tag, look, rng)
                if p is None:
                    break
                path = os.path.join(OUT, scope, f"{c['id']}_{s}.png")
                if not os.path.exists(path):
                    gen = torch.Generator('cpu').manual_seed(c['id'] * 101 + s)
                    img = pipe(prompt=p[0], negative_prompt=p[1], width=W, height=H, num_inference_steps=STEPS, guidance_scale=CFG, generator=gen).images[0]
                    img.save(path)
                    json.dump({'prompt': p[0], 'seed': c['id'] * 101 + s, 'model': MODEL_NAME}, open(path[:-4] + '.json', 'w'))
                files.append(path)
            if files:
                made[c['id']] = files
    finally:
        if pipe is not None:
            free_pipe(pipe)
    return made


def strict_check(pic, ref, om):
    ok, s, why = om.check(pic, ref)
    if not ok:
        return False, s, why
    if s['margin'] < AI_MARGIN:
        return False, s, f"AI off-model: margin {s['margin']:+.2f} < {AI_MARGIN}"
    if s['hair'] < AI_HAIR:
        return False, s, f"AI off-model: hair {s['hair']:.2f}"
    if s['eye'] is None or s['eye'] < AI_EYE:
        return False, s, f"AI off-model: eye {s['eye']}"
    return True, s, 'on-model (AI strict)'


def stage_result(scope, f, cid, r, pe):
    """Write the picture(s) of a passing item into the staging folder (merged by finish_theme / finish_v2 where no real item exists)."""
    d = os.path.join(STAGE, scope, f)
    os.makedirs(d, exist_ok=True)
    if f == 'silhouette':
        pe.save_jpg(r['col'], os.path.join(d, f'{cid}.jpg'), 76)
        r['mask'].save(os.path.join(d, f'{cid}.png'), optimize=True)
        r['col'] = r['mask'] = None
    else:
        pe.save_jpg(r['full'], os.path.join(d, f'{cid}.jpg'), 80)
        r['full'] = None


def score(made, scope, th, pe, cache, ai, anime, chars_by_id, om):
    """Stage 2: gate + strict on-model + artefact tags + theme tag; best seed per format. Stages the pictures for finish_*."""
    from PIL import Image
    fmts = ['character', 'silhouette', 'outfit']
    if th:
        fmts = [f for f in fmts if f in th['formats']]
    elif scope == 'base':
        fmts = ['silhouette', 'outfit']
    n_ok = 0
    for cid, files in made.items():
        c = chars_by_id[cid]
        cls = pe.theme_class(c, th, cache, anime) if th else pe.rule_class(c, cache, anime)
        w = cache.get(f"wikichar:{cid}") or {}
        try:
            ref = pe.official_ref(c, w, cache)
        except Exception as e:  # noqa: BLE001
            ai[f'ai:{scope}:{cid}'] = {'error': str(e)[:60]}
            continue
        res = {f: {'dropped': 'no picture passed'} for f in fmts}
        best = {}
        why_all = {}
        for path in files:
            meta = json.load(open(path[:-4] + '.json'))
            pic = Image.open(path).convert('RGB')
            t = pv.tag(pe.flatten(pic))
            bad = [k for k in ARTEFACT_TAGS if t['tags'].get(k, 0) >= ART_THRESH]
            if bad:
                why_all['artefact'] = why_all.get('artefact', 0) + 1
                continue
            if th:
                ok_t, hit = pe.theme_confirm(t, th)
                if not ok_t:
                    why_all['not on theme'] = why_all.get('not on theme', 0) + 1
                    continue
            else:
                hit = []
            ok_m, sim, why_m = strict_check(pic, ref, om)
            if not ok_m:
                kk = re.sub(r'[0-9.+-]+', '#', why_m)[:40]
                why_all[kk] = why_all.get(kk, 0) + 1
                continue
            for f in fmts:
                if f == 'outfit' and not (cls in pv.ADULT_CLASSES and c.get('gender') == 'Female'):
                    continue
                fn = {'silhouette': pe.do_silhouette, 'outfit': pe.do_outfit, 'character': pe.do_character_art}[f]
                r, why = fn(c, pic, cls)
                if not r:
                    kk = f + ': ' + re.sub(r'[0-9.+-]+', '#', str(why))[:40]
                    why_all[kk] = why_all.get(kk, 0) + 1
                    continue
                if f not in best or sim['margin'] > best[f][1]['margin']:
                    best[f] = (r, sim, meta, hit)
        for f, (r, sim, meta, hit) in best.items():
            stage_result(scope, f, cid, r, pe)
            r['source'] = {'kind': 'ai', 'srctype': 'AI', 'artist': f'AI-generated ({MODEL_NAME})', 'post': '', 'model': MODEL_NAME,
                           'seed': meta['seed'], 'prompt': meta['prompt'], 'onmodel': sim,
                           'licence': 'Generated with Illustrious-XL v1.0 (CreativeML OpenRAIL++-M); character (c) its rights holders'}
            if th:
                r['theme_hit'] = hit
            res[f] = json.loads(json.dumps(r, default=lambda o: None))
            n_ok += 1
        ai[f'ai:{scope}:{cid}'] = {'class': cls, 'seeds': len(files), 'why': why_all, **res}
    return n_ok


def plan_prompts(scopes, n_per_scope=150, seeds=3, path=None):
    """Kaggle job list (owner 2026-10-11: generation runs on Kaggle, never on the local GPU): popular first, ~75 % women, characters with a
    Danbooru tag, rule class allowed, no real item for character + silhouette yet. One JSON line per image; the kernel (scripts/kaggle/ai_art_kernel.py)
    renders them, the local gates (score_dir) judge them afterwards."""
    import pop_extras as pe
    sys.path.insert(0, os.path.dirname(HERE))
    from render_pop import SCENE_SKIP, same_series, franchise
    chars = json.load(open(os.path.join(pe.ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    pool = [c for c in chars if not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) != 'bakemonogatari']
    anime = pe.anime_list()
    cache = AICacheView(pe)
    ai = load_ai_cache()
    lines = []
    for scope in scopes:
        th = pe.themes()[scope] if scope != 'base' else None
        only = None
        if scope == 'base':   # characters picked by the selector themes first (their silhouette / eyes pools are the thin ones)
            only = {int(k.split(':')[2]) for k, v in cache.d.items() if v and (k.startswith('themesel:villains:') or k.startswith('themesel:glasses:'))}
        for c, cls, tag in jobs_for(scope, pool, pe, cache, ai, anime, th, n_per_scope, only):
            look = appearance_tags(c, cache, ai)
            rng = random.Random(c['id'] * 7919 + 13)
            for sd in range(seeds):
                p = make_prompt(c, scope, cls, tag, look, rng)
                if p is None:
                    break
                if os.path.exists(os.path.join(OUT, scope, f"{c['id']}_{sd}.png")):
                    continue
                lines.append({'scope': scope, 'id': c['id'], 'k': sd, 'seed': c['id'] * 101 + sd, 'prompt': p[0], 'negative': p[1], 'w': W, 'h': H})
    save_ai_cache(ai)
    cache.save()
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            for ln in lines:
                f.write(json.dumps(ln, ensure_ascii=False) + chr(10))
    return lines


def score_dir(scopes, minutes=40):
    """Judge the images the kernel returned (OUT/<scope>/<id>_<k>.png + .json): all local gates, strict on-model; resumable (ai-cache)."""
    import pop_extras as pe
    import pop_onmodel as om
    sys.path.insert(0, os.path.dirname(HERE))
    chars = json.load(open(os.path.join(pe.ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    by_id = {c['id']: c for c in chars}
    anime = pe.anime_list()
    cache = AICacheView(pe)
    ai = load_ai_cache()
    t_end = time.time() + minutes * 60
    tot = tried = 0
    stats = {}
    for scope in scopes:
        th = pe.themes()[scope] if scope != 'base' else None
        d = os.path.join(OUT, scope)
        if not os.path.isdir(d):
            continue
        made = {}
        for f in sorted(os.listdir(d)):
            if f.endswith('.png'):
                cid = int(f.split('_')[0])
                if f'ai:{scope}:{cid}' not in ai or ai[f'ai:{scope}:{cid}'].get('skip'):
                    made.setdefault(cid, []).append(os.path.join(d, f))
        for cid, files in made.items():
            if time.time() > t_end:
                break
            while time.time() < t_end and pe.heavy_running():
                time.sleep(60)
            tot += score({cid: files}, scope, th, pe, cache, ai, anime, by_id, om)
            tried += 1
            r = ai.get(f'ai:{scope}:{cid}') or {}
            st = stats.setdefault(scope, {'chars': 0, 'passed_chars': 0, 'why': {}})
            st['chars'] += 1
            st['passed_chars'] += 1 if any(isinstance(r.get(f), dict) and 'dropped' not in r[f] for f in ('character', 'silhouette', 'outfit')) else 0
            for k, v in (r.get('why') or {}).items():
                st['why'][k] = st['why'].get(k, 0) + v
            if tried % 10 == 0:
                save_ai_cache(ai)
    save_ai_cache(ai)
    for scope in scopes:
        pe.finish_v2() if scope == 'base' else pe.finish_theme(scope)
    return stats


def aipost(scopes, minutes=40):
    """Danbooru ai-generated posts for characters whose real art was dropped / missing: stricter on-model (pop_gen thresholds), WD14 artefact tags,
    the usual gate / theme / age rules. Results go to ai-cache.json (key ai:<scope>:<id>, source srctype AI) like our own generations.
    Resumable (aipost:<scope>:<id> marker)."""
    import pop_extras as pe
    import pop_fanart as pf
    import pop_onmodel as om
    from PIL import Image
    sys.path.insert(0, os.path.dirname(HERE))
    from render_pop import SCENE_SKIP, same_series, franchise
    chars = json.load(open(os.path.join(pe.ASSETS, 'anime-characters.json'), encoding='utf-8'))['characters']
    pool = [c for c in chars if not c.get('nsfw') and not any(same_series(c['anime'], s) for s in SCENE_SKIP) and franchise(c['anime']) != 'bakemonogatari']
    anime = pe.anime_list()
    cache = AICacheView(pe)
    ai = load_ai_cache()
    t_end = time.time() + minutes * 60
    got = tried = 0
    for scope in scopes:
        th = pe.themes()[scope] if scope != 'base' else None
        fmts = ['silhouette', 'outfit'] if scope == 'base' else [f for f in ('character', 'silhouette', 'outfit') if f in th['formats']]
        only = None
        if scope == 'base':
            only = {int(k.split(':')[2]) for k, v in cache.d.items() if v and (k.startswith('themesel:villains:') or k.startswith('themesel:glasses:'))}
        for c in pe.theme_order(pool):
            if time.time() > t_end:
                break
            if only is not None and c['id'] not in only:
                continue
            mk = f"aipost:{scope}:{c['id']}"
            if ai.get(mk):
                continue
            cls = pe.theme_class(c, th, cache, anime) if th else pe.rule_class(c, cache, anime)
            if cls is None:
                continue
            real = cache.get(f"so:{c['id']}:v2") if scope == 'base' else cache.get(f"theme:{scope}:{c['id']}")
            mine = ai.get(f"ai:{scope}:{c['id']}") or {}
            need = [f for f in fmts if not (isinstance(real, dict) and isinstance(real.get(f), dict) and 'dropped' not in real[f])
                    and not (isinstance(mine.get(f), dict) and 'dropped' not in mine[f])
                    and not (f == 'outfit' and not (cls in pv.ADULT_CLASSES and c.get('gender') == 'Female'))]
            if not need:
                continue
            while time.time() < t_end and pe.heavy_running():
                time.sleep(60)
            w = cache.get(f"wikichar:{c['id']}") or {}
            res = dict(mine) if isinstance(mine, dict) else {}
            res['class'] = cls
            for f in need:
                try:
                    cands = pf.ai_candidates(c, f, pe.show_titles(c, anime), cache, th, cls)
                except Exception as e:  # noqa: BLE001
                    print('  aipost', c['name'], str(e)[:60], flush=True)
                    continue
                fn = {'silhouette': pe.do_silhouette, 'outfit': pe.do_outfit, 'character': pe.do_character_art}[f]
                for url, src, _ in cands:
                    try:
                        pic = pe.load_img(url)
                        t = pv.tag(pe.flatten(pic))
                        if [k for k in ARTEFACT_TAGS if t['tags'].get(k, 0) >= ART_THRESH]:
                            continue
                        hit = []
                        if th:
                            ok_t, hit = pe.theme_confirm(t, th)
                            if not ok_t:
                                continue
                        ok_m, sim, _why = strict_check(pic, pe.official_ref(c, w, cache), om)
                        if not ok_m:
                            continue
                        r, _why = fn(c, pic, cls)
                    except Exception:  # noqa: BLE001
                        continue
                    if r:
                        stage_result(scope, f, c['id'], r, pe)
                        r['source'] = {**src, 'onmodel': sim, 'model': 'Danbooru ai-generated post'}
                        if th:
                            r['theme_hit'] = hit
                        res[f] = json.loads(json.dumps(r, default=lambda o: None))
                        got += 1
                        print(f'aipost NEW {scope}/{f}: {c["name"]}', flush=True)
                        break
            ai[f"ai:{scope}:{c['id']}"] = res
            ai[mk] = True
            tried += 1
            if tried % 10 == 0:
                save_ai_cache(ai)
                cache.save()
    save_ai_cache(ai)
    cache.save()
    for scope in scopes:
        pe.finish_v2() if scope == 'base' else pe.finish_theme(scope)
    print(f'aipost: {tried} characters tried, {got} new items', flush=True)
    return got
