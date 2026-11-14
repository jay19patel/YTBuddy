/** A generic camera-glyph outline (rounded square, lens, dot) used next to your handle. `fill` 0→1 blends in the
 * gradient background for the outro. */
export const InstagramIcon: React.FC<{size: number | string; color: string; fill?: number}> = ({size, color, fill = 0}) => (
	<svg viewBox="0 0 100 100" style={{width: size, height: size, display: 'block'}}>
		<defs>
			<linearGradient id="ig" x1="0" y1="1" x2="1" y2="0">
				<stop offset="0" stopColor="#FEDA75" />
				<stop offset="0.3" stopColor="#FA7E1E" />
				<stop offset="0.6" stopColor="#D62976" />
				<stop offset="0.8" stopColor="#962FBF" />
				<stop offset="1" stopColor="#4F5BD5" />
			</linearGradient>
		</defs>
		<rect x="4" y="4" width="92" height="92" rx="26" fill="url(#ig)" opacity={fill} />
		<rect x="12" y="12" width="76" height="76" rx="22" fill="none" stroke={fill > 0.5 ? '#fff' : color} strokeWidth="8" />
		<circle cx="50" cy="50" r="17" fill="none" stroke={fill > 0.5 ? '#fff' : color} strokeWidth="8" />
		<circle cx="72" cy="28" r="5" fill={fill > 0.5 ? '#fff' : color} />
	</svg>
);
