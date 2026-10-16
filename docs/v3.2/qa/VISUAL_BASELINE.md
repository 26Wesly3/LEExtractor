# M0 Visual Baseline — design tokens and component inventory (extracted from source)

**Source of truth:** the working tree of `E:\DOCTOR 'S work content\LEExtractor_v0.9.14_work`
on branch `feature/v3.2` at commit `e601861bde58b641a1161369a7b5c037b59955c1`
(`git rev-parse --abbrev-ref HEAD`, `git log -1 --format=%H`).

**Why this file exists:** `docs/v3.2/05_UX_UI_SPEC.md:5` requires M0 to extract
design tokens, fonts and sizes, spacing, radii, colours, button sizes, drawer and
navigation transition timings, and the Qiushi eagle plus wordmark **from the
current runnable commit**, and states that "先从源码提取并记录" — extract and
record from source first. Every value below is quoted from the file and line
named next to it. No value in this document is estimated, remembered or
invented; anything that could not be read from source is marked **NOT VERIFIED**.

**Screenshot clause.** `docs/v3.2/05_UX_UI_SPEC.md:5` also asks for screenshots of
the home page, results, detail drawer and graph at 1440×900, 1024×768 and
390×844. **NOT VERIFIED — not captured.** Reason: the M0 work item `task-5`
allocates only the four QA documents, three session fixtures and one test file,
and its acceptance command (`python -m pytest tests/test_v32_legacy_fixtures.py -q`)
does not require screenshots; capturing them needs a running local server plus a
browser capture session, which this work item does not allocate. The same
sentence in the spec says a missing screenshot is not a reason to change the
theme, so the token extraction below stands on its own.

---

## 1. Where the tokens live

| Group | File | Lines |
|---|---|---|
| CSS custom properties (`:root`) | `web/src/styles.css` | 2 |
| Webfont declaration | `web/src/styles.css` | 1 |
| Base element typography and controls | `web/src/styles.css` | 3 |
| Layout shell, cards, lists, tables | `web/src/styles.css` | 6, 7, 13, 14 |
| Home/cover theme and motion | `web/src/styles.css` | 9, 16, 17 |
| Menu (nav panel), scrim, drawer motion | `web/src/styles.css` | 63–112 |
| Graph palette and legend | `web/src/styles.css` | 24–29, 42, 44–53 |
| Overview stat strip and milestones | `web/src/styles.css` | 114–144 |
| Colour constants held in JS | `web/src/App.vue` | 69, 70, 79, 80 |
| Component markup | `web/src/App.vue` | 236–357 |
| HTML shell (viewport, title, favicon) | `web/index.html`; `litsearch/web/static/index.html` | 1 (both are single-line files) |

`web/index.html:1` (dev shell) and `litsearch/web/static/index.html:1`
(production bundle shell) both declare
`<meta name="viewport" content="width=device-width, initial-scale=1">` and
`<title>LEExtractor — 惟学无际</title>`; both use the same inline SVG favicon
(a black rounded square, `rx=7`, fill `#1b1c20`, two white 5px strokes).

## 2. Colour tokens (`web/src/styles.css:2`)

| Property | Value | Meaning in the stylesheet |
|---|---|---|
| `--paper` | `#f8f9fb` | home theme background (used at line 9) |
| `--surface` | `#fff` | base surface / body background (line 3) |
| `--surface-2` | `#fafafa` | side cards, job panels, download blocks |
| `--surface-3` | `#f5f5f5` | `pre`/`code`, selection bar, hover fill |
| `--ink` | `#1b1c20` | brand, headings on home, graph node fill, rotor bars |
| `--text` | `#171717` | body text, graph node fill |
| `--text-2` | `#3f3f3f` | secondary text, table headers, legend |
| `--muted` | `#707780` | meta lines, eyebrows, captions |
| `--line` | `#d9dde3` | home band rules, stat strip rules |
| `--border` | `#e2e2e2` | card/table/input borders |
| `--link` | `#1f6feb` | links, hover state, graph focus ring |

Additional literal colours outside `:root` (each cited where it appears):

