import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {float, stickerSpring} from '../motion';
import {useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';

/** BUTTON_STATE "approve" / "reject": a big green or red button with a 👍 / 👎 sticker; pressed at `tap`. */
export const ApproveReject: React.FC<{variant: 'approve' | 'reject'; at: number; tap: number | null}> = ({variant, at, tap}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const {visual} = useScene();
	const fs = useVisualFont(2);
	const s = stickerSpring(frame, at, fps);
	const press = tap !== null && frame >= tap && frame < tap + d(4) ? 0.95 : 1;
	const approve = variant === 'approve';
	return (
		<div
			style={{
				position: 'absolute',
				left: visual.x,
				top: visual.y + (approve ? 0 : visual.h * 0.5),
				width: visual.w,
				height: visual.h * 0.5,
				display: 'flex',
				alignItems: 'center',
				justifyContent: 'center',
				gap: '0.5em',
				fontSize: fs,
			}}
		>
			<div
				style={{
					fontFamily: SANS,
					fontWeight: 800,
					letterSpacing: '-0.03em',
					color: '#fff',
					background: approve ? THEME.palette.green : THEME.palette.crimson,
					padding: '0.45em 1.1em',
					borderRadius: '0.5em',
					boxShadow: THEME.shadow.card,
					transform: `scale(${s * press})`,
				}}
			>
				{approve ? 'approve ✓' : 'reject ✕'}
			</div>
			<Img
				src={staticFile(`stickers/${approve ? 'thumbs_up' : 'thumbs_down'}.png`)}
				style={{width: '1.8em', transform: `scale(${stickerSpring(frame, at + d(5), fps)}) translateY(${float(frame, d)}px)`}}
			/>
		</div>
	);
};
