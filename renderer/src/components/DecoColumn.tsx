import {THEME, useUnits} from '../theme';

/** Optional tiny vertical text column near the right edge. CJK glyphs come from the system font, so install a CJK font
 * (e.g. Noto Sans JP) on Linux render machines if you use it. */
export const DecoColumn: React.FC<{text: string; color: string}> = ({text, color}) => {
	const {u, width} = useUnits();
	return (
		<div
			style={{
				position: 'absolute',
				right: width * THEME.layout.safe * 0.45,
				top: '50%',
				transform: 'translateY(-50%)',
				writingMode: 'vertical-rl',
				fontSize: u * 0.014,
				letterSpacing: '0.3em',
				color,
				opacity: 0.3,
			}}
		>
			{text}
		</div>
	);
};