| Value | Where | Use |
|---|---|---|
| `#677b94` | `styles.css:3` | `:focus-visible` outline, 3px, offset 3px |
| `#b3261e` | `styles.css:6` | `.error-text` |
| `#fdf4e3` / `#f0dcb4` / `#785100` | `styles.css:6` | demo banner background / border / text |
| `#1f7a45`, `#eaf6ee`, `#bfe3cb` | `styles.css:7`, `styles.css:131` | configured badge and completed milestone |
| `#9a6100`, `#fdf4e3`, `#f0dcb4` | `styles.css:14` | `badge.review-needed` |
| `#555b63` | `styles.css:9` | `.invitation` |
| `#f8f9fbdd`, radial gradients `#e3e9f25a`, `#e9e6ef6e` | `styles.css:9` | home topbar tint and cover background |
| `#555f6c` | `styles.css:13` | `.bar-fill` (year/topic bars) |
| `#fcfcfd`, `#1e1e24`, `#2c2c36` | `styles.css:44`, `45` | light/dark graph shell |
| `#26262e`, `#3a3a46`, `#c9c9d4`, `#5b5b6b` | `styles.css:52`, `53` | dark graph tool buttons |
| `rgba(35,47,62,.28)` + `blur(2px)` | `styles.css:111` | `.nav-scrim` (menu overlay) |
| `#1b1c2014` | `styles.css:63` | nav panel shadow `-18px 0 32px` |
| `#fffffff5` + `blur(10px)` | `styles.css:5` | sticky topbar (`.topbar`) |
| `#677b94`, `#c9ced6`, `#9aa2ad`, `#8a8f99` | `styles.css:72`, `73`, `51`, `76` | hover borders, graph tool hover, nav index |

Graph relation-layer colours (kept separate per layer, never merged — see
`App.vue:66`): `citation #aab3bf`, `bibliographic_coupling #8a6da4`,
`co_citation #5b8c81`, `text_similarity #9a7a44`
(`styles.css:26–29` legend, `styles.css:7` and `styles.css:13` edges,
`App.vue:79` light palette, `App.vue:80` dark palette
`#767a8d / #a98fc9 / #7fbfae / #c9a86a`).

Community palette: `App.vue:69` light `['#1b1c20','#5b8c81','#8a6da4','#9a7a44','#556070','#707780']`,
`App.vue:70` dark `['#c9c9d4','#7fbfae','#a98fc9','#c9a86a','#9aa2ad','#8f95a3']`.

## 3. Typography

### Font families (`styles.css:1`, `styles.css:2`)

* `--font-display: Anton, Impact, sans-serif` — brand, wordmark, footer brand.
* `--font-sans: Inter, -apple-system, 'Segoe UI', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif`.
* `@font-face{font-family:Anton;src:url('/assets/anton.ttf') format('truetype');font-display:swap}` (`styles.css:1`).
* Monospace appears as `monospace` (`styles.css:76`), `ui-monospace,SFMono-Regular,Menlo,Consolas,monospace` (`styles.css:33`), and `font='7px monospace'` for the eagle canvas (`App.vue:99`).

### Sizes and weights

