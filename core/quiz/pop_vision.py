"""
Snapshot-time vision for the new pop quiz formats (silhouette, eyes, outfit): free CPU models, cached outside
the repo (POP_MODELS or ~/.cache/pop_quiz_models). Used by scripts/build-pop-media.py --newformats; the daily
render never runs a model, it only reads the masks / crops / gate results stored in the snapshot.

  Segmenter   ISNet anime-seg (the rembg "isnet-anime" ONNX)           -> soft mask of the character
  Tagger      WD14 ViT tagger v3 (SmilingWolf, ONNX)                    -> rating + danbooru-style tags
  eye boxes   core/edit/fx_eyes.EyeDetector (imported, CPU path)        -> both eyes
  upscale     Real-ESRGAN realesr-animevideov3 (x4, 2.4 MB)             -> sharp crops from AniList's 230 px art

GATE (age class decides the rule):
  always rejected, everyone:  questionable / explicit rating, nudity, genitals, sex acts, underwear / panties /
                              lingerie / pantyshot / see-through, hentai (isAdult) sources
  adult (18+, confirmed):     "sensitive" accepted; swimwear, bare legs / feet and suggestive poses allowed
  everyone:                  questionable > 0.20 / explicit > 0.10 rejected
  everyone, colour art only:  monochrome / greyscale / lineart / comic / sketch rejected
  minor / unknown:            strict: rating "general" only, no swimwear / suggestive / body-focus tags
"""
import os
import sys

import numpy as np
from PIL import Image

MODELS = os.environ.get('POP_MODELS') or os.path.join(os.path.expanduser('~'), '.cache', 'pop_quiz_models')
HERE = os.path.dirname(os.path.abspath(__file__))

# ---- tag rules (WD14 / danbooru tag names, underscores)
ALWAYS_REJECT = {
    'nude', 'completely_nude', 'nipples', 'areolae', 'pussy', 'penis', 'testicles', 'anus', 'uncensored', 'censored', 'mosaic_censoring',
    'sex', 'vaginal', 'oral', 'fellatio', 'cum', 'cumdrip', 'paizuri', 'masturbation', 'fingering', 'bdsm', 'bondage', 'rape', 'ahegao',
    'topless', 'bottomless', 'naked_shirt', 'naked_towel', 'naked_apron', 'no_bra', 'no_panties', 'nipple_slip', 'wardrobe_malfunction',
    'panties', 'underwear', 'lingerie', 'bra', 'pantyshot', 'panty_peek', 'upskirt', 'see-through', 'see-through_clothes',
    'sheer_clothes', 'thong', 'g-string', 'micro_bikini', 'slingshot_swimsuit', 'pubic_hair', 'crotch', 'groin', 'ass_focus',
    'crotch_focus', 'butt_crack', 'pussy_juice', 'spread_pussy', 'spread_legs', 'sex_toy', 'pasties', 'male_focus_nude',
    'underwear_only', 'bra_lift', 'shirt_lift', 'skirt_lift', 'lifted_by_self', 'clothes_lift', 'dress_lift', 'breasts_out',
    'topless_male', 'male_underwear', 'briefs', 'boxers', 'fundoshi', 'jockstrap',
    'no_pants', 'cameltoe', 'covered_nipples', 'areola_slip', 'pussy_peek', 'ass_visible_through_thighs', 'panties_under_pantyhose',
    'reverse_bunnysuit', 'foot_focus', 'soles',
    # coordinator 2026-10-11: any censor means the art underneath is nude
    'bar_censor', 'heart_censor', 'convenient_censoring', 'novelty_censor', 'censored_nipples', 'light_censor', 'blur_censor',
    'steam_censor', 'tape_censor', 'nipple_tape', 'nipple_covers', 'nipple_stickers', 'pasties', 'heart_pasties', 'star_pasties',
    'maebari', 'censor_bar', 'bandaid_on_nipples', 'bottomless', 'topless',
}
TATTOO_TAGS = {'tattoo', 'breast_tattoo', 'body_markings', 'slayer_mark', 'facial_mark', 'arm_tattoo', 'chest_tattoo', 'stomach_tattoo', 'leg_tattoo', 'shoulder_tattoo'}
TATTOO_MISREAD = {'pasties', 'heart_pasties', 'star_pasties', 'nipple_tape', 'nipple_covers', 'nipple_stickers', 'maebari', 'novelty_censor'}   # only these; a real censor tag (censored / bar_censor / ...) still rejects
STRICT_REJECT = {  # minors and unknown ages
    'swimsuit', 'bikini', 'swimwear', 'one-piece_swimsuit', 'competition_swimsuit', 'school_swimsuit', 'bikini_top_only',
    'cleavage', 'large_breasts', 'huge_breasts', 'gigantic_breasts', 'breast_focus', 'breasts', 'underboob', 'sideboob',
    'ass', 'thighs', 'thick_thighs', 'thigh_focus', 'leg_focus', 'foot_focus', 'midriff', 'navel', 'stomach', 'bare_legs', 'barefoot',
    'feet', 'toes', 'soles', 'from_below', 'sexually_suggestive', 'seductive_smile', 'suggestive', 'lying', 'on_back', 'bent_over',
    'all_fours', 'squatting', 'on_stomach', 'crop_top', 'microskirt', 'short_shorts', 'micro_shorts', 'leotard', 'playboy_bunny',
    'bare_shoulders', 'off_shoulder', 'wet', 'wet_clothes', 'sweat', 'blush_stickers_x', 'bathing', 'bath', 'onsen', 'towel',
    'shower_(place)', 'steam', 'strapless', 'backless_outfit', 'bodysuit', 'latex', 'lower_body', 'head_out_of_frame', 'torso_crop',
    'cowboy_shot', 'jewelry_x', 'pectorals', 'abs', 'bare_back', 'bare_chest', 'open_shirt', 'sideless_outfit',
}
NOT_COLOUR = {'monochrome', 'greyscale', 'lineart', 'comic', 'sketch', 'partially_colored', 'spot_color'}
# Classes the gate understands: 'adult' (full adult rules), 'adult-modest' (adult rules, no suggestive poses), and everything
# else strict ('minor', 'unknown', 'adult-strict' = per-character override in assets/appearance-overrides.json,
# 'adult-young-picture' = THIS picture is tagged as a child / aged-down drawing).
ADULT_CLASSES = ('adult', 'adult-modest')
YOUNG_LOOK = ('loli', 'shota', 'child', 'aged_down', 'female_child', 'male_child', 'toddler')
SUGGESTIVE = {'sexually_suggestive', 'suggestive', 'seductive_smile', 'lying', 'on_back', 'on_stomach', 'bent_over', 'all_fours',
              'from_below', 'ass', 'breast_focus', 'thigh_focus', 'leg_focus', 'cleavage', 'underboob', 'sideboob', 'arched_back',
              'leaning_forward', 'knees_up', 'wet'}
