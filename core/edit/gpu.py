"""
GPU video encode / decode for the edit renderer, with automatic CPU fallback.

    from gpu import venc, convert, run, hwdec, usable, info

    venc('final', preset='slow', crf=16)      # -> ['-c:v', ...]   libx264 args unchanged on CPU, NVENC on this PC
    convert(cmd, 'test')                       # rewrite an existing ffmpeg cmd list: libx264 (+preset/crf/threads) -> NVENC
    run(cmd, 'final', **subprocess_kwargs)     # convert + subprocess.run; if NVENC fails, rerun the ORIGINAL cmd with libx264
    hwdec(path)                                # ['-hwaccel', 'cuda'] for big H.264/HEVC/AV1/VP9 inputs when usable, else []
    torch_device()                             # 'cuda' for torch models (SigLIP, Demucs, aep_replay) when usable, else 'cpu'

GitHub Actions / CI (GITHUB_ACTIONS or CI set) -> CPU only: every helper returns the caller's args untouched, so the
production path is byte-identical to before. EDIT_GPU=0 forces CPU, EDIT_GPU=1 forces the GPU probe even on CI.
Detected once (a tiny test encode / decode), cached in the process and in the environment (child processes inherit it).

Purposes (what the NVENC settings are tuned for):
    final         deliverables           p7 hq vbr cq18 spatial+temporal AQ, bf3 (a caller's -b:v / -maxrate caps are kept)
    test          previews, proofs, SBS  p4 cq27
    intermediate  near-lossless hand-offs p5 cq12
"""
import os
import subprocess

_CACHE = {}
_ENV_KEY = 'EDIT_GPU_PROBE'          # 'nvenc=1,cuda=1' once probed, inherited by child processes

NVENC = {
    'final': ['-c:v', 'h264_nvenc', '-preset', 'p7', '-tune', 'hq', '-rc', 'vbr', '-cq', '18', '-b:v', '0',
              '-spatial-aq', '1', '-temporal-aq', '1', '-bf', '3', '-profile:v', 'high'],
    'test': ['-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr', '-cq', '27', '-b:v', '0',
             '-profile:v', 'high'],
    'intermediate': ['-c:v', 'h264_nvenc', '-preset', 'p5', '-tune', 'hq', '-rc', 'vbr', '-cq', '12', '-b:v', '0',
                     '-spatial-aq', '1', '-bf', '3', '-profile:v', 'high'],
}
# options a libx264 command carries that NVENC does not understand (each takes one value)
_X264_ONLY = ('-preset', '-crf', '-tune', '-x264-params', '-x264opts', '-profile:v', '-level', '-refs')


def _env_off():
    v = os.environ.get('EDIT_GPU', '').strip().lower()
    if v in ('0', 'off', 'false', 'no'):
        return True
    if v in ('1', 'on', 'true', 'yes'):
        return False
    return bool(os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI'))


def _candidates():
    """ffmpeg binaries to try for NVENC: the one on PATH first; then EDIT_FFMPEG_NVENC, then ~/tools/ffmpeg-nvenc (an
    older build whose NVENC API matches an older NVIDIA driver: ffmpeg >= 7.1 wants driver 570+, 6.1 works from 530)."""
    c = ['ffmpeg']
    env = os.environ.get('EDIT_FFMPEG_NVENC')
    if env:
        c.append(env)
    c.append(os.path.join(os.path.expanduser('~'), 'tools', 'ffmpeg-nvenc', 'bin', 'ffmpeg.exe'))
    c.append(os.path.join(os.path.expanduser('~'), 'tools', 'ffmpeg-nvenc', 'bin', 'ffmpeg'))
    return [x for x in c if x == 'ffmpeg' or os.path.isfile(x)]


def _probe():
    if 'p' in _CACHE:
        return _CACHE['p']
    res = {'nvenc': False, 'cuda': False, 'bin': 'ffmpeg', 'why': ''}
    if _env_off():
        res['why'] = 'disabled (EDIT_GPU=0 or CI/GITHUB_ACTIONS)'
        _CACHE['p'] = res
        return res
    prev = os.environ.get(_ENV_KEY)
    if prev:
        kv = dict(x.split('=', 1) for x in prev.split(',') if '=' in x)
        res['nvenc'], res['cuda'], res['bin'], res['why'] = kv.get('nvenc') == '1', kv.get('cuda') == '1', kv.get('bin', 'ffmpeg'), 'inherited'
        _CACHE['p'] = res
        return res
    try:
        for b in _candidates():
            r = subprocess.run([b, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:d=0.2:r=25',
                                '-c:v', 'h264_nvenc', '-pix_fmt', 'yuv420p', '-f', 'null', '-'],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                res['nvenc'], res['bin'] = True, b
                break
            res['why'] = r.stderr.decode(errors='ignore').strip().splitlines()[0][-140:] if r.stderr.strip() else 'nvenc failed'
        # CUDA decode probe (PATH ffmpeg; independent of NVENC)
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x240:d=0.3:r=25',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', tmp], capture_output=True, timeout=30)
            r2 = subprocess.run(['ffmpeg', '-v', 'error', '-hwaccel', 'cuda', '-i', tmp, '-f', 'null', '-'],
                                capture_output=True, timeout=30)
            res['cuda'] = r2.returncode == 0 and not r2.stderr.strip()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    except Exception as e:  # no ffmpeg / timeout: CPU
        res['why'] = str(e)[:120]
    os.environ[_ENV_KEY] = f"nvenc={int(res['nvenc'])},cuda={int(res['cuda'])},bin={res['bin']}"
    _CACHE['p'] = res
    return res


def ffmpeg_bin():
    """The ffmpeg that does the NVENC encodes (PATH ffmpeg, or the older build when the driver is too old for it)."""
    return _probe()['bin']


