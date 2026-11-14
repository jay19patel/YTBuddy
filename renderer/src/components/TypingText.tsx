import {useCurrentFrame, useVideoConfig} from 'remotion';
import {THEME} from '../theme';

/** TYPING: text types in at ~25 chars/s with a blinking caret; `paste` drops it all in at once. */
export const TypingText: React.FC<{text: string; at: number; paste?: boolean; caret?: boolean; color?: string}> = ({
	text,
	at,
	paste = false,
	caret = true,
	color,
}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const t = frame - at;
	const n = t < 0 ? 0 : paste ? text.length : Math.min(text.length, Math.floor((t / fps) * THEME.motion.typingCps));
	const typing = n > 0 && n < text.length;
	const blinkOn = Math.floor(frame / (fps / 2)) % 2 === 0;
	return (
		<span style={{color, whiteSpace: 'pre-wrap'}}>
			{text.slice(0, n)}
			{caret ? (
				<span
					style={{
						display: 'inline-block',
						width: '0.08em',
						height: '1.05em',
						marginLeft: '0.04em',
						verticalAlign: '-0.15em',
						background: 'currentColor',
						opacity: typing || blinkOn ? 1 : 0,
					}}
				/>
			) : null}
		</span>
	);
};
