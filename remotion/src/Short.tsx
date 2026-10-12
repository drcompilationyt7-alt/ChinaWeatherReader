import React, {useMemo} from 'react';
import {AbsoluteFill, Sequence, Solid, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio} from '@remotion/media';
import {lightLeak} from '@remotion/effects/light-leak';
import {C, CAP, UI, W, CAP_Y, OVERLAP} from './theme';
import {ShortProps, Scene, Word} from './types';
import {SCENES, rel, itemCues} from './scenes';
import {Finish} from './ui';

type Hit = {role: string; at: number; vol: number};

/** Every sound effect, at the frame where its animation lands (the same cue maths the scenes use). */
export const sfxPlan = (scenes: Scene[]): Hit[] => {
  const out: Hit[] = [];
  scenes.forEach((sc, i) => {
    const base = sc.from - OVERLAP;
    const abs = (r: number) => base + r;
    if (i > 0) out.push({role: i % 2 ? 'whoosh' : 'whoosh_soft', at: base + 3, vol: 0.42});
    switch (sc.type) {
      case 'hook':
        out.push({role: 'hit', at: abs(6), vol: 0.55});
        break;
      case 'counter': {
        const a = rel(sc, sc.cueFrame, OVERLAP + 4);
        const b = Math.max(a + 18, rel(sc, sc.cueEndFrame, a + Math.min(40, sc.durationInFrames * 0.45)));
        out.push({role: 'pop_whoosh', at: abs(a), vol: 0.35}, {role: 'confirm', at: abs(b), vol: 0.4});
        break;
      }
      case 'bars':
        itemCues(sc, sc.items, OVERLAP + 4).forEach((c) => out.push({role: 'click', at: abs(c + 2), vol: 0.55}));
        break;
      case 'vs': {
        const ca = rel(sc, sc.before?.cueFrame, OVERLAP + 2);
        const cb = rel(sc, sc.after?.cueFrame, ca + 26);
        out.push({role: 'pop', at: abs(ca + 3), vol: 0.55}, {role: 'hit', at: abs(cb + 4), vol: 0.4});
        break;
      }
      case 'list':
        itemCues(sc, sc.items).forEach((c) => out.push({role: 'select', at: abs(c + 3), vol: 0.45}));
        break;
      case 'timeline':
        itemCues(sc, sc.events).forEach((c) => out.push({role: 'click', at: abs(c + 2), vol: 0.55}));
        break;
      case 'photo':
        if (sc.label) out.push({role: 'pop', at: abs(rel(sc, sc.cueFrame, 16) + 3), vol: 0.35});
        break;
      case 'quote':
        out.push({role: 'whoosh_air', at: abs(rel(sc, sc.cueFrame, OVERLAP + 6)), vol: 0.3});
        break;
      case 'stack': {
        const a = OVERLAP + 4;
        const b = sc.durationInFrames - 6;
        for (let k = 3; k <= (sc.floors ?? 57); k += 3) {
          const t = k / (sc.floors ?? 57);
          // inverse of the in-out quad easing used by the tower
          const u = t < 0.5 ? Math.sqrt(t / 2) : 1 - Math.sqrt((1 - t) / 2);
          out.push({role: 'tick', at: abs(Math.round(a + (b - a) * u)), vol: 0.18});
        }
        out.push({role: 'thud', at: abs(b), vol: 0.5});
        break;
      }
      case 'outro': {
        const pressAt = sc.durationInFrames - 34;
        out.push({role: 'pop', at: abs(6), vol: 0.45}, {role: 'click', at: abs(pressAt), vol: 0.7}, {role: 'confirm', at: abs(pressAt + 3), vol: 0.35});
        break;
      }
    }
  });
  return out;
};

const pagesOf = (words: Word[]) => {
  const pages: Word[][] = [];
  let cur: Word[] = [];
  for (const w of words) {
    const joined = [...cur, w].map((x) => x.text).join(' ');
    const prevEnds = cur.length && /[.,?!:;]$/.test(cur[cur.length - 1].text);
    if (cur.length && (cur.length >= 3 || joined.length > 18 || prevEnds)) {
      pages.push(cur);
      cur = [];
    }
    cur.push(w);
  }
  if (cur.length) pages.push(cur);
  return pages;
};

const Captions: React.FC<{words: Word[]}> = ({words}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pages = useMemo(() => pagesOf(words), [words]);
  const page = pages.find((p, i) => frame >= p[0].from - 2 && frame < (pages[i + 1]?.[0].from ?? p[p.length - 1].to + 8) - 2 && frame <= p[p.length - 1].to + 10);
  if (!page) return null;
  const pop = spring({frame: frame - page[0].from + 2, fps, config: {damping: 13, stiffness: 220, mass: 0.5}});
  return (
    <div style={{position: 'absolute', top: CAP_Y - 70, left: 60, width: W - 120, display: 'flex', flexWrap: 'wrap', justifyContent: 'center',
      columnGap: 40, rowGap: 0, transform: `scale(${interpolate(pop, [0, 1], [0.86, 1])})`}}>
      {page.map((w, i) => {
        const active = frame >= w.from - 1 && frame <= w.to + 1;
        const s = spring({frame: frame - w.from, fps, config: {damping: 12, stiffness: 260, mass: 0.4}});
        return (
          <span key={i} style={{fontFamily: CAP, fontWeight: 900, fontSize: 84, lineHeight: 1.15, textTransform: 'uppercase',
            color: active ? C.gold : C.white, WebkitTextStroke: '14px #000', paintOrder: 'stroke fill',
            textShadow: '0 8px 24px rgba(0,0,0,.55)', display: 'inline-block',
            transform: `scale(${active ? 1 + 0.08 * s : 1})`}}>{w.text.replace(/[.,]$/, '')}</span>
        );
      })}
    </div>
  );
};

