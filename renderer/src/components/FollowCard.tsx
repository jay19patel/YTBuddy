import {Img, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {cardSpring} from '../motion';
import {useScene, useVisualFont} from '../SceneContext';
import {SANS, THEME, useUnits} from '../theme';
import {HandCursor} from './HandCursor';

/** BUTTON_STATE "follow": a profile card; the hand taps blue "Follow" and it becomes gray "Following ✓". */
export const FollowCard: React.FC<{at: number; tap: number}> = ({at, tap}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const {d} = useUnits();
	const {brand, visual} = useScene();
	const fs = useVisualFont(1.3);
	const s = cardSpring(frame, at, fps);
	const done = frame >= tap + d(2);
	const press = frame >= tap && frame < tap + d(4) ? 0.95 : 1;
	return (
		<div style={{position: 'absolute', left: visual.x, top: visual.y, width: visual.w, height: visual.h, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
			<div
				style={{
					fontFamily: SANS,
					fontSize: fs,
					background: '#fff',
					color: THEME.palette.ink,
					borderRadius: '1.2em',
					boxShadow: THEME.shadow.window,
					padding: '1.4em 1.8em',
					display: 'flex',
					flexDirection: 'column',
					alignItems: 'center',
					gap: '0.6em',
					opacity: Math.min(1, s * 2),
					transform: `translateY(${(1 - s) * 40}%)`,
				}}
			>
				{brand.avatar ? (
					<Img src={staticFile(brand.avatar)} style={{width: '4.5em', height: '4.5em', borderRadius: 999, objectFit: 'cover'}} />
				) : null}
				<div style={{fontWeight: 800, fontSize: '1.3em', letterSpacing: '-0.03em'}}>{brand.handle}</div>
				<div style={{position: 'relative', marginTop: '0.4em'}}>
					<div
						style={{
							minWidth: '8em',
							textAlign: 'center',
							padding: '0.55em 1.4em',
							borderRadius: '0.6em',
							fontWeight: 700,
							background: done ? '#EFEFEF' : THEME.palette.blue,
							color: done ? THEME.palette.ink : '#fff',
							transform: `scale(${press})`,
						}}
					>
						{done ? 'Following ✓' : 'Follow'}
					</div>
					<HandCursor tap={tap} size="2.4em" />
				</div>
			</div>
		</div>
	);
};
