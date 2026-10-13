"""Merge the extras-cache.json of the CI shards into the seed cache (pop-pool-build.yml merge job).
Usage: pop-merge-shards.py <seed cache> <shards dir> <out cache>
A key is taken from a shard when its value differs from the seed's (shards work on disjoint characters; the shared keys
(wiki / Danbooru answers) are deterministic, so a later shard winning is harmless). Also copies the shards' new pop2 files."""
import json
import os
import shutil
import sys

seed, shards, out = sys.argv[1:4]
d = json.load(open(seed, encoding='utf-8'))
base = dict(d)
n_keys = n_files = 0
for sd in sorted(os.listdir(shards)):
    cp = os.path.join(shards, sd, 'extras-cache.json')
    if not os.path.exists(cp):
        print('no cache in', sd)
        continue
    for k, v in json.load(open(cp, encoding='utf-8')).items():
        if base.get(k) != v:
            d[k] = v
            n_keys += 1
    src = os.path.join(shards, sd, 'pop2')
    for root, _, files in os.walk(src):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), src)
            dst = os.path.join('core', 'quiz', 'assets', 'pop2', rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(root, f), dst)
            n_files += 1
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(d, open(out, 'w', encoding='utf-8'))
print(f'merged: {n_keys} changed keys, {n_files} files, {len(d)} keys total')
