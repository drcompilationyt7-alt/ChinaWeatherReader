#!/usr/bin/env python3
"""
LOCAL idle-time pool miner on the owner PC (owner 2026-10-23: "keep growing, u can use local now when there is nothing [that]
need[s] it, to grow"). Runs NEXT TO the light nightly GitHub job (pool-mine-nightly.yml), never at the same time, and only STAGES:
the candidates go to the release `edit-pools-staging` with the CI naming, so the normal review flow (pull-staged -> Claude review ->
import on CI / import-staged) handles them. Nothing is ever imported or pushed to a live pool from here.

Priorities: SOUND (pins are the bottleneck) -> SLOW-MO -> QUIZ IRL categories (night city / dishes / geo / wyr food + travel).
Per job it reuses the pool's own code in a private, clean git worktree of origin/main (LOCAL_MINE_DIR/repo), exactly as CI runs it:
  sound   core/pools/sound_refresh.py mine (fetch -> hit_scan -> visible-source -> pre-filter -> vision -> safety), small batches;
          then merge (vs the pulled live sound MANIFESTS) -> pool_visible stamp -> baseline check -> contact sheet -> stage ->
          sound-cand-<date>-pc<HHMM>-n<N>.tar. (No AI sharpening here: clips stay 1080p, the documented fallback.) The fetch state
          memory/edit-pool-fetch.json is merged (union) and pushed after EVERY batch, so CI never re-mines a PC video.
  slowmo  core/pools/slowmo_grow.py mine (one shard, small batch) + publish --dry -> slowmo-cand-<date>-spc-sg<N>-db<M>.tar + the
          merged slowmo-grow-state.json (re-downloaded just before the upload, merged, uploaded).
  quiz    core/quiz/quiz_pool_grow.py mine --shard photos (geo / nightcity / cuisine) and --shard llm --cats wyr_food,wyr_travel, once
          per UTC day -> quiz-cand-<date>-pc-<shard>-n<N>.tar + the merged quiz-grow-state.json.

YouTube safety: ONLY the spare cookies (C:/Users/Home/yt-cookies.txt, copied per yt-dlp call by the pool code, never printed);
yt-dlp[default] + --js-runtimes node; the pool code's pacing; <= DAILY_CAP sources a day; no job while a GitHub mining run is queued
or in progress, and no YouTube job from 19:00 UTC until tonight's nightly run finished when tonight's rotation is sound / slow-mo;
the first bot check / "sign in to confirm" stops the session and backs off BACKOFF_H hours.
PC load: starts a job only when >= 4 GB RAM is free, the CPU used by OTHER processes averaged < 50 % over the last minute, no foreign
heavy process (ffmpeg / blender / renders, any python > 0.6 core, any process > 2 cores) in the last minute and no keyboard / mouse
input for 2 min. While a job runs the same checks run every 10 s (RAM floor 2 GB, since our own job holds memory): when any fails the
whole job tree is SUSPENDED and resumed after 2 idle minutes. IDLE priority, 4 threads, CPU only, one job at a time, lock file.
Never kills a foreign process (only its own job tree, on stop).

    python scripts/local-mine.py run                    loop until stopped (start it with scripts/local-mine.ps1 start)
    python scripts/local-mine.py once --job sound       one batch now (+ stage it), for tests
    python scripts/local-mine.py stop | status
"""
import argparse
import base64
import ctypes
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from collections import deque

MAIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.abspath(os.environ.get('LOCAL_MINE_DIR') or os.path.join(os.path.expanduser('~'), 'local-miner'))
WT = os.path.join(HOME, 'repo')
POOL = os.path.join(WT, 'output', 'edit-pools')
LOG = os.path.join(HOME, 'local-mine.log')
LOCK = os.path.join(HOME, 'local-mine.lock')
STOP = os.path.join(HOME, 'STOP')
STATE = os.path.join(HOME, 'state.json')
COOKIES = 'C:/Users/Home/yt-cookies.txt'
REPO = 'drcompilationyt7-alt/ChinaWeatherReader'
ACCOUNT = 'drcompilationyt7-alt'
RELEASE = 'edit-pools-staging'
YT_POOLS = ('sound', 'slowmo')
MINE_WORKFLOWS = ('pool-mine-nightly.yml', 'edit-pool-refresh.yml', 'slowmo-pool-mine.yml', 'quiz-pool-mine.yml')

