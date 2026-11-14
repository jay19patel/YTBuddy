import {Img, staticFile, useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useItemAt} from '../SceneContext';
import {THEME, useUnits} from '../theme';
import type {Item} from '../types.gen';

const TINTS = [THEME.palette.orange, THEME.palette.blue, THEME.palette.crimson, THEME.palette.charcoal, THEME.palette.yellow];

/** A folder window that phone-shaped reel thumbnails fly into, landing in a grid. */
export const DropZone: React.FC<{items: Item[]; at: number}> = ({items, at}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const itemAt = useItemAt(at);
	return (
		<div style={{position: 'relative', height: '100%', display: 'flex', flexWrap: 'wrap', alignContent: 'center', justifyContent: 'center', gap: '1.2em', padding: '1em'}}>
			{items.map((it, i) => {
				const p = ease(frame, itemAt(it, i), d(14));
				return (
					<div
						key={i}
						style={{
							width: '5.4em',
							height: '9.6em',
							borderRadius: '0.7em',
							background: TINTS[i % TINTS.length],
							border: '0.25em solid #111',
							display: 'flex',
							flexDirection: 'column',
							justifyContent: 'flex-end',
							padding: '0.5em',
							color: '#fff',
							fontWeight: 800,
							fontSize: '0.8em',
							opacity: p,
							transform: `translate(${(1 - p) * -900}px, ${(1 - p) * -260}px) rotate(${(1 - p) * -30 + (i % 2 ? 4 : -4)}deg)`,
						}}
					>
						<Img src={staticFile('stickers/film.png')} style={{width: '60%', alignSelf: 'center', marginBottom: 'auto', marginTop: '30%'}} />
						{it.label}
					</div>
				);
			})}
		</div>
	);
};
