import {useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring} from '../motion';
import {useScene} from '../SceneContext';
import {colorOf, SANS, THEME, useUnits} from '../theme';
import type {TagPillProps} from '../types.gen';

/** "prompt 01" / "step 01" label: a rounded pill that springs in. */
export const TagPill: React.FC<{props: TagPillProps; at: number; k: number}> = ({props, at, k}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {tu: u} = useUnits();
	const {palette} = useScene();
	const s = cardSpring(frame, at, fps);
	return (
		<div
			style={{
				display: 'inline-block',
				alignSelf: 'flex-start',
				background: colorOf(props.color, palette),
				color: colorOf(props.text_color, palette),
				fontFamily: SANS,
				fontWeight: 700,
				fontSize: THEME.type.pill * u * k,
				letterSpacing: '-0.01em',
				padding: '0.3em 0.85em',
				borderRadius: 999,
				opacity: frame < at ? 0 : Math.min(1, s * 2),
				transform: `scale(${0.6 + 0.4 * s})`,
				transformOrigin: 'left center',
			}}
		>
			{props.label}
		</div>
	);
};