DAILY_CAP = int(os.environ.get('LOCAL_MINE_DAILY_CAP', '60'))      # sources (download attempts) per UTC day
SOUND_SRC = int(os.environ.get('LOCAL_MINE_SOUND_SRC', '4'))       # per sound batch: sources, queries, minutes
SOUND_Q = int(os.environ.get('LOCAL_MINE_SOUND_Q', '2'))
SOUND_BUDGET = int(os.environ.get('LOCAL_MINE_SOUND_MIN', '45'))
SLOWMO_BATCH, SLOWMO_BUDGET = 4, 50
QUIZ_BUDGET = 30
STAGE_AT = 8                    # stage the pending sound batches once this many minis passed every gate (or at a pause / day end)
MAX_STAGED_SOUND = 4            # CI's own review-backlog gate: no new sound tars while this many wait
BACKOFF_H = 8
MIN_FREE_GB, FLOOR_FREE_GB, MAX_OTHER_CPU, INPUT_IDLE_S = 4.0, 2.0, 50.0, 120
# NOW mode (owner 2026-10-24 "u can use pc to mine right now"; LOCAL_MINE_NOW=1, local-mine.ps1 start -Now): no owner-activity
# pause and no 19:00 UTC YouTube blackout (the GitHub-run guard stays: no YouTube while a mining workflow is queued / running, a
# running YouTube job is terminated when one starts); start at >= 3 GB free, pause under 2.5 GB; quiz (no YouTube) may run up
# to QUIZ_NOW_MAX times a day while YouTube waits. IDLE priority, 4 threads, one job, heavy-process pause: unchanged.
NOW = os.environ.get('LOCAL_MINE_NOW') == '1'
QUIZ_NOW_MAX = int(os.environ.get('LOCAL_MINE_QUIZ_NOW_MAX', '4'))
if NOW:
    MIN_FREE_GB, FLOOR_FREE_GB = 3.0, 2.5
HEAVY_NAMES = {'ffmpeg', 'blender', 'aerender', 'afterfx', 'adobe premiere pro', 'resolve', 'handbrake', 'handbrakecli', 'obs64',
               'x264', 'x265', 'melt', 'vegas', 'davinci resolve', 'photoshop', 'unity', 'unrealeditor'}
BOT_RE = re.compile(r"bot-block|BOT-CHECK|not a bot|sign in to confirm(?! your age)|http error 429|too many requests|"
                    r"temporarily blocked", re.I)
IDLE_PRIO = 0x40
NO_WINDOW = 0x08000000

_token = {}


# ------------------------------------------------------------------ small helpers

def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def today():
    return now_utc().strftime('%Y-%m-%d')


def log(*a):
    os.makedirs(HOME, exist_ok=True)
    line = f"{now_utc().strftime('%Y-%m-%d %H:%M:%S')}Z " + ' '.join(str(x) for x in a)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 2 ** 20:
            os.replace(LOG, LOG + '.1')
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass
    print(line, flush=True)


def rj(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return d


def wj(p, d, indent=1):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=indent)
    os.replace(p + '.tmp', p)


def token():
    if not _token.get('t'):
        r = subprocess.run(['gh', 'auth', 'token', '-u', ACCOUNT], capture_output=True, text=True, timeout=60)
        _token['t'] = r.stdout.strip()
        if not _token['t']:
            raise SystemExit(f'no gh token for {ACCOUNT}')
    return _token['t']


def git(*args, check=True, cwd=WT, timeout=600):
    """git in the private worktree with the alt account's token as an http header (never logged)"""
    hdr = 'AUTHORIZATION: basic ' + base64.b64encode(f'x-access-token:{token()}'.encode()).decode()
    cmd = ['git', '-c', f'http.extraheader={hdr}', '-c', 'user.name=Bot', '-c', 'user.email=bot@bot.com'] + list(args)
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError(f'git {args[0]} failed: {(r.stderr or "").strip()[-300:]}')
    return r


def gh(*args, check=False, timeout=1800):
    r = subprocess.run(['gh'] + list(args), cwd=WT if os.path.isdir(WT) else MAIN, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, GH_TOKEN=token(), GH_REPO=REPO), creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError(f'gh {" ".join(args[:2])} failed: {(r.stderr or "").strip()[-300:]}')
    return r


def staged_assets():
    r = gh('release', 'view', RELEASE, '--json', 'assets', '-q', '.assets[].name', timeout=120)
    return r.stdout.split() if r.returncode == 0 else None


def job_env():
    env = dict(os.environ)
    for k in list(env):
        if k.startswith('GITHUB_') or k in ('CI', 'EDIT_POOL_DIR', 'MEMORY_DIR', 'EDIT_CACHE'):
            env.pop(k, None)
    keys = [env.get('GEMINI_API_KEY', '')] + [x.strip() for x in env.get('GEMINI_API_KEYS', '').split(',')]
    keys = [k for i, k in enumerate(keys) if k and k not in keys[:i]]
    for i, k in enumerate(keys):
        env['GEMINI_API_KEY' if i == 0 else f'GEMINI_API_KEY_{i + 1}'] = k
    t = '4'
    env.update(PYTHONIOENCODING='utf-8', OMP_NUM_THREADS=t, MKL_NUM_THREADS=t, OPENBLAS_NUM_THREADS=t, TORCH_NUM_THREADS=t,
               NUMEXPR_MAX_THREADS=t, EDIT_THREADS=t, POP_THREADS=t, EDIT_GPU='0', CUDA_VISIBLE_DEVICES='', EDIT_POOL_DIR=POOL,
               YT_COOKIES=COOKIES, YT_COOKIE_COPY='1', YT_JS_RUNTIMES='node', SLOWMO_GROW_MIN_GB='0', GH_TOKEN=token(), GH_REPO=REPO)
    return env


# ------------------------------------------------------------------ idle / load monitor

