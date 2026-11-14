import {useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring} from '../motion';
import {useItemAt, useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME} from '../theme';
import type {StoryboardProps} from '../types.gen';

const TINTS = [THEME.palette.orange, THEME.palette.blue, THEME.palette.crimson, THEME.palette.yellow];

/** A row of storyboard cards (beat 01–04) that spring in one by one. */
export const StoryboardRow: React.FC<{props: StoryboardProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {visual} = useScene();
	const fs = Math.min(useVisualFont(0.95), visual.w / (props.items.length * 10.4)); // one row, always
	const itemAt = useItemAt(at);
	return (
		<div
			style={{
				position: 'absolute',
				left: visual.x,
				top: visual.y + visual.h * (visual.h > visual.w ? 0.4 : 0.55),
				width: visual.w,
				display: 'flex',
				flexWrap: 'wrap',
				justifyContent: 'center',
				gap: '0.8em',
				fontFamily: SANS,
				fontSize: fs,
			}}
		>
			{props.items.map((it, i) => {
				const t = itemAt(it, i);
				const s = cardSpring(frame, t, fps);
				return (
					<div
						key={i}
						style={{
							width: '9em',
							background: '#fff',
							borderRadius: '0.6em',
							padding: '0.45em',
							boxShadow: THEME.shadow.card,
							color: THEME.palette.ink,
							opacity: frame < t ? 0 : 1,
							transform: `translateY(${(1 - s) * 50}px) rotate(${(i % 2 ? 1 : -1) * (1 - s) * 8}deg)`,
						}}
					>
						<div style={{height: '5.2em', borderRadius: '0.4em', background: TINTS[i % TINTS.length], opacity: 0.9}} />
						<div style={{fontWeight: 800, marginTop: '0.4em'}}>{it.label}</div>
						<div style={{height: '0.4em', width: '80%', background: '#E3E3E3', borderRadius: 99, marginTop: '0.3em'}} />
					</div>
				);
			})}
		</div>
	);
};
