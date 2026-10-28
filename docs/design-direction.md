# TenderCheck web page — design direction (2026-10-28)

Written before the rebuild, following the shared design playbook (PB rule "Threads 디자인 자료를 제작에 반영하는 기준",
2026-10-24; local `크몽프로젝트/shared-media/design-reference-playbook.md`). It replaces a first draft (commit 87f3fe4)
that was built from the frontend-design skill alone: cream paper, serif type, rotated rubber stamps, ruled lines and a
staggered entrance on every row. That draft hit two of the playbook's MUST NOTs — swapping the usual AI palette for
"cream · serif" and applying badges/entrance motion everywhere — and the owner rejected it.

## Who, what, result
- **Users.** (1) Hackathon judges: desktop, 3–5 minutes, must see within seconds that this is a *verifying* agent,
  not a chatbot. (2) A bid preparer at a small firm checking a package before upload. (3) A procurement reviewer.
- **Representative task.** Open a case → see what is wrong, worst first → open the exact tender line that proves it →
  copy the question for the issuing authority.
- **Result to show.** Per requirement: status, the verbatim quote with its clause, the files it was matched to, why,
  and what to do. Conflicts side by side. The run's own evidence (calls, quote checks, invariants).
- **Keep.** Every statement already in the report (wording is the engine's), the fixed disclaimer, the replay/live
  labelling, textContent-only rendering, CSP `'self'`.

## Sources chosen (checked 2026-10-28)
| ID | Source | Principle adopted | Where it lands |
|---|---|---|---|
| threads-design-02 | UI Craft (github.com/educlopez/ui-craft, MIT, commit 49b7517) via Threads @croissmouse | Start from the product's intent and its one core interaction; check error, empty and mobile states in the real output | Core interaction = *finding → source line*; empty/err states; 3 widths checked |
| threads-design-04 | Taste Skill (github.com/Leonxlnx/taste-skill, MIT) via Threads @unclejobs.ai | An operational screen is not a landing page: density and motion follow the work | No hero, no marketing copy; dense list; motion only on user actions |
| threads-design-08 | Threads @_0.beomi_ | Replace adjectives with concrete screen conditions | The conditions below |
| awesome-design-md / IBM | github.com/VoltAgent/awesome-design-md (MIT) `design-md/ibm/DESIGN.md` (Carbon) | Enterprise/governance product: 0 px radius, 1 px hairlines, depth by surface change not shadow, one chromatic accent reserved for interaction, semantic colours only for status, 4 px spacing, 48 px top bar, sentence-case labels | Tokens, top bar, tabs (2 px underline), focus ring |
| awesome-design-md / Linear | `design-md/linear.app/DESIGN.md` | Dense work list: one hairline per row, IDs in mono, status as a small indicator rather than a banner | Findings list rows |
| IBM Carbon motion | github.com/carbon-design-system/carbon `packages/motion` (Apache-2.0, main @ 131963f) | Durations 70/110/150/240/400 ms; productive standard `cubic-bezier(.2,0,.38,.9)`, entrance `(0,0,.38,.9)`, exit `(.2,0,1,.9)` | All transitions |
| transitions.dev | "Panel reveal", "Tabs sliding", "Thinking states" (library, checked 2026-10-28) | Pressed element and next state stay connected; the active-stage line swaps to the next | Mobile evidence sheet, status filter indicator, progress strip. **Concept only, no code copied**: the terms allow use in projects but do not say whether publishing in a public repository counts as redistribution, so nothing from the library is in this repo. |

Real product screens used as patterns (not copied): **GitHub pull-request review** (a comment is anchored to a line;
clicking it jumps to and marks that line), **Sentry issue detail** (event summary next to the source line that caused
it, context lines around it), **Carbon data table with tags** (dense status rows, filter by tag). Checked from their
public documentation and my knowledge of the products; no logged-in screens were opened.

## Concrete screen conditions
- First viewport at 1440×900 shows: product name, case picker, the status counts, the first 5–6 findings, and the
  source document pane — no hero, no illustration.
- Findings left (reading order first), evidence right (sticky, full height). The evidence pane is the product's
  differentiator, so it is always visible on desktop.
- **Signature interaction.** Selecting a finding marks it with a 2 px blue left rule; the source pane scrolls to the
  cited line (240 ms, productive standard) and marks that line with the same 2 px blue rule and a blue-10 fill,
  the exact quote underlined. Same mark on both ends = the connection is visible.
- Status = icon shape + word + colour (never colour alone): error ✕ red, missing ○ red outline, needs confirmation
  △ amber, conditional ◇ grey-blue, met ✓ green. Counts double as filters (Carbon tag filter); the active filter
  slides its underline (150 ms).
- Rows ~64–80 px: status, ID (mono), requirement sentence, clause link on the right; reason and action on a second
  line; quote, files and Q1–Q8 proof expand in place.
- Mobile ≤ 672 px: single column; the evidence pane becomes a bottom sheet opened by a citation (Panel reveal concept,
  240 ms entrance / 150 ms exit), closable, focus moves into it and back.
- No entrance animation on load, no rotated stamps, no paper texture, no gradients, no shadows.

## Colour, type, density, motion — and why
- **Colour.** White canvas `#ffffff`, layer `#f4f4f4`, hairline `#e0e0e0`, ink `#161616`, muted `#525252`
  (Carbon g10 values). One accent, blue `#0f62fe`, only for interaction (selection, links, focus) — a reviewer's eye
  should land on statuses, not on decoration. Status colours from Carbon support colours: red `#da1e28`,
  green `#198038` (green-60, for text contrast on white), amber `#8e6a00` text on `#fcf4d6`, grey-blue `#4d5358`.
  Dark mode uses Carbon g100 (`#161616` / `#262626` / `#f4f4f4`).
- **Type.** Pretendard Variable (OFL, local copy) because the same page shows Korean and English tenders and needs one
  family with both scripts on matched metrics; IBM Plex Mono (OFL) for IDs, clause labels and line numbers so they
  align and read as references. Sizes from the IBM scale: 14/20 body, 12/16 caption, 16/24 row title, 20/28 section.
- **Density.** Work tool, so dense: 4 px grid, hairline separators instead of cards, 0 px radius.
- **Motion.** Only where the user acted: select (110 ms), scroll-to-line (240 ms), filter underline (150 ms),
  sheet (240/150 ms), progress stage swap (150 ms). `prefers-reduced-motion` turns all of it off.

## Not reused, and why
Cream/paper background, serif display type, rubber-stamp badges, ruled-paper lines, staggered row entrance, highlighter
gradient on the wordmark (first draft — decorative, and the playbook's anti-pattern list). Linear's dark-only canvas
and lavender accent (a review tool must also print and read in daylight; one accent is enough). IBM's marketing
display sizes and the 1584 px grid (this is a tool screen, not a marketing page). Any brand mark, copy or layout taken
from those products.

## Checks to record after the build
Render at 375, 768 and 1440 wide in light and dark; keyboard focus order; contrast of status text; citation → line on
every case; empty filter result; failed re-run message; side-by-side before/after screenshots in `docs/design/`.