class Monitor:
    """samples every 10 s: free RAM, CPU of OTHER processes, foreign heavy processes, last user input"""

    def __init__(self):
        import psutil
        self.ps = psutil
        self.ncpu = psutil.cpu_count() or 4
        self.samples = deque(maxlen=6)       # (t, free_gb, other_cpu, heavy_reason, input_idle_s)
        self.job = None                      # psutil.Process of the running job (our tree)
        self.lock = threading.Lock()
        psutil.cpu_percent(None)
        threading.Thread(target=self._loop, daemon=True).start()

    def ours(self):
        me = {os.getpid()}
        roots = [self.ps.Process(os.getpid())] + ([self.job] if self.job else [])
        for r in roots:
            try:
                me.add(r.pid)
                me |= {c.pid for c in r.children(recursive=True)}
            except self.ps.Error:
                pass
        return me

    @staticmethod
    def input_idle_s():
        try:
            class LII(ctypes.Structure):
                _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]
            li = LII()
            li.cbSize = ctypes.sizeof(LII)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
                return 10 ** 6
            return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
        except Exception:  # noqa: BLE001
            return 10 ** 6

    def _sample(self):
        ps = self.ps
        total = ps.cpu_percent(None)
        ours = self.ours()
        our_cpu, our_rss, heavy = 0.0, 0.0, None
        for p in ps.process_iter(['name']):
            try:
                c = p.cpu_percent(None)
            except ps.Error:
                continue
            if p.pid in ours:
                our_cpu += c
                if p.pid != os.getpid():
                    try:
                        our_rss += p.memory_info().rss / 2 ** 30
                    except ps.Error:
                        pass
                continue
            if p.pid in (0, 4):
                continue
            n = (p.info.get('name') or '').lower().removesuffix('.exe')
            if (n in HEAVY_NAMES and c > 10) or (n.startswith('python') and c > 60) or c > 200:
                try:                                      # a job child started between the tree walk and this loop is ours
                    if HOME.lower() in ' '.join(p.cmdline()).lower() or any(a.pid in ours for a in p.parents()):
                        our_cpu += c
                        continue
                except ps.Error:
                    pass
                heavy = heavy or f'{n} pid {p.pid} {c:.0f}%'
        other = max(0.0, total - our_cpu / self.ncpu)
        free = ps.virtual_memory().available / 2 ** 30
        with self.lock:
            self.samples.append((time.time(), free, other, heavy, self.input_idle_s(), our_rss))

    def _loop(self):
        while True:
            try:
                self._sample()
            except Exception as e:  # noqa: BLE001
                log('monitor error', str(e)[:120])
            time.sleep(10)

    def verdict(self, running=False):
        """(ok, why): running=True uses the RAM floor (our own job holds memory)"""
        with self.lock:
            s = list(self.samples)
        if len(s) < 6:
            return False, 'warming up'
        free, inp, ours = s[-1][1], s[-1][4], s[-1][5]
        cpu = sum(x[2] for x in s) / len(s)
        heavy = next((x[3] for x in reversed(s) if x[3]), None)
        # 2026-10-24: while a job runs, the floor counts our own job's memory as free (suspending it frees nothing: the
        # 10-24 quiz job paused itself at 1.7 GB free for good); a hard 1 GB floor still pauses whatever the cause
        if (free + ours if running else free) < (FLOOR_FREE_GB if running else MIN_FREE_GB) or free < 1.0:
            return False, f'RAM {free:.1f} GB free'
        if cpu >= MAX_OTHER_CPU:
            return False, f'other CPU {cpu:.0f} %'
        if heavy:
            return False, f'busy: {heavy}'
        if inp < INPUT_IDLE_S and not NOW:
            return False, f'owner active ({inp:.0f} s ago)'
        return True, 'idle'


MON = None


def stop_requested():
    return os.path.exists(STOP)


def sleep_checked(sec):
    end = time.time() + sec
    while time.time() < end:
        if stop_requested():
            return False
        time.sleep(min(15, max(0.1, end - time.time())))
    return True


def wait_idle(what):
    t0, last = time.time(), ''
    while True:
        if stop_requested():
            return False
        ok, why = MON.verdict()
        if ok:
            return True
        kind = re.sub(r'[\d.]+', '', why)
        if kind != last and why != 'warming up':
            log(f'{what}: waiting ({why})')
            last = kind
        time.sleep(20)
        if time.time() - t0 > 6 * 3600:
            log(f'{what}: no idle window for 6 h')
            t0 = time.time()


def tree(p):
    try:
        return [p] + p.children(recursive=True)
    except MON.ps.Error:
        return [p]


