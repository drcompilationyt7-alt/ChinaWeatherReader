"""
On-model check for fan art (owner 2026-10-09): fan art adds style and variety, but a fan of the anime must recognise the
character at once (the design as in the ANIME; rendering / lighting may differ).

Reference = the anime look: the AniList portrait + the wiki infobox / gallery pictures (screenshots, key visuals).
Measured on the HEAD (face crop from the anime eye detector; tall pictures without a clear face: the top square), with
SigLIP (google/siglip-base-patch16-224, CPU, local Hugging Face cache):
  margin   cosine to the character's own official art minus the best cosine to any castmate's (other characters of the same
           show, AniList portraits). A plain similarity does not separate characters (same-show castmates scored HIGHER
           than the character's own gallery art: art style dominates), the margin does.
  hair     zero-shot hair-colour distribution (SigLIP text prompts) agreement with the official art's
  eyes     zero-shot eye-colour agreement; the eyes format also needs the iris colour (Lab) to match
All three must pass (thresholds from scratchpad calibration, see NEW-FORMATS-PROGRESS.md). When unsure: official art.
"""
import os

import numpy as np

import pop_vision as pv

SIGLIP = 'google/siglip-base-patch16-224'
HAIR = ['black', 'brown', 'blonde', 'red', 'orange', 'pink', 'purple', 'dark blue', 'light blue', 'green', 'white', 'silver grey']
EYE = ['black', 'brown', 'red', 'orange', 'golden yellow', 'green', 'dark blue', 'light blue', 'purple', 'pink', 'grey']
# calibrated on 40 popular characters, face-visible pictures (NEW-FORMATS-PROGRESS.md): own official gallery art passes ~40%,
# castmates' official art claimed as the character ~2%: strict on purpose (owner: when unsure, official art)
MARGIN_MIN = 0.05
HAIR_MIN = 0.35
EYE_MIN = 0.20
IRIS_MAX = 20.0
_m = {}
_refs = {}


def _load():
    if 'm' not in _m:
        import torch
        from transformers import AutoProcessor, SiglipModel
        torch.set_num_threads(int(os.environ.get('POP_THREADS', '2')))
        try:   # the local Hugging Face cache first; a CI runner downloads it once (cached with actions/cache)
            _m['p'] = AutoProcessor.from_pretrained(SIGLIP, local_files_only=True)
            _m['m'] = SiglipModel.from_pretrained(SIGLIP, local_files_only=True).eval()
        except OSError:
            _m['p'] = AutoProcessor.from_pretrained(SIGLIP)
            _m['m'] = SiglipModel.from_pretrained(SIGLIP).eval()
        _m['dev'] = 'cuda' if torch.cuda.is_available() and os.environ.get('POP_GPU') == '1' else 'cpu'
        _m['m'] = _m['m'].to(_m['dev'])
        with torch.no_grad():
            for key, words, tmpl in (('hair', HAIR, 'an anime character with {} hair'), ('eye', EYE, 'a close-up of anime {} eyes')):
                tx = _m['p'](text=[tmpl.format(w) for w in words], padding='max_length', return_tensors='pt')
                t = _m['m'].get_text_features(**{k: v.to(_m['dev']) for k, v in tx.items()})
                t = t if hasattr(t, 'shape') else t.pooler_output
                _m[key] = (t / t.norm(dim=-1, keepdim=True)).cpu().numpy()
        _m['scale'] = float(_m['m'].logit_scale.exp().detach().cpu())
    return _m['p'], _m['m']


def face_crop(img):
    """(head crop, found a face?)"""
    from PIL import Image
    rgb = pv.to_rgb(img)
    W, H = rgb.size
    pair = pv.eye_pair(pv.eyes(rgb))
    if not pair and H > 0.9 * W:   # full-body art: small face -> look again in the upper part, upscaled 2x
        top = rgb.crop((0, 0, W, int(H * 0.45)))
        p2 = pv.eye_pair(pv.eyes(top.resize((top.size[0] * 2, top.size[1] * 2), Image.LANCZOS)))
        if p2:
            pair = tuple((d[0], d[1] * 0.45, d[2], d[3] * 0.45, d[4]) for d in p2)
    if pair:
        a, b = pair
        cx, cy = (a[0] + b[0]) / 2 * W, (a[1] + b[1]) / 2 * H
        span = abs(b[0] - a[0]) * W + (a[2] + b[2]) / 2 * W
        side = max(24.0, span * 2.8)
        x0, y0 = max(0, cx - side / 2), max(0, cy - side * 0.6)
        return rgb.crop((int(x0), int(y0), int(min(W, x0 + side)), int(min(H, y0 + side)))), True
    if H > 1.2 * W:
        return rgb.crop((0, 0, W, W)), False
    return rgb, False


