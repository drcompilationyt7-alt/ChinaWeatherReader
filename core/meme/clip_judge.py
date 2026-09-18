#!/usr/bin/env python3
"""
Second opinion on the clip finder's picks with a small CPU vision model:
SigLIP (google/siglip-base-patch16-224, Apache-2.0, ~200M params) compares
three frames of every clip with text prompts and scores

  vibe     does the clip match its layer (dance / cool / fight / cute)?
  theme    anime vs real footage, as the short's theme needs
  quality  not blurry, not a black screen, not a text card, not static
  safety   no revealing or suggestive frames (on top of the NudeNet filter)

Clips that fail are dropped from the manifest (kept in `rejected`); the rest
are re-ranked inside their vibe. The manifest is rewritten in place.

    python core/meme/clip_judge.py --clips work/clips/clips.json --theme anime

Install (CI): pip install torch --index-url https://download.pytorch.org/whl/cpu
              pip install transformers sentencepiece protobuf
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

MODEL = os.environ.get('CLIP_JUDGE_MODEL', 'google/siglip-base-patch16-224')

VIBES = {
    'dance': ['people dancing', 'an anime character dancing', 'a dance performance on stage', 'a group doing a dance challenge'],
    'cool': ['a cool confident person posing', 'an anime character with a powerful aura', 'a stylish slow motion shot',
             'someone walking in confidently'],
    'fight': ['a fight scene with a punch', 'an anime battle with a big impact', 'a martial arts kick', 'an action scene with an explosion'],
    'cute': ['a cute animal', 'a cute smiling person', 'a cute anime character', 'a small child being adorable'],
}
BAD = ['a blurry low quality frame', 'a black screen', 'a screenshot full of text', 'a title card or logo',
       'an empty room with nothing happening']
GOOD = ['a clear video frame of people', 'a clear anime scene', 'a clear video frame of an animal']
ANIME = ['a frame from a Japanese anime', 'a 2D animated cartoon frame', 'hand-drawn anime characters']
REAL = ['a live-action video of real people', 'a frame from a Korean TV variety show with captions',
        'a photo of real people', 'a real video of an animal', 'a person performing on a TV stage']
UNSAFE = ['a person in a bikini or swimsuit', 'a person in underwear', 'a sexually suggestive pose',
          'a person showing a lot of bare skin']
SAFE = ['a person in normal clothes', 'an ordinary everyday scene', 'a fully clothed anime character',
        'a comedy costume or sparkly stage outfit', 'a singer performing on stage']
ANIME_THEMES = {'anime'}
REAL_THEMES = {'kpop', 'chinese', 'japanese', 'funny', 'cute'}


def _tensor(out):
    """Newer transformers return an output object from get_*_features; older ones a tensor."""
    return getattr(out, 'pooler_output', None) if not hasattr(out, 'norm') else out


def frames(clip, n=3):
    dur = float(clip.get('duration') or (clip.get('end', 0) - clip.get('start', 0)) or 3.0)
    peak = clip.get('peak')
    ts = [dur * 0.25, float(peak) if peak is not None else dur * 0.5, dur * 0.75][:n]
    out = []
    with tempfile.TemporaryDirectory() as d:
        for k, t in enumerate(ts):
            p = os.path.join(d, f'{k}.jpg')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{max(0.0, t):.2f}', '-i', clip['path'], '-frames:v', '1',
                            '-vf', 'scale=448:-2', p], check=False)
            if os.path.exists(p):
                out.append(Image.open(p).convert('RGB').copy())
    return out


class Judge:
    def __init__(self):
        import torch
        from transformers import AutoModel, AutoProcessor
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        self.torch = torch
        self.model = AutoModel.from_pretrained(MODEL).eval()
        self.proc = AutoProcessor.from_pretrained(MODEL)
        self.texts = []
        for group in list(VIBES.values()) + [BAD, GOOD, ANIME, REAL, UNSAFE, SAFE]:
            self.texts += group
        with torch.inference_mode():
            t = self.proc(text=self.texts, padding='max_length', return_tensors='pt')
            e = _tensor(self.model.get_text_features(**t))
            self.text_emb = e / e.norm(dim=-1, keepdim=True)

    def scores(self, images):
        """Mean image-text logit per prompt over the frames."""
        torch = self.torch
        with torch.inference_mode():
            x = self.proc(images=images, return_tensors='pt')
            e = _tensor(self.model.get_image_features(**x))
            e = e / e.norm(dim=-1, keepdim=True)
            logits = (e @ self.text_emb.T) * self.model.logit_scale.exp() + self.model.logit_bias
        return dict(zip(self.texts, logits.mean(0).tolist()))

    @staticmethod
    def softmax_groups(sc, groups):
        """Relative strength of each named group of prompts (best prompt per group, softmax across groups)."""
        vals = np.array([max(sc[p] for p in ps) for ps in groups.values()])
        e = np.exp(vals - vals.max())
        return dict(zip(groups.keys(), (e / e.sum()).tolist()))

    def judge(self, clip, theme):
        imgs = frames(clip)
        if not imgs:
            return {'ok': False, 'reason': 'no frames'}
        sc = self.scores(imgs)
        vibe = self.softmax_groups(sc, VIBES)
        quality = self.softmax_groups(sc, {'good': GOOD, 'bad': BAD})['good']
        anime = self.softmax_groups(sc, {'anime': ANIME, 'real': REAL})['anime']
        unsafe = self.softmax_groups(sc, {'unsafe': UNSAFE, 'safe': SAFE})['unsafe']
        want = clip.get('vibe')
        res = {'vibe': {k: round(v, 3) for k, v in vibe.items()}, 'quality': round(quality, 3),
               'anime': round(anime, 3), 'unsafe': round(unsafe, 3)}
        reasons = []
        if unsafe > 0.7:
            reasons.append('suggestive')
        if quality < 0.35:
            reasons.append('low quality / text / static')
        if theme in ANIME_THEMES and anime < 0.4:
            reasons.append('not anime')
        if theme in REAL_THEMES and anime > 0.85:
            reasons.append('anime in a real-footage theme')
        if want in vibe and vibe[want] < 0.18 and max(vibe, key=vibe.get) != want:
            reasons.append(f'does not look like {want}')
        res['ok'] = not reasons
        res['reason'] = ', '.join(reasons)
        res['score'] = round((vibe.get(want, max(vibe.values())) if want else max(vibe.values())) * quality, 4)
        return res


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--clips', required=True)
    ap.add_argument('--theme', default='mixed')
    ap.add_argument('--min-keep', type=int, default=4, help='never drop below this many clips (best rejected ones come back)')
    args = ap.parse_args()
    manifest = json.load(open(args.clips, encoding='utf-8'))
    clips = manifest['clips'] if isinstance(manifest, dict) else manifest
    try:
        judge = Judge()
    except Exception as e:
        print(json.dumps({'ok': True, 'skipped': f'model unavailable: {str(e)[:120]}', 'kept': len(clips)}))
        return
    for c in clips:
        c['judge'] = judge.judge(c, args.theme)
    kept = [c for c in clips if c['judge'].get('ok')]
    rejected = [c for c in clips if not c['judge'].get('ok')]
    # safety rejections never come back; the others can refill the minimum
    if len(kept) < args.min_keep:
        spare = sorted([c for c in rejected if 'suggestive' not in c['judge'].get('reason', '')],
                       key=lambda c: -c['judge'].get('score', 0))
        kept += spare[:args.min_keep - len(kept)]
        rejected = [c for c in rejected if c not in kept]
    # strongest first inside each vibe (the editor takes the first clip of each vibe)
    kept.sort(key=lambda c: -c['judge'].get('score', 0))
    out = {'clips': kept, 'rejected': rejected} if isinstance(manifest, dict) or rejected else kept
    if isinstance(manifest, dict):
        manifest.update(out if isinstance(out, dict) else {'clips': out})
        out = manifest
    json.dump(out, open(args.clips, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'ok': True, 'kept': len(kept), 'rejected': [(c.get('source_id'), c['judge'].get('reason')) for c in rejected]},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