def run_job(name, cmd, cwd, env, active_max_min, youtube=False):
    """the job runs at IDLE priority; suspended (whole tree) whenever the PC is not idle, resumed after 2 idle minutes; terminated
    only on stop / after its active-time cap / after 6 h paused. Returns (rc, log text)."""
    ps = MON.ps
    lp = os.path.join(HOME, 'logs', f"{now_utc().strftime('%Y%m%d-%H%M%S')}-{name}.log")
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    with open(lp, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=lf, stderr=subprocess.STDOUT, creationflags=IDLE_PRIO | NO_WINDOW)
        pp = ps.Process(proc.pid)
        MON.job = pp
        paused_at, idle_since, active, last, gh_t = None, None, 0.0, time.time(), time.time()
        try:
            while proc.poll() is None:
                time.sleep(10)
                t = time.time()
                if paused_at is None:
                    active += t - last
                last = t
                for q in tree(pp):                       # children inherit IDLE; re-assert for any that did not
                    try:
                        if q.nice() != ps.IDLE_PRIORITY_CLASS:
                            q.nice(ps.IDLE_PRIORITY_CLASS)
                    except ps.Error:
                        pass
                ci = None
                if youtube and t - gh_t > 300:            # the GitHub mining job (same spare account) started: get out of its way
                    gh_t = t
                    try:
                        ci = gh_mining_busy()
                    except Exception:  # noqa: BLE001
                        ci = None
                if ci or stop_requested() or active > active_max_min * 60 or (paused_at and t - paused_at > 6 * 3600):
                    log(f"{name}: terminating our job tree ({ci or 'stop / time cap'})")
                    for q in reversed(tree(pp)):
                        try:
                            q.resume()
                            q.terminate()
                        except ps.Error:
                            pass
                    proc.wait(60)
                    break
                ok, why = MON.verdict(running=True)
                if paused_at is None and not ok and why != 'warming up':
                    for q in tree(pp):
                        try:
                            q.suspend()
                        except ps.Error:
                            pass
                    paused_at, idle_since = t, None
                    log(f'{name}: PAUSED ({why})')
                elif paused_at is not None:
                    ok2, why2 = MON.verdict(running=False)
                    if ok2 or (ok and why2.startswith('RAM')):     # our own suspended job may hold the RAM: the floor is enough
                        idle_since = idle_since or t
                        if t - idle_since >= 120:
                            for q in reversed(tree(pp)):
                                try:
                                    q.resume()
                                except ps.Error:
                                    pass
                            log(f'{name}: resumed after {(t - paused_at) / 60:.0f} min')
                            paused_at = None
                    else:
                        idle_since = None
        finally:
            MON.job = None
    txt = open(lp, encoding='utf-8', errors='replace').read()
    return proc.returncode, txt


# ------------------------------------------------------------------ guards (GitHub job, schedule, bot checks, caps)

def gh_mining_busy():
    for wf in MINE_WORKFLOWS:
        r = gh('run', 'list', '--workflow', wf, '-L', '5', '--json', 'status', timeout=120)
        if r.returncode != 0:
            return f'gh run list failed ({wf})'
        if any(x.get('status') not in ('completed',) for x in json.loads(r.stdout or '[]')):
            return f'{wf} is queued / running'
    return None


def quiz_mining_busy():
    """NOW mode, quiz jobs: only a GitHub QUIZ mining run conflicts (same staging state); a sound / slow-mo run does not"""
    r = gh('run', 'list', '--workflow', 'quiz-pool-mine.yml', '-L', '5', '--json', 'status', timeout=120)
    if r.returncode != 0:
        return 'gh run list failed (quiz-pool-mine.yml)'
    return 'quiz-pool-mine.yml is queued / running' if any(x.get('status') != 'completed' for x in json.loads(r.stdout or '[]')) else None


