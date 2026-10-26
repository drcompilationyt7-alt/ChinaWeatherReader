"""
Kaggle kernel: AI sharpening of the new sound hit clips (Real-ESRGAN realesr-animevideov3 x2, GPU). Pushed by scripts/edit-upscale-kaggle.py with
the dataset <user>/edit-upscale-input:
  code.tar.gz   core/edit/upscale_pool.py
  clips.tar     the clips to sharpen, with their pool-relative paths (sound/<vibe>/clips/<file>.mp4)
  budget.json   {"minutes": N, "clips": [...]}: hard time box
Output (/kaggle/working): result.tar = the sharp clips (same relative paths, 2x size, audio copied) + stats.json. A clip that is not finished in time
is simply missing from the result; the next refresh does it. The model (2.4 MB) is downloaded here from the Real-ESRGAN release.
"""
import json
import os
import subprocess
import sys
import tarfile
import time

T0 = time.time()
# 2026-10-26: Kaggle mounts datasets nested (/kaggle/input/datasets/<user>/<slug>/...): the top-level lookup missed budget.json
# (first real run 40337517332 -> FileNotFoundError). Walk the whole input tree, like ai_art_kernel.py.
INP = next((dp for dp, _, fs in os.walk('/kaggle/input') if 'budget.json' in fs and 'edit-upscale-input' in dp),
           next((dp for dp, _, fs in os.walk('/kaggle/input') if 'budget.json' in fs), '/kaggle/input/edit-upscale-input'))
print('input:', INP, sorted(os.listdir(INP)) if os.path.isdir(INP) else [p for p, _, _ in os.walk('/kaggle/input')][:20], flush=True)
WORK = '/kaggle/working'
MINUTES = float(json.load(open(os.path.join(INP, 'budget.json')))['minutes'])
END = T0 + MINUTES * 60 - 4 * 60

repo = os.path.join(WORK, 'repo')
pool = os.path.join(WORK, 'pool')
os.makedirs(repo, exist_ok=True)
os.makedirs(pool, exist_ok=True)
# 2026-10-26: Kaggle now auto-extracts dataset archives (code.tar.gz -> code/, clips.tar -> clips/): accept both forms.
import shutil  # noqa: E402
if os.path.isfile(os.path.join(INP, 'code.tar.gz')):
    with tarfile.open(os.path.join(INP, 'code.tar.gz')) as t:
        t.extractall(repo)
else:
    shutil.copytree(os.path.join(INP, 'code'), repo, dirs_exist_ok=True)
if os.path.isfile(os.path.join(INP, 'clips.tar')):
    with tarfile.open(os.path.join(INP, 'clips.tar')) as t:
        t.extractall(pool)
else:                                              # put each budget clip back at its relative path (the result tar keeps it)
    have = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(INP, 'clips')) for f in fs if f.endswith('.mp4')]
    for rel in json.load(open(os.path.join(INP, 'budget.json'))).get('clips', []):
        src = next((h for h in have if h.replace(os.sep, '/').endswith('/' + rel)), None)
        if src:
            os.makedirs(os.path.dirname(os.path.join(pool, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(pool, rel))
        else:
            print('missing in the input:', rel, flush=True)
code_dir = next((dp for dp, _, fs in os.walk(repo) if 'upscale_pool.py' in fs), os.path.join(repo, 'core', 'edit'))
sys.path.insert(0, code_dir)
import upscale_pool as U  # noqa: E402

files = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(pool) for f in fs if f.endswith('.mp4'))
import torch  # noqa: E402
device = 'cuda' if torch.cuda.is_available() else 'cpu'
net = U.build_net(U.model_path(), device)
done, failed, frames = [], [], 0
for f in files:
    if time.time() > END:
        break
    tmp = f + '.up.tmp.mp4'
    try:
        n = U.upscale_clip(net, f, tmp, device, 540)
        ok, info = U.verify(f, tmp, n)
    except Exception as e:  # noqa: BLE001
        ok, info = False, {'error': str(e)[:100]}
    if ok:
        os.replace(tmp, f)
        done.append(os.path.relpath(f, pool).replace(os.sep, '/'))
        frames += n
    else:
        failed.append(os.path.basename(f))
        if os.path.exists(tmp):
            os.remove(tmp)
stats = {'device': device, 'clips': len(files), 'done': len(done), 'failed': failed, 'frames': frames, 'secs': round(time.time() - T0),
         'fps': round(frames / max(1.0, time.time() - T0), 2)}
with tarfile.open(os.path.join(WORK, 'result.tar'), 'w') as t:
    for rel in done:
        t.add(os.path.join(pool, rel), rel)
json.dump(stats, open(os.path.join(WORK, 'stats.json'), 'w'))
print(json.dumps(stats))
