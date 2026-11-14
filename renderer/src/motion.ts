import {Easing, interpolate, spring} from 'remotion';
import {THEME} from './theme';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const M = THEME.motion;
type D = (frames: number) => number;

/** 0→1 over `frames`, eased out, starting at `at`. */
export const ease = (frame: number, at: number, frames: number): number =>
	interpolate(frame - at, [0, frames], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});

/** WORD_IN: opacity 0→1, blur 12→0, y +12→0. */
export const wordIn = (frame: number, at: number, d: D) => {
	const p = ease(frame, at, d(M.wordIn.frames));
	return {opacity: p, filter: `blur(${(1 - p) * M.wordIn.blur}px)`, transform: `translateY(${(1 - p) * M.wordIn.y}px)`};
};

export const springIn = (frame: number, at: number, fps: number, cfg: {damping: number; stiffness: number; mass: number}) =>
	spring({frame: frame - at, fps, config: cfg});

export const cardSpring = (frame: number, at: number, fps: number) => springIn(frame, at, fps, M.cardSpring);
export const stickerSpring = (frame: number, at: number, fps: number) => springIn(frame, at, fps, M.stickerSpring);

/** Idle float: ±amplitude px on a loop, so nothing on screen is ever fully still. */
export const float = (frame: number, d: D, phase = 0, amp: number = M.float.amplitude, period: number = M.float.period) =>
	Math.sin(((frame + phase) / d(period)) * Math.PI * 2) * amp;

/** 1 while shown, fading to 0 over the last few frames before `end` (null: never leaves). */
export const exitFade = (frame: number, end: number | null, d: D): number =>
	end === null ? 1 : interpolate(frame, [end - d(M.exit), end], [1, 0], clamp);

export const interp = (frame: number, input: number[], output: number[]) => interpolate(frame, input, output, clamp);
