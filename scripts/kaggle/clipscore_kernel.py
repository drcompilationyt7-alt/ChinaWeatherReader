"""
zyo-clipscore: Kaggle GPU FEATURE pass over the edit-pool candidates GitHub already staged (Zero Yen Otaku; owner OK 2026-10-22).
Pushed by scripts/clipscore-kaggle.py; CONFIG below is injected by the driver (no dataset, no GitHub token, no cookies, no
YouTube: the kernel only downloads the PUBLIC release assets named in CONFIG).

  mode 'score'  CONFIG.tars   = [{name, url, kind: slowmo|sound}]   staged candidate tars (edit-pools-staging)
  mode 'calib'  CONFIG.sets   = [{url, kind}] + CONFIG.labels {id: {kind, label, hit_t?}}   by-eye labelled clips

Per clip it writes FEATURES only (the pass / fail verdict + rank score is computed on GitHub by core/pools/clipscore.py from these
features and core/pools/clipscore-thresholds.json, so a re-calibration never needs the GPU again):
  both    WD14 v3 tagger (rating, subject 1girl / 1boy, open_mouth, chibi, 3d, text, nsfw tags) on 8 frames; SigLIP so400m prompts
  slowmo  anime faces (deepghs YOLOv8 v1.4 s: share of frames, size, centring, cut off), mouth-region motion vs the face, repeated /
          jump frames + unique drawings per second, hard cuts (luma + SigLIP embedding drop) and dissolves (blend fit), colour,
          aesthetic (cafe_aesthetic)
  sound   audio transient (spectral flux) vs RAFT optical-flow motion / acceleration peak (frames off), moving area / centre / spread
          at the impact, a cut in the 0.5 s before the hit, build-up (motion before vs at the hit)
Output (/kaggle/working): feats.json {id: {...}}, stats.json; inputs live in /tmp (never in the output).
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.request

T0 = time.time()
CONFIG = json.loads(r'''__CONFIG__''')
WORK = '/kaggle/working' if os.path.isdir('/kaggle/working') else os.path.abspath(CONFIG.get('work', 'clipscore-work'))
TMP = '/tmp/zyo-clipscore'
END = T0 + float(CONFIG.get('minutes', 40)) * 60 - 4 * 60
os.makedirs(TMP, exist_ok=True)
os.makedirs(WORK, exist_ok=True)
STATS = {'mode': CONFIG.get('mode'), 'phases': {}, 'errors': []}


def log(*a):
    print(f'[{time.time() - T0:7.1f}s]', *a, flush=True)


def phase(name, t):
    STATS['phases'][name] = round(STATS['phases'].get(name, 0) + time.time() - t, 1)


t = time.time()
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'onnxruntime-gpu'], check=False)
import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

DEV = 'cuda' if torch.cuda.is_available() else 'cpu'
STATS['gpu'] = torch.cuda.get_device_name(0) if DEV == 'cuda' else None
FFMPEG = shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')
if not FFMPEG:
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'imageio-ffmpeg'], check=False)
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
phase('setup', t)
log('device', DEV, STATS['gpu'], 'ffmpeg', FFMPEG)


# ------------------------------------------------------------------ media
def probe(p):
    r = subprocess.run([FFMPEG, '-hide_banner', '-i', p], capture_output=True, text=True)
    import re
    e = r.stderr
    m = re.search(r'Duration: (\d+):(\d+):([\d.]+)', e)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    m = re.search(r'Video:.*?(\d{2,5})x(\d{2,5})', e)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    m = re.search(r'([\d.]+) fps', e)
    fps = float(m.group(1)) if m else 0.0
    if FFPROBE:
        q = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=avg_frame_rate', '-of',
                            'csv=p=0', p], capture_output=True, text=True).stdout.strip()
        try:
            a, b = q.split('/')
            fps = float(a) / float(b) or fps
        except (ValueError, ZeroDivisionError):
            pass
    return {'fps': fps or 30.0, 'dur': dur, 'w': w, 'h': h, 'audio': 'Audio:' in e}


def decode(p, w, t0=None, t1=None, src=None):
    """RGB frames (N, h, w, 3) uint8 at the clip's own frame rate (no resampling: duplicates must stay visible)"""
    src = src or probe(p)
    h = int(round(w * src['h'] / max(1, src['w']) / 2)) * 2 if src['w'] else int(w * 9 / 16)
    cmd = [FFMPEG, '-v', 'error', '-nostdin']
    if t0 is not None:
        cmd += ['-ss', f'{max(0.0, t0):.3f}']
    cmd += ['-i', p]
    if t1 is not None:
        cmd += ['-t', f'{max(0.05, t1 - max(0.0, t0 or 0)):.3f}']
    cmd += ['-vf', f'scale={w}:{h}:flags=area', '-vsync', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    a = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8)
    n = a.size // (w * h * 3)
    return a[:n * w * h * 3].reshape(n, h, w, 3)


