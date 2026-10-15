# ProcessLens web app — design rules

The reference is the approved mock: https://claude.ai/artifact/BioXAntzA4B5FwQTzYUJoW (version 5). When this
file and the mock disagree, this file wins. Follow these rules for every screen.

## 1. Who it is for

The user does repetitive data entry. They are not technical. If a screen needs an explanation, it is wrong.
- Show **one obvious next action** per item.
- Show **at most one primary button** per screen. It is the near-black one.
- If something went wrong, say what happened and what to do, in one sentence.

## 2. Words (UI vocabulary)

The engine's words never appear on screen. Use the right-hand column.

| Engine / code | On screen |
|---|---|
| workflow, recipe | **task** |
| recipe version vN | not shown (Task history shows "Changed on 14 Oct") |
| run | **run** ("Run again", "Runs") |
| record, row | **row**, or the person's or thing's name when there is one ("Priya Nair") |
| verified | **added**, **updated** or **done** (the task's verb) |
| skipped (duplicate) | **already there** |
| parked | **needs your help** |
| failed | **didn't work**, plus the reason |
| submitting / reconciling | **checking…** |
| record key, input schema, step id, rung, ledger | never shown |
| value_mappings | **saved answers** |
| allowed origins | **sites this task may use** |

Rules for copy:
- Use sentence case.
- Name things by what they are ("Odoo CRM", "your spreadsheet").
- Write "Choose salesperson", not "Resolve".

## 3. Type

- **Public Sans** only, self-hosted with `@fontsource/public-sans`, weights 300, 400, 500 and 600. There is no
  second font, and no monospace in the UI.
- Never use uppercase labels or letter-spaced eyebrows.
- Numbers use `font-variant-numeric: tabular-nums`.

| Role | Size and weight | Notes |
|---|---|---|
| Page headline | 34–52 px, weight 300 | tracking −0.04em, line-height 1.04 |
| Second line of a headline | same as above | in `--muted` |
| Section and panel titles | 15 px, weight 600 | |
| Body | 14.5 px, weight 400 | |
| Secondary text | 13–14 px | `--ink-2` |
| Meta | 13 px | `--muted` |

## 4. Color tokens (CSS custom properties on `:root`)

**Light only** (the owner's call, 2026-10-15). Don't follow the OS dark mode. Copy the values from the mock.

**Gold is the one accent:**
- `--gold`, `--gold-deep`, `--gold-soft` and `--gold-line`;
- the primary button is a soft gold gradient (`--primary-a` to `--primary-b`) with dark text (`--on-primary`);
- progress bars use the same gradient;
- the **time saved** figure is in `--gold-deep` on a `--gold-soft` panel.

Gold is never used for text blocks or backgrounds.

**Text:** soft charcoal, not black. `--ink` is `#2a2d2b`.

**Background:**
- `--fog-1`, `--fog-2` and `--fog-3` form the page background: two soft radial fogs over a vertical gradient,
  fixed to the page. The light values are near-white: `#fbfcfc`, `#f1f4f3`, `#e3e8e6`.

**Surfaces:**
- `--glass`, `--glass-line` and `--glass-strong` are the frosted panels: white at 72% / 90% / 92%, with
  `backdrop-filter: blur(18px)`.

**Text:**
- `--ink` (primary), `--ink-2` (secondary), `--muted` (meta).
- `--hair` and `--hair-2` are the dividers.

**Primary button:**
- `--primary` (near-black) with `--on-primary`.

**State, always shown as a dot plus a word, never alone:**
- `--ok` for done;
- `--help` for needs your help;
- `--live` for running;
- `--muted` for idle or already there.

**Banned:**
- the fonts Inter, Space Grotesk, Manrope, Playfair Display and Lora;
- warm paper backgrounds;
- purple-to-blue gradients;
- pill chips and tag clouds;
- card grids;
- Tailwind default styling;
- uppercase mono eyebrows.

## 5. Layout

- **The app shell:**
  - a frosted **sidebar**, 216 px wide;
  - a **header** with the page title on the left, then Search (Ctrl K), the page's one primary action, and the
    avatar;
  - the **content column**, at most 1180 px wide.
- **Panels:** frosted glass, 14 px radius, a 1 px light border, and a very soft shadow.
  - Panels are for **groups of rows**, never for decoration.
  - Never put more than **2 panels side by side**.
- **Lists and tables:** rows separated by hairlines, 12–14 px vertical padding.
  - Each row: a title (weight 500), one line of explanation, and **one** quiet button on the right.
  - Tables have at most 4–5 columns. No sorting UI, no filter tabs and no ⋯ menus unless the screen truly needs
    them.
- **Empty states:** one sentence about what will appear here, and the one button that makes it appear.
- **Phone width** (under 780 px): the sidebar becomes a top strip, the panels stack, and minor columns hide.

## 6. Sidebar (information architecture)

```
Home
Needs help        (count, amber, only when > 0)
— Automate
Tasks             taught tasks; opens the task (steps · spreadsheet columns · rules · history)
Runs              every run, live and past; opens the run (progress · results)
— Your data
Spreadsheets      uploaded CSVs and their check (the brief's "input review")
Documents         PDFs (résumés, invoices) and what was read from them
Saved answers     e.g. "R. Iyer" → "Ravi Iyer"; reused on later runs
— Connect
Apps              Odoo, Twenty …; the sites each task may use; the Chrome extension's status
(bottom)
Settings          workspace, people, how long screenshots are kept, approval rules
Help
Chrome connected  live extension status
```

Group labels are plain 12 px muted text, not capitals.

## 7. Motion

- **On page load, sections blur in, in order:** `opacity 0 → 1`, `filter: blur(10px) → 0` and
  `translateY(8px) → 0`, over 850 ms with `cubic-bezier(.2,.7,.2,1)`, staggered 70 ms apart. Only on the first
  render of a page, never on data refresh.
- **Live data:** progress bars ease their width (900 ms). The "Live" dot pulses. New rows in a live list rise in
  with a 5 px blur.
- **Hover:** the primary button lifts 1 px; rows get a faint glass highlight. Nothing bounces or scales.
- Under `prefers-reduced-motion`, every animation is off, and everything stays visible at rest.

## 8. The core flow (from the brief, in this order)

1. **Teach.** Pick the app tab, record, stop. You see the steps in plain words.
2. **Task page.**
   - Steps: rename, remove and reorder them.
   - Spreadsheet columns: which recorded value comes from which column.
   - Rules: what happens if it's already there, if a value is missing, or if an option doesn't exist.
3. **Spreadsheet check.** Upload, see the problems flagged per row, fix them inline.
4. **Try one row.** Watch it run, see the exact values it is about to save, then approve.
5. **Run.** Live progress; "needs your help" items appear as they happen.
6. **Results.** Done, already there, needs help and didn't work, each with its reason. Time saved compared with
   doing it by hand. Export.