/** music level: ducked under the narration with a smooth attack / release */
const useDuck = (words: Word[], total: number) =>
  useMemo(() => {
    const target = new Float32Array(total).fill(1);
    for (const w of words) for (let f = Math.max(0, w.from - 3); f < Math.min(total, w.to + 6); f++) target[f] = 0.42;
    const out = new Float32Array(total);
    let v = 1;
    for (let f = 0; f < total; f++) {
      const k = target[f] < v ? 0.35 : 0.08; // fast attack, slow release
      v += (target[f] - v) * k;
      out[f] = v;
    }
    return out;
  }, [words, total]);

export const Short: React.FC<ShortProps> = (props) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const duck = useDuck(props.words, durationInFrames);
  const plan = useMemo(() => sfxPlan(props.scenes), [props.scenes]);
  const g = Math.min(1, props.music.gain);
  return (
    <AbsoluteFill style={{backgroundColor: C.ink}}>
      {props.scenes.map((sc, i) => {
        const Comp = SCENES[sc.type];
        const start = Math.max(0, sc.from - OVERLAP);
        const next = props.scenes[i + 1];
        const end = next ? next.from - OVERLAP + OVERLAP + 2 : durationInFrames;
        return (
          <Sequence key={i} from={start} durationInFrames={end - start} name={`${i} ${sc.type}`} premountFor={fps}>
            <SceneIn first={i === 0}><Comp sc={sc} /></SceneIn>
          </Sequence>
        );
      })}
      {props.scenes.map((sc, i) => (i > 0 && i % 2 === 0 && sc.type !== 'outro' ? (
        <Sequence key={`l${i}`} from={sc.from - OVERLAP - 5} durationInFrames={20} name={`light leak ${i}`} premountFor={fps}>
          <LightLeak seed={i} />
        </Sequence>
      ) : null))}
      <Captions words={props.words} />
      <Finish />
      <div style={{position: 'absolute', top: 0, left: 0, height: 10, width: (W * frame) / durationInFrames, background: C.gold}} />
      <div style={{position: 'absolute', top: 44, left: 40, fontFamily: UI, fontWeight: 800, fontSize: 32, color: 'rgba(255,255,255,.55)'}}>@mainlandunpacked</div>

      {props.scenes.map((sc, i) => (
        <Sequence key={`v${i}`} from={sc.voiceFrom} name={`vo ${i}`} premountFor={fps}>
          <Audio src={staticFile(sc.voice)} volume={1} />
        </Sequence>
      ))}
      <Audio src={staticFile(props.music.file)} loop
        volume={(f) => g * (duck[Math.max(0, Math.min(durationInFrames - 1, Math.floor(f)))] ?? 1) * interpolate(f, [0, 10, durationInFrames - 40, durationInFrames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} />
      {plan.map((h, i) => {
        const s = props.sfx[h.role];
        if (!s) return null;
        const pre = Math.min(s.peak, 7);          // at most ~0.23 s of build-up before the hit: long risers would mask speech
        const from = h.at - pre;
        const skip = s.peak - pre + (from < 0 ? -from : 0);
        return (
          <Sequence key={`s${i}`} from={Math.max(0, from)} name={`sfx ${h.role}`} premountFor={fps}>
            <Audio src={staticFile(s.file)} trimBefore={skip}
              volume={(f) => h.vol * Math.min(1, (f + 1) / Math.max(1, pre))} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

/** warm film light leak over a cut (seeded so every cut looks different), kept subtle */
const LightLeak: React.FC<{seed: number}> = ({seed}) => {
  const frame = useCurrentFrame();
  const {durationInFrames, width, height} = useVideoConfig();
  return (
    <AbsoluteFill style={{opacity: 0.28, mixBlendMode: 'screen'}}>   {/* 0.55 washed whole frames yellow */}
      <Solid width={width} height={height} effects={[lightLeak({seed, hueShift: 345,
        progress: interpolate(frame, [0, durationInFrames - 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})})]} />
    </AbsoluteFill>
  );
};

/** each scene punches in: scale + blur settle over its first frames */
const SceneIn: React.FC<{first: boolean; children: React.ReactNode}> = ({first, children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (first) return <>{children}</>;
  const s = spring({frame, fps, config: {damping: 20, stiffness: 170, mass: 0.6}});
  return (
    <AbsoluteFill style={{transform: `scale(${interpolate(s, [0, 1], [1.12, 1])})`, filter: `blur(${(1 - s) * 14}px)`, opacity: Math.min(1, s * 2.2)}}>
      {children}
    </AbsoluteFill>
  );
};
