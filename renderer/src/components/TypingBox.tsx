import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, stickerSpring} from '../motion';
import {useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';
import type {TypingProps} from '../types.gen';
import {KeyBadge} from './KeyBadge';
import {TypingText} from './TypingText';

/** A standalone input (a comment box, a feedback field) that something is typed into. */
export const TypingBox: React.FC<{props: TypingProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const {visual} = useScene();
	const fs = useVisualFont(1.5);
	const s = cardSpring(frame, at, fps);
	const typeAt = at + d(6);
	const doneAt = typeAt + (props.paste ? 0 : Math.ceil((props.text.length / THEME.motion.typingCps) * fps));
	return (
		<div style={{position: 'absolute', left: visual.x, top: visual.y, width: visual.w, height: visual.h * 0.4, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
			<div
				style={{
					position: 'relative',
					width: '84%',
					fontFamily: SANS,
					fontSize: fs,
					fontWeight: 600,
					background: '#fff',
					color: THEME.palette.ink,
					borderRadius: '0.6em',
					padding: '0.6em 0.9em',
					boxShadow: THEME.shadow.card,
					display: 'flex',
					alignItems: 'center',
					gap: '0.5em',
					opacity: Math.min(1, s * 2),
					transform: `translateY(${(1 - s) * 30}px)`,
				}}
			>
				<span style={{flex: 1}}>
					{frame < typeAt && props.placeholder ? <span style={{color: '#AAA'}}>{props.placeholder}</span> : <TypingText text={props.text} at={typeAt} paste={props.paste} />}
				</span>
				{props.heart ? (
					<Img src={staticFile('stickers/heart.png')} style={{width: '1.3em', transform: `scale(${stickerSpring(frame, doneAt + d(4), fps)})`}} />
				) : null}
				{props.paste ? <KeyBadge label="⌘V" at={typeAt} size="0.9em" /> : null}
			</div>
		</div>
	);
};