| Element | Value | Citation |
|---|---|---|
| `body` | `font-size:15px; line-height:1.6` | `styles.css:3` |
| `h1` | `32px / 1.25 / weight 650 / letter-spacing -.025em` | `styles.css:3` |
| `h2` | `24px / 1.33 / weight 650` | `styles.css:3` |
| `h3` | `18px / 1.45 / weight 650` | `styles.css:3` |
| `p` | `line-height:1.7`, `p+p{margin-top:10px}` | `styles.css:3` |
| `pre` | `12.5px / 1.7`, padding `14px`, radius `6px`, max-height `430px` | `styles.css:3` |
| `code` | `13px`, padding `8px 12px`, radius `6px` | `styles.css:3` |
| `details > summary` | `14px` | `styles.css:3` |
| Vuetify overrides | `.v-btn{letter-spacing:0;text-transform:none;font-weight:550}`, `.v-field/.v-alert/.v-label{font-size:14px}` | `styles.css:3` |
| `.brand` | `23px` in `--font-display`; `.brand-tag` `12px` sans | `styles.css:5` |
| `.toplinks > a` | `14px` | `styles.css:5` |
| `.eyebrow` | `12px`, `letter-spacing:.08em`, uppercase | `styles.css:6` |
| `.paper-title` | `19px / 1.45 / weight 600` | `styles.css:6` |
| `.paper-meta`, `.paper-authors` | `13px` | `styles.css:6` |
| `.abstract-preview` | `14px`, 3-line clamp | `styles.css:6` |
| `.badge` | `12px`, padding `3px 8px`, radius `4px` | `styles.css:6` |
| `.metrics b` | `28px / weight 600`, tabular-nums | `styles.css:6` |
| `.data-table` | `13px`, cell padding `14px 16px`, `min-width:620px` | `styles.css:13` |
| `.drawer-inner h1` | `26px` | `styles.css:8` |
| `.drawer-inner .full-abstract` | `15px / 1.8` | `styles.css:8` |
| `.footer > a` | `17px` display font; footer text `12px` | `styles.css:8` |
| `.wordmark` | `clamp(65px, 15vw, 270px) / 1.2 / letter-spacing -.047em` | `styles.css:9` |
| `.invitation` | `13px / letter-spacing .035em` | `styles.css:9` |
| `.home-band h2` | `32px`, letter-spacing `-.02em` | `styles.css:9` |
| `.home-copy` | `16px / 1.85` | `styles.css:9` |
| `.feature h3` | `19px` | `styles.css:9` |
| `.overview-heading` | `22px` | `styles.css:115` |
| `.stat b` | `44px / 1.05 / weight 700 / letter-spacing -1px`, tabular-nums | `styles.css:119` |
| `.stat-label` | `15px / weight 600` | `styles.css:120` |
| `.stat-desc` | `13px / 1.55`; `.stat-sub` `12.5px` | `styles.css:121`, `122` |
| `.milestone h3` | `17px`; `.milestone-status` `11.5px` pill | `styles.css:132`, `130` |
| `.meta-grid dt / dd` | `12px` / `13.5px`; `dd.mono` `12px` monospace | `styles.css:31–33` |

Breakpoint-specific type overrides: `h1 26px`, `h2 22px`, `.brand 22px`,
`.paper-title 17px`, `.drawer-inner h1 23px`, `.wordmark 16vw`,
`.metrics b 24px`, `.stat b 36px`
(`styles.css:11`, `styles.css:12`, `styles.css:142`).

## 4. Spacing, layout and radii

### Spacing scale (`styles.css:2`)

`--s-1:4px`, `--s-2:8px`, `--s-3:12px`, `--s-4:16px`, `--s-5:20px`,
`--s-6:24px`, `--s-8:32px`, `--s-16:64px`.

These variables are declared but, per `grep` over `web/src/styles.css`, the
stylesheet sizes its boxes with literal px values (e.g. `gap:24px`,
`padding:24px`). The literal values observed in use are
4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 26, 28, 32, 34, 40, 48, 56, 64 px.

### Layout metrics (`styles.css:2`, `5`, `6`)

| Token | Value | Citation |
|---|---|---|
| `--rail-w` | `320px` | `styles.css:2` |
| `--rail-w-wide` | `360px` | `styles.css:2` |
| `--gutter` | `24px` (overridden to `16px` at `max-width:599px`) | `styles.css:2`, `12` |
| `--container-wide` | `1920px` | `styles.css:2` |
| `--topbar-h` | `56px` | `styles.css:2` |
| `--z-topbar` | `100` | `styles.css:2` |
| `.container` | `max-width:1280px; padding:40px var(--gutter) 64px` | `styles.css:6` |
| `.shell` | `grid-template-columns: var(--rail-w) minmax(0,1fr); gap:32px` | `styles.css:6` |
| `.shell-wide` | `var(--rail-w-wide) minmax(0,1fr)` | `styles.css:6` |
| `.main-first` | `minmax(0,1fr) var(--rail-w-wide)` | `styles.css:6` |
| `.topbar` | height `var(--topbar-h)`, padding `0 var(--gutter)` | `styles.css:5` |
| `.work-tabs` | height `49px`, buttons `padding:13px 16px`, `height:49px` | `styles.css:5` |
| `.side-card`, `.facet-rail` | sticky at `top:calc(var(--topbar-h) + 24px)`, padding `20px`, `max-height:calc(100svh - var(--topbar-h) - 48px)` | `styles.css:6` |
| `.result-toolbar` | `minmax(200px,1fr) 150px 100px auto`, gap `12px` | `styles.css:6` |
| `.metrics` | 4 columns, `>article{padding:18px 20px}` | `styles.css:6` |
| `.paper-row` | `padding:24px 0; gap:12px`; checkbox `flex:0 0 36px` | `styles.css:6` |
| `.stat-strip` | 4 columns, gap `0 34px`, top/bottom `1px` rules | `styles.css:117` |
| `.stat` | `padding:26px 0 22px` | `styles.css:118` |
| `.nav-panel` | `width:min(380px,92vw)`, padding `20px 18px 32px` | `styles.css:63` |
| `.navmap__item` | `grid-template-columns:34px minmax(0,1fr)`, padding `11px 12px` | `styles.css:72` |
| `.graph-shell .graph-canvas` | `aspect-ratio:3/2; min-height:380px; max-height:560px` | `styles.css:46` |
| `.cover` | `height:calc(100svh - var(--topbar-h)); min-height:420px` | `styles.css:9` |
| `.eagle` | `left:24px; bottom:24px; width:220px; max-height:160px; opacity:.75` | `styles.css:9` |
| `.home-band` | `max-width:1180px; padding:0 24px; margin:0 auto 64px` | `styles.css:9` |
| `.logo-band img` | `height:30px; max-width:240px` | `styles.css:9` |
| `.paper-drawer` | `width:50vw!important; max-width:880px!important; min-width:400px!important` | `styles.css:8` |

