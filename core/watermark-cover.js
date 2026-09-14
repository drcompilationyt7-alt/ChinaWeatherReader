/**
 * Watermark pass — cover source watermarks with ours, or place ours normally.
 *
 * Runs on the rendered 1080x1920 short (second, lossless FFV1 pass):
 *   1. core/watermark-detector.py proposes static overlays (channel handles,
 *      platform logos, "AI generated" tags) by looking for edges that stay
 *      put across the whole clip while the content moves.
 *   2. Gemini looks at crops of each proposal and confirms it is a
 *      watermark / logo / username and not part of the content. Very strong
 *      proposals are trusted even when Gemini is unavailable.
 *   3. Confirmed regions get a `delogo` pass, a dark translucent badge and our
 *      logo + handle on top, sized to cover the region. When nothing is
 *      found the usual small watermark goes bottom-right.
 */
const path = require('path');
const fs = require('fs');
const { execSync, execFileSync } = require('child_process');
const { Logger } = require('./logger');

const logger = new Logger('Watermark');

const DETECTOR = path.join(__dirname, 'watermark-detector.py');
const LOGO = path.join(__dirname, 'assets', 'mrw-logo.png');
const HANDLE = process.env.YOUTUBE_HANDLE || '@Mr.WorldWideWebster';
const FRAME_W = 1080;
const FRAME_H = 1920;

// default (no source watermark) look — same as before
const DEF = { logo: 80, marginRight: 20, marginBottom: 80, fontSize: 28, alpha: 0.40 };
// covering badge look
const COVER = { minW: 360, minH: 84, pad: 10, fontMin: 22, fontMax: 32, logoMax: 96, boxAlpha: 0.62, alpha: 0.95 };

function pythonCommand() {
  for (const cmd of [process.env.PYTHON_BIN, 'python3', 'python'].filter(Boolean)) {
    try { execFileSync(cmd, ['-c', 'import cv2, numpy'], { stdio: 'ignore', timeout: 20000 }); return cmd; } catch {}
  }
  return null;
}

/** Static-overlay proposals from the detector, in output pixel coordinates. */
function proposeWatermarks(videoPath) {
  const py = pythonCommand();
  if (!py || !fs.existsSync(DETECTOR)) { logger.info('Watermark detector unavailable (python/opencv) — using default position'); return { boxes: [], static: false }; }
  try {
    const out = execFileSync(py, [DETECTOR, videoPath, '--frames', '32'], { encoding: 'utf8', timeout: 240000, maxBuffer: 8 * 1024 * 1024 });
    const res = JSON.parse(out.trim().split('\n').pop());
    if (res.error) logger.warn(`Detector: ${res.error}`);
    if (res.static) logger.info('Static shot — cannot separate a watermark from the background, using default position');
    return { boxes: res.boxes || [], static: !!res.static, width: res.width, height: res.height };
  } catch (e) {
    logger.warn(`Detector failed: ${(e.message || '').substring(0, 80)}`);
    return { boxes: [], static: false };
  }
}