def usable():
    """True when NVENC encodes are on (GPU present, not CI, not EDIT_GPU=0)."""
    return _probe()['nvenc']


def info():
    p = _probe()
    return {'nvenc': p['nvenc'], 'bin': p['bin'], 'cuda_decode': p['cuda'], 'note': p['why'],
            'ci': bool(os.environ.get('GITHUB_ACTIONS') or os.environ.get('CI')), 'EDIT_GPU': os.environ.get('EDIT_GPU')}


def venc(purpose='final', preset=None, crf=None, threads=None, bitrate=None, maxrate=None, bufsize=None, force_cpu=False):
    """Video encoder args. CPU: exactly '-c:v libx264 -preset P -crf N [-threads T]' (what the callers wrote before).
    GPU: the NVENC set for the purpose; a bitrate (-b:v) switches to capped VBR and keeps maxrate/bufsize."""
    if usable() and not force_cpu:
        a = list(NVENC.get(purpose, NVENC['final']))
        if bitrate or maxrate:
            i = a.index('-cq')
            del a[i:i + 2]
            a[a.index('-b:v') + 1] = str(bitrate or maxrate)
            if maxrate:
                a += ['-maxrate', str(maxrate)]
            if bufsize:
                a += ['-bufsize', str(bufsize)]
        return a
    a = ['-c:v', 'libx264']
    if preset:
        a += ['-preset', str(preset)]
    if crf is not None:
        a += ['-crf', str(crf)]
    if threads:
        a += ['-threads', str(threads)]
    return a


def _pick_purpose(cmd, purpose):
    if purpose:
        return purpose
    try:
        crf = float(cmd[cmd.index('-crf') + 1])
    except (ValueError, IndexError):
        return 'final'
    return 'intermediate' if crf <= 12 else ('test' if crf >= 19 else 'final')


def convert(cmd, purpose=None):
    """Rewrite an ffmpeg arg list that encodes with libx264 into the NVENC equivalent (same pix_fmt, audio, filters,
    container flags). Returns the list unchanged when the GPU is off, or when the cmd has no '-c:v libx264'."""
    cmd = list(cmd)
    if not usable():
        return cmd
    try:
        k = next(i for i in range(len(cmd) - 1) if cmd[i] in ('-c:v', '-vcodec') and cmd[i + 1] == 'libx264')
    except StopIteration:
        return cmd
    purpose = _pick_purpose(cmd, purpose)
    if cmd and os.path.basename(str(cmd[0])).lower() in ('ffmpeg', 'ffmpeg.exe'):
        cmd[0] = ffmpeg_bin()
    br = {}
    for key in ('-b:v', '-maxrate', '-bufsize'):
        if key in cmd:
            br[key[1:].replace(':v', '')] = cmd[cmd.index(key) + 1]
    out, skip = [], 0
    for i, x in enumerate(cmd):
        if skip:
            skip -= 1
            continue
        if i == k:
            out += venc(purpose, bitrate=br.get('b'), maxrate=br.get('maxrate'), bufsize=br.get('bufsize'))
            skip = 1
            continue
        if x in _X264_ONLY and i + 1 < len(cmd):
            skip = 1
            continue
        if br and x in ('-b:v', '-maxrate', '-bufsize'):
            skip = 1
            continue
        out.append(x)
    return out


def run(cmd, purpose=None, **kw):
    """subprocess.run(convert(cmd)); a failed NVENC encode is retried once with the original libx264 command.
    check=True is honoured on the final attempt only."""
    chk = kw.pop('check', False)
    new = convert(cmd, purpose)
    if new != list(cmd):
        try:
            r = subprocess.run(new, **kw)
            if r.returncode == 0:
                return r
        except subprocess.TimeoutExpired:
            pass
        print('[gpu] NVENC encode failed -> retrying with libx264', flush=True)
    return subprocess.run(list(cmd), check=chk, **kw)


_BIG_CODECS = ('h264', 'hevc', 'av1', 'vp9', 'mpeg2video', 'vp8')


def hwdec(path=None, w=None, h=None, min_pixels=1920 * 1080, codec=None):
    """['-hwaccel', 'cuda'] when CUDA decode is usable and the input is big enough to profit (>= 1080p by default),
    else []. Frames come back to system memory, so every CPU filter after it behaves as before."""
    if _env_off() or not _probe()['cuda'] or os.environ.get('EDIT_GPU_DECODE', '1') == '0':
        return []
    if path is not None and (w is None or h is None or codec is None):
        try:
            r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                'stream=codec_name,width,height', '-of', 'csv=p=0', path], capture_output=True, timeout=20)
            c, ww, hh = r.stdout.decode().strip().split(',')[:3]
            codec, w, h = c, int(ww), int(hh)
        except Exception:
            return []
    if not w or not h or w * h < min_pixels or (codec and codec not in _BIG_CODECS):
        return []
    return ['-hwaccel', 'cuda']


def torch_device():
    """'cuda' when torch is installed with CUDA, a GPU is visible and the GPU is not disabled (EDIT_GPU=0, or CI /
    GITHUB_ACTIONS without EDIT_GPU=1); else 'cpu'. Cached per process. Callers keep their CPU path unchanged."""
    if 'dev' in _CACHE:
        return _CACHE['dev']
    dev = 'cpu'
    if not _env_off():
        try:
            import torch
            if torch.cuda.is_available():
                dev = 'cuda'
        except Exception:  # noqa: BLE001  (no torch / broken CUDA runtime -> CPU)
            dev = 'cpu'
    _CACHE['dev'] = dev
    return dev


if __name__ == '__main__':
    import json
    print(json.dumps(info()))
    print(' '.join(venc('final', preset='slow', crf=16)))