def tonight_pool(day):
    rot = gh('variable', 'get', 'MINE_ROTATION', timeout=60).stdout.strip()
    r = subprocess.run([sys.executable, os.path.join(WT, 'scripts', 'pool-mine-rotation.py'), '--rotation', rot, '--date', day],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or 'sound'


def youtube_blackout():
    """19:00 UTC .. 04:00 UTC: when tonight's nightly job mines a YouTube pool, wait until that run has completed"""
    n = now_utc()
    if NOW or 4 <= n.hour < 19:
        return None
    night = (n if n.hour >= 19 else n - dt.timedelta(days=1)).date()
    pool = tonight_pool(night.isoformat())
    if pool not in YT_POOLS:
        return None
    since = dt.datetime.combine(night, dt.time(19, 0), dt.timezone.utc)
    r = gh('run', 'list', '--workflow', 'pool-mine-nightly.yml', '-L', '5', '--json', 'status,createdAt', timeout=120)
    for x in json.loads(r.stdout or '[]') if r.returncode == 0 else []:
        c = dt.datetime.fromisoformat(x['createdAt'].replace('Z', '+00:00'))
        if c >= since and x['status'] == 'completed':
            return None
    return f"tonight's nightly job mines {pool} (YouTube): waiting for it to finish"


def day_state():
    st = rj(STATE, {})
    if st.get('day') != today():
        st = {'day': today(), 'sources': 0, 'batches': 0, 'quiz_done': False, 'backoff_until': st.get('backoff_until', 0),
              'jobs': []}
    return st


def bot_check(txt, st, name):
    m = BOT_RE.search(txt or '')
    if not m:
        return False
    st['backoff_until'] = time.time() + BACKOFF_H * 3600
    wj(STATE, st)
    log(f'{name}: BOT CHECK / rate limit seen ({m.group(0)!r}): no YouTube for {BACKOFF_H} h')
    return True


# ------------------------------------------------------------------ worktree

def ensure_worktree():
    os.makedirs(HOME, exist_ok=True)
    if not os.path.isdir(os.path.join(WT, '.git')) and not os.path.isfile(os.path.join(WT, '.git')):
        git('fetch', 'origin', 'main', cwd=MAIN)
        git('worktree', 'add', '--detach', WT, 'origin/main', cwd=MAIN)
        log(f'worktree created: {WT}')
    nm = os.path.join(WT, 'node_modules')
    if not os.path.exists(nm) and os.path.isdir(os.path.join(MAIN, 'node_modules')):
        subprocess.run(['cmd', '/c', 'mklink', '/J', nm, os.path.join(MAIN, 'node_modules')], capture_output=True)


def sync_wt():
    """the worktree = exactly origin/main (it only ever holds this miner's throw-away changes)"""
    git('fetch', 'origin', 'main')
    git('reset', '--hard', 'origin/main')
    git('clean', '-fdq', '-e', 'output/', '-e', 'node_modules')


# ------------------------------------------------------------------ SOUND

SOUND_HOME = os.path.join(HOME, 'sound')
PENDING = os.path.join(SOUND_HOME, 'pending')
FETCH_REL = os.path.join('memory', 'edit-pool-fetch.json')


def pending_dirs():
    return sorted(d for d in glob.glob(os.path.join(PENDING, '*', 'out')) if os.path.isdir(d))


def pending_count():
    return sum(len(rj(os.path.join(d, 'approved.json'), {})) for d in pending_dirs())


def push_fetch_state(fstates, label):
    """union of the batch fetch states into memory/edit-pool-fetch.json on origin/main (CI and the PC never mine a seen video)"""
    for i in range(4):
        sync_wt()
        p = os.path.join(WT, FETCH_REL)
        d = rj(p, {'seen': {}, 'queries': {}})
        before = json.dumps(d, sort_keys=True)
        for fs in fstates:
            for k in ('seen', 'queries'):
                for key, v in (fs.get(k) or {}).items():
                    old = d.setdefault(k, {}).get(key)
                    if old is None or (k == 'seen' and v != old and str(old).startswith('unclean: burned-in text') and ';' not in str(old)):
                        d[k][key] = v          # 2026-10-24: a text-only reject retried (text is per clip now) takes the new verdict
        if json.dumps(d, sort_keys=True) == before:
            return True
        wj(p, d)
        git('add', FETCH_REL)
        git('commit', '-q', '-m', f'Sound pool growth (PC, idle) {today()}: fetch state ({label})\n\n'
                                    'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
        if git('push', '-q', 'origin', 'HEAD:main', check=False).returncode == 0:
            log(f'fetch state pushed ({label})')
            return True
        time.sleep(10 + 10 * i)
    log('fetch state push FAILED (kept in the pending batch; retried at the next push)')
    return False


def sound_batch(st):
    sync_wt()
    stamp = now_utc().strftime('%Y%m%d-%H%M%S')
    work = os.path.join(PENDING, stamp)
    os.makedirs(work, exist_ok=True)
    seen0 = set(rj(os.path.join(WT, FETCH_REL), {}).get('seen', {}))
    env = job_env()
    env.update(SOUND_VID_SHARD='1', REFRESH_PER_SOURCE='14')
    cmd = [sys.executable, os.path.join(WT, 'core', 'pools', 'sound_refresh.py'), 'mine', '--work', work, '--shard', '0/1',
           '--budget-min', str(SOUND_BUDGET * 8), '--queries', str(SOUND_Q), '--max-sources', str(min(SOUND_SRC, cap_left(st)))]
    # the pool code's budget is WALL time and our pauses would eat it (2026-10-23 test: the mini was never judged): a wide wall
    # budget there, the real cap is our ACTIVE-time cap (paused minutes do not count)
    rc, txt = run_job('sound-mine', cmd, WT, env, SOUND_BUDGET * 2, youtube=True)
    fs = rj(os.path.join(work, 'out', 'fetch.json'), None) or rj(os.path.join(work, 'fetch.json'), {})
    new = [k for k, v in (fs.get('seen') or {}).items() if k not in seen0 and v != 'have']
    st['sources'] += len(new)
    rep = rj(os.path.join(work, 'out', 'report.json'), {})
    for sub in ('src', 'mini', 'vtmp', 'tmp'):                     # keep only out/ (approved minis + strips + verdicts)
        shutil.rmtree(os.path.join(work, sub), ignore_errors=True)
    log(f"sound batch {stamp}: rc {rc}, {len(new)} sources, {rep.get('minis')} minis, {rep.get('approved')} passed every gate")
    if fs:
        push_fetch_state([fs], f'{len(new)} sources')
    bot = bot_check(txt, st, 'sound')
    if not rj(os.path.join(work, 'out', 'approved.json'), {}):
        shutil.rmtree(work, ignore_errors=True)
    return bot


def sound_stage(force=False):
    dirs = pending_dirs()
    n = pending_count()
    if not dirs or (n < STAGE_AT and not force):
        return 0
    names = staged_assets()
    if names is None or sum(1 for x in names if x.startswith('sound-cand-')) >= MAX_STAGED_SOUND:
        log(f'sound: {n} pending minis wait (staging release unreachable or >= {MAX_STAGED_SOUND} sound tars wait for review)')
        return 0
    sync_wt()
    rep = os.path.join(SOUND_HOME, 'report')
    shutil.rmtree(rep, ignore_errors=True)
    os.makedirs(rep)
    shutil.rmtree(os.path.join(POOL, 'sound'), ignore_errors=True)
    r = subprocess.run(['node', os.path.join(WT, 'scripts', 'edit-pools-store.js'), 'pull', '--manifests-only', '--preset', 'sound',
                        '--dir', POOL], cwd=WT, env=job_env(), capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not glob.glob(os.path.join(POOL, 'sound', '*', 'clips.json')):
        log('sound stage: could not pull the live sound manifests:', (r.stderr or '')[-200:])
        return 0
    env = job_env()
    sr = [sys.executable, os.path.join(WT, 'core', 'pools', 'sound_refresh.py')]
    new_p = os.path.join(rep, 'new.json')
    steps = [('merge', sr + ['merge', '--pool', POOL, '--from'] + dirs + ['--new', new_p])]
    for name, cmd in steps:
        rc, txt = run_job('sound-' + name, cmd, WT, env, 60)
        if rc != 0:
            log(f'sound stage: {name} failed rc {rc}: {txt[-300:]}')
            return 0
    news = rj(new_p, [])
    if news:
        wj(os.path.join(rep, 'new-ids.json'), [x['id'] for x in news], indent=None)
        steps = [('stamp', [sys.executable, os.path.join(WT, 'core', 'pools', 'pool_visible.py'), 'stamp', '--dir', POOL,
                            '--ids', os.path.join(rep, 'new-ids.json')]),
                 ('check', sr + ['check', '--pool', POOL, '--new', new_p, '--out', os.path.join(rep, 'check-report.json')]),
                 ('sheet', sr + ['sheet', '--pool', POOL, '--new', new_p, '--out', os.path.join(rep, 'new-hits.jpg')]),
                 ('stage', sr + ['stage', '--pool', POOL, '--new', new_p, '--out', os.path.join(rep, 'stage'),
                                 '--sheets', os.path.join(rep, 'new-hits*.jpg')])]
        for name, cmd in steps:
            rc, txt = run_job('sound-' + name, cmd, WT, env, 60)
            if rc != 0 and name != 'stamp':
                log(f'sound stage: {name} failed rc {rc}: {txt[-300:]}')
                return 0
    tars = glob.glob(os.path.join(rep, 'stage', 'sound-cand-*.tar'))
    staged = 0
    for t in tars:
        m = re.match(r'sound-cand-(\d{8})-n(\d+)\.tar$', os.path.basename(t))
        dst = os.path.join(os.path.dirname(t), f"sound-cand-{m.group(1)}-pc{now_utc().strftime('%H%M')}-n{m.group(2)}.tar")
        os.replace(t, dst)
        gh('release', 'upload', RELEASE, dst, '--clobber', check=True)
        staged += int(m.group(2))
        log(f'sound: STAGED {os.path.basename(dst)}')
    push_fetch_state([rj(os.path.join(d, 'fetch.json'), {}) for d in dirs], 'staged batches')
    sync_wt()                                                    # drops the worktree's allowlist / manifest edits
    for d in dirs:
        shutil.rmtree(os.path.dirname(d), ignore_errors=True)
    log(f'sound stage: {len(news)} merged, {staged} staged (from {n} minis)')
    return staged


# ------------------------------------------------------------------ SLOW-MO

def slowmo_batch(st):
    sync_wt()
    stamp = now_utc().strftime('%Y%m%d-%H%M%S')
    base = os.path.join(HOME, 'slowmo', stamp)
    work, art, out, sd = (os.path.join(base, x) for x in ('work', 'art', 'staged', 'st'))
    os.makedirs(sd, exist_ok=True)
    shutil.rmtree(os.path.join(POOL, 'slowmo'), ignore_errors=True)
    os.makedirs(POOL, exist_ok=True)
    gh('release', 'download', RELEASE, '--pattern', 'slowmo-live-manifests.tar', '--dir', sd, '--clobber', check=True)
    with tarfile.open(os.path.join(sd, 'slowmo-live-manifests.tar')) as t:
        t.extractall(POOL, members=[m for m in t.getmembers() if m.isfile() and m.name.startswith('slowmo/') and '..' not in m.name])
    state = os.path.join(sd, 'slowmo-grow-state.json')
    if gh('release', 'download', RELEASE, '--pattern', 'slowmo-grow-state.json', '--dir', sd, '--clobber').returncode != 0:
        log('slowmo: no remote grow state: skipped (it is the dedupe)')
        return False
    n0 = len(rj(state, {}).get('sources') or {})
    env = job_env()
    env.update(SLOWMO_LIBRARY_CAP='9', SLOWMO_BOT_MAX='1')
    sm = [sys.executable, os.path.join(WT, 'core', 'pools', 'slowmo_grow.py')]
    rc, txt = run_job('slowmo-mine', sm + ['mine', '--work', work, '--shard', '0/1', '--budget-min', str(SLOWMO_BUDGET), '--batch',
                                            str(min(SLOWMO_BATCH, cap_left(st))), '--state', state], WT, env, SLOWMO_BUDGET + 60,
                      youtube=True)
    o = os.path.join(work, 'out')
    n1 = len(rj(os.path.join(o, 'state.json'), {}).get('sources') or {})
    st['sources'] += max(0, n1 - n0)
    cj = os.path.join(o, 'candidates.json')
    if os.path.isfile(cj):
        c = rj(cj, {})
        c['shard'] = 'pc/1'                                       # tar name slowmo-cand-<day>-spc-...: never clobbers CI's s0
        wj(cj, c)
        os.makedirs(art, exist_ok=True)
        shutil.move(o, os.path.join(art, 'pc'))
        fresh = os.path.join(sd, 'fresh')
        os.makedirs(fresh, exist_ok=True)
        gh('release', 'download', RELEASE, '--pattern', 'slowmo-grow-state.json', '--dir', fresh, '--clobber', check=True)
        r = subprocess.run(sm + ['publish', '--artifacts', art, '--out', out, '--state', os.path.join(fresh, 'slowmo-grow-state.json'),
                                 '--dry'], cwd=WT, env=env, capture_output=True, text=True, timeout=1800)
        tars = glob.glob(os.path.join(out, 'slowmo-cand-*.tar'))
        if r.returncode == 0 and os.path.isfile(os.path.join(out, 'slowmo-grow-state.json')):
            gh('release', 'upload', RELEASE, *tars, os.path.join(out, 'slowmo-grow-state.json'), '--clobber', check=True)
            log(f"slowmo batch {stamp}: {n1 - n0} sources, staged {[os.path.basename(x) for x in tars] or 'nothing'}")
        else:
            log(f'slowmo publish failed: {(r.stderr or "")[-300:]}')
    else:
        log(f'slowmo batch {stamp}: rc {rc}, no candidates.json')
    bot = bot_check(txt, st, 'slowmo')
    shutil.rmtree(base, ignore_errors=True)
    return bot


# ------------------------------------------------------------------ QUIZ (IRL photos + wyr pairs; no YouTube)

def quiz_batch(st):
    sync_wt()
    stamp = now_utc().strftime('%Y%m%d-%H%M%S')
    base = os.path.join(HOME, 'quiz', stamp)
    sd, art, out = (os.path.join(base, x) for x in ('st', 'art', 'staged'))
    os.makedirs(sd, exist_ok=True)
    state = os.path.join(sd, 'quiz-grow-state.json')
    if gh('release', 'download', RELEASE, '--pattern', 'quiz-grow-state.json', '--dir', sd, '--clobber').returncode != 0:
        wj(state, {})
    env = job_env()
    env.update(MEMORY_DIR='memory/quiz-channel')
    env.pop('EDIT_POOL_DIR', None)
    qg = [sys.executable, os.path.join(WT, 'core', 'quiz', 'quiz_pool_grow.py')]
    shards = (('photos', 'nightcity,cuisine,geo'), ('llm', 'wyr_food,wyr_travel'))
    if NOW:                       # no-YouTube categories: Commons photos, LLM pairs / puzzles, Wikidata / wiki people
        shards = (('photos', 'nightcity,cuisine,geo'), ('llm', 'wyr_food,wyr_travel,emoji,trivia,kpop_song'),
                  ('people', 'kpop_idol,vtuber'))
    for shard, cats in shards:
        w = os.path.join(base, 'w-' + shard)
        rc, txt = run_job(f'quiz-{shard}', qg + ['mine', '--shard', shard, '--cats', cats, '--work', w, '--state', state,
                                                  '--budget-min', str(QUIZ_BUDGET)], WT, env, QUIZ_BUDGET + 30)
        o = os.path.join(w, 'out')
        cj = os.path.join(o, 'candidates.json')
        if os.path.isfile(cj):
            c = rj(cj, {})
            c['shard'] = 'pc-' + shard
            wj(cj, c)
            if os.path.isfile(os.path.join(o, 'state.json')):
                shutil.copy2(os.path.join(o, 'state.json'), state)    # the next shard dedupes against this one's picks
            os.makedirs(art, exist_ok=True)
            shutil.move(o, os.path.join(art, shard))
        got = rj(os.path.join(art, shard, 'candidates.json'), {}).get('items') or {}
        log(f"quiz {shard}: rc {rc}, {({k: len(v) for k, v in got.items()}) or 'no candidates'}")
    if os.path.isdir(art):
        fresh = os.path.join(sd, 'fresh')
        os.makedirs(fresh, exist_ok=True)
        gh('release', 'download', RELEASE, '--pattern', 'quiz-grow-state.json', '--dir', fresh, '--clobber')
        fs = os.path.join(fresh, 'quiz-grow-state.json')
        r = subprocess.run(qg + ['publish', '--artifacts', art, '--out', out, '--state', fs if os.path.isfile(fs) else state, '--dry'],
                           cwd=WT, env=env, capture_output=True, text=True, timeout=1800)
        tars = glob.glob(os.path.join(out, 'quiz-cand-*.tar'))
        if r.returncode == 0 and os.path.isfile(os.path.join(out, 'quiz-grow-state.json')):
            gh('release', 'upload', RELEASE, *tars, os.path.join(out, 'quiz-grow-state.json'), '--clobber', check=True)
            log(f"quiz: staged {[os.path.basename(x) for x in tars] or 'nothing'}")
        else:
            log(f'quiz publish failed: {(r.stderr or "")[-300:]}')
    shutil.rmtree(base, ignore_errors=True)
    st['quiz_done'] = True
    st['quiz_runs'] = st.get('quiz_runs', 0) + 1
    return False


# ------------------------------------------------------------------ loop

def cap_left(st):
    return max(0, DAILY_CAP - st.get('sources', 0))


def lock():
    import psutil
    os.makedirs(HOME, exist_ok=True)
    if os.path.exists(LOCK):
        pid = rj(LOCK, {}).get('pid')
        try:
            if pid and psutil.pid_exists(pid) and 'local-mine' in ' '.join(psutil.Process(pid).cmdline()):
                raise SystemExit(f'another local miner runs (pid {pid})')
        except psutil.Error:
            pass
    wj(LOCK, {'pid': os.getpid(), 'started': now_utc().isoformat()})


def unlock():
    try:
        if rj(LOCK, {}).get('pid') == os.getpid():
            os.remove(LOCK)
    except OSError:
        pass


def guards(st, youtube=True):
    if youtube and st.get('backoff_until', 0) > time.time():
        return f"bot-check back-off until {dt.datetime.fromtimestamp(st['backoff_until'], dt.timezone.utc):%H:%M} UTC"
    if not os.path.isfile(COOKIES) and youtube:
        return 'no spare cookies file'
    b = gh_mining_busy() if youtube or not NOW else quiz_mining_busy()
    if b:
        return b
    return youtube_blackout() if youtube else None


def run_one(job, st):
    if not wait_idle(job):
        return False
    why = guards(st, youtube=job in YT_POOLS)              # again: the wait for an idle PC may have been long
    if why:
        log(f'{job}: not now ({why})')
        return False
    log(f'--- {job} batch (sources today {st["sources"]}/{DAILY_CAP})')
    bot = {'sound': sound_batch, 'slowmo': slowmo_batch, 'quiz': quiz_batch}[job](st)
    st['batches'] += 1
    st.setdefault('jobs', []).append(job)
    wj(STATE, st)
    return bot


def cmd_run(a):
    global MON
    lock()
    try:
        import psutil
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
        MON = Monitor()
        ensure_worktree()
        log(f'local miner started (pid {os.getpid()}, cap {DAILY_CAP} sources / day{", NOW mode: no owner-activity pause" if NOW else ""})')
        while not stop_requested():
            st = day_state()
            wj(STATE, st)
            nxt = None
            if cap_left(st) > 0:
                yt = sum(1 for j in st['jobs'] if j in YT_POOLS)
                nxt = 'slowmo' if yt % 3 == 2 else 'sound'          # sound : slow-mo = 2 : 1
                if nxt == 'sound' and pending_count() >= 30:
                    nxt = 'slowmo'                                   # sound review backlog: grow slow-mo meanwhile
            quiz_ok = not st['quiz_done'] or (NOW and st.get('quiz_runs', 0) < QUIZ_NOW_MAX)
            if not st['quiz_done'] and (st['batches'] >= 2 or nxt is None):
                nxt = 'quiz'
            why = guards(st, youtube=nxt in YT_POOLS) if nxt else None
            if why and nxt in YT_POOLS and quiz_ok and not guards(st, youtube=False):
                nxt, why = 'quiz', None                              # YouTube blocked: do the quiz job meanwhile
            if why or not nxt:
                if pending_dirs() and not gh_mining_busy() and wait_idle('sound stage'):
                    sound_stage(force=True)                          # nothing to mine now: stage what waits
                log(f"sleeping: {why or 'daily cap reached / all done'}")
                if not sleep_checked(30 * 60 if why else 3600):
                    break
                continue
            try:
                bot = run_one(nxt, st)
                if nxt == 'sound':
                    sound_stage(force=bot or cap_left(st) == 0)
            except Exception as e:  # noqa: BLE001
                log(f'{nxt} batch failed: {str(e)[:300]}')
                bot = False
                sleep_checked(15 * 60)
            if bot:
                log('bot check: session stopped for YouTube; quiz-only until the back-off ends')
            sleep_checked(180)
        if pending_dirs():
            try:
                sound_stage(force=True)
            except Exception as e:  # noqa: BLE001
                log(f'final stage failed: {str(e)[:200]}')
        log('local miner stopped')
    finally:
        unlock()


def cmd_once(a):
    global MON
    lock()
    try:
        import psutil
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
        MON = Monitor()
        ensure_worktree()
        st = day_state()
        why = guards(st, youtube=a.job in YT_POOLS)
        if why:
            raise SystemExit(f'not now: {why}')
        run_one(a.job, st)
        if a.job == 'sound':
            log(f'staged {sound_stage(force=True)} sound clips')
    finally:
        unlock()


def cmd_stop(a):
    os.makedirs(HOME, exist_ok=True)
    open(STOP, 'w').close()
    print(f'stop requested ({STOP}); the miner exits at its next check (a running job is terminated, its batch staged)')


def cmd_status(a):
    import psutil
    pid = rj(LOCK, {}).get('pid')
    alive = bool(pid and psutil.pid_exists(pid))
    print(json.dumps({'running': alive, 'pid': pid if alive else None, 'state': rj(STATE, {}), 'pending_sound_minis': pending_count(),
                      'log': LOG}, indent=1))
    try:
        print(''.join(open(LOG, encoding='utf-8').readlines()[-15:]))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('run')
    o = sp.add_parser('once')
    o.add_argument('--job', choices=['sound', 'slowmo', 'quiz'], default='sound')
    sp.add_parser('stop')
    sp.add_parser('status')
    a = ap.parse_args()
    if a.cmd in ('run', 'once') and os.path.exists(STOP):
        os.remove(STOP)
    {'run': cmd_run, 'once': cmd_once, 'stop': cmd_stop, 'status': cmd_status}[a.cmd](a)


if __name__ == '__main__':
    main()
