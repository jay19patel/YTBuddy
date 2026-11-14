import {Img, staticFile} from 'remotion';
import {THEME} from '../theme';

const EMOJI = THEME.emoji as Record<string, string>;
const PICTO = /(\p{Extended_Pictographic}️?)/u;

/** Text with inline emoji drawn as Fluent 3D stickers. Unknown emoji are dropped (never the system emoji font). */
export const EmojiText: React.FC<{text: string; size?: string}> = ({text, size = '1.1em'}) => (
	<>
		{text.split(PICTO).map((part, i) => {
			if (!PICTO.test(part)) return <span key={i}>{part}</span>;
			const name = EMOJI[part.replace('️', '')];
			return name ? (
				<Img key={i} src={staticFile(`stickers/${name}.png`)} style={{width: size, height: size, verticalAlign: '-0.2em', margin: '0 0.08em'}} />
			) : null;
		})}
	</>
);