### Radii

| Radius | Used by | Citation |
|---|---|---|
| `4px` | `.badge` | `styles.css:6` |
| `6px` | `pre`, `code`, `.selection-bar`, `.graph-tools button` | `styles.css:3`, `6`, `50` |
| `8px` | `.provider-pick`, `.review-paper`, `.download-result`, `.project-card`, `.job-panel`, `.metrics`, `.empty`, `.next-step` | `styles.css:6`, `7` |
| `10px` | `.side-card`, `.facet-rail`, `.feature`, `.graph-shell`, `.navmap__item` | `styles.css:6`, `9`, `44`, `72` |
| `999px` | `.milestone-status` pill | `styles.css:130` |
| `50%` | `.milestone::before` dot (9×9px) | `styles.css:127` |

### Responsive breakpoints

`@media(max-width:1279px)` (`styles.css:10`), `@media(max-width:1023px)`
(`styles.css:137`), `@media(max-width:959px)` (`styles.css:11`, `48`),
`@media(max-width:599px)` (`styles.css:12`, `15`, `138`).
Mapping the three required capture viewports onto these tiers:
1440×900 → widest tier; 1024×768 → the `≤1279px` tier (1024 > 1023);
390×844 → the `≤599px` tier.

## 5. Button and control sizing

Vuetify `v-btn` supplies the default geometry; the project overrides:

| Control | Size | Citation |
|---|---|---|
| `.v-btn` | `letter-spacing:0`, no text transform, `font-weight:550` | `styles.css:3` |
| `.toplinks .v-btn` | `min-width:40px` | `styles.css:5` |
| `button:focus-visible` | outline `3px solid #677b94`, offset `3px` | `styles.css:3` |
| `.graph-tools button` | `min-width:30px; height:28px; padding:0 8px; font-size:12px; line-height:1` | `styles.css:50` |
| `@media(max-width:599px)` buttons | `height:auto!important; min-height:36px; padding-top:8px; padding-bottom:8px` for `.actions`, `.review-controls`, `.selection-bar` | `styles.css:15` |
| `.nav-panel__close` | `font-size:22px`, padding `0 6px` | `styles.css:68` |
| `.skip-link` | padding `8px 16px`, `top:5px; left:10px`, `z-index:10000` | `styles.css:4` |
| `.bar-track` | `height:8px; border-radius:3px` | `styles.css:13` |
| `.provider-pick` | padding `12px 16px`, radius `8px`, gap `8px` | `styles.css:6` |

## 6. Drawer, menu and navigation motion

