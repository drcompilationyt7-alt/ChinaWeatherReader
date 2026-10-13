"""
Selftest of the monthly sound / slow-mo pool refresh (core/pools/pool_refresh.py), synthetic pools in a temp folder:
  1. no baseline -> check fails, run never promotes
  2. good staging -> passes; items below the baseline p10, with a failed safety gate, or a missing file are dropped
  3. staging whose medians fall below the baseline -> check fails
  4. too few new items -> fails
  5. run(): a failing check never calls promote (live pool byte-identical); a passing run calls refresh, validate, promote in order
  6. sound pools (visible-source stamp): unstamped / same-picture clips dropped, too few HIT clips / too many voiced hits fail; shards
     pack + merge (dedupe vs live moments, ledger, between shards, ids) into a staging copy of the live manifests
The vibe_pool.py CLI is replaced by a stub that logs its calls.   python scripts/edit-pool-refresh-selftest.py
"""
import hashlib
import json
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix='edit-pool-selftest-')
CALLS = os.path.join(T, 'calls.log')
STUB = os.path.join(T, 'vibe_pool_stub.py')
with open(STUB, 'w') as f:
    f.write("import sys\nopen(%r,'a').write(sys.argv[1]+'\\n')\n" % CALLS)
os.environ['VIBE_POOL_CMD'] = STUB
os.environ['POOL_BASELINE'] = os.path.join(T, 'baseline.json')
os.environ['EDIT_POOL_DIR'] = os.path.join(T, 'live')
os.environ['POOL_SKIP_STAMP'] = '1'
os.environ['MEMORY_DIR'] = os.path.join(T, 'memory')
os.makedirs(os.environ['MEMORY_DIR'], exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, 'core', 'pools'))
import pool_refresh as pr  # noqa: E402

FAILS = []


def check(cond, what):
    print(('PASS  ' if cond else 'FAIL  ') + what)
    if not cond:
        FAILS.append(what)


def make(root, preset, vibe, n, lo, hi, prefix, gate='pass', seed=1):
    rnd = random.Random(seed)
    d = os.path.join(root, preset, vibe, 'clips')
    os.makedirs(d, exist_ok=True)
    items = []
    for i in range(n):
        cid = f'{prefix}{i}'
        fn = f'clips/{cid}.mp4'
        with open(os.path.join(root, preset, vibe, fn), 'wb') as f:
            f.write(b'x' * 4096)
        q = rnd.uniform(lo, hi)
        items.append({'id': cid, 'file': fn, 'status': 'fresh', 'safety': {'gate': gate},
                      'scores': {'aesthetic': q, 'sharpness': 500 + 600 * q, 'motion': q, 'appeal': q, 'taste': q, 'face_ok': True}})
    json.dump({'version': 1, 'preset': preset, 'vibe': vibe, 'items': items}, open(os.path.join(root, preset, vibe, 'clips.json'), 'w'))
    return items


def digest(root):
    h = hashlib.sha1()
    for dp, _, fs in sorted(os.walk(root)):
        for f in sorted(fs):
            h.update(f.encode())
            h.update(open(os.path.join(dp, f), 'rb').read())
    return h.hexdigest()


live = os.path.join(T, 'live')
make(live, 'slowmo', 'summer_glow', 80, 0.5, 0.9, 'L', seed=1)
pr.MIN_NEW = 20

# 1. no baseline
st1 = os.path.join(T, 'st1')
make(st1, 'slowmo', 'summer_glow', 40, 0.5, 0.9, 'N', seed=2)
check(not pr.check(st1)['ok'] and 'no quality baseline' in pr.check(st1)['problems'][0], 'no baseline: check fails')
before = digest(live)
rc = pr.run(st1, skip_refresh=True)
check(rc == 1 and not os.path.exists(CALLS), 'no baseline: run() fails and never calls vibe_pool')

# baseline from the live pool
pr.approve_baseline(root=live)
b = json.load(open(os.environ['POOL_BASELINE']))['pools']['slowmo/summer_glow']
check(b['n'] == 80 and 'aesthetic' in b['metrics'], 'baseline stored from the live pool')

