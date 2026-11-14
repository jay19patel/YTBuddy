import {useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {SANS, THEME, useUnits} from '../theme';

/** The scene's keyword, huge, outline only, low opacity, drifting slowly behind everything, cropped by the edge. */
export const OutlineBgWord: React.FC<{text: string; color: string; side: 'left' | 'right'}> = ({text, color, side}) => {
	const frame = useCurrentFrame();
	const {u, width, height, d} = useUnits();
	const fs = THEME.type.bgWord * u;
	const p = ease(frame, 0, d(12));
	return (
		<div
			style={{
				position: 'absolute',
				[side]: -width * 0.02,
				bottom: -height * 0.06,
				fontFamily: SANS,
				fontWeight: 900,
				fontSize: fs,
				lineHeight: 0.8,
				letterSpacing: '-0.06em',
				whiteSpace: 'nowrap',
				color: 'transparent',
				WebkitTextStroke: `${Math.max(2, u * 0.0025)}px ${color}`,
				opacity: THEME.type.bgWordOpacity * p,
				transform: `translateX(${(side === 'left' ? -1 : 1) * frame * 0.4}px) scale(${1.04 - 0.04 * p})`,
			}}
		>
			{text}
		</div>
	);
};
