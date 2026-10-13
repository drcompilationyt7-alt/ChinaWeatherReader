"""
Selftest of the source probe's detectors (core/pools/source_probe.py) on synthetic frames, no network:
a sharp moving picture is clean; a blurred one is soft; black bars are borders; a static high-contrast corner logo is a
watermark; burned-in text (reported by edit_fx.text_bands) is rejected; and edit_fx.text_bands really finds subtitle-like
text drawn on a moving background.   python scripts/source-probe-selftest.py
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core', 'pools'))
import source_probe as sp  # noqa: E402

FAILS = []


def check(c, w):
    print(('PASS  ' if c else 'FAIL  ') + w)
    if not c:
        FAILS.append(w)


rng = np.random.RandomState(0)
H, W = 360, 640


def scene(i):
    """moving textured picture"""
    return cv2.resize(np.random.RandomState(i).randint(0, 255, (H // 4, W // 4)).astype(np.uint8), (W, H), interpolation=cv2.INTER_CUBIC)


frames = [scene(i) for i in range(10)]
r = sp.analyse(frames)
check(r['clean'] and r['sharp'] >= sp.MIN_SHARP, f"sharp moving picture is clean ({r['sharp']})")
soft = [cv2.GaussianBlur(f, (0, 0), 6) for f in frames]
check('soft' in ' '.join(sp.analyse(soft)['reasons']), 'a blurred picture is soft')
barred = []
for f in frames:
    g = f.copy()
    g[:40] = 0
    g[-40:] = 0
    barred.append(g)
check(any('borders' in x for x in sp.analyse(barred)['reasons']), 'black bars are borders')
logo = []
for f in frames:
    g = f.copy()
    cv2.putText(g, 'LOGO', (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 255, 4)
    g[0:70, 0:150] = np.maximum(g[0:70, 0:150] * 0, 0)
    cv2.putText(g, 'LOGO', (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 255, 4)
    logo.append(g)
check(sp.static_watermark(logo), 'a static corner logo is a watermark')
check(not sp.static_watermark(frames), 'a clean moving picture has no watermark')
check('burned-in text' in ' '.join(sp.analyse(frames, (0.0, 0.12))['reasons']), 'burned-in text from text_bands is rejected')

# edit_fx.text_bands on a real file with subtitle-like text over a moving background
import tempfile, subprocess  # noqa: E402
d = tempfile.mkdtemp(prefix='probe-test-')
p = os.path.join(d, 'sub.mp4')
vw = cv2.VideoWriter(os.path.join(d, 'raw.avi'), cv2.VideoWriter_fourcc(*'MJPG'), 24, (640, 360))
for i in range(120):
    bg = cv2.cvtColor(cv2.resize(np.random.RandomState(i // 3).randint(40, 160, (90, 160)).astype(np.uint8), (640, 360), interpolation=cv2.INTER_CUBIC), cv2.COLOR_GRAY2BGR)
    for k, (x, y) in enumerate(((60, 320),)):
        cv2.putText(bg, 'SUBTITLE LINE OF TEXT HERE', (x, y), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 0), 6)
        cv2.putText(bg, 'SUBTITLE LINE OF TEXT HERE', (x, y), cv2.FONT_HERSHEY_DUPLEX, 1.0, (255, 255, 255), 2)
    vw.write(bg)
vw.release()
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', os.path.join(d, 'raw.avi'), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', p], check=True)
t, b = sp.text_bands(p)
check(b > sp.MAX_TEXT, f'edit_fx.text_bands finds the subtitle band (bottom {b:.2f})')
res = sp.analyse(sp.read_frames(p), (t, b))
check(not res['clean'] and any('text' in x for x in res['reasons']), 'the probe rejects the subtitled file end to end')
print('\n%s' % ('%d FAILED' % len(FAILS) if FAILS else 'all passed'))
sys.exit(1 if FAILS else 0)
