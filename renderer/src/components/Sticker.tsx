import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {float, stickerSpring} from '../motion';
import {useScene} from '../SceneContext';
import {THEME, useUnits} from '../theme';
import type {StickerProps} from '../types.gen';

/** STICKER: a Fluent 3D emoji pops in (0 → 1.1 → 1, slight rotate), then floats ±4 px on a 2 s loop. */
export const Sticker: React.FC<{props: StickerProps; at: number}> = ({props, at}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {u, d} = useUnits();
	const {visual} = useScene();
	const s = stickerSpring(frame, at, fps);
	const size = THEME.layout.stickerSize * u * props.size;
	const rot = props.rotate + (1 - s) * -18;
	return (
		<div
			style={{
				position: 'absolute',
				left: visual.x + visual.w * props.x - size / 2,
				top: visual.y + visual.h * props.y - size / 2 + float(frame, d, at * 7),
				width: size,
				height: size,
				transform: `scale(${s}) rotate(${rot}deg)`,
			}}
		>
			<Img src={staticFile(`stickers/${props.name}.png`)} style={{width: '100%', height: '100%', filter: 'drop-shadow(0 12px 18px rgba(0,0,0,.25))'}} />
			{props.cross ? (
				<Img
					src={staticFile('stickers/cross.png')}
					style={{position: 'absolute', width: '55%', right: '-12%', bottom: '-8%', transform: `scale(${stickerSpring(frame, at + d(8), fps)})`}}
				/>
			) : null}
		</div>
	);
};