def _softmax(x):
    e = np.exp(x - x.max())
    return e / e.sum()


def features(img):
    """{'vec', 'hair', 'eye', 'face'} of a picture's head."""
    import torch
    p, m = _load()
    crop, face = face_crop(img)
    with torch.no_grad():
        v = m.get_image_features(**{k: x.to(_m['dev']) for k, x in p(images=crop.convert('RGB'), return_tensors='pt').items()})
        v = v if hasattr(v, 'shape') else v.pooler_output
        v = (v / v.norm(dim=-1, keepdim=True))[0].cpu().numpy()
    s = _m['scale']
    return {'vec': v, 'hair': _softmax(s * (_m['hair'] @ v)), 'eye': _softmax(s * (_m['eye'] @ v)), 'face': face}


def refs(c, official_imgs, castmate_imgs):
    """Reference of a character (cached per run): official pictures (anime look) + castmates' AniList portraits."""
    if c['id'] not in _refs:
        own = [features(im) for im in official_imgs if im is not None]
        mates = [features(im)['vec'] for im in castmate_imgs if im is not None]
        irises = [ir for ir in (iris_colour(im) for im in official_imgs if im is not None) if ir is not None]
        _refs[c['id']] = {'own': own, 'mates': mates, 'iris': irises,
                          'hair': np.mean([f['hair'] for f in own], axis=0) if own else None,
                          'eye': np.mean([f['eye'] for f in own if f['face']], axis=0) if any(f['face'] for f in own) else None}
    return _refs[c['id']]


def scores(img, ref):
    f = features(img)
    own = max(float(np.dot(f['vec'], o['vec'])) for o in ref['own'])
    mate = max((float(np.dot(f['vec'], v)) for v in ref['mates']), default=own - 0.1)
    out = {'sim': round(own, 3), 'margin': round(own - mate, 3), 'hair': round(float(np.dot(f['hair'], ref['hair'])), 3), 'face': f['face']}
    out['eye'] = round(float(np.dot(f['eye'], ref['eye'])), 3) if (ref['eye'] is not None and f['face']) else None
    return out


def iris_colour(img, min_conf=0.4):
    """Median Lab colour of the iris of the bigger eye of a clear pair, or None."""
    import cv2
    rgb = pv.to_rgb(img)
    W, H = rgb.size
    pair = pv.eye_pair(pv.eyes(rgb), min_conf=min_conf)
    if not pair:
        return None
    e = max(pair, key=lambda d: d[2] * d[3])
    x0, x1 = int((e[0] - e[2] * 0.22) * W), int((e[0] + e[2] * 0.22) * W)
    y0, y1 = int((e[1] - e[3] * 0.30) * H), int((e[1] + e[3] * 0.30) * H)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    a = np.asarray(rgb.crop((x0, y0, x1, y1)), np.uint8)
    lab = cv2.cvtColor(a, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    L = lab[:, 0] * 100 / 255
    keep = (L > 12) & (L < 90)
    if keep.sum() < 6:
        return None
    lab = lab[keep]
    return [float(np.median(L[keep])), float(np.median(lab[:, 1]) - 128), float(np.median(lab[:, 2]) - 128)]


def iris_distance(a, b):
    return float(np.sqrt((0.5 * (a[0] - b[0])) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2))


def check(img, ref, eyes=False):
    """(ok, scores dict, why)."""
    if not ref['own'] or ref['hair'] is None:
        return False, None, 'no official art to compare with'
    s = scores(img, ref)
    if not s['face']:
        return False, s, 'no clear face to verify the design (official art wins)'
    if s['margin'] < MARGIN_MIN:
        return False, s, f"off-model: closer to a castmate (margin {s['margin']:+.2f})"
    if s['hair'] < HAIR_MIN:
        return False, s, f"off-model: hair colour ({s['hair']:.2f})"
    if s['eye'] is None:
        return False, s, 'eye colour not comparable (official art wins)'
    if s['eye'] < EYE_MIN:
        return False, s, f"off-model: eye colour ({s['eye']:.2f})"
    if eyes:
        ir = iris_colour(img)
        if ir is None or not ref['iris'] or s['eye'] is None:
            return False, s, 'eye colour not comparable (official art wins)'
        d = min(iris_distance(ir, r) for r in ref['iris'])
        s['iris'] = round(d, 1)
        if d > IRIS_MAX:
            return False, s, f'eye colour differs ({d:.0f} > {IRIS_MAX:.0f})'
    return True, s, 'on-model'
