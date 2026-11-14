import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, stickerSpring} from '../motion';
import {useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';
import type {DmCardProps} from '../types.gen';
import {EmojiText} from './EmojiText';

/** A direct-message card from you: a message bubble and file chips (e.g. three .txt prompts). */
export const DmCard: React.FC<{props: DmCardProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const {brand, visual} = useScene();
	const fs = useVisualFont(1.3);
	const s = cardSpring(frame, at, fps);
	return (
		<div style={{position: 'absolute', left: visual.x, top: visual.y + visual.h * 0.42, width: visual.w, display: 'flex', justifyContent: 'center'}}>
			<div
				style={{
					width: '84%',
					fontFamily: SANS,
					fontSize: fs,
					background: '#fff',
					color: THEME.palette.ink,
					borderRadius: '1em',
					padding: '0.9em 1.1em',
					boxShadow: THEME.shadow.window,
					opacity: Math.min(1, s * 2),
					transform: `translateY(${(1 - s) * 40}px)`,
				}}
			>
				<div style={{display: 'flex', alignItems: 'center', gap: '0.5em', fontWeight: 700, fontSize: '0.8em', color: THEME.palette.gray}}>
					{brand.avatar ? <Img src={staticFile(brand.avatar)} style={{width: '1.8em', height: '1.8em', borderRadius: 99}} /> : null}
					{brand.handle}
				</div>
				<div style={{marginTop: '0.6em', display: 'inline-block', background: THEME.palette.blue, color: '#fff', padding: '0.45em 0.8em', borderRadius: '1em', fontWeight: 600}}>
					<EmojiText text={props.text} />
				</div>
				<div style={{display: 'flex', gap: '0.5em', marginTop: '0.6em', flexWrap: 'wrap'}}>
					{props.chips.map((c, i) => (
						<div
							key={i}
							style={{
								fontSize: '0.75em',
								fontWeight: 700,
								padding: '0.35em 0.7em',
								borderRadius: '0.5em',
								background: '#F2F0EA',
								border: '1px solid #E0DDD3',
								transform: `scale(${stickerSpring(frame, at + d(8) + i * d(THEME.motion.stagger), fps)})`,
							}}
						>
							{c}
						</div>
					))}
				</div>
			</div>
		</div>
	);
};
