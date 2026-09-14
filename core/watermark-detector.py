#!/usr/bin/env python3
"""
Source watermark detector.

Finds static overlays (channel handles, platform logos, "AI generated" tags)
burned into a short. Idea: real content moves, watermarks do not. We sample
frames across the clip, take an edge map of each and keep the pixels whose
edges are present in almost every frame. Small, dense clusters of those
persistent edges near the frame border are watermarks.

Usage:
    python core/watermark-detector.py <video> [--frames 32] [--json]

Prints one JSON line:
    {"width": W, "height": H, "static": false,
     "boxes": [{"x": .., "y": .., "w": .., "h": .., "score": 0.83, "persistence": 0.91}, ...]}

`static` is true when the whole shot is static (tripod, no motion): then
everything is persistent and nothing can be told apart, so no boxes are
returned and the caller should use the default watermark position.

Requires: ffmpeg/ffprobe on PATH, numpy, opencv-python(-headless).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

try:
    import cv2
except Exception as e:  # pragma: no cover
    print(json.dumps({'error': f'opencv unavailable: {e.__class__.__name__}', 'boxes': []}))
    sys.exit(0)

ANALYSIS_W = 540           # analyse at half of 1080 for speed, boxes are scaled back
PERSIST_THRESHOLD = 0.62   # fraction of sampled frames in which a pixel must be an edge
STATIC_SHOT_FRACTION = 0.22  # if this much of the frame is persistent, the shot is static
MIN_AREA_FRAC = 0.0004     # of the frame area
MAX_AREA_FRAC = 0.06
MAX_W_FRAC = 0.55
MAX_H_FRAC = 0.22
MAX_BOXES = 4              # proposals; the caller confirms them (Gemini) before covering
MIN_COMPONENT_PERSIST = 0.50   # mean persistence inside the component to be proposed at all
STRONG_PERSIST = 0.85          # above this a proposal is trusted even without confirmation


def probe(path):
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                   '-show_entries', 'stream=width,height:format=duration', '-of', 'json', path])
    j = json.loads(out.decode('utf-8'))
    return int(j['streams'][0]['width']), int(j['streams'][0]['height']), float(j['format']['duration'])


def sample_frames(path, n, width, height, duration):
    """Extract n frames evenly spread over 5%..95% of the clip (as BGR arrays)."""
    scale_w = ANALYSIS_W
    scale_h = int(round(height * scale_w / width / 2)) * 2
    tmp = tempfile.mkdtemp(prefix='wmdet_')
    frames = []
    try:
        for i in range(n):
            t = duration * (0.05 + 0.90 * i / max(1, n - 1))
            out = os.path.join(tmp, f'f{i:03d}.png')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', path, '-frames:v', '1',
                            '-vf', f'scale={scale_w}:{scale_h}', out], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out):
                img = cv2.imread(out, cv2.IMREAD_COLOR)
                if img is not None:
                    frames.append(img)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return frames, scale_w, scale_h


def edge_map(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 70, 150)
    return (cv2.dilate(edges, np.ones((3, 3), np.uint8)) > 0).astype(np.float32)


def detect(path, n_frames):
    width, height, duration = probe(path)
    frames, aw, ah = sample_frames(path, n_frames, width, height, duration)
    result = {'width': width, 'height': height, 'frames': len(frames), 'static': False, 'boxes': []}
    if len(frames) < 8:
        result['error'] = 'too few frames'
        return result

    persistence = np.mean([edge_map(f) for f in frames], axis=0)
    persistent = persistence >= PERSIST_THRESHOLD
    frac = float(persistent.mean())
    result['persistentFraction'] = round(frac, 4)
    if frac > STATIC_SHOT_FRACTION:
        result['static'] = True
        return result

    mask = persistent.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    sx, sy = width / aw, height / ah
    frame_area = aw * ah
    cands = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        bbox_area = w * h
        if bbox_area < MIN_AREA_FRAC * frame_area or bbox_area > MAX_AREA_FRAC * frame_area:
            continue
        if w > MAX_W_FRAC * aw or h > MAX_H_FRAC * ah or w < 10 or h < 6:
            continue
        comp = (labels[y:y + h, x:x + w] == i)
        density = float(area) / float(bbox_area)
        # persistence of the core (undilated) edge pixels of this component:
        # a burned-in mark keeps its edges in nearly every frame, while a
        # static background object gets occluded by people now and then
        core = comp & persistent[y:y + h, x:x + w]
        mean_persist = float(persistence[y:y + h, x:x + w][core].mean()) if core.any() else 0.0
        if mean_persist < MIN_COMPONENT_PERSIST:
            continue
        # watermarks live near the border: distance of the box centre to the nearest edge
        cx, cy = x + w / 2, y + h / 2
        edge_dist = min(cx / aw, (aw - cx) / aw, cy / ah, (ah - cy) / ah)   # 0 = on the border, 0.5 = centre
        border_score = max(0.0, 1.0 - edge_dist / 0.3)                      # 1 on the border, 0 beyond 30% in
        size_score = 1.0 - min(1.0, bbox_area / (MAX_AREA_FRAC * frame_area))
        score = 0.45 * border_score + 0.25 * mean_persist + 0.15 * size_score + 0.15 * min(1.0, density / 0.35)
        if border_score <= 0.05:
            continue  # sitting in the middle of the frame: much more likely to be content
        pad = 6
        cands.append({
            'x': int(max(0, (x - pad) * sx)), 'y': int(max(0, (y - pad) * sy)),
            'w': int(min(width, (w + 2 * pad) * sx)), 'h': int(min(height, (h + 2 * pad) * sy)),
            'score': round(score, 3), 'persistence': round(mean_persist, 3), 'density': round(density, 3),
            'strong': bool(mean_persist >= STRONG_PERSIST),
        })
    cands.sort(key=lambda c: (c['persistence'], c['score']), reverse=True)
    # keep the strongest, drop weak or overlapping ones
    kept = []
    for c in cands:
        if c['score'] < 0.5:
            continue
        if any(_overlap(c, k) for k in kept):
            continue
        kept.append(c)
        if len(kept) >= MAX_BOXES:
            break
    for c in kept:
        c['w'] = min(c['w'], width - c['x'])
        c['h'] = min(c['h'], height - c['y'])
    result['boxes'] = kept
    return result


def _overlap(a, b):
    ix = max(0, min(a['x'] + a['w'], b['x'] + b['w']) - max(a['x'], b['x']))
    iy = max(0, min(a['y'] + a['h'], b['y'] + b['h']) - max(a['y'], b['y']))
    return ix * iy > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--frames', type=int, default=32)
    args = ap.parse_args()
    try:
        res = detect(args.video, args.frames)
    except Exception as e:
        res = {'error': f'{e.__class__.__name__}: {e}', 'boxes': []}
    print(json.dumps(res))


if __name__ == '__main__':
    main()
