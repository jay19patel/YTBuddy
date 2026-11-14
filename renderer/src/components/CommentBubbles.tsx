import {useCurrentFrame, useVideoConfig} from 'remotion';
import {float, stickerSpring} from '../motion';
import {useItemAt, useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';
import type {CommentBubblesProps} from '../types.gen';
import {EmojiText} from './EmojiText';

const DOTS = [THEME.palette.orange, THEME.palette.blue, THEME.palette.green, THEME.palette.crimson];
const SCATTER = [
	{x: -6, r: -2},
	{x: 8, r: 1.5},
	{x: -2, r: -1},
	{x: 5, r: 2},
];

/** COMMENT_BUBBLES: white chat bubbles (avatar dot, username, text) pop in stacked and slightly scattered. */
export const CommentBubbles: React.FC<{props: CommentBubblesProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const {visual} = useScene();
	const fs = useVisualFont(1.25);
	const itemAt = useItemAt(at);
	return (
		<div
			style={{
				position: 'absolute',
				left: visual.x,
				top: visual.y + visual.h * 0.2, // leaves the top of the area for an avatar badge
				width: visual.w,
				height: visual.h * 0.8,
				display: 'flex',
				flexDirection: 'column',
				justifyContent: 'center',
				alignItems: 'center',
				gap: fs * 0.8,
				fontFamily: SANS,
				fontSize: fs,
			}}
		>
			{props.items.map((it, i) => {
				const t = itemAt(it, i);
				const s = stickerSpring(frame, t, fps);
				const sc = SCATTER[i % SCATTER.length];
				return (
					<div
						key={i}
						style={{
							display: 'flex',
							gap: '0.6em',
							alignItems: 'flex-start',
							maxWidth: '92%',
							background: '#fff',
							color: THEME.palette.ink,
							padding: '0.7em 1em',
							borderRadius: '1.1em',
							boxShadow: THEME.shadow.card,
							opacity: frame < t ? 0 : 1,
							transform: `translate(${sc.x}%, ${float(frame, d, i * 20, 3)}px) rotate(${sc.r}deg) scale(${0.6 + 0.4 * s})`,
							transformOrigin: 'left bottom',
						}}
					>
						<div style={{flex: 'none', width: '1.6em', height: '1.6em', borderRadius: 99, background: DOTS[i % DOTS.length]}} />
						<div>
							<div style={{fontWeight: 700, fontSize: '0.75em', color: THEME.palette.gray}}>{it.label}</div>
							<div style={{fontWeight: 600, letterSpacing: '-0.02em'}}>
								<EmojiText text={it.sub ?? ''} />
							</div>
						</div>
					</div>
				);
			})}
		</div>
	);
};
