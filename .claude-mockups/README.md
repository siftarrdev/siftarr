# Request-details modal — desktop layout tweaks

Open any file directly in a browser. All three share `_shared.css` (an approximation of the
current dark theme) and use fake content. No application files are changed.

## What the screenshots show

1. **Labels touching.** `Season 1` + `10/10 available`, `Stage individual episodes` + `Search season`,
   `S24E01` + `Episode 1`, `Season packs` + `0 cached` all render with zero horizontal space.
   In the live markup these rows use `gap-x-3` on a `flex` container, so the built Tailwind CSS
   very likely does not contain the `gap-x-*` utilities in use (worth checking
   `npm run build:css` output before styling anything else).
2. **Everything is squashed.** 8–12px padding throughout, small type, boxes nested inside boxes.

## The three options

| File | Idea |
|---|---|
| `01-spacing-rhythm.html` | Minimum change: real `gap` on every inline row, 12/16/20px padding scale, labelled filter grid. Keeps today's structure exactly. |
| `02-aligned-columns.html` | Season/episode summaries become CSS grids with fixed tracks (chevron · code · title · status · actions) so badges and links line up down the list. |
| `03-breathing-room.html` | Attacks the squashed feel: wider modal, narrower rails, taller rows, season actions on a quiet second line, episodes as divider-separated rows instead of nested boxes. |

They compose: 1 is the spacing baseline, 2 adds alignment, 3 adds density/hierarchy changes.
