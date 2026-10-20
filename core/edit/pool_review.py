#!/usr/bin/env python3
"""Visual review sheets of a vibe pool (the safety / gore / vibe check a human or a vision model reads at every refresh):
4 frames per fresh clip, 3 clips per row, 24 clips per sheet, numbered; DIR/<preset>_<vibe>_NN.jpg + DIR/<..>.json (index).

    python core/edit/pool_review.py --out DIR [--preset sound|slowmo|all] [--vibe V]
    python core/edit/pool_review.py --reject sheets.json:N,N ... (see vibe_pool.py; or edit clips.json status)"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vibe_pool as V  # noqa: E402

W, H = 200, 112


def strip(path, n=4):
    c = cv2.VideoCapture(path)
    N = int(c.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out = []
    for k in range(n):
        c.set(cv2.CAP_PROP_POS_FRAMES, int((k + 0.5) * N / n))
        ok, f = c.read()
        out.append(cv2.resize(f, (W, H)) if ok else np.zeros((H, W, 3), np.uint8))
    c.release()
    return np.hstack(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--preset', default='all')
    ap.add_argument('--vibe')
    ap.add_argument('--unreviewed', action='store_true', help='only clips without a "reviewed" stamp (then stamps them)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    D = V.load_vibes()['presets']
    res = {}
    for p in (['sound', 'slowmo'] if a.preset == 'all' else [a.preset]):
        for vn in D[p]['vibes']:
            if a.vibe and vn != a.vibe:
                continue
            sh = V.fresh_clips(p, vn, rotation=False)
            if a.unreviewed:
                sh = [x for x in sh if not x.get('reviewed')]
            for si in range(0, len(sh), 24):
                cells = []
                for k, s in enumerate(sh[si:si + 24]):
                    im = strip(s['path'])
                    cv2.putText(im, str(si + k), (4, 22), 0, 0.7, (0, 0, 0), 4)
                    cv2.putText(im, str(si + k), (4, 22), 0, 0.7, (0, 255, 255), 2)
                    cells.append(im)
                while len(cells) % 3:
                    cells.append(np.zeros_like(cells[0]))
                g = np.vstack([np.hstack(cells[r:r + 3]) for r in range(0, len(cells), 3)])
                f = os.path.join(a.out, f'{p}_{vn}_{si // 24:02d}.jpg')
                cv2.imwrite(f, g, [cv2.IMWRITE_JPEG_QUALITY, 80])
            res[f'{p}/{vn}'] = [s['pool_id'] for s in sh]
    with open(os.path.join(a.out, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({'ok': True, 'pools': {k: len(v) for k, v in res.items()}}))


if __name__ == '__main__':
    main()
