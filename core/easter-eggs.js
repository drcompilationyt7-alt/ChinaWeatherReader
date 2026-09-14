/**
 * Easter Egg Overlays — tiny green-screen cartoon characters that pop in
 * at random moments of a short, "watch along" for a few seconds and get
 * out of the way again. Think: Spidey chasing a robber across the bottom
 * of the screen, losing him, and stopping to watch the video with you.
 *
 * Design rules (so the eggs never fight with the actual content):
 *   - small: each egg is ~27-36% of the frame width, in a bottom corner
 *   - semi-transparent (default 82% opacity)
 *   - short: 3-8 s each, with a quick fade/slide out
 *   - never in the first 1.5 s, never in the last second, never overlapping
 *   - kept above YouTube's bottom title/channel overlay (safeBottom)
 *
 * Assets live in core/easter-eggs/, prepared once by
 * scripts/prepare-easter-eggs.py. Each asset is a plain h264 mp4 holding a
 * stacked frame: the colour image on top and its alpha matte (grey) below.
 * At render time the two halves are split and recombined with `alphamerge`,
 * so no chroma keying happens in the pipeline and any ffmpeg build works.
 *
 * Usage inside a filter_complex:
 *   const plan = planEasterEggs({ clipDuration });
 *   const eggs = buildEasterEggFilters(plan, firstInputIdx, '[v1]');
 *   inputs += eggs.inputArgs;               // -i egg1.mp4 -i egg2.mp4 ...
 *   filters.push(...eggs.filters);          // keyed chains + overlay steps
 *   currentLabel = eggs.outLabel;           // e.g. 'egg_out2'
 */
const path = require('path');
const fs = require('fs');
const { Logger } = require('./logger');

const logger = new Logger('EasterEggs');

const EGG_DIR = path.join(__dirname, 'easter-eggs');
const MANIFEST_PATH = path.join(EGG_DIR, 'manifest.json');

const FRAME_W = 1080;
const FRAME_H = 1920;

const DEFAULTS = {
  enabled: (process.env.EASTER_EGGS_ENABLED || 'true') !== 'false',
  opacity: parseFloat(process.env.EASTER_EGG_OPACITY) || 0.82,
  // global multiplier on each egg's own widthFrac (1.0 = as designed)
  sizeScale: parseFloat(process.env.EASTER_EGG_SIZE) || 1.0,
  // px between the egg's bottom edge and the frame's bottom edge — keeps
  // the character above the YouTube title / channel-name overlay
  safeBottom: parseInt(process.env.EASTER_EGG_SAFE_BOTTOM, 10) || 360,
  // how many eggs per short, by clip length: 1 on short clips, 1 or 2 once
  // the clip is long enough to keep them far apart (never more than 2)
  minClipForTwo: 20,
  maxPerShort: Math.min(2, parseInt(process.env.EASTER_EGG_MAX, 10) || 2),
  firstEggAfter: 1.5,
  endMargin: 1.0,
  // minimum silence between two eggs: at least this many seconds, or a
  // quarter of the clip, whichever is larger
  minGap: 6.0,
  minGapFrac: 0.25,
  slideDuration: 0.35,
  fadeDuration: 0.3,
};

