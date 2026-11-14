import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {float, stickerSpring} from '../motion';
import {useScene} from '../SceneContext';
import {THEME, useUnits} from '../theme';

/** Circular avatar with a white ring; a `size` multiple of the theme's sticker size. */
export const AvatarBadge: React.FC<{x: number; y: number; at: number; size?: number}> = ({x, y, at, size = 1.1}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {u, d} = useUnits();
	const {brand, visual} = useScene();
	if (!brand.avatar) return null;
	const px = THEME.layout.stickerSize * u * size;
	const s = stickerSpring(frame, at, fps);
	return (
		<Img
			src={staticFile(brand.avatar)}
			style={{
				position: 'absolute',
				left: visual.x + visual.w * x - px / 2,
				top: visual.y + visual.h * y - px / 2 + float(frame, d, 15),
				width: px,
				height: px,
				borderRadius: 999,
				objectFit: 'cover',
				border: `${px * 0.05}px solid #fff`,
				boxShadow: THEME.shadow.card,
				transform: `scale(${s})`,
			}}
		/>
	);
};
