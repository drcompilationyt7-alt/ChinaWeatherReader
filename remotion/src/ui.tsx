import React from 'react';
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig, Easing} from 'remotion';
import {Video} from '@remotion/media';
import {C, HEAD, UI, W, H, VIS_TOP, VIS_BOT} from './theme';
import {Scene} from './types';

export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** spring that starts at frame `at` (scene-relative) */
export const useSpringAt = (at: number, cfg: {damping?: number; mass?: number; stiffness?: number} = {}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: frame - at, fps, config: {damping: 14, mass: 0.7, stiffness: 140, ...cfg}});
};

/** Photo with a slow push-in and drift. `card` shows the whole landscape photo on a blurred backdrop. */
export const Photo: React.FC<{sc: Scene; dim?: number; backdropOnly?: boolean; cardTop?: number}> = ({sc, dim = 0, backdropOnly, cardTop}) => {
  const frame = useCurrentFrame();
  const d = sc.durationInFrames + 20;
  const isVideo = !!sc.videoFile;
  const src = staticFile((sc.videoFile ?? sc.imageFile)!);
  const aspect = sc.imageAspect ?? 1.5;
  // stock video arrives pre-cropped to 1080x1920 (core/china/stock.py), so it always fills the frame
  const fit = isVideo ? 'cover' : sc.fit && sc.fit !== 'auto' ? sc.fit : aspect > 0.9 ? 'card' : 'cover';
  const [fx, fy] = sc.focus ?? [0.5, 0.5];
  const z = interpolate(frame, [0, d], [1.06, 1.18], {...clamp, easing: Easing.inOut(Easing.sin)});
  const px = interpolate(frame, [0, d], [-18, 18], clamp);
  const cover = isVideo ? (
    // real footage already moves: only a gentle push-in on top
    <Video src={src} muted style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover',
      transform: `scale(${interpolate(frame, [0, d], [1.0, 1.06], clamp)})`}} />
  ) : (
    <Img src={src} style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover',
      objectPosition: `${fx * 100}% ${fy * 100}%`, transform: `scale(${z}) translateX(${px}px)`}} />
  );
  if (fit === 'cover' || backdropOnly) {
    // backdropOnly: data scenes draw numbers / cards on top, so the photo stays sharp under a dark wash
    return (
      <AbsoluteFill style={{backgroundColor: C.ink}}>
        {cover}
        <AbsoluteFill style={{background: backdropOnly
          ? 'linear-gradient(180deg, rgba(8,3,5,.85) 0%, rgba(8,3,5,.55) 35%, rgba(8,3,5,.6) 60%, rgba(8,3,5,.95) 82%)'
          : 'linear-gradient(180deg, rgba(8,3,5,.75) 0%, rgba(8,3,5,0) 22%, rgba(8,3,5,0) 48%, rgba(8,3,5,.92) 78%)'}} />
        {dim > 0 && <AbsoluteFill style={{background: `rgba(10,3,5,${dim})`}} />}
        {isVideo && sc.videoMask !== false && (
          // news footage carries burned-in subtitles in its lower band: covered by a solid fade below our captions
          // (that area is under YouTube's own title / buttons on Shorts anyway)
          <AbsoluteFill style={{background: 'linear-gradient(180deg, transparent 0%, transparent 66%, rgba(10,4,6,.97) 74%, rgba(10,4,6,1) 100%)'}} />
        )}
      </AbsoluteFill>
    );
  }
  const cw = W - 80;
  const ch = Math.min(860, cw / aspect);
  const top = cardTop ?? (VIS_TOP + VIS_BOT) / 2 - ch / 2;
  const ci = interpolate(frame, [0, d], [1.04, 1.14], {...clamp, easing: Easing.inOut(Easing.sin)});
  return (
    <AbsoluteFill style={{backgroundColor: C.ink}}>
      <Img src={src} style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover',
        filter: 'blur(38px) brightness(0.45) saturate(1.2)', transform: `scale(${1.25 * z})`}} />
      {!backdropOnly && (
        <div style={{position: 'absolute', left: 40, top, width: cw, height: ch, borderRadius: 34, overflow: 'hidden',
          boxShadow: '0 30px 80px rgba(0,0,0,.6)', border: '2px solid rgba(255,255,255,.12)'}}>
          <Img src={src} style={{width: '100%', height: '100%', objectFit: 'cover',
            objectPosition: `${fx * 100}% ${fy * 100}%`, transform: `scale(${ci}) translateX(${px * 0.6}px)`}} />
        </div>
      )}
      <AbsoluteFill style={{background: 'linear-gradient(180deg, rgba(8,3,5,.55) 0%, rgba(8,3,5,0) 18%, rgba(8,3,5,0) 60%, rgba(8,3,5,.85) 85%)'}} />
      {dim > 0 && <AbsoluteFill style={{background: `rgba(10,3,5,${dim})`}} />}
    </AbsoluteFill>
  );
};

