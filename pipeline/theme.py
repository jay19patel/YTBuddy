"""The look of every video: palette, type scale and motion presets. This is the one source; `python -m pipeline gen`
writes it to renderer/src/theme.gen.ts so the Remotion components use the same numbers."""

PALETTE: dict[str, str] = {
    "black": "#030003",
    "orange": "#DF5C1D",
    "blue": "#2533F8",
    "beige": "#E9E5DC",
    "crimson": "#DA2C32",
    "charcoal": "#1B181D",
    "yellow": "#F5C842",
    "green": "#34C759",
    "gold": "#C9A227",
    "darkred": "#B3161D",
    "white": "#FFFFFF",
    "ink": "#111111",
    "gray": "#6B6B6B",
}
# Inline emoji in on-screen text become these Fluent 3D stickers (assets/stickers/<name>.png). Any other emoji is
# dropped, so the system (Apple) emoji font is never used.
EMOJI: dict[str, str] = {
    "⏱": "stopwatch", "🔑": "key", "📦": "package", "📁": "folder", "🌐": "globe", "❌": "cross", "💻": "laptop",
    "⚙": "gear", "🛠": "hammer", "🪄": "wand", "✨": "sparkles", "👀": "eyes", "🧠": "brain", "🎨": "palette",
    "🎬": "clapper", "🎥": "camera", "🎞": "film", "✂": "scissors", "👍": "thumbs_up", "👎": "thumbs_down",
    "💯": "hundred", "📈": "chart_up", "🔒": "lock", "👆": "pointer", "❓": "question", "🔥": "fire", "❤": "heart",
    "🤔": "thinking", "🙏": "pray", "✅": "check",
}  # fmt: skip

THEME: dict[str, object] = {
    "palette": PALETTE,
    "emoji": EMOJI,
    "fonts": {
        "sans": "Inter Tight",
        "serif": "Playfair Display",
        "display": "Bebas Neue",
        "devanagari": "Noto Sans Devanagari",  # Hindi scripts: the Latin fonts have no Devanagari letters
    },
    # sizes are fractions of min(width, height), so 16:9 and 9:16 get the same type size
    "type": {
        "lead": {"size": 0.034, "weight": 400, "tracking": "-0.03em"},
        "hero": {"size": 0.088, "weight": 800, "tracking": "-0.045em"},
        "sub": {"size": 0.034, "weight": 400, "tracking": "-0.03em"},
        "caption": {"size": 0.03, "weight": 600, "tracking": "-0.02em"},
        "card": {"size": 0.06, "weight": 800, "tracking": "-0.04em"},
        "boldWeight": 800,
        "serifScale": 1.25,
        "lineHeight": 1.06,
        "lineHeightDevanagari": 1.4,  # vowel signs above and below the line need room
        "bgWord": 0.55,
        "bgWordOpacity": 0.15,
        "watermark": 0.026,
        "pill": 0.026,
        "verticalBoost": 1.3,  # 9:16 has twice the height: text can be bigger (fit still caps it)
    },
    "layout": {"safe": 0.05, "gap": 0.018, "stickerSize": 0.15},
    # durations in frames at 30 fps (the renderer rescales for other rates)
    "motion": {
        "wordIn": {"frames": 6, "blur": 3, "y": 14},  # a light blur only: heavy blur reads as lag
        "ghost": {"frames": 6, "opacity": 0, "blur": 0},  # off: a blurred gray preview of the next word looks smeared
        "accent": {"frames": 6},
        "highlight": {"frames": 5, "pop": 1.08},
        "strike": {"delay": 3, "frames": 6},
        "serif": {"frames": 10, "scaleFrom": 0.9, "blur": 6},
        "cardSpring": {"damping": 14, "stiffness": 180, "mass": 1},
        "stickerSpring": {"damping": 9, "stiffness": 170, "mass": 0.9},
        "float": {"amplitude": 4, "period": 60},
        "bob": {"amplitude": 6, "period": 90},
        "window": {"frames": 10, "scaleFrom": 0.95},
        "stagger": 8,
        "typingCps": 25,
        "exit": 5,
        "crossfade": 5,
        "wipe": 12,
    },
    "shadow": {"window": "0 30px 60px rgba(0,0,0,.25)", "card": "0 18px 40px rgba(0,0,0,.22)", "radius": 12},
}
