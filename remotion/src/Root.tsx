import React from 'react';
import {Composition} from 'remotion';
import {Short} from './Short';
import {ShortProps} from './types';
import {W, H} from './theme';

const empty: ShortProps = {slug: 'empty', fps: 30, durationInFrames: 30, scenes: [], words: [], sfx: {},
  music: {file: 'music/Voxel_Revolution.mp3', gain: 0, track: ''}, credits: []};

export const Root: React.FC = () => (
  <Composition
    id="Short"
    component={Short as any}
    width={W}
    height={H}
    fps={30}
    durationInFrames={30}
    defaultProps={empty as any}
    calculateMetadata={({props}) => ({durationInFrames: (props as unknown as ShortProps).durationInFrames})}
  />
);
