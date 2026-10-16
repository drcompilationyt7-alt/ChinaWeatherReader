"""
Mainland Unpacked: render a week of Shorts with Remotion and write the upload plan.

  python core/china/build_week.py content/china/week-2026-10-13 [out_dir] [--only slug] [--skip-existing]

For each <slug>.json in the week folder (name order = posting order):
  1. prep_short.prep(): voice, word timings, cues, photos, sfx, music -> remotion/public/china/<slug>/props.json
  2. npx remotion render ... -> remotion/out/<slug>.mp4
  3. audio mastered to -14 LUFS (limiter, true peak < -1 dBTP after AAC) -> <out_dir>/<slug>.mp4
Then <out_dir>/week.json (descriptions with photo and music credits) for scripts/china/push-week.js.
Run with the ttsvenv Python (Chatterbox / faster-whisper). Set KOKORO_DIR if CHINA_TTS=kokoro.
"""
import glob
import json
import os
import subprocess
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'core', 'quiz'))
import sound  # noqa: E402

REM = os.path.join(ROOT, 'remotion')
NPX = 'npx.cmd' if os.name == 'nt' else 'npx'
# Renders run at below-normal priority (inherited by Chrome / ffmpeg) with 2 workers, so the owner's PC stays
# responsive while a week renders (owner 2026-10-12: "limit the usage").
LOW = {'creationflags': 0x00004000} if os.name == 'nt' else {}   # BELOW_NORMAL_PRIORITY_CLASS


# Owner 2026-10-16: "the current pace and just 2x it" (from the week of 2026-10-20 on). The finished edit is sped up
# as a whole (picture, voice, SFX, music together; the voice keeps its pitch), so every cue stays in sync.
PACE = float(os.environ.get('CHINA_PACE', '2.0'))


def master(src, dst):
    tmp = dst + '.a.wav'
    sped = dst + '.pace.mp4'
    if abs(PACE - 1.0) > 1e-3:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-filter_complex',
                        f'[0:v]setpts=PTS/{PACE},fps=30[v];[0:a]atempo={PACE}[a]', '-map', '[v]', '-map', '[a]',
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le',
                        sped.replace('.mp4', '.mkv')], check=True, **LOW)
        src = sped.replace('.mp4', '.mkv')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-ac', '1', '-ar', str(sound.SR), tmp], check=True)
    with wave.open(tmp) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16) / 32768.0
    sound.write_wav(tmp, sound.master(x, -14.0))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-i', tmp, '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
                    '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', dst], check=True)
    os.remove(tmp)
    if src.endswith('.pace.mkv'):
        os.remove(src)


def render_one(spec_path, slug, props_path, mp4):
    # prep in its own process: the voice / whisper models (several GB) are freed before Chrome renders
    subprocess.run([sys.executable, os.path.join(HERE, 'prep_short.py'), spec_path], check=True, **LOW)
    raw = os.path.join(REM, 'out', f'{slug}.mp4')
    cmd = [NPX, 'remotion', 'render', 'src/index.ts', 'Short', raw, f'--props={props_path}', '--log=error',
           f'--concurrency={os.environ.get("REMOTION_CONCURRENCY", "1")}', '--timeout=120000']
    # renders failed now and then while the PC was busy (asset loading past the 30 s default): 2 min timeout, one retry,
    # and the error kept in remotion/out/<slug>.log
    for attempt in (1, 2):
        with open(os.path.join(REM, 'out', f'{slug}.log'), 'w', encoding='utf-8') as log:
            r = subprocess.run(cmd, cwd=REM, stdout=log, stderr=subprocess.STDOUT, **LOW)
        if r.returncode == 0:
            break
        print(f'! {slug}: render attempt {attempt} failed (see remotion/out/{slug}.log)', flush=True)
    else:
        raise RuntimeError(f'{slug}: render failed twice')
    master(raw, mp4)


def main():
    argv = sys.argv[1:]
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--only')]
    week = args[0]
    out = os.path.abspath(args[1] if len(args) > 1 else os.path.join(ROOT, 'output', 'china', os.path.basename(week.rstrip('/\\'))))
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    os.makedirs(out, exist_ok=True)
    plan_path = os.path.join(out, 'week.json')
    items = json.load(open(plan_path, encoding='utf-8'))['items'] if os.path.exists(plan_path) else []
    by_file = {i['file']: i for i in items}

    for spec_path in sorted(glob.glob(os.path.join(week, '*.json'))):
        spec = json.load(open(spec_path, encoding='utf-8'))
        slug = spec.get('slug') or os.path.splitext(os.path.basename(spec_path))[0]
        if only and only != slug:
            continue
        t0 = time.time()
        mp4 = os.path.join(out, f'{slug}.mp4')
        props_path = os.path.join(REM, 'public', 'china', slug, 'props.json')
        if '--skip-existing' in sys.argv and os.path.exists(mp4) and os.path.exists(props_path):
            # resume an interrupted week: keep the finished render, only rebuild its plan entry
            props = json.load(open(props_path, encoding='utf-8'))
            print(f'= {slug}: already rendered, kept', flush=True)
        else:
            render_one(spec_path, slug, props_path, mp4)
            props = json.load(open(props_path, encoding='utf-8'))
        desc = spec.get('description', '')
        photos = [c for c in props['credits'] if not c.startswith('Music:')]
        music = [c for c in props['credits'] if c.startswith('Music:')]
        if photos:
            desc += '\n\nFootage & photos:\n' + '\n'.join('• ' + c for c in photos)
        if music:
            desc += '\n\n' + '\n'.join(music)
        by_file[f'{slug}.mp4'] = {'type': 'short', 'file': f'{slug}.mp4', 'title': spec['title'], 'description': desc[:4900],
                                  'tags': spec.get('tags', []), 'categoryId': spec.get('categoryId', '28')}
        print(f'✓ {slug}: {props["durationInFrames"] / 30:.1f} s, built in {time.time() - t0:.0f} s', flush=True)

    items = [by_file[k] for k in sorted(by_file)]
    json.dump({'items': items}, open(plan_path, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(f'week.json: {len(items)} item(s) in {out}')


if __name__ == '__main__':
    main()