def audio(p, sr=16000):
    r = subprocess.run([FFMPEG, '-v', 'error', '-nostdin', '-i', p, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'], capture_output=True)
    return np.frombuffer(r.stdout, np.float32)


def grey(fr):
    return fr.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def small(fr, w=64):
    t_ = torch.from_numpy(fr).permute(0, 3, 1, 2).float()
    h = max(8, int(round(w * fr.shape[1] / fr.shape[2])))
    return torch.nn.functional.interpolate(t_, size=(h, w), mode='area').permute(0, 2, 3, 1).numpy() / 255.0


# ------------------------------------------------------------------ models
def hf(repo, fn):
    from huggingface_hub import hf_hub_download
    return hf_hub_download(repo, fn)


class ORT:
    def __init__(self, path):
        import onnxruntime as ort
        prov = [p for p in ('CUDAExecutionProvider', 'CPUExecutionProvider') if p in ort.get_available_providers()]
        try:
            self.s = ort.InferenceSession(path, providers=prov)
        except Exception:  # noqa: BLE001  (CUDA libs missing -> CPU)
            self.s = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
        self.inp = self.s.get_inputs()[0].name
        STATS.setdefault('ort', {})[os.path.basename(os.path.dirname(path)) or path] = self.s.get_providers()[0]

    def __call__(self, x):
        return self.s.run(None, {self.inp: x})


class Tagger:
    """SmilingWolf wd-swinv2-tagger-v3: white-padded square, 448, BGR float 0-255 NHWC; sigmoid outputs"""
    REPO = 'SmilingWolf/wd-swinv2-tagger-v3'

    def __init__(self):
        import csv
        self.m = ORT(hf(self.REPO, 'model.onnx'))
        rows = list(csv.DictReader(open(hf(self.REPO, 'selected_tags.csv'), encoding='utf-8')))
        self.names = [r['name'] for r in rows]
        self.cat = [int(r['category']) for r in rows]
        self.size = self.m.s.get_inputs()[0].shape[1] or 448

    def prep(self, im):
        im = Image.fromarray(im)
        s = max(im.size)
        sq = Image.new('RGB', (s, s), (255, 255, 255))
        sq.paste(im, ((s - im.size[0]) // 2, (s - im.size[1]) // 2))
        a = np.asarray(sq.resize((self.size, self.size), Image.BICUBIC), np.float32)[:, :, ::-1]
        return np.ascontiguousarray(a)

    def __call__(self, ims):
        p = self.m(np.stack([self.prep(i) for i in ims]))[0]
        return p                                                   # (n, tags)


TAGS_KEEP = ['1girl', '2girls', 'multiple_girls', '1boy', '2boys', 'multiple_boys', 'male_focus', 'solo', 'open_mouth', 'closed_mouth',
             'teeth', 'tongue', 'chibi', '3d', 'english_text', 'subtitled', 'text_focus', 'speech_bubble', 'comic', 'multiple_views',
             'salute', 'talking', 'blood', 'nude', 'completely_nude', 'nipples', 'pussy', 'penis', 'sex', 'cum', 'censored', 'bikini',
             'swimsuit', 'underwear', 'panties', 'cleavage', 'large_breasts', 'face', 'portrait', 'close-up', 'upper_body', 'from_behind',
             'faceless', 'blurry', 'motion_blur', 'motion_lines', 'speed_lines', 'weapon', 'sword', 'gun', 'fighting', 'punching',
             'kicking', 'explosion', 'fire', 'magic', 'smile', 'crying', 'blush', 'eyes_closed', 'looking_at_viewer', 'scenery',
             'no_humans', 'outdoors', 'sky', 'night', 'sunset', 'cherry_blossoms', 'water', 'rain', 'wind', 'hair_flowing',
             'floating_hair', 'monochrome', 'greyscale', 'letterboxed', 'pillarboxed', 'border', 'watermark', 'logo']
NSFW_TAGS = ['nude', 'completely_nude', 'nipples', 'pussy', 'penis', 'sex', 'cum']


def tag_feats(tagger, ims):
    p = tagger(ims)
    out = {}
    for i, (n, c) in enumerate(zip(tagger.names, tagger.cat)):
        if c == 9:
            out['rating_' + n] = [round(float(p[:, i].mean()), 3), round(float(p[:, i].max()), 3)]
    idx = {n: i for i, n in enumerate(tagger.names)}
    for n in TAGS_KEEP:
        if n in idx:
            out['tag_' + n] = [round(float(p[:, idx[n]].mean()), 3), round(float(p[:, idx[n]].max()), 3)]
    om = p[:, idx['open_mouth']] > 0.5 if 'open_mouth' in idx else np.zeros(len(ims), bool)
    out['open_mouth_toggles'] = int(np.sum(om[1:] != om[:-1]))
    fem = max(out.get('tag_1girl', [0])[0], out.get('tag_multiple_girls', [0])[0], out.get('tag_2girls', [0])[0])
    mal = max(out.get('tag_1boy', [0])[0], out.get('tag_multiple_boys', [0])[0], out.get('tag_male_focus', [0])[0], out.get('tag_2boys', [0])[0])
    out['p_female'], out['p_male'] = round(fem, 3), round(mal, 3)
    out['subject'] = 'female' if fem > 0.5 and fem >= mal else 'male' if mal > 0.5 else 'none' if out.get('tag_no_humans', [0])[0] > 0.5 else 'unclear'
    out['nsfw_tag_max'] = round(max([out.get('tag_' + n, [0, 0])[1] for n in NSFW_TAGS] + [0]), 3)
    return out


class Faces:
    """deepghs anime face detector (YOLOv8 s, v1.4): 640 letterbox, output (1, 5, A) cx cy w h conf"""

    def __init__(self):
        self.m = ORT(hf('deepghs/anime_face_detection', 'face_detect_v1.4_s/model.onnx'))

    def __call__(self, im, conf=0.35):
        H, W = im.shape[:2]
        s = 640 / max(H, W)
        nh, nw = int(round(H * s)), int(round(W * s))
        canvas = np.full((640, 640, 3), 114, np.uint8)
        top, left = (640 - nh) // 2, (640 - nw) // 2
        canvas[top:top + nh, left:left + nw] = np.asarray(Image.fromarray(im).resize((nw, nh), Image.BILINEAR))
        x = (canvas.astype(np.float32) / 255).transpose(2, 0, 1)[None]
        o = self.m(x)[0][0]                                   # (5, A)
        o = o.T if o.shape[0] < o.shape[1] else o
        o = o[o[:, 4] > conf]
        boxes = []
        for cx, cy, w, h, c in sorted(o.tolist(), key=lambda r: -r[4]):
            b = [(cx - w / 2 - left) / s / W, (cy - h / 2 - top) / s / H, (cx + w / 2 - left) / s / W, (cy + h / 2 - top) / s / H, c]
            if all(_iou(b, k) < 0.5 for k in boxes):
                boxes.append(b)
        return boxes                                           # normalised x0 y0 x1 y1 conf, best first


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0


PROMPTS = {
    'beauty': 'a beautiful anime character close-up with soft cinematic lighting',
    'milky': 'a washed-out, milky, low-contrast faded anime frame',
    'garish': 'an oversaturated anime frame with harsh garish neon colors',
    'talk': 'an anime character talking with the mouth open',
    'chibi': 'a chibi super-deformed cartoon drawing',
    'mmd': 'a 3D rendered MMD video game character',
    'text': 'a video frame with large subtitles or text on screen',
    'blend': 'two different images blended together in a crossfade',
    'salute': 'an anime character saluting with a hand at the forehead',
    'placing': 'an anime character gently placing an object on a table',
    'impact': 'a powerful punch or kick impact in an anime fight',
    'slash': 'a sword slash in an anime fight',
    'gun': 'a gun firing in an anime',
    'explosion': 'an explosion in an anime',
    'wide': 'a wide chaotic battle scene with many small characters',
    'closeup_action': 'a close-up of an anime action at the moment of impact',
}


def _t(o):
    """newer transformers return a BaseModelOutputWithPooling from get_*_features: take the pooled embedding"""
    return o if torch.is_tensor(o) else (o.pooler_output if getattr(o, 'pooler_output', None) is not None else o[0])


class Sig:
    REPO = 'google/siglip-so400m-patch14-384'

    def __init__(self):
        from transformers import AutoModel, AutoProcessor
        self.p = AutoProcessor.from_pretrained(self.REPO)
        self.m = AutoModel.from_pretrained(self.REPO, torch_dtype=torch.float16 if DEV == 'cuda' else torch.float32).to(DEV).eval()
        with torch.no_grad():
            ti = self.p(text=list(PROMPTS.values()), padding='max_length', return_tensors='pt').to(DEV)
            self.te = torch.nn.functional.normalize(_t(self.m.get_text_features(**ti)).float(), dim=-1)

    @torch.no_grad()
    def embed(self, ims, bs=16):
        out = []
        for k in range(0, len(ims), bs):
            x = self.p(images=[Image.fromarray(i) for i in ims[k:k + bs]], return_tensors='pt')['pixel_values'].to(DEV, self.m.dtype)
            out.append(torch.nn.functional.normalize(_t(self.m.get_image_features(pixel_values=x)).float(), dim=-1))
        return torch.cat(out)

    @torch.no_grad()
    def prompts(self, emb):
        lg = emb @ self.te.T * self.m.logit_scale.detach().exp().float() + self.m.logit_bias.detach().float()
        p = torch.sigmoid(lg).cpu().numpy()
        return {k: [round(float(p[:, i].mean()), 4), round(float(p[:, i].max()), 4)] for i, k in enumerate(PROMPTS)}


class Aesthetic:
    def __init__(self):
        from transformers import pipeline
        self.pipe = pipeline('image-classification', model='cafeai/cafe_aesthetic', device=0 if DEV == 'cuda' else -1)

    def __call__(self, ims):
        r = self.pipe([Image.fromarray(i) for i in ims])
        return round(float(np.mean([next((x['score'] for x in rr if x['label'] == 'aesthetic'), 0.0) for rr in r])), 4)


class Flow:
    def __init__(self):
        from torchvision.models.optical_flow import Raft_Large_Weights, raft_large
        w = Raft_Large_Weights.DEFAULT
        self.m = raft_large(weights=w).to(DEV).eval()
        self.tf = w.transforms()

    @torch.no_grad()
    def __call__(self, fr, bs=8):
        """flow (N-1, 2, h, w) between consecutive RGB frames (h, w divisible by 8)"""
        x = torch.from_numpy(np.ascontiguousarray(fr)).permute(0, 3, 1, 2)
        out = []
        for k in range(0, len(x) - 1, bs):
            a, b = x[k:k + bs], x[k + 1:k + 1 + bs]
            a = a[:len(b)]
            a, b = self.tf(a, b)
            out.append(self.m(a.to(DEV), b.to(DEV))[-1].float().cpu())
        return torch.cat(out).numpy() if out else np.zeros((0, 2) + fr.shape[1:3], np.float32)


# ------------------------------------------------------------------ shared stats
def stutter_stats(g, fps, dur):
    if len(g) < 6:
        return {'rep': 1.0, 'jump': 1.0, 'stutter': 2.0, 'unique_fps': 0.0, 'max_rep_run': 99}
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    loc = np.array([np.median(d[max(0, i - 4):i + 5]) for i in range(len(d))])
    mov = loc > 0.25
    rep_m = (d < 0.3 * loc) & mov
    rep, jump = float(rep_m.mean()), float(((d > 3.0 * loc) & mov).mean())
    run = best = 0
    for v in rep_m:
        run = run + 1 if v else 0
        best = max(best, run)
    uniq = float((d >= 0.3 * np.maximum(loc, 0.25)).sum()) / max(0.1, dur)
    return {'rep': round(rep, 3), 'jump': round(jump, 3), 'stutter': round(rep + jump, 3), 'unique_fps': round(uniq, 2),
            'unique_share': round(uniq / max(1.0, fps), 3), 'max_rep_run': int(best), 'med_diff': round(float(np.median(d)), 3)}


def cut_stats(fr_small, g, emb_sims):
    """hard cuts: a luma-difference spike that is also a colour-histogram jump; dissolves: a middle frame that is a blend of two
    DIFFERENT frames (8 fps samples, k = 2 and 4 apart)"""
    d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    med = float(np.median(d)) + 1e-3
    q = (fr_small * 7.999).astype(np.int32)
    hist = np.stack([np.bincount((f[..., 0] * 64 + f[..., 1] * 8 + f[..., 2]).ravel(), minlength=512) for f in q]).astype(np.float32)
    hist /= hist.sum(1, keepdims=True)
    hd = 0.5 * np.abs(np.diff(hist, axis=0)).sum(1)
    spike = d / med
    cuts = [int(i) for i in np.where((spike > 6) & (hd > 0.35))[0]]
    return {'cut_spike_max': round(float(spike.max()) if len(spike) else 0, 2), 'hist_jump_max': round(float(hd.max()) if len(hd) else 0, 3),
            'hard_cuts': len(cuts), 'cut_frames': cuts[:8], 'emb_sim_min': round(float(min(emb_sims)), 4) if len(emb_sims) else None}


def dissolve_score(s8):
    """max over 8-fps samples i of how well f_i = a f_(i-k) + (1-a) f_(i+k) (0.15 <= a <= 0.85) while the ends differ"""
    best, at = 0.0, None
    for k in (2, 3, 4):
        for i in range(k, len(s8) - k):
            A, B, M = s8[i - k].ravel(), s8[i + k].ravel(), s8[i].ravel()
            dAB = float(np.abs(A - B).mean())
            if dAB < 0.08:
                continue
            v = A - B
            a = float(np.clip(np.dot(M - B, v) / (np.dot(v, v) + 1e-9), 0.15, 0.85))
            res = float(np.abs(M - (a * A + (1 - a) * B)).mean())
            near = min(float(np.abs(M - A).mean()), float(np.abs(M - B).mean()))
            sc = 1 - res / (near + 1e-6)
            if sc > best:
                best, at = sc, i
    return round(best, 3), at


def colour(fr8):
    x = fr8.astype(np.float32) / 255
    R, G, B = x[..., 0], x[..., 1], x[..., 2]
    mx, mn = x.max(-1), x.min(-1)
    sat = (mx - mn) / (mx + 1e-6)
    y = x @ np.array([0.299, 0.587, 0.114], np.float32)
    return {'red': round(float(((R > G + 0.3) & (R > B + 0.3)).mean()), 3), 'blown': round(float((x.mean(-1) > 0.97).mean()), 3),
            'sat': round(float(sat.mean()), 3), 'black_p2': round(float(np.percentile(y, 2)), 3), 'white_p98': round(float(np.percentile(y, 98)), 3),
            'contrast': round(float(y.std()), 3), 'luma': round(float(y.mean()), 3)}


# ------------------------------------------------------------------ per kind
M = {}


def models():
    t = time.time()
    for k, cls in (('tag', Tagger), ('face', Faces), ('sig', Sig), ('aes', Aesthetic), ('flow', Flow)):
        try:
            M[k] = cls()
        except Exception as e:  # noqa: BLE001
            STATS['errors'].append(f'model {k}: {e!r}'[:300])
            log('MODEL FAILED', k, repr(e)[:300])
    phase('models', t)


def slowmo_feats(p):
    src = probe(p)
    fr = decode(p, 640, src=src)
    if len(fr) < 4:
        return {'error': 'no frames'}
    n = len(fr)
    f = {'fps': round(src['fps'], 2), 'dur': round(n / max(1, src['fps']), 2), 'w': src['w'], 'h': src['h'], 'n': n}
    g = grey(small(fr, 160) * 255)
    f.update(stutter_stats(g, src['fps'], f['dur']))
    step = max(1, int(round(src['fps'] / 8)))
    s8i = list(range(0, n, step))
    s8 = small(fr[s8i], 48)
    pick = [int(round(x)) for x in np.linspace(0, n - 1, 8)]
    ims = [fr[i] for i in pick]
    f.update(colour(np.stack(ims)))
    sims = []
    if 'sig' in M:
        try:                                          # a SigLIP failure must not cost the clip's other features
            e = M['sig'].embed([fr[i] for i in s8i])
            sims = (e[1:] * e[:-1]).sum(-1).cpu().numpy().tolist()
            f['sig'] = M['sig'].prompts(e[[min(len(s8i) - 1, int(round(k * (len(s8i) - 1) / 7))) for k in range(8)]])
        except Exception as ex:  # noqa: BLE001
            f['sig_error'] = repr(ex)[:160]
    f.update(cut_stats(small(fr, 32), g, sims))
    f['dissolve'], f['dissolve_at'] = dissolve_score(s8)
    if 'tag' in M:
        f.update(tag_feats(M['tag'], ims))
    if 'aes' in M:
        f['aesthetic'] = M['aes'](ims[1::2])
    if 'face' in M:
        boxes = [M['face'](im) for im in ims]
        have = [b[0] for b in boxes if b]
        f['face_frac'] = round(len(have) / len(ims), 3)
        f['faces_max'] = max(len(b) for b in boxes)
        if have:
            hs = [b[3] - b[1] for b in have]
            f['face_h'] = round(float(np.median(hs)), 3)
            f['face_h_min'] = round(float(min(hs)), 3)
            f['face_cx_off'] = round(float(np.median([abs((b[0] + b[2]) / 2 - 0.5) for b in have])), 3)
            f['face_cy'] = round(float(np.median([(b[1] + b[3]) / 2 for b in have])), 3)
            f['face_edge'] = round(float(np.mean([min(b[0], b[1], 1 - b[2], 1 - b[3]) < 0.01 for b in have])), 3)
            f['face_conf'] = round(float(np.median([b[4] for b in have])), 3)
            f.update(mouth_motion(fr, pick, boxes, src['fps']))
    return f


def mouth_motion(fr, pick, boxes, fps):
    """talking: frame-to-frame change inside the mouth region (lower middle of the face box) relative to the eye band of the same
    face, at ~24 fps; a still face with a moving mouth is talk, a moving camera moves both"""
    step = max(1, int(round(fps / 24)))
    H, W = fr.shape[1:3]
    g = grey(fr[::step])
    idx = list(range(0, len(fr), step))
    md, ed, fd = [], [], []
    for j in range(1, len(idx)):
        k = int(np.argmin([abs(idx[j] - q) for q in pick]))
        if not boxes[k]:
            continue
        x0, y0, x1, y1 = boxes[k][0][:4]
        bw, bh = (x1 - x0) * W, (y1 - y0) * H
        if bw < 24 or bh < 24:
            continue
        X0, Y0 = x0 * W, y0 * H

        def reg(a, b, c, d):
            return slice(int(Y0 + c * bh), max(int(Y0 + c * bh) + 1, int(Y0 + d * bh))), slice(int(X0 + a * bw), max(int(X0 + a * bw) + 1, int(X0 + b * bw)))
        dif = np.abs(g[j] - g[j - 1])
        md.append(float(dif[reg(0.3, 0.7, 0.62, 0.92)].mean()))
        ed.append(float(dif[reg(0.15, 0.85, 0.3, 0.55)].mean()))
        fd.append(float(dif[reg(0, 1, 0, 1)].mean()))
    if len(md) < 4:
        return {}
    md, ed, fd = np.array(md), np.array(ed), np.array(fd)
    ratio = md / (ed + 1.0)
    return {'mouth_rel': round(float(np.median(ratio)), 3), 'mouth_rel_p90': round(float(np.percentile(ratio, 90)), 3),
            'mouth_flap_share': round(float(((md > 2.5 * (ed + 0.5)) & (md > 2.0)).mean()), 3),
            'face_motion': round(float(np.median(fd)), 3)}


def onset_env(y, sr=16000, hop=160, win=512):
    if len(y) < win * 2:
        return np.zeros(1), hop / sr
    n = 1 + (len(y) - win) // hop
    fr = np.lib.stride_tricks.as_strided(y, (n, win), (y.strides[0] * hop, y.strides[0]))
    S = np.log1p(10 * np.abs(np.fft.rfft(fr * np.hanning(win), axis=1)))
    flux = np.maximum(0, np.diff(S, axis=0)).sum(1)
    return np.concatenate([[0], flux]), hop / sr


def sound_feats(p, hit_t):
    src = probe(p)
    fps = src['fps']
    f = {'fps': round(fps, 2), 'dur': round(src['dur'], 2), 'hit_t': hit_t}
    y = audio(p)
    env, dt = onset_env(y)
    if env.size > 1:
        a, b = int(max(0, (hit_t - 0.12) / dt)), int(min(len(env) - 1, (hit_t + 0.12) / dt))
        k = a + int(np.argmax(env[a:b + 1])) if b > a else int(hit_t / dt)
        f['t_audio'] = round(k * dt, 3)
        f['transient'] = round(float(env[k] / (np.median(env) + 1e-6)), 2)
    else:
        f['t_audio'] = hit_t
    ta = f['t_audio']
    t0 = max(0.0, ta - 1.3)
    fr = decode(p, 384, t0, min(src['dur'], ta + 0.4), src=src)
    if len(fr) < 6:
        return dict(f, error='no frames')
    H = fr.shape[1] // 8 * 8
    fr = fr[:, :H, :384]
    times = t0 + np.arange(len(fr)) / fps
    hit_i = int(np.argmin(np.abs(times - ta)))
    g = grey(small(fr, 160) * 255)
    cs = cut_stats(small(fr, 32), g, [])
    pre = [c + 1 for c in cs['cut_frames'] if -0.5 <= times[c + 1] - ta < 0.0]
    f['cut_before'] = round(float(ta - times[pre[-1]]), 3) if pre else None
    f['cuts_in_window'] = cs['hard_cuts']
    if 'flow' in M:
        fl = M['flow'](fr)                                    # pair j = frames j -> j+1
        mag = np.sqrt((fl ** 2).sum(1))                       # (N-1, h, w)
        for c in cs['cut_frames']:
            if c < len(mag):
                mag[c] = np.nan                               # a cut is not motion
        e = np.nanmean(mag.reshape(len(mag), -1), 1)
        acc = np.full(len(fl), np.nan)
        for j in range(1, len(fl)):
            if not (np.isnan(e[j]) or np.isnan(e[j - 1])):
                acc[j] = float(np.abs(fl[j] - fl[j - 1]).mean())
        j_hit = min(len(e) - 1, max(0, hit_i - 1))           # the pair arriving at the hit frame
        lo, hi = max(0, j_hit - 2), min(len(e), j_hit + 3)
        f['e_peak_rel'] = round(float(np.nanmax(e[lo:hi]) / (np.nanmax(e) + 1e-6)), 3) if np.isfinite(e[lo:hi]).any() else 0.0
        f['acc_peak_rel'] = round(float(np.nanmax(acc[lo:hi]) / (np.nanmax(acc) + 1e-6)), 3) if np.isfinite(acc[lo:hi]).any() else 0.0
        jj = int(np.nanargmax(acc)) if np.isfinite(acc).any() else j_hit
        f['acc_peak_off_frames'] = int(jj - j_hit)
        je = int(np.nanargmax(e)) if np.isfinite(e).any() else j_hit
        f['e_peak_off_frames'] = int(je - j_hit)
        jm = lo + int(np.nanargmax(np.nan_to_num(acc[lo:hi], nan=-1))) if hi > lo else j_hit
        m = np.nan_to_num(mag[min(jm, len(mag) - 1)])
        thr = max(0.4 * float(m.max()), 1.0)
        mask = m > thr
        f['area'] = round(float(mask.mean()), 3)
        if mask.any():
            ys, xs = np.nonzero(mask)
            hh, ww = m.shape
            f['centre'] = round(float(math.hypot(xs.mean() / ww - 0.5, ys.mean() / hh - 0.5)), 3)
            f['spread'] = round(float(math.hypot(xs.std() / ww, ys.std() / hh)), 3)
            f['bbox_h'] = round(float((ys.max() - ys.min() + 1) / hh), 3)
        pre_i = [j for j in range(len(e)) if -0.8 <= times[min(j + 1, len(times) - 1)] - ta <= -0.1 and np.isfinite(e[j])]
        early = [j for j in range(len(e)) if times[min(j + 1, len(times) - 1)] - ta < -0.8 and np.isfinite(e[j])]
        peak = float(np.nanmax(e[lo:hi])) if np.isfinite(e[lo:hi]).any() else 0.0
        f['build_rel'] = round(float(np.mean(e[pre_i]) / (peak + 1e-6)), 3) if pre_i else None
        f['build_rise'] = round(float(np.mean(e[pre_i]) / (np.mean(e[early]) + 0.05)), 3) if pre_i and early else None
        f['motion_peak'] = round(peak, 3)
        f['motion_med'] = round(float(np.nanmedian(e)), 3)
        f['impact_ratio'] = round(peak / (float(np.nanmedian(e)) + 0.05), 3)
    pick = sorted({max(0, hit_i - int(0.4 * fps)), max(0, hit_i - int(0.2 * fps)), hit_i, min(len(fr) - 1, hit_i + 2)})
    big = decode(p, 640, ta - 0.45, ta + 0.15, src=src)
    ims = [big[int(round(x))] for x in np.linspace(0, len(big) - 1, 4)] if len(big) else [fr[i] for i in pick]
    if 'tag' in M:
        f.update(tag_feats(M['tag'], ims))
    if 'sig' in M:
        try:
            f['sig'] = M['sig'].prompts(M['sig'].embed(ims))
        except Exception as ex:  # noqa: BLE001
            f['sig_error'] = repr(ex)[:160]
    f.update(colour(np.stack(ims)))
    return f


# ------------------------------------------------------------------ inputs
def fetch(url, dst):
    if not os.path.exists(dst):
        for k in range(4):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'zyo-clipscore'})
                with urllib.request.urlopen(req, timeout=300) as r, open(dst + '.part', 'wb') as fh:
                    shutil.copyfileobj(r, fh, 1 << 20)
                os.replace(dst + '.part', dst)
                break
            except Exception as e:  # noqa: BLE001
                log('fetch retry', url, repr(e)[:120])
                time.sleep(5 * (k + 1))
    return dst if os.path.exists(dst) else None


def safe_extract(tp, dst, want=None):
    with tarfile.open(tp) as t:
        for m in t.getmembers():
            if m.name.startswith('/') or '..' in m.name or not (m.isfile() or m.isdir()):
                continue
            if want is not None and m.isfile() and m.name.endswith('.mp4') and os.path.basename(m.name)[:-4] not in want:
                continue
            if want is not None and m.isfile() and not m.name.endswith(('.mp4', '.json')):
                continue
            t.extract(m, dst)


def mp4s(root):
    for dp, _, fs in os.walk(root):
        for fn in fs:
            if fn.endswith('.mp4'):
                yield fn[:-4], os.path.join(dp, fn)


def jobs_score():
    out = []
    for tar in CONFIG.get('tars', []):
        d = os.path.join(TMP, tar['name'][:-4])
        tp = fetch(tar['url'], os.path.join(TMP, tar['name']))
        if not tp:
            STATS['errors'].append('download failed ' + tar['name'])
            continue
        safe_extract(tp, d)
        hits = {}
        if tar['kind'] == 'sound':
            try:
                al = json.load(open(os.path.join(d, 'allow.json'), encoding='utf-8'))
                hits = {k: float(v[0]['t']) for k, v in al.items() if v}
            except (OSError, ValueError, KeyError, IndexError):
                pass
        for cid, p in mp4s(d):
            out.append({'tar': tar['name'], 'kind': tar['kind'], 'id': cid, 'path': p, 'hit_t': hits.get(cid, 1.2)})
    return out


def jobs_calib():
    labels = CONFIG['labels']
    out, seen = [], set()
    for k, s in enumerate(CONFIG.get('sets', [])):
        name = s['url'].rsplit('/', 1)[-1]
        d = os.path.join(TMP, f'set{k}')
        tp = fetch(s['url'], os.path.join(TMP, name))
        if not tp:
            STATS['errors'].append('download failed ' + name)
            continue
        safe_extract(tp, d, want={i for i, v in labels.items() if v['kind'] == s['kind']})
        os.remove(tp)
        for cid, p in mp4s(d):
            lab = labels.get(cid)
            if lab and lab['kind'] == s['kind'] and cid not in seen:
                seen.add(cid)
                out.append({'tar': name, 'kind': s['kind'], 'id': cid, 'path': p, 'hit_t': float(lab.get('hit_t') or 1.2), 'label': lab['label']})
    return out


def main():
    t = time.time()
    jobs = jobs_calib() if CONFIG.get('mode') == 'calib' else jobs_score()
    phase('download', t)
    log(len(jobs), 'clips')
    STATS['clips'] = len(jobs)
    models()
    feats = {}
    for n, j in enumerate(jobs):
        if time.time() > END:
            STATS['errors'].append(f'time box reached after {n} clips')
            break
        t = time.time()
        try:
            f = slowmo_feats(j['path']) if j['kind'] == 'slowmo' else sound_feats(j['path'], j['hit_t'])
        except Exception as e:  # noqa: BLE001
            f = {'error': repr(e)[:300], 'trace': traceback.format_exc()[-600:]}
        f.update(kind=j['kind'], tar=j['tar'], sec=round(time.time() - t, 2))
        if 'label' in j:
            f['label'] = j['label']
        feats[j['id']] = f
        phase('clips_' + j['kind'], t)
        if n % 10 == 0:
            log(n, j['id'], json.dumps(f)[:300])
            json.dump(feats, open(os.path.join(WORK, 'feats.json'), 'w'))
    json.dump(feats, open(os.path.join(WORK, 'feats.json'), 'w'))
    STATS['done'] = len(feats)
    STATS['total_min'] = round((time.time() - T0) / 60, 2)
    STATS['models'] = {'tagger': Tagger.REPO, 'faces': 'deepghs/anime_face_detection v1.4_s', 'siglip': Sig.REPO,
                       'aesthetic': 'cafeai/cafe_aesthetic', 'flow': 'torchvision raft_large'}
    json.dump(STATS, open(os.path.join(WORK, 'stats.json'), 'w'), indent=1)
    shutil.rmtree(TMP, ignore_errors=True)
    log('done', json.dumps(STATS)[:600])


main()
