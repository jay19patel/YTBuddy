# assets/

Drop a file in the right folder and the next video can use it. Nothing to configure: the folders are scanned every
time a video is planned, and the **file name is the name the storyboard (the LLM) uses**, so name files for what they
show, in lowercase with underscores: `money_bag.png`, not `IMG_0042.png`.

| Folder | Files | Used for |
|---|---|---|
| `stickers/` | `<name>.png` (transparent, ~512 px) | 3D stickers popped on the word they show; list and window icons; thumbnails |
| `characters/<who>/` | `<expression>.png` / `.webp` / `.svg` | the presenter; the LLM picks the face that fits the words |
| `characters/<who>/` | `<expression>-talk.png` | the same picture with the mouth open: the presenter lip-syncs to the voice |
| `backgrounds/` | `<name>.jpg` / `.png` / `.webp` (1920×1080 or larger) | a picture behind a scene, tinted with the scene colour |
| `sfx/` | `pop.wav`, `whoosh.wav`, `click.wav`, `typing.wav`, variants `pop-2.wav` … | sounds on pops, transitions, taps and typing |
| `sfx/` | `<sticker>.wav`, `<expression>.wav` | plays instead of the pop when that sticker or face comes in (`money.wav`, `shocked.wav`) |
| `music/` | `*.mp3` / `*.wav` / `*.m4a` | a music bed under the voice, ducked; each video gets its own track |

## Presenter tips

- Keep every picture of one presenter on the **same canvas size, with the body in the same place**, or the presenter
  jumps when the face changes. Only the face (and hands, for a pose) should differ.
- `neutral.png` is the default face (else `default`, `normal`, `happy`, `smile`, else the first file).
- Useful set: `neutral`, `happy`, `excited`, `shocked`, `thinking`, `worried`, `pointing`, `laughing`. Add a `-talk`
  twin for the faces used most; the others still work, without the mouth moving.
- More than one presenter: one folder each (`characters/jay/`, `characters/riya/`).
- Use one art style for all stickers and presenters; mixed styles look cheap. Check the licence of anything you add.