/** Dark red studio background with a slow light sweep and a faint grid. */
export const InkBg: React.FC<{seed?: number}> = ({seed = 0}) => {
  const frame = useCurrentFrame();
  const gx = 50 + 30 * Math.sin((frame + seed * 40) / 90);
  return (
    <AbsoluteFill style={{background: `radial-gradient(120% 70% at ${gx}% 85%, #5A0A14 0%, #22070C 55%, #0E0609 100%)`}}>
      <AbsoluteFill style={{opacity: 0.18, backgroundImage:
        'linear-gradient(rgba(247,184,52,.25) 1px, transparent 1px), linear-gradient(90deg, rgba(247,184,52,.25) 1px, transparent 1px)',
        backgroundSize: '90px 90px', backgroundPosition: `0 ${frame * 0.6}px`,
        maskImage: 'radial-gradient(70% 60% at 50% 45%, black, transparent)'}} />
    </AbsoluteFill>
  );
};

/** Section label: a gold bar + caps text, sliding in. */
export const Kicker: React.FC<{text?: string; at?: number; top?: number}> = ({text, at = 4, top = 250}) => {
  const s = useSpringAt(at);
  if (!text) return null;
  return (
    <div style={{position: 'absolute', top, left: 0, width: W, display: 'flex', justifyContent: 'center'}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 18, padding: '14px 30px', borderRadius: 999,
        background: 'rgba(14,8,10,.72)', border: `2px solid ${C.gold}`, backdropFilter: 'blur(8px)',
        transform: `translateY(${(1 - s) * -40}px)`, opacity: s}}>
        <div style={{width: 14, height: 14, borderRadius: 3, background: C.red, transform: 'rotate(45deg)'}} />
        <span style={{fontFamily: UI, fontWeight: 900, fontSize: 38, letterSpacing: 4, color: C.gold}}>{text.toUpperCase()}</span>
      </div>
    </div>
  );
};

/** Lower-third label on photos. */
export const Label: React.FC<{text?: string; at?: number; top: number}> = ({text, at = 14, top}) => {
  const s = useSpringAt(at);
  if (!text) return null;
  return (
    <div style={{position: 'absolute', top, left: 60, display: 'flex', overflow: 'hidden', borderRadius: 10,
      transform: `translateX(${(1 - s) * -80}px)`, opacity: s}}>
      <div style={{width: 12, background: C.gold}} />
      <div style={{background: C.red, padding: '14px 26px', fontFamily: UI, fontWeight: 800, fontSize: 40, color: C.white}}>{text}</div>
    </div>
  );
};

export const Source: React.FC<{text?: string}> = ({text}) =>
  text ? (
    <div style={{position: 'absolute', top: VIS_BOT + 14, width: W, textAlign: 'center', fontFamily: UI, fontWeight: 600,
      fontSize: 28, color: 'rgba(240,230,220,.75)', letterSpacing: 0.5}}>{text}</div>
  ) : null;

/** Film grain + vignette over everything, for a filmed look. */
export const Finish: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <AbsoluteFill style={{background: 'radial-gradient(85% 70% at 50% 45%, transparent 55%, rgba(0,0,0,.55) 100%)'}} />
      <svg width={W} height={H} style={{position: 'absolute', opacity: 0.07, mixBlendMode: 'overlay'}}>
        <filter id="g"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed={frame % 7} /></filter>
        <rect width="100%" height="100%" filter="url(#g)" />
      </svg>
    </AbsoluteFill>
  );
};

export const fmt = (v: number, dec = 0) =>
  v.toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec});

export const Check: React.FC<{size: number; color: string; cross?: boolean; progress?: number}> = ({size, color, cross, progress = 1}) => (
  <svg width={size} height={size} viewBox="0 0 64 64">
    {cross ? (
      <g stroke={color} strokeWidth={9} strokeLinecap="round">
        <path d="M14 14 L50 50" strokeDasharray={52} strokeDashoffset={52 * (1 - progress)} />
        <path d="M50 14 L14 50" strokeDasharray={52} strokeDashoffset={52 * (1 - progress)} />
      </g>
    ) : (
      <path d="M10 34 L26 50 L54 16" fill="none" stroke={color} strokeWidth={9} strokeLinecap="round" strokeLinejoin="round"
        strokeDasharray={70} strokeDashoffset={70 * (1 - progress)} />
    )}
  </svg>
);

export {HEAD, UI, C};
