import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, ease} from '../motion';
import {THEME, useUnits} from '../theme';
import type {Scene} from '../types.gen';

type Kind = Scene['transition_in'];

/** How long a transition overlaps the previous scene (which stays mounted underneath for that long). */
export const overlapFrames = (kind: Kind, d: (f: number) => number): number =>
	kind === 'crossfade' ? d(THEME.motion.crossfade) : kind === 'cut' ? 0 : d(THEME.motion.wipe);

/** Cut, Crossfade (150 ms), DiagonalWipe (slanted edge sweeps right→left) or SplitSlide (scene slides in from left). */
export const TransitionIn: React.FC<{kind: Kind; children: React.ReactNode}> = ({kind, children}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {width, height, d} = useUnits();
	const n = overlapFrames(kind, d);
	if (kind === 'cut' || frame >= n) return <AbsoluteFill>{children}</AbsoluteFill>;
	if (kind === 'crossfade') return <AbsoluteFill style={{opacity: ease(frame, 0, n)}}>{children}</AbsoluteFill>;
	if (kind === 'split_slide') {
		const s = cardSpring(frame, 0, fps);
		return <AbsoluteFill style={{transform: `translateX(${(s - 1) * 100}%)`}}>{children}</AbsoluteFill>;
	}
	const skew = width * 0.25;
	const x = width + skew - ease(frame, 0, n) * (width + 2 * skew);
	return (
		<AbsoluteFill style={{clipPath: `polygon(${x}px 0, ${width + skew}px 0, ${width + skew}px ${height}px, ${x - skew}px ${height}px)`}}>
			{children}
		</AbsoluteFill>
	);
};