# 2. good staging with a few bad items
st2 = os.path.join(T, 'st2')
items = make(st2, 'slowmo', 'summer_glow', 40, 0.55, 0.9, 'N', seed=3)
items[0]['scores'].update(aesthetic=0.05, sharpness=10, motion=0.05, appeal=0.05, taste=0.05)   # far below p10
items[1]['safety']['gate'] = 'fail'
os.remove(os.path.join(st2, 'slowmo', 'summer_glow', items[2]['file']))
json.dump({'items': items}, open(os.path.join(st2, 'slowmo', 'summer_glow', 'clips.json'), 'w'))
rep = pr.check(st2)
why = {d['id']: d['why'] for d in rep['dropped']}
check(rep['ok'], f"good staging passes ({rep['problems']})")
check(why.get('N0', '').startswith('below baseline') and why.get('N1') == 'safety gate not pass' and why.get('N2') == 'missing file',
      'bad items dropped: below p10 / safety gate / missing file')
left = json.load(open(os.path.join(st2, 'slowmo', 'summer_glow', 'clips.json')))['items']
check(len(left) == 37 and not os.path.exists(os.path.join(st2, 'slowmo', 'summer_glow', items[0]['file'])), 'dropped items removed from staging manifest + files')

# 3. weak medians
st3 = os.path.join(T, 'st3')
make(st3, 'slowmo', 'summer_glow', 60, 0.48, 0.62, 'W', seed=4)
rep3 = pr.check(st3)
check(not rep3['ok'], f"weak batch fails ({[p for p in rep3['problems'] if 'median' in p][:1] or rep3['problems'][:1]})")

# 4. too few new items
st4 = os.path.join(T, 'st4')
make(st4, 'slowmo', 'summer_glow', 8, 0.6, 0.9, 'F', seed=5)
check(any('new items' in p for p in pr.check(st4)['problems']), 'too few new items: nothing worth swapping')

# 5. run(): ordered calls on success, no promote on failure, live untouched
if os.path.exists(CALLS):
    os.remove(CALLS)
st5 = os.path.join(T, 'st5')
make(st5, 'slowmo', 'summer_glow', 40, 0.6, 0.9, 'G', seed=6)
rc = pr.run(st5, skip_refresh=True)
calls = open(CALLS).read().split() if os.path.exists(CALLS) else []
check(rc == 0 and calls == ['validate', 'promote'], f'passing run: validate then promote ({calls})')
os.remove(CALLS)
rc = pr.run(st3, skip_refresh=True)
check(rc == 1 and not os.path.exists(CALLS), 'failing run: promote never called')
check(digest(live) == before, 'live pool byte-identical after the failed runs')

# 6. sound pools: the visible-source stamp
import base64
import numpy as np
sys.path.insert(0, os.path.join(ROOT, 'core', 'pools'))
import pool_visible as PV  # noqa: E402


def sig(seed):
    r = np.random.RandomState(seed)
    return base64.b64encode(r.randint(0, 255, PV.SIG_FRAMES * PV.SIG_W * PV.SIG_H).astype(np.uint8).tobytes()).decode()


SEEDS = {}


def make_sound(root, n, prefix, hit_share, seed, vis=True, voiced=0.0, vis_lo=0.4, vis_hi=0.9):
    items = make(root, 'sound', 'impact', n, 0.55, 0.9, prefix, seed=seed)
    rnd = random.Random(seed)
    base = SEEDS.setdefault(prefix, 1000 * (len(SEEDS) + 1))
    for i, it in enumerate(items):
        it['source_id'] = f'src{prefix}{i // 2}'
        it['start'], it['end'] = float(i % 2) * 10, float(i % 2) * 10 + 2
        if vis:
            hit = rnd.random() < hit_share
            it['vis'] = {'v': 1, 'hit': hit, 'sig': sig(base + i)}
            if hit:
                it['vis'].update(score=rnd.uniform(vis_lo, vis_hi), sound_type='gun', voiced=rnd.random() < voiced)
    json.dump({'version': 1, 'preset': 'sound', 'vibe': 'impact', 'items': items}, open(os.path.join(root, 'sound', 'impact', 'clips.json'), 'w'))
    return items


live2 = os.path.join(T, 'live2')
os.environ['EDIT_POOL_DIR'] = live2
make_sound(live2, 90, 'LS', 0.6, 11)
pr.MIN_FRESH['sound'] = 70
pr.MIN_NEW_HITS = 20
pr.approve_baseline(root=live2)
b2 = json.load(open(os.environ['POOL_BASELINE']))['pools']['sound/impact']
check(b2.get('hits', 0) > 30 and 'vis_score' in b2['metrics'] and 0.4 < b2['hit_share'] < 0.8, f"baseline holds hits / hit_share / vis_score ({b2.get('hits')}, {b2.get('hit_share'):.2f})")

