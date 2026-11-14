import {useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useScene} from '../SceneContext';
import {colorOf, SERIF, THEME, useUnits} from '../theme';
import type {Word} from '../types.gen';

const M = THEME.motion.serif;

/** SERIF_REVEAL: one dramatic word in bold italic serif (gold "magic", dark red "secret?"), scaling up out of a blur. */
export const SerifReveal: React.FC<{word: Word; at: number}> = ({word, at}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const {palette} = useScene();
	const p = ease(frame, at, d(M.frames));
	return (
		<span
			style={{
				display: 'inline-block',
				fontFamily: SERIF,
				fontStyle: 'italic',
				fontWeight: 900,
				fontSize: `${THEME.type.serifScale}em`,
				letterSpacing: '-0.02em',
				color: colorOf(word.color ?? 'gold', palette),
				opacity: p,
				filter: `blur(${(1 - p) * M.blur}px)`,
				transform: `scale(${M.scaleFrom + (1 - M.scaleFrom) * p})`,
				transformOrigin: 'left 70%',
			}}
		>
			{word.text}
		</span>
	);
};
