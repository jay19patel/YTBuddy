import {useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {THEME, useUnits} from '../theme';

/** STRIKE: a line draws across the word left→right, a beat after the word lands. */
export const StrikeThrough: React.FC<{at: number; color: string}> = ({at, color}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const p = ease(frame, at + d(THEME.motion.strike.delay), d(THEME.motion.strike.frames));
	return (
		<span
			style={{
				position: 'absolute',
				left: '-0.04em',
				width: `calc(${p * 100}% + ${p * 0.08}em)`,
				top: '54%',
				height: '0.09em',
				background: color,
				borderRadius: '0.05em',
			}}
		/>
	);
};
