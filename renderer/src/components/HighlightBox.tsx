import {useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {THEME, useUnits} from '../theme';

/** HIGHLIGHT_BOX: a solid rectangle wipes in left→right behind the word. */
export const HighlightBox: React.FC<{color: string; at: number}> = ({color, at}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const p = ease(frame, at, d(THEME.motion.highlight.frames));
	return (
		<span
			style={{
				position: 'absolute',
				left: 0,
				right: 0,
				top: '0.04em',
				bottom: '-0.02em',
				background: color,
				transform: `scaleX(${p})`,
				transformOrigin: 'left center',
			}}
		/>
	);
};