| Motion | Duration / curve | Citation |
|---|---|---|
| Nav panel open/close | `transform 260ms cubic-bezier(.16,1,.3,1)`, `opacity 200ms ease` | `styles.css:63` |
| Nav map item entrance | `le-nav-in 320ms cubic-bezier(.16,1,.3,1)` both, delay `calc(var(--nav-i,0) * 28ms)` | `styles.css:79`, `80` |
| Nav item hover / active | `background .12s, border-color .12s, transform .12s`; `:active{translateY(1px)}` | `styles.css:72`, `74` |
| Nav scrim | `rgba(35,47,62,.28)` + `backdrop-filter:blur(2px)`, `opacity 200ms` | `styles.css:111`, `112` |
| Drawer close (content) | `le-drawer-out 240ms cubic-bezier(.4,0,.8,1)` both, to `opacity:0; translateX(18px)` | `styles.css:84`, `85` |
| Drawer inner transition | `transform 260ms cubic-bezier(.16,1,.3,1), opacity 200ms ease` | `styles.css:95` |
| Drawer content stagger | `le-drawer-in 320ms cubic-bezier(.16,1,.3,1)`, delays 40/80/120/160/200/240/280ms | `styles.css:97–105` |
| Drawer scrim | `.v-navigation-drawer__scrim{backdrop-filter:blur(3px)}` | `styles.css:94` |
| Reduced motion | `body.reduced *` and `@media(prefers-reduced-motion:reduce)` disable all animation/transition | `styles.css:16`, `17`, `86–89`, `106–109` |
| Graph node fade-in | `graphFade .32s ease-out both`; focus ring `opacity .12s` | `styles.css:19`, `20`, `21` |
| Rotor (wordmark X) | `transition:transform .32s`; hover `rotate(14deg)`; bars at ±55°, `width:105%`, `height:26%`, radius `.018em` | `styles.css:9` |
| Project card hover | `transition:border-color .12s`, hover border `#000` | `styles.css:7` |
| App-level timers | `closeNav` 260ms unload timer; `closeDrawer` 260ms unload timer | `App.vue:172`, `App.vue:184` |
| Cover entrance | exit `280ms cubic-bezier(.4,0,.8,1)`, arrival `620ms cubic-bezier(.16,1,.3,1)` (Web Animations, depth blur `6px`) | `App.vue:205`, `App.vue:208` |
| Eagle animation tick | `setInterval(...,125)` with `eagleFrame += .125` | `App.vue:101` |
| Rotor spring | `rotorAngle += diff*(1-exp(-dt/180))`, frame cap 48ms | `App.vue:189–192` |

## 7. Qiushi eagle and the wordmark

* **Wordmark**: markup `LEExtractor` split as `LEE` + `.x-slot`/`#rotor` + `tractor`
  (`App.vue:263`). The `.rotor` is drawn in CSS as two ink bars
  (`styles.css:9`); the pointer-driven rotation uses the original Demo v0.3
  algorithm `${}rotate(...)` in `App.vue:186–196`.
* **Eagle**: an animated **character canvas**, not an image, in
  `App.vue:263` — `<div id="eagle" class="eagle" role="img">` containing
  `<canvas id="eagleCanvas" width="640" height="400">`. Logical draw size is
  `320×200` with `devicePixelRatio` capped at 2 (`App.vue:104`). Glyph set
  `EAGLE_GLYPHS = ['/','\\',':','·','—','+']` (`App.vue:96`), ink-ish fill
  `#647789` (`App.vue:99`), grid `EAGLE_GRID` = 60 columns × 36 rows × 810 cells
  (`web/src/eagle-grid.js:1`), mapped at `x:12+col*4.4`, `y:10+row*4.7`
  (`App.vue:104`). It pauses on pointer-enter, on a hidden tab and under reduced
  motion (`App.vue:98`, `101–103`). Exposed to assistive tech as
  `aria-label="动态字符版求是鹰"` / "Animated Qiushi eagle character art"
  (`App.vue:263`).
* **Wordmark image asset**: `/assets/zju-wordmark.png` in the `.logo-band`
  ("参赛单位 / Affiliation"), `App.vue:264`.
* **Asset files** (`web/public/assets/`, copied verbatim into `web/dist/assets/`
  by the Vite build): `anton.ttf` 130 324 B, `Anton-OFL.txt` 4 484 B,
  `zju-wordmark.png` 15 183 B, `qiushi-characters.png` 1 455 628 B
  (`Get-ChildItem web/public/assets`).
  `qiushi-characters.png` appears in the build output but **NOT VERIFIED** as
  referenced by current source: a `Select-String` over `web/src/*.vue`,
  `web/src/*.js` and `web/index.html` for `qiushi` returns only
  `App.vue:263` (the `aria-label "动态字符版求是鹰"` text), not an `<img>` or CSS
  `url()`. The eagle is rendered from `EAGLE_GRID`, so the PNG is an unused
  leftover from the prototype (or is loaded by a later file not present in the
  working tree).
