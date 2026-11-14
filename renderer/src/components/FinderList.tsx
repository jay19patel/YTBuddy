import {Img, staticFile, useCurrentFrame} from 'remotion';
import {ease} from '../motion';
import {useItemAt, useRel} from '../SceneContext';
import {THEME, useUnits} from '../theme';
import type {Item} from '../types.gen';
import {TypingText} from './TypingText';

/** Finder list: folders appear one by one; a row turns blue when highlighted; an item with `sub` is renamed to it
 * (typed into a white box) at `highlight_at`. */
export const FinderList: React.FC<{items: Item[]; at: number}> = ({items, at}) => {
	const frame = useCurrentFrame();
	const {d} = useUnits();
	const {rel} = useRel();
	const itemAt = useItemAt(at);
	return (
		<div style={{display: 'flex', height: '100%'}}>
			<div style={{width: '24%', background: '#F2F1F3', padding: '1em 0.9em', color: '#8A8A8A', fontSize: '0.85em', lineHeight: 2}}>
				<div style={{fontWeight: 700}}>Favourites</div>
				<div>Desktop</div>
				<div>Documents</div>
				<div>Downloads</div>
			</div>
			<div style={{flex: 1, padding: '0.6em 0.8em'}}>
				{items.map((it, i) => {
					const t = itemAt(it, i);
					const hi = it.highlight_at === null ? null : rel(it.highlight_at);
					const on = hi !== null && frame >= hi;
					const p = ease(frame, t, d(THEME.motion.wordIn.frames));
					return (
						<div
							key={i}
							style={{
								display: 'flex',
								alignItems: 'center',
								gap: '0.6em',
								padding: '0.35em 0.6em',
								paddingLeft: `${0.6 + it.depth * 1.4}em`,
								borderRadius: 6,
								background: on && !it.sub ? THEME.palette.blue : 'transparent',
								color: on && !it.sub ? '#fff' : undefined,
								opacity: p,
								transform: `translateY(${(1 - p) * 10}px)`,
							}}
						>
							<Img src={staticFile(`stickers/${it.icon ?? 'folder'}.png`)} style={{width: '1.4em', height: '1.4em'}} />
							{it.sub && on ? (
								<span style={{background: '#fff', outline: `2px solid ${THEME.palette.blue}`, borderRadius: 4, padding: '0 0.3em', fontWeight: 600}}>
									<TypingText text={it.sub} at={hi} />
								</span>
							) : (
								<span>{it.label}</span>
							)}
						</div>
					);
				})}
			</div>
		</div>
	);
};