st6 = os.path.join(T, 'st6')
it6 = make_sound(st6, 60, 'GS', 0.65, 12)
it6[0].pop('vis')                                                       # unstamped
it6[1]['vis']['sig'] = json.load(open(os.path.join(live2, 'sound', 'impact', 'clips.json')))['items'][3]['vis']['sig']   # same picture as a live clip
json.dump({'items': it6}, open(os.path.join(st6, 'sound', 'impact', 'clips.json'), 'w'))
r6 = pr.check(st6, live=live2)
why6 = {d['id']: d['why'] for d in r6['dropped']}
check(r6['ok'], f"stamped sound batch passes ({r6['problems']})")
check(why6.get('GS0', '').startswith('no visible-source stamp') and why6.get('GS1', '').startswith('same picture as'), f'unstamped + same-picture clips dropped ({why6.get("GS0")}; {why6.get("GS1")})')

st7 = os.path.join(T, 'st7')
make_sound(st7, 60, 'HS', 0.1, 13)
check(any('HIT clips' in p for p in pr.check(st7, live=live2)['problems']), 'too few new HIT clips fails')
st8 = os.path.join(T, 'st8')
make_sound(st8, 60, 'VS', 0.65, 14, voiced=0.8)
check(any('voiced' in p for p in pr.check(st8, live=live2)['problems']), 'too many voiced hits fails')
st9 = os.path.join(T, 'st9')
make_sound(st9, 60, 'WS', 0.65, 15, vis_lo=0.05, vis_hi=0.3)
r9 = pr.check(st9, live=live2)
check(any('below baseline p10' in d['why'] for d in r9['dropped']) or any('vis_score' in p for p in r9['problems']), 'weak visible-source scores dropped / fail')

# shards: pack + merge (dedupe vs live moments, between shards, same id)
json.dump({'version': 1, 'items': {}, 'moments': {'srcLS0': [[0.0, 2.0, 'x']]}}, open(os.path.join(os.environ['MEMORY_DIR'], 'edit-pool-used.json'), 'w'))
shA, shB, shC = os.path.join(T, 'shA'), os.path.join(T, 'shB'), os.path.join(T, 'shC')
make_sound(shA, 10, 'AA', 0.7, 21)
make_sound(shB, 10, 'AA', 0.7, 21)                                      # same ids as shard A
cit = make_sound(shC, 10, 'LC', 0.7, 22)                                # same sources / moments as the live pool + the used ledger
for it in cit[:4]:
    it['source_id'], it['start'], it['end'] = 'srcLS0', 0.5, 2.5                                # overlaps the ledger moment (and live LS0)
json.dump({'items': cit}, open(os.path.join(shC, 'sound', 'impact', 'clips.json'), 'w'))
m = json.load(open(os.path.join(shA, 'sound', 'impact', 'clips.json')))
m['items'].append({'id': 'old1', 'status': 'live', 'file': 'clips/none.mp4'})
json.dump(m, open(os.path.join(shA, 'sound', 'impact', 'clips.json'), 'w'))
check(pr.pack(shA) == 10, 'pack keeps only the new fresh rows')
mg = pr.merge(os.path.join(T, 'stM'), [shA, shB, shC], live=live2)
mm = json.load(open(os.path.join(T, 'stM', 'sound', 'impact', 'clips.json')))['items']
fresh_m = [i for i in mm if i.get('status') == 'fresh']
check(len(fresh_m) == 16 and mg['skipped'].get('dup_id') == 10, f"merge: shard B ids skipped as duplicates ({mg})")
check(mg['skipped'].get('moment_used', 0) == 4, 'merge: moments of the used ledger / the live pool are not re-added')
check(sum(1 for i in mm if i.get('status') == 'live') == 90 and all(os.path.isfile(os.path.join(T, 'stM', 'sound', 'impact', i['file'])) for i in fresh_m),
      'merge: live rows kept as non-fresh, new clips copied')

print('\n%s' % ('%d FAILED' % len(FAILS) if FAILS else 'all passed'))
sys.exit(1 if FAILS else 0)
