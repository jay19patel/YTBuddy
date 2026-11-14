import {useItemAt} from '../SceneContext';
import {THEME} from '../theme';
import type {Item} from '../types.gen';
import {TypingText} from './TypingText';

/** A markdown note (e.g. brand-kit.md) whose lines type in one after another. */
export const NoteCard: React.FC<{items: Item[]; at: number}> = ({items, at}) => {
	const itemAt = useItemAt(at);
	return (
		<div style={{padding: '1.2em 1.5em', fontFamily: 'ui-monospace, Menlo, monospace', fontSize: '0.95em', lineHeight: 1.7}}>
			{items.map((it, i) => (
				<div key={i} style={{color: it.label.startsWith('#') ? THEME.palette.orange : THEME.palette.ink, fontWeight: it.label.startsWith('#') ? 700 : 400}}>
					<TypingText text={it.label} at={itemAt(it, i)} caret={i === items.length - 1} />
				</div>
			))}
		</div>
	);
};
