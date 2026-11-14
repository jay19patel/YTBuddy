import {useCurrentFrame, useVideoConfig} from 'remotion';
import {stickerSpring} from '../motion';
import {useScene} from '../SceneContext';
import {colorOf, THEME, useUnits} from '../theme';
import type {SwatchesProps} from '../types.gen';

/** A row of palette colours: dots (brand kit) or dashes (outro). */
export const Swatches: React.FC<{props: SwatchesProps; at: number; k: number}> = ({props, at, k}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {u, d} = useUnits();
	const {palette, align} = useScene();
	const size = u * 0.045 * k;
	return (
		<div style={{display: 'flex', gap: size * 0.4, justifyContent: align === 'center' ? 'center' : 'flex-start'}}>
			{props.colors.map((c, i) => {
				const s = stickerSpring(frame, at + i * d(THEME.motion.stagger / 2), fps);
				return (
					<div
						key={i}
						style={{
							width: props.shape === 'dots' ? size : size * 1.6,
							height: props.shape === 'dots' ? size : size * 0.22,
							borderRadius: 999,
							background: colorOf(c, palette),
							border: c === 'black' ? '2px solid rgba(255,255,255,.35)' : undefined,
							transform: `scale(${s})`,
						}}
					/>
				);
			})}
		</div>
	);
};