* **Font licence**: the Anton webfont ships with its OFL text
  (`web/public/assets/Anton-OFL.txt`); see `docs/v3.2/12_OPEN_SOURCE_ASSET_POLICY.md`.

## 8. Component inventory (`web/src/App.vue`)

| Component / region | Lines | Notes |
|---|---|---|
| Skip link `#main` | 238 | `styles.css:4` |
| Topbar: brand + brand tag, Projects/Settings links, language toggle, Navigate button | 239–242 | `.brand` is `LEExtractor` + `文献研究工作台` |
| Nav scrim (menu overlay) | 243 | `.nav-scrim`, closes on click |
| Nav panel (vertical menu) | 244–253 | `navModules()` = home + 7 work pages + projects; `navDesc` per item |
| Work tabs (per project) | 254 | 7 tabs; `project-name` on the right |
| Global error/notice alerts | 256–257 | `.v-alert` tonal, closable |
| Demo banner | 258 | shown when `project.demo` |
| Loading line | 259 | indeterminate `v-progress-linear`, height 2 |
| Project-not-found empty state | 260 | |
| Home cover (wordmark, rotor, eagle) | 262–263 | |
| Home affiliation logo band | 264 | `zju-wordmark.png` |
| Home intro band | 265 | eyebrow + h2 + copy + 3 CTAs |
| Home feature trio (`cols-3 > .feature`) | 266 | `01 / DISCOVERY`, `02 / UNDERSTANDING`, `03 / HANDOFF` |
| Home overview band | 267 | `HomeOverview.vue` (stat strip + milestones) |
| Home recent projects | 268 | up to 3 `.project-card` |
| Page head (eyebrow, title, lead, Refresh) | 272 | |
| Job panel (status, cancel, counts, timing details, downloads) | 273–300 | `.job-panel`, `details.diag-details`, `data-job-summary` |
| Provider outcomes card + data table | 302–309 | `.data-table`, `data-manifest-summary` |
| Search page | 310–313 | `form-card`, `provider-pick`, side card |
| Results page | 315–331 | `facet-rail`, `.metrics`, `result-toolbar`, `selection-bar`, `paper-row` list |
| Expand page | 333 | rounds/budget form + related-literature buttons |
| Landscape page | 335–339 | `.metrics`, `graph-shell` + force-graph canvas, `graph-tools`, `graph-legend`, topic cards, coverage, novelty table |
| Questions page | 341 | candidate-question cards |
| Review page | 343 | stage select, retrieve full text, include/exclude/maybe |
| Export page | 345 | format/scope, download result, handoff card |
| Projects page | 347 | create project, demo, import session (`accept=".json,application/json"`) |
| Settings page | 349 | provider configuration + credential fields (`type="password"`), interface preferences |
| Paper background placeholder | 352 | shown behind the drawer |
| Paper detail drawer | 354 | `v-navigation-drawer` `.paper-drawer`, `meta-grid`, identifiers, abstract, evidence, screening |
| Footer | 355 | brand + two taglines |

Responsive behaviour of the drawer: width is computed in JS as
`viewportWidth<960 ? viewportWidth : min(880, max(400, viewportWidth/2))`
(`App.vue:12`) and forced to `100vw` by CSS at `≤959px` (`styles.css:11`).

Graph rendering constants that affect the visual baseline: node cap
`GRAPH_MAX_NODES=24` (`App.vue:47`), near-duplicate text threshold `0.995`
(`App.vue:48`), relation-edge cap `60` (`App.vue:61`), citation edges drawn
solid with arrow, other layers dashed (`App.vue:62`, `App.vue:88`), node radius
by rank 14/10/7 or 6 (`App.vue:75`), warmup ticks 320 with
`cooldownTicks(0)` then one `zoomToFit` (`App.vue:88`).

## 9. Items explicitly NOT VERIFIED in this document

1. **Screenshots at 1440×900 / 1024×768 / 390×844** — not captured; see the
   screenshot clause at the top.
2. **The three `--s-*` spacing variables are not the ones actually consumed** —
   `grep -n "var(--s-"` over `web/src/styles.css` returns no match, so the scale
   in §4 is a declared token set, not the applied one; the applied values are the
   literal pixels listed there.
3. **`qiushi-characters.png` has no source reference** — see §7.
4. **Rendered appearance** (actual computed font fallback, DPI, Vuetify theme
   defaults applied at runtime) — cannot be asserted from source; would need the
   screenshot pass.