RATING_NAMES = ('general', 'sensitive', 'questionable', 'explicit')
TH_REJECT = 0.30
TH_STRICT = 0.28

_cache = {}


MODEL_URLS = {   # downloaded once when missing (CI runner: cached with actions/cache)
    'isnet-anime.onnx': 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-anime.onnx',
    'wd-v3.onnx': 'https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/main/model.onnx',
    'wd-v3-tags.csv': 'https://huggingface.co/SmilingWolf/wd-vit-tagger-v3/resolve/main/selected_tags.csv',
    'realesr-animevideov3.pth': 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth',
}


def _model(name):
    p = os.path.join(MODELS, name)
    if not os.path.exists(p) and name in MODEL_URLS:
        import urllib.request
        os.makedirs(MODELS, exist_ok=True)
        print(f'[pop_vision] downloading {name}', file=sys.stderr, flush=True)
        with urllib.request.urlopen(urllib.request.Request(MODEL_URLS[name], headers={'User-Agent': 'Mozilla/5.0'}), timeout=600) as r,                 open(p + '.tmp', 'wb') as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        os.replace(p + '.tmp', p)
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    return p


def _ort(name):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(os.environ.get('POP_THREADS', '4'))
    prov = [p for p in ('CUDAExecutionProvider', 'CPUExecutionProvider') if p in ort.get_available_providers()]   # GPU on Kaggle
    return ort.InferenceSession(_model(name), so, providers=prov)


