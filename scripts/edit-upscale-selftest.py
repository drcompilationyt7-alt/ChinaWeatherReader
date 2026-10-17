"""Offline selftest of the sound-pool sharpening integration (scripts/edit-upscale-kaggle.py, scripts/kaggle/edit_upscale_kernel.py).

    python scripts/edit-upscale-selftest.py

No Kaggle, no network, no model: a fake pool with two tiny clips (one hit, one bed), the bundle is built, a fake kernel result (ffmpeg x2) is merged,
the checks (day guard, budget, pick, verify, rejects a wrong-size result, sidecar) are asserted. core/edit/upscale_pool.py has its own
`--selftest` (needs torch and downloads the 2.4 MB model on demand).
"""
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('euk', os.path.join(ROOT, 'scripts', 'edit-upscale-kaggle.py'))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)

py_compile.compile(os.path.join(ROOT, 'scripts', 'kaggle', 'edit_upscale_kernel.py'), doraise=True)
py_compile.compile(os.path.join(ROOT, 'core', 'edit', 'upscale_pool.py'), doraise=True)

d = tempfile.mkdtemp(prefix='edit_upscale_selftest_')
K.LEDGER = os.path.join(d, 'ledger.json')
pool = os.path.join(d, 'pool')
cdir = os.path.join(pool, 'sound', 'impact', 'clips')
os.makedirs(cdir)


def mk(path, w, h, audio=True):
    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc=size={w}x{h}:rate=12:duration=0.5']
    if audio:
        cmd += ['-f', 'lavfi', '-i', 'sine=d=0.5']
    cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p'] + (['-c:a', 'aac', '-shortest'] if audio else []) + [path]
    subprocess.run(cmd, check=True)


mk(os.path.join(cdir, 'hit.mp4'), 64, 64)
mk(os.path.join(cdir, 'bed.mp4'), 64, 64)
json.dump({'items': [{'id': 'hit', 'file': 'clips/hit.mp4', 'status': 'fresh', 'vis': {'hit': True}},
                     {'id': 'bed', 'file': 'clips/bed.mp4', 'status': 'fresh', 'vis': {'hit': False}}]},
          open(os.path.join(pool, 'sound', 'impact', 'clips.json'), 'w'))

# day guard + budget
assert K.day_ok(datetime(2026, 10, 12, tzinfo=timezone.utc)) and not K.day_ok(datetime(2026, 10, 17, tzinfo=timezone.utc))
left, used = K.budget_left()
assert left == K.MONTH_GPU_MIN and used == 0
K.record(30, 'complete', {})
assert K.budget_left()[0] == K.MONTH_GPU_MIN - 30

# pick + bundle: only the HIT clip
assert [r for r, _ in K.pick_clips(pool)] == ['sound/impact/clips/hit.mp4']
ds, kn, user, clips = K.bundle(pool, os.path.join(d, 'bundle'), 30)
for f in ('code.tar.gz', 'clips.tar', 'budget.json', 'dataset-metadata.json'):
    assert os.path.isfile(os.path.join(ds, f)), f
meta = json.load(open(os.path.join(kn, 'kernel-metadata.json')))
assert meta['id'].endswith('/edit-upscale-refresh') and meta['enable_gpu'] and meta['code_file'] == 'edit_upscale_kernel.py'
with tarfile.open(os.path.join(ds, 'clips.tar')) as t:
    assert t.getnames() == ['sound/impact/clips/hit.mp4']

# merge: a good x2 result is taken, a wrong-size one is rejected
res = os.path.join(d, 'res')
os.makedirs(os.path.join(res, 'sound', 'impact', 'clips'))
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', os.path.join(cdir, 'hit.mp4'), '-vf', 'scale=128:128', '-c:v', 'libx264', '-c:a', 'copy',
                os.path.join(res, 'sound', 'impact', 'clips', 'hit.mp4')], check=True)
tar = os.path.join(d, 'result.tar')
with tarfile.open(tar, 'w') as t:
    t.add(os.path.join(res, 'sound', 'impact', 'clips', 'hit.mp4'), 'sound/impact/clips/hit.mp4')
r = K.merge(tar, pool)
assert r == {'replaced': 1, 'rejected': 0}, r
assert K._probe(os.path.join(cdir, 'hit.mp4'))[:2] == (128, 128) and os.path.isfile(os.path.join(cdir, 'hit.mp4.up.json'))
assert K.pick_clips(pool) == []                                    # already sharpened: not picked again
mk(os.path.join(res, 'sound', 'impact', 'clips', 'bed.mp4'), 100, 100)   # wrong size: rejected
with tarfile.open(tar, 'w') as t:
    t.add(os.path.join(res, 'sound', 'impact', 'clips', 'bed.mp4'), 'sound/impact/clips/bed.mp4')
assert K.merge(tar, pool) == {'replaced': 0, 'rejected': 1}
shutil.rmtree(d, ignore_errors=True)
print('edit-upscale selftest OK')