// ─── Deterministic RNG (mulberry32) so a seed reproduces a plan ────
function makeRng(seed) {
  let a = (seed >>> 0) || 1;
  return function rng() {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function loadEggs() {
  try {
    if (!fs.existsSync(MANIFEST_PATH)) return [];
    const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
    return (manifest.eggs || [])
      .map(e => ({ ...e, path: path.join(EGG_DIR, e.file) }))
      .filter(e => e.width > 0 && e.height > 0 && e.duration > 0.5 && fs.existsSync(e.path));
  } catch (e) {
    logger.warn(`Manifest load failed: ${(e.message || '').substring(0, 80)}`);
    return [];
  }
}

function pickCount(clipDuration, opts, rng) {
  if (clipDuration < 8) return 0;
  if (clipDuration < opts.minClipForTwo || opts.maxPerShort < 2) return 1;
  // long enough for two: coin flip, leaning towards two on really long clips
  const pTwo = clipDuration >= 40 ? 0.7 : 0.5;
  return rng() < pTwo ? 2 : 1;
}

/**
 * Decide which eggs appear, where, and when.
 *
 * @param {Object} options
 * @param {number} options.clipDuration  seconds of the final short
 * @param {number} [options.seed]        reproducible plan (default: random)
 * @param {number} [options.count]       force number of eggs
 * @param {string[]} [options.only]      restrict to these egg ids
 * @returns {{ eggs: Array, seed: number }}
 */
function planEasterEggs(options = {}) {
  const opts = { ...DEFAULTS, ...options };
  const clipDuration = Number(options.clipDuration) || 0;
  const seed = Number.isFinite(options.seed) ? options.seed : Math.floor(Math.random() * 2 ** 31);
  const rng = makeRng(seed);
  const empty = { eggs: [], seed };

  if (!opts.enabled) { logger.info('Easter eggs disabled by config'); return empty; }
  let pool = loadEggs();
  if (Array.isArray(opts.only) && opts.only.length) pool = pool.filter(e => opts.only.includes(e.id));
  if (pool.length === 0) { logger.warn('No easter egg assets found — skipping'); return empty; }

  const usable = clipDuration - opts.firstEggAfter - opts.endMargin;
  if (usable < 3) return empty;

  const wanted = Math.min(opts.maxPerShort, Number.isFinite(options.count) ? options.count : pickCount(clipDuration, opts, rng));
  if (wanted <= 0) return empty;
  const gap = Math.max(opts.minGap, clipDuration * opts.minGapFrac);

  // Shuffle the pool (Fisher-Yates), then greedily place eggs in random, well-separated slots.
  const shuffled = [...pool];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  const placed = [];
  for (const egg of shuffled) {
    if (placed.length >= wanted) break;
    const dur = Math.min(egg.duration, usable);
    // try a handful of random start times, keep the first that fits
    let startAt = null;
    for (let attempt = 0; attempt < 20; attempt++) {
      const latest = clipDuration - opts.endMargin - dur;
      if (latest < opts.firstEggAfter) break;
      const candidate = opts.firstEggAfter + rng() * (latest - opts.firstEggAfter);
      const clash = placed.some(p =>
        candidate < p.startAt + p.duration + gap && p.startAt < candidate + dur + gap);
      if (!clash) { startAt = candidate; break; }
    }
    if (startAt === null) continue;

    const side = egg.mirror && rng() < 0.5 ? (egg.nativeSide === 'left' ? 'right' : 'left') : egg.nativeSide;
    const mirror = side !== egg.nativeSide;
    const w = Math.round(FRAME_W * egg.widthFrac * opts.sizeScale / 2) * 2;
    const h = Math.round((w * egg.height / egg.width) / 2) * 2;
    const y = Math.max(0, FRAME_H - h - opts.safeBottom);
    placed.push({
      id: egg.id, path: egg.path, desc: egg.desc,
      srcW: egg.width, srcH: egg.height,
      enter: egg.enter, exit: egg.exit,
      side, mirror, w, h, y,
      startAt: Math.round(startAt * 100) / 100,
      duration: Math.round(dur * 100) / 100,
      opacity: opts.opacity,
    });
  }

  placed.sort((a, b) => a.startAt - b.startAt);
  for (const p of placed) {
    logger.info(`Egg: ${p.id} @ ${p.startAt}s for ${p.duration}s, ${p.side} side${p.mirror ? ' (mirrored)' : ''}, ${p.w}x${p.h}`);
  }
  return { eggs: placed, seed };
}

function fmt(n) { return Number(n).toFixed(3); }

/**
 * Build the ffmpeg pieces for a plan.
 *
 * @param {{eggs: Array}} plan          from planEasterEggs()
 * @param {number} firstInputIdx        index the first egg file will get in the ffmpeg command
 * @param {string} inLabel              filtergraph label to overlay onto, e.g. 'v1'
 * @param {Object} [options]            { slideDuration, fadeDuration }
 * @returns {{ inputArgs: string, filters: string[], outLabel: string, inputCount: number }}
 */
function buildEasterEggFilters(plan, firstInputIdx, inLabel, options = {}) {
  const opts = { ...DEFAULTS, ...options };
  const eggs = (plan && plan.eggs) || [];
  if (eggs.length === 0) return { inputArgs: '', filters: [], outLabel: inLabel, inputCount: 0 };

  const filters = [];
  let inputArgs = '';
  let current = inLabel;

  eggs.forEach((egg, i) => {
    const idx = firstInputIdx + i;
    inputArgs += ` -i "${egg.path}"`;

    const T = egg.startAt, D = egg.duration;
    const fadeIn = egg.enter === 'fade' ? opts.fadeDuration : 0.12;
    const fadeOut = egg.exit === 'fade' ? opts.fadeDuration : 0.15;
    // The asset is a stacked frame: colour on top, alpha matte below
    // (see scripts/prepare-easter-eggs.py). Split, then alphamerge.
    const sw = egg.srcW, sh = egg.srcH;
    filters.push(`[${idx}:v]trim=duration=${fmt(D)},setpts=PTS-STARTPTS,split[eggc${i}][eggm${i}]`);
    filters.push(`[eggc${i}]crop=${sw}:${sh}:0:0[eggc${i}b]`);
    filters.push(`[eggm${i}]crop=${sw}:${sh}:0:${sh},format=gray[eggm${i}b]`);
    const chain = [
      `[eggc${i}b][eggm${i}b]alphamerge`,
      // premultiply before scaling so transparent pixels never bleed into the edges
      `format=gbrap`,
      `premultiply=inplace=1`,
      `scale=${egg.w}:${egg.h}:flags=lanczos`,
      `unpremultiply=inplace=1`,
    ];
    if (egg.mirror) chain.push('hflip');
    chain.push(
      `format=rgba`,
      `colorchannelmixer=aa=${fmt(egg.opacity)}`,
      `fade=t=in:st=0:d=${fmt(fadeIn)}:alpha=1`,
      `fade=t=out:st=${fmt(Math.max(0, D - fadeOut))}:d=${fmt(fadeOut)}:alpha=1`,
      `setpts=PTS+${fmt(T)}/TB`,
    );
    const eggLabel = `egg${i}`;
    filters.push(`${chain.join(',')}[${eggLabel}]`);

    // Position: parked at the screen edge; slide terms push it off-screen
    // during the enter/exit windows. `w` is the overlay width inside ffmpeg.
    const S = opts.slideDuration;
    const rel = `(t-${fmt(T)})`;
    const dir = egg.side === 'left' ? -1 : 1;
    const base = egg.side === 'left' ? '0' : `(W-w)`;
    let xExpr = base;
    if (egg.enter === 'slide') xExpr += `${dir > 0 ? '+' : '-'}w*pow(max(0\\,1-${rel}/${fmt(S)})\\,1.6)`;
    if (egg.exit === 'slide') xExpr += `${dir > 0 ? '+' : '-'}w*pow(max(0\\,(${rel}-${fmt(D - S)})/${fmt(S)})\\,1.6)`;
    const outLabel = `egg_out${i}`;
    filters.push(
      `[${current}][${eggLabel}]overlay=x='${xExpr}':y=${egg.y}:eof_action=pass:format=auto` +
      `:enable='between(t\\,${fmt(T)}\\,${fmt(T + D)})'[${outLabel}]`
    );
    current = outLabel;
  });

  return { inputArgs, filters, outLabel: current, inputCount: eggs.length };
}

module.exports = { planEasterEggs, buildEasterEggFilters, loadEggs, DEFAULTS, EGG_DIR };
