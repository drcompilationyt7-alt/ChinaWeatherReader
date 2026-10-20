"""
Pre-upload sanity checks for a quiz short (pipeline/quiz-guard.js calls this; light work, no rendering).

  python qa_guard.py --images a.jpg,b.png      every file exists, is non-empty and not a blank (flat) picture
  python qa_guard.py --video out.mp4           duration, plus N sampled frames: mean / std-dev (blank) and
                                               the difference between neighbours (static)

Prints one JSON line: {"images": [...], "video": {...}}.
"""
import argparse
import json
import os
import subprocess

import numpy as np

BLANK_STD = 6.0      # a frame / picture with less grey-level spread than this is flat (one colour)
STATIC_DIFF = 1.0    # mean absolute difference between neighbouring samples below this: nothing changed
SW, SH = 108, 192    # sampled frames are scaled down to this (9:16), greyscale


def image_stats(p):
    out = {'path': p, 'exists': os.path.isfile(p)}
    out['size'] = os.path.getsize(p) if out['exists'] else 0
    if not out['size']:
        out['ok'] = False
        return out
    try:
        from PIL import Image
        with Image.open(p) as im:
            a = np.asarray(im.convert('L').resize((128, 128)), dtype=np.float32)
        out.update(mean=round(float(a.mean()), 1), std=round(float(a.std()), 1))
        out['ok'] = out['std'] >= BLANK_STD
    except Exception as e:  # unreadable picture
        out.update(ok=False, error=str(e)[:120])
    return out


def duration(p):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', p],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def video_stats(p, n=16):
    out = {'path': p, 'exists': os.path.isfile(p)}
    out['size'] = os.path.getsize(p) if out['exists'] else 0
    if not out['size']:
        return out
    d = duration(p)
    out['duration'] = round(d, 2)
    if d <= 0:
        return out
    frames = []
    for k in range(n):
        t = d * (k + 0.5) / n
        r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', p, '-frames:v', '1', '-vf', f'scale={SW}:{SH}',
                            '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True, timeout=60)
        if len(r.stdout) == SW * SH:
            frames.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(np.float32))
    out['sampled'] = len(frames)
    if not frames:
        return out
    stds = [float(f.std()) for f in frames]
    diffs = [float(np.abs(a - b).mean()) for a, b in zip(frames, frames[1:])]
    out['means'] = [round(float(f.mean()), 1) for f in frames]
    out['stds'] = [round(s, 1) for s in stds]
    out['blankShare'] = round(sum(s < BLANK_STD for s in stds) / len(stds), 2)
    out['staticShare'] = round(sum(x < STATIC_DIFF for x in diffs) / len(diffs), 2) if diffs else 1.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--images', default='')
    ap.add_argument('--video', default='')
    ap.add_argument('--samples', type=int, default=16)
    a = ap.parse_args()
    res = {}
    if a.images:
        res['images'] = [image_stats(p) for p in a.images.split(',') if p]
    if a.video:
        res['video'] = video_stats(a.video, a.samples)
    print(json.dumps(res))


if __name__ == '__main__':
    main()
