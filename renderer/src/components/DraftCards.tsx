import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring, ease, stickerSpring} from '../motion';
import {useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';

const TINTS = [THEME.palette.charcoal, THEME.palette.blue, THEME.palette.orange, THEME.palette.crimson, THEME.palette.black];

/** Phone-ish draft cards appear one by one with a "feedback → next draft" arrow; the last gets "Approved ✓". */
export const DraftCards: React.FC<{count: number; at: number; approved: number | null}> = ({count, at, approved}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const {visual, ink} = useScene();
	const fs = useVisualFont(1.1);
	const gap = d(14);
	return (
		<div
			style={{
				position: 'absolute',
				left: visual.x,
				top: visual.y,
				width: visual.w,
				height: visual.h,
				display: 'flex',
				alignItems: 'center',
				justifyContent: 'center',
				gap: '2.6em',
				fontFamily: SANS,
				fontSize: fs,
			}}
		>
			{Array.from({length: count}, (_, i) => {
				const t = at + i * gap;
				const s = cardSpring(frame, t, fps);
				const last = i === count - 1;
				const ok = last && approved !== null && frame >= approved;
				return (
					<div key={i} style={{position: 'relative', opacity: frame < t ? 0 : 1, transform: `translateY(${(1 - s) * 60}px) scale(${ok ? 1.06 : 1})`}}>
						<div
							style={{
								width: '7em',
								height: '12.4em',
								borderRadius: '1em',
								background: TINTS[i % TINTS.length],
								border: '0.3em solid #111',
								boxShadow: THEME.shadow.card,
								color: '#fff',
								fontWeight: 800,
								display: 'flex',
								alignItems: 'flex-end',
								padding: '0.6em',
							}}
						>
							Draft {i + 1}
						</div>
						{i < count - 1 ? <Arrow at={t + d(6)} color={ink} /> : null}
						{ok ? (
							<div
								style={{
									position: 'absolute',
									top: '-1.2em',
									left: '50%',
									transform: `translateX(-50%) scale(${stickerSpring(frame, approved, fps)})`,
									background: THEME.palette.green,
									color: '#fff',
									fontWeight: 800,
									padding: '0.3em 0.7em',
									borderRadius: 99,
									whiteSpace: 'nowrap',
								}}
							>
								Approved ✓
							</div>
						) : null}
						{ok ? (
							<Img src={staticFile('stickers/hundred.png')} style={{position: 'absolute', width: '3.6em', right: '-2.6em', bottom: '-1.4em', transform: `scale(${stickerSpring(frame, approved + d(6), fps)}) rotate(12deg)`}} />
						) : null}
					</div>
				);
			})}
		</div>
	);
};

const Arrow: React.FC<{at: number; color: string}> = ({at, color}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const p = ease(frame, at, d(10));
	return (
		<div style={{position: 'absolute', left: '100%', top: '-2.4em', width: '2.6em', textAlign: 'center', color}}>
			<svg viewBox="0 0 100 60" style={{width: '100%', overflow: 'visible'}}>
				<path d="M5 50 Q50 -10 92 40" fill="none" stroke={color} strokeWidth={5} strokeLinecap="round" strokeDasharray={140} strokeDashoffset={140 * (1 - p)} />
				<path d="M80 38 L93 41 L90 27" fill="none" stroke={color} strokeWidth={5} strokeLinecap="round" opacity={p} />
			</svg>
			<div style={{fontSize: '0.55em', fontWeight: 700, opacity: p, whiteSpace: 'nowrap', marginLeft: '-1.2em'}}>feedback →</div>
		</div>
	);
};