/** Ask Gemini whether each proposal is really a watermark; returns confirmed boxes. */
async function confirmWatermarks(videoPath, boxes, gemini, tmpDir) {
  const confirmed = [];
  if (!boxes.length) return confirmed;
  const canAsk = gemini && typeof gemini.analyzeFrames === 'function';
  let duration = 30;
  try { duration = parseFloat(execSync(`ffprobe -v error -show_entries format=duration -of csv=p=0 "${videoPath}"`, { encoding: 'utf8' }).trim()) || 30; } catch {}

  for (const box of boxes) {
    if (!canAsk) {
      if (box.strong) { confirmed.push(box); logger.info(`Strong static overlay accepted without confirmation at ${box.x},${box.y} ${box.w}x${box.h}`); }
      continue;
    }
    // crops with context from two different moments
    const crops = [];
    const cx = Math.max(0, box.x - 40), cy = Math.max(0, box.y - 40);
    const cw = Math.min(FRAME_W - cx, box.w + 80), ch = Math.min(FRAME_H - cy, box.h + 80);
    for (const frac of [0.3, 0.7]) {
      const p = path.join(tmpDir, `wm_${Math.round(frac * 100)}_${box.x}_${box.y}.png`);
      try {
        execSync(`ffmpeg -y -v error -ss ${(duration * frac).toFixed(2)} -i "${videoPath}" -frames:v 1 -vf "crop=${cw}:${ch}:${cx}:${cy},scale=${Math.min(600, cw * 2)}:-2" "${p}"`, { timeout: 20000 });
        if (fs.existsSync(p)) crops.push(p);
      } catch {}
    }
    let verdict = null;
    try {
      const prompt = `These are two crops (with some margin) of the SAME spot of a short video at two different moments. Is the element that stays identical in both crops a burned-in WATERMARK, platform LOGO, channel/username HANDLE or "AI generated" tag that was added on top of the footage (as opposed to a real object, sign, subtitle or caption that belongs to the content)?
Return STRICT JSON: {"isWatermark": true|false, "what": "short description", "confidence": 0-10}`;
      const res = await gemini.analyzeFrames(crops, prompt, 'You spot watermarks, logos and username overlays in social media videos. Be precise: subtitles, captions and real-world signs are NOT watermarks.');
      const m = res && res.match(/\{[\s\S]*\}/);
      if (m) verdict = JSON.parse(m[0]);
    } catch (e) {
      logger.warn(`Watermark confirm failed: ${(e.message || '').substring(0, 60)}`);
    }
    for (const p of crops) { try { fs.unlinkSync(p); } catch {} }
    if (verdict && verdict.isWatermark === true && (Number(verdict.confidence) || 0) >= 5) {
      logger.success(`Source watermark confirmed: ${verdict.what || '?'} at ${box.x},${box.y} ${box.w}x${box.h}`);
      confirmed.push({ ...box, what: verdict.what || '' });
    } else if (!verdict && box.strong) {
      logger.info(`Gemini unavailable — trusting strong static overlay at ${box.x},${box.y}`);
      confirmed.push(box);
    } else {
      logger.info(`Proposal at ${box.x},${box.y} rejected (${verdict ? (verdict.what || 'not a watermark') : 'no verdict'})`);
    }
    if (confirmed.length >= 2) break;
  }
  return confirmed;
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

/**
 * Build the filter graph for the watermark pass.
 * @returns {{ filterComplex: string, out: string }}
 */
function buildWatermarkFilter(boxes, fontfile = null) {
  const font = fontfile ? `fontfile='${fontfile}':` : '';
  const text = HANDLE.replace(/'/g, "\\'").replace(/:/g, '\\:');
  const filters = [];
  let cur = '0:v';
  if (!boxes.length) {
    filters.push(`[1:v]scale=${DEF.logo}:${DEF.logo}:force_original_aspect_ratio=decrease,format=rgba,colorchannelmixer=aa=${DEF.alpha}[wm]`);
    filters.push(`[${cur}][wm]overlay=W-w-${DEF.marginRight}:H-h-${DEF.marginBottom}:format=auto,drawtext=${font}text='${text}':fontcolor=white@${DEF.alpha}:fontsize=${DEF.fontSize}:x=W-tw-${DEF.marginRight}:y=H-th-${Math.round(DEF.marginBottom / 2)}:shadowcolor=black@${DEF.alpha}:shadowx=1:shadowy=1[vout]`);
    return { filterComplex: filters.join(';'), out: '[vout]' };
  }
  boxes.forEach((b, i) => {
    // badge geometry: at least big enough for logo + handle, centred on the mark
    const bw = clamp(Math.max(b.w + 2 * COVER.pad, COVER.minW), 120, FRAME_W - 8);
    const bh = clamp(Math.max(b.h + 2 * COVER.pad, COVER.minH), 48, FRAME_H - 8);
    const bx = clamp(Math.round(b.x + b.w / 2 - bw / 2), 4, FRAME_W - bw - 4);
    const by = clamp(Math.round(b.y + b.h / 2 - bh / 2), 4, FRAME_H - bh - 4);
    // delogo needs a box strictly inside the frame
    const dx = clamp(b.x, 1, FRAME_W - 3), dy = clamp(b.y, 1, FRAME_H - 3);
    const dw = clamp(b.w, 2, FRAME_W - dx - 1), dh = clamp(b.h, 2, FRAME_H - dy - 1);
    const logo = clamp(Math.round(bh * 0.72), 40, COVER.logoMax);
    const fontSize = clamp(Math.round(bh * 0.36), COVER.fontMin, COVER.fontMax);
    const lx = bx + COVER.pad, ly = Math.round(by + (bh - logo) / 2);
    const tx = lx + logo + 14, ty = `${by}+(${bh}-th)/2`;
    filters.push(`[${i + 1}:v]scale=${logo}:${logo}:force_original_aspect_ratio=decrease,format=rgba,colorchannelmixer=aa=${COVER.alpha}[wm${i}]`);
    filters.push(
      `[${cur}]delogo=x=${dx}:y=${dy}:w=${dw}:h=${dh},` +
      `drawbox=x=${bx}:y=${by}:w=${bw}:h=${bh}:color=black@${COVER.boxAlpha}:t=fill[b${i}]`
    );
    filters.push(
      `[b${i}][wm${i}]overlay=${lx}:${ly}:format=auto,` +
      `drawtext=${font}text='${text}':fontcolor=white@${COVER.alpha}:fontsize=${fontSize}:x=${tx}:y=${ty}:shadowcolor=black@0.6:shadowx=1:shadowy=1[v${i}]`
    );
    cur = `v${i}`;
  });
  filters.push(`[${cur}]null[vout]`);
  return { filterComplex: filters.join(';'), out: '[vout]' };
}

/**
 * Detect + confirm + apply. Writes `outputPath` (FFV1 lossless, audio copied).
 * @returns {{ ok: boolean, covered: Array, mode: 'cover'|'default' }}
 */
async function applyWatermarkPass(inputPath, outputPath, { gemini = null, tmpDir = null, fontfile = null } = {}) {
  if (!fs.existsSync(LOGO)) {
    logger.warn(`Logo missing at ${LOGO} — copying video without watermark`);
    fs.copyFileSync(inputPath, outputPath);
    return { ok: true, covered: [], mode: 'none' };
  }
  const work = tmpDir || path.dirname(outputPath);
  const { boxes } = proposeWatermarks(inputPath);
  if (boxes.length) logger.info(`Static overlay proposals: ${boxes.map(b => `${b.x},${b.y} ${b.w}x${b.h} p=${b.persistence}${b.strong ? '*' : ''}`).join(' | ')}`);
  const covered = await confirmWatermarks(inputPath, boxes, gemini, work);

  // try covering; if that render fails for any reason fall back to the default spot
  for (const boxes of (covered.length ? [covered, []] : [[]])) {
    const { filterComplex, out } = buildWatermarkFilter(boxes, fontfile);
    const scriptPath = path.join(work, `wm_filter_${Date.now()}.txt`);
    fs.writeFileSync(scriptPath, filterComplex, 'utf8');
    const logoInputs = boxes.length ? boxes.map(() => ` -i "${LOGO}"`).join('') : ` -i "${LOGO}"`;
    const cmd = `ffmpeg -y -i "${inputPath}"${logoInputs} -filter_complex_script "${scriptPath}" -map "${out}" -map 0:a? -c:v ffv1 -level 3 -coder 1 -context 1 -g 1 -slices 16 -slicecrc 1 -pix_fmt yuv444p10le -c:a copy "${outputPath}"`;
    try {
      execSync(cmd, { timeout: 600000, maxBuffer: 500 * 1024 * 1024, stdio: ['ignore', 'ignore', 'pipe'] });
      try { fs.unlinkSync(scriptPath); } catch {}
      if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100000) {
        logger.success(boxes.length ? `Watermark: covered ${boxes.length} source mark(s)` : 'Watermark: default bottom-right');
        return { ok: true, covered: boxes, mode: boxes.length ? 'cover' : 'default' };
      }
    } catch (e) {
      const err = (e.stderr ? String(e.stderr) : e.message || '').trim();
      logger.warn(`Watermark pass failed${boxes.length ? ' (cover mode, retrying default)' : ''}: ${err.substring(Math.max(0, err.length - 300))}`);
    }
  }
  return { ok: false, covered: [], mode: 'failed' };
}

module.exports = { applyWatermarkPass, proposeWatermarks, confirmWatermarks, buildWatermarkFilter };