def to_rgb(img):
    """Any PIL image -> RGB on white."""
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        bg = Image.new('RGBA', img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        return bg.convert('RGB')
    return img.convert('RGB')


# ------------------------------------------------------------------ segmentation
def mask(img):
    """Soft character mask (float32 0-1, same size as `img`) from ISNet anime-seg."""
    if 'seg' not in _cache:
        _cache['seg'] = _ort('isnet-anime.onnx')
    s = _cache['seg']
    rgb = to_rgb(img)
    a = np.asarray(rgb.resize((1024, 1024), Image.BILINEAR), np.float32) / 255.0
    a = (a - np.array([0.485, 0.456, 0.406], np.float32))
    out = s.run(None, {s.get_inputs()[0].name: a.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    out = (out - out.min()) / max(1e-6, float(out.max() - out.min()))
    m = Image.fromarray((out * 255).astype(np.uint8)).resize(rgb.size, Image.BICUBIC)
    return np.asarray(m, np.float32) / 255.0


def shape_stats(m):
    """Coverage, solidity (area / convex hull), biggest-blob share, bbox (x0, y0, x1, y1 in px) of a 0-1 mask."""
    import cv2
    b = (m > 0.5).astype(np.uint8)
    h, w = b.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    if n <= 1:
        return None
    areas = st[1:, cv2.CC_STAT_AREA]
    big = 1 + int(np.argmax(areas))
    comp = (lab == big).astype(np.uint8)
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.contourArea(cv2.convexHull(max(cnts, key=cv2.contourArea)))
    x, y, bw, bh = st[big, cv2.CC_STAT_LEFT], st[big, cv2.CC_STAT_TOP], st[big, cv2.CC_STAT_WIDTH], st[big, cv2.CC_STAT_HEIGHT]
    return {'coverage': float(comp.sum()) / (w * h), 'solidity': float(comp.sum()) / max(1.0, hull),
            'blob': float(areas.max()) / float(areas.sum()), 'bbox': (int(x), int(y), int(x + bw), int(y + bh)), 'size': (w, h)}


# ------------------------------------------------------------------ tagger
def tag(img):
    """{'rating': {general..}, 'tags': {name: prob >= 0.2}} for a PIL image (WD14 ViT v3)."""
    import csv
    if 'tag' not in _cache:
        rows = list(csv.reader(open(_model('wd-v3-tags.csv'), encoding='utf-8')))[1:]
        _cache['tag'] = (_ort('wd-v3.onnx'), [r[1] for r in rows], [int(r[2]) for r in rows])
    s, names, cats = _cache['tag']
    rgb = to_rgb(img)
    w, h = rgb.size
    side = max(w, h)
    sq = Image.new('RGB', (side, side), (255, 255, 255))
    sq.paste(rgb, ((side - w) // 2, (side - h) // 2))
    a = np.asarray(sq.resize((448, 448), Image.BICUBIC), np.float32)[:, :, ::-1]
    p = s.run(None, {s.get_inputs()[0].name: a[None].astype(np.float32)})[0][0]
    rating = {n: float(p[i]) for i, (n, c) in enumerate(zip(names, cats)) if c == 9}
    tags = {n: float(p[i]) for i, (n, c) in enumerate(zip(names, cats)) if c == 0 and p[i] >= 0.2}
    return {'rating': rating, 'tags': tags}


def effective_class(t, cls):
    """The class for THIS picture: an adult rule falls back to strict when the picture itself is tagged as a child /
    aged-down drawing (WD14 loli / shota / child / aged_down ...). No body-shape heuristics (owner 2026-10-09)."""
    if cls in ADULT_CLASSES and any(t['tags'].get(k, 0) >= 0.15 for k in YOUNG_LOOK):
        return 'adult-young-picture'
    return cls


def gate(t, age_class, framing=None):
    """
    (ok, reason) for a tag() result under the age class's rule. framing: optional list of tags of which one
    must be present for minors / unknown ("portrait", "upper_body", "full_body", "close-up", ...).
    When in doubt: reject.
    """
    r, tg = t['rating'], t['tags']
    top = max(r, key=r.get)
    if top in ('questionable', 'explicit') or r.get('explicit', 0) > 0.10 or r.get('questionable', 0) > 0.20:
        return False, f'rating {top} (q {r.get("questionable", 0):.2f}, e {r.get("explicit", 0):.2f})'
    # tattoos / body markings / slayer marks are art on the skin, never a reject; a tattoo on the chest is often read as pasties by WD14
    tattoo = any(tg.get(k, 0) >= 0.3 for k in TATTOO_TAGS)
    hit = sorted(k for k in ALWAYS_REJECT if tg.get(k, 0) >= TH_REJECT and not (tattoo and k in TATTOO_MISREAD))
    if hit:
        return False, 'tag ' + ','.join(hit[:3])
    hit = sorted(k for k in NOT_COLOUR if tg.get(k, 0) >= 0.35)
    if hit:   # colour art only (black-and-white manga panels / line art look out of place and are near impossible to guess)
        return False, 'not colour art: ' + ','.join(hit[:3])
    if age_class == 'adult-modest':
        hit = sorted(k for k in SUGGESTIVE if tg.get(k, 0) >= 0.3)
        if hit:
            return False, 'modest: suggestive ' + ','.join(hit[:3])
    if age_class in ADULT_CLASSES:
        return True, 'adult ok' + (' (sensitive)' if top == 'sensitive' else '')
    if top != 'general' or r.get('general', 0) < 0.6:
        return False, f'strict: rating {top} {r.get(top, 0):.2f}'
    hit = sorted(k for k in STRICT_REJECT if tg.get(k, 0) >= TH_STRICT)
    if hit:
        return False, 'strict tag ' + ','.join(hit[:3])
    if framing and not any(tg.get(k, 0) >= 0.3 for k in framing):
        return False, 'strict: framing'
    return True, 'strict ok'


# ------------------------------------------------------------------ eyes
def eyes(img):
    """[(cx, cy, w, h, conf)] in 0-1 units from the edit pipeline's anime eye detector, forced onto the CPU."""
    import cv2
    if 'eye' not in _cache:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'core', 'edit'))
        import fx_eyes
        d = fx_eyes.EyeDetector.__new__(fx_eyes.EyeDetector)
        d.conf, d.m = 0.25, None
        d.net = cv2.dnn.readNetFromONNX(fx_eyes._model_path())
        _cache['eye'] = d
    bgr = np.asarray(to_rgb(img))[:, :, ::-1].copy()
    return _cache['eye'](bgr)


def eye_pair(dets, min_conf=0.5):
    """The one clear pair of eyes, or None (two sizeable eyes side by side, no third confident eye)."""
    good = [d for d in dets if d[4] >= min_conf]
    if len(good) != 2:
        return None
    a, b = sorted(good, key=lambda d: d[0])
    aw = (a[2] + b[2]) / 2
    ratio = a[2] / max(1e-6, b[2])
    dist = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
    if not (0.6 <= ratio <= 1.67) or abs(b[1] - a[1]) > 0.8 * aw or not (1.2 * aw <= dist <= 5.5 * aw):
        return None
    return a, b


# ------------------------------------------------------------------ upscale
def upscale(img, times=4):
    """Real-ESRGAN anime video v3 (SRVGGNetCompact, x4), CPU. Falls back to Lanczos when torch/weights are missing."""
    try:
        import torch
        import torch.nn as nn
        if 'sr' not in _cache:
            class Net(nn.Module):
                def __init__(self, nf=64, nc=16, up=4):
                    super().__init__()
                    body = [nn.Conv2d(3, nf, 3, 1, 1), nn.PReLU(nf)]
                    for _ in range(nc):
                        body += [nn.Conv2d(nf, nf, 3, 1, 1), nn.PReLU(nf)]
                    body.append(nn.Conv2d(nf, 3 * up * up, 3, 1, 1))
                    self.body, self.up_n, self.shuffle = nn.ModuleList(body), up, nn.PixelShuffle(up)

                def forward(self, x):
                    o = x
                    for l in self.body:
                        o = l(o)
                    return self.shuffle(o) + nn.functional.interpolate(x, scale_factor=self.up_n, mode='nearest')
            net = Net()
            sd = torch.load(_model('realesr-animevideov3.pth'), map_location='cpu')
            net.load_state_dict(sd.get('params', sd))
            _cache['sr'] = net.eval().to('cuda' if torch.cuda.is_available() and os.environ.get('POP_GPU') == '1' else 'cpu')
        rgb = to_rgb(img)
        x = torch.from_numpy(np.asarray(rgb, np.float32) / 255.0).permute(2, 0, 1)[None].to(next(_cache['sr'].parameters()).device)
        with torch.no_grad():
            y = _cache['sr'](x)[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
        out = Image.fromarray((y * 255 + 0.5).astype(np.uint8))
        if times != 4:
            out = out.resize((rgb.size[0] * times, rgb.size[1] * times), Image.LANCZOS)
        return out
    except Exception as e:  # noqa: BLE001
        print(f'[pop_vision] upscale fallback (Lanczos): {e}', file=sys.stderr)
        rgb = to_rgb(img)
        return rgb.resize((rgb.size[0] * times, rgb.size[1] * times), Image.LANCZOS)
