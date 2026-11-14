import {Img, staticFile, useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useItemAt, useScene} from '../SceneContext';
import {colorOf, THEME, useUnits} from '../theme';
import type {Item} from '../types.gen';

/** Folder tree: nodes appear at their `at`, indented by depth, with branch lines. */
export const FolderTree: React.FC<{items: Item[]; at: number}> = ({items, at}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const {palette} = useScene();
	const itemAt = useItemAt(at);
	return (
		<div style={{padding: '1.1em 1.4em', fontSize: '1.1em'}}>
			{items.map((it, i) => {
				const p = ease(frame, itemAt(it, i), d(THEME.motion.wordIn.frames));
				return (
					<div
						key={i}
						style={{
							display: 'flex',
							alignItems: 'center',
							gap: '0.5em',
							height: '2.1em',
							marginLeft: `${it.depth * 1.6}em`,
							opacity: p,
							transform: `translateX(${(1 - p) * -16}px)`,
							fontWeight: it.depth === 0 ? 800 : 600,
						}}
					>
						{it.depth > 0 ? <span style={{color: '#BBB', marginLeft: '-1.1em'}}>└</span> : null}
						<Img src={staticFile(`stickers/${it.icon ?? 'folder'}.png`)} style={{width: '1.3em', height: '1.3em'}} />
						<span
							style={
								it.color
									? {background: colorOf(it.color, palette), color: '#fff', padding: '0 0.3em', borderRadius: 4}
									: undefined
							}
						>
							{it.label}
						</span>
					</div>
				);
			})}
		</div>
	);
};
