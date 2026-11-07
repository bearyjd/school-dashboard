# School Dashboard redesign: design

Status: DRAFT for approval. Nothing is built until this is approved.
Date: 2026-11-06. Owner: the parents. Builder: whoever plans from this document.

## Intent

Parents open the dashboard to answer a handful of questions without hunting:

- What does each kid need to do **tonight**?
- What is **overdue**?
- What is **missing** (teacher marked not turned in)?
- What **events** do we have (practices, games, school events)?
- Who owes what, across all kids, in one view.

Today the web app is a phone-first dark app with one card per kid and small IXL / Schoology tiles. PowerSchool
missing/late work is a small chip, GameChanger schedules are not in the app at all, and nothing says how old
each number is. The goal is a modern, easy-to-navigate web app that answers the questions above at a glance,
on a phone and on a desktop.

## Decided by the parents

- **Layout "A, the Tonight board" is the home page.** Four answer tiles on top (Overdue, Missing, Due tonight,
  Events this week), kid filter pills, one combined to-do list grouped by urgency, a small "this week" panel.
- **Clickable tabs to "B", the per-question tables:** Tonight, Overdue, Missing, Events (and Grades, see
  open decisions). Each tab is a clean, sortable table across all kids.
- **A calendar view ("C", 7-day strip)** reachable by links: from the home "this week" panel, from the Events
  tab, and from a Calendar link in the navigation.
- **Not one card per kid.** Kids are a filter and a colour, not a container.
- **Look:** "Midnight" (dark) is the default. "Daylight" (light, warm paper background, serif headline) is an
  option the parents can switch to.
- Modern feel; works as a website on a desktop and as the installed home-screen app on a phone.

## Goals

1. The home page answers the four questions without scrolling on a typical phone and desktop.
2. One consistent list model across home, tabs and calendar (same rows, same colours, same words).
3. Every number shows how fresh it is and says so loudly when a source is stale or failing (this app already
   has a watchdog; the UI should agree with it).
4. Both themes feel intentional and meet WCAG AA contrast; meaning is never colour-only.
5. Fast on a phone: app page JS under 300 kB gzipped, no layout shift, no web-font payload in v1.

## Non-goals (v1)

- Month view. Grades *unless* the parents say yes (see open decisions).
- Redesigning push notifications (separate work).
- Changing sign-in/auth, adding new data sources, or editing anything inside the school systems.
- A native app. Multi-family support.
- Rewriting the scrapers or the sync pipeline.

## Anti-goals (a failure even if it technically works)

- A wall of small cards or chips with no hierarchy; one-card-per-kid in disguise.
- Hiding urgency (overdue/missing buried below the fold) or hiding staleness.
- A generic template-looking dashboard. The look must be deliberate (see Visual design).
- Dark-only, or colour as the only carrier of meaning.
- Regressing what works today: check-off, manual items, chat, sync controls.

## What exists today (so the plan starts from reality)

- SPA: Vite + React + TypeScript in `web/spa`, served by Flask at `/app`. Routes today: home, child detail,
  sync, chat, settings. PWA with a service worker. Build check is `npm run build`; lint is `npm run lint`.
- Data: `GET /api/dashboard` returns per-child IXL remaining, Schoology open items, PowerSchool
  missing/late/upcoming and per-source warnings. `GET /api/items` (+ PATCH/POST/DELETE) are manual and scraped
  tasks. `/api/sync*` drive and report syncs; `sync_meta` has `last_run`, `last_result`, `last_ok` per source.
  GameChanger schedules live in `state/gc-schedule.json` and are **not exposed to the SPA**. Roster (kids,
  aliases) is config-driven; names must never be hardcoded.
- Already built and reusable (all merged on `main`): text sanitisation helpers (`school_dashboard/powerschool.py`),
  data-age fields (`updated`, `scraped_at`), per-child alias resolution (`state.canonical_name`), `sync_meta`
  with `last_ok` (`school_dashboard/sync_meta.py`) and the watchdog (`school_dashboard/watchdog.py`: the schedule
  is weekdays 06:00 and 14:30 America/New_York with a 90-minute grace; "stale" = `last_ok` older than the
  latest scheduled sync minus the grace; "failing" = last result `error`). The overview module must **reuse**
  the watchdog's functions for stale/failing, not re-implement them.
- GameChanger events: `gc-schedule.json` teams carry `child`, `team_name`, `schedule_source` (api|ics) and
  events `{date,time,type,opponent,location,home_away}` in New York local time.
- School calendar events: the SQLite `events` table (rows imported with `school-state calendar import`,
  `source = calendar:<id>`; e.g. NO SCHOOL days, early dismissals).

## Information architecture

```
Navigation (desktop: top bar + tabs; phone: bottom bar):  Today | Tabs... | Calendar | Chat | Settings
Home "Today" (A)
  tiles: Overdue n | Missing n | Due tonight n | Events this week n   (each tile is a link to its tab)
  kid filter pills: Everyone | <kid> ...   (state in the URL)
  list grouped: Overdue, Due tonight, then collapsed "Missing (n)" and "Coming up"
  side panel: "This week" events (links to Calendar)
Tabs (B): Tonight | Overdue | Missing | Events | (Grades)
  sortable table, kid filter, source filter, "Late" filter on Missing
Calendar (C): 7-day strip, homework due + events together, today highlighted, kid colours,
  previous/next week; on phones an agenda list for the week.
Secondary (menu): Chat, Sync, Settings (theme toggle, kid colours read-only), "Classic view" link.
```

URL is state: tab, kid filter, week offset, theme choice persisted (cookie/localStorage) with the filters in the
query string so a view can be bookmarked or shared between the parents.

## Definitions (single source of truth, implemented once on the server)

- **Overdue**: has a due date before today (local day rolls over at 04:00), not completed. Sources: Schoology and
  manual items only. IXL has no due dates and is never "overdue".
- **No cross-source matching in v1** (my call): PowerSchool "missing" and Schoology "overdue" are shown as
  separate lists and may describe the same assignment. Each row carries a source badge so the parents can see
  that. Fuzzy matching by course and title is deliberately out of scope.
- **Missing**: PowerSchool score flagged `ismissing` and not exempt. Shown separately because a teacher recorded
  it; the count can be large (17 in the sample).
- **Late**: PowerSchool `islate` (turned in late). Informational; shown under Missing as a filter, never as
  an action in "Tonight".
- **Due tonight** (my call): due today or on the **next school day** (next weekday; Friday, Saturday and Sunday
  roll to Monday; v1 ignores school holidays, a later version can skip `NO SCHOOL` days from the `events`
  table), not completed. "Today" rolls over at 04:00 local, so late-evening use still means "tonight". Items
  without a due date are excluded. IXL "skills remaining" is one read-only "tonight" row per kid while any
  remain (it disappears when the scraper reports zero; there is no done state).
- **Row actions** (my call): Schoology and manual items can be checked off through the existing
  `/api/items` PATCH. PowerSchool rows and IXL rows are read-only (they clear when the school's data changes).
- **Events**: GameChanger events (practices, games) and school calendar events from the `events` table. Times in
  America/New_York. A "this week" window is the next 7 days. School-wide events (no kid) are always shown, even
  under a kid filter. Teams with no upcoming events are hidden by default (past seasons), with a "show past
  seasons" toggle (my call).
- **An event can have several kids** (my call): the overview payload uses `kids: [name, ...]`; a kid filter
  matches if any listed kid matches; chips show every kid. The GC team map label `Penn+Jack` means both kids.
- **Freshness**: each source has `last_ok`; a source is **stale** per the watchdog's rule and **failing** when
  its last result is `error`. Stale/failing sources show a banner on any screen that uses them, with the age in
  hours, and the affected rows get a muted "as of <date>" tag.

## Data and backend (my call)

Add one read-only endpoint, `GET /api/overview?days=14`, built by a pure, tested module
(`school_dashboard/overview.py`) so the UI stays thin:

```
{ generated_at, kids:[{name, colorIndex}],
  items:[{id, kid, title, course, due, status: overdue|missing|late|tonight|upcoming|done,
          source: powerschool|schoology|ixl|manual, category, score}],
  events:[{kids:[name], title, date, time, type, location, source: gc|school, scheduleSource: api|ics}],
  freshness:{source:{last_ok, last_run, result, stale, age_hours}},
  warnings:[...] }
```

- Text fields pass through the existing sanitiser (control characters stripped, length-capped); the UI renders
  as plain text, never as HTML.
- Kids and colours come from the roster in order (fixed palette, assigned by position), never by name.
- Overdue and Missing lists are returned in full (cap 500 each, with a warning if truncated); the 14-day window
  applies to upcoming items and events only (my call).
- Existing endpoints keep working. The old screens stay reachable at `/app/classic/*` for two weeks after launch
  as a lazy-loaded route group **in the same bundle** (one build, one service worker, nothing to deploy
  separately), then are deleted (my call). If anything goes badly wrong, the rollback is the previous image tag,
  as for every deploy.
- The existing `PennJack` team label is read as two kids (`Penn+Jack`), pending the parents' confirmation.

## Visual design

Direction: "Midnight" default, "Daylight" option. One token set, two themes; no hard-coded colours in components.

- **Midnight**: background #0d1220, surface #151c31, border #232c4a, text #e8ecf7, muted #8c97b5, selected tab
  #2b3a73. Semantic: overdue #ff6b6b (with a warm glow on the hero tile), missing #ffb454, tonight #7aa2ff,
  ok #27c28a. Kid colours: #4d7cff, #27c28a, #b07cff (by roster position).
- **Daylight**: background #faf7f2, surface #ffffff, border #e9e3d8, text #1f2430, muted #5b6070. Semantic:
  overdue #c8402f, missing #b9770e, tonight #2a55c8. Kid colours: #2a55c8, #1c8a63, #8f4fc9. Headline in a
  serif; the hero tile is the dark ink colour.
- **Type** (my call): system font stacks, no web fonts in v1 (performance, offline); tabular numerals for counts;
  a serif headline in Daylight, a heavy sans in Midnight; one scale with strong size contrast (hero number about
  56 px, tiles 34 px, rows 13 px).
- **Meaning is never colour-only**: kid chips carry the first initial and name on wider screens; status uses an
  icon and a word as well as colour.
- Motion is limited to compositor-friendly transforms and opacity (tab/underline slide, row check-off), and
  respects reduced-motion.
- Hover, focus and active states are designed for every interactive element; full keyboard navigation.
- Theme choice: Midnight, Daylight, or follow the device; default Midnight.

## States

Loading (skeleton rows, no layout shift), empty ("Nothing due tonight" with the next thing due), partial data
(one source failing: banner plus the other sources still shown), offline (cached data labelled with its age),
and error (plain message, retry).

## Quality bar

- Accessibility: AA contrast in both themes, keyboard and screen-reader pass, reduced motion honoured.
- Responsive and visual checks at 320, 375, 768, 1024 and 1440 widths, in both themes (Playwright
  screenshots), plus a Lighthouse pass; Core Web Vitals targets: LCP under 2.5 s, CLS under 0.1.
- SPA test tooling does not exist yet (my call): add `vitest` + `@testing-library/react` for components and
  `@playwright/test` for the screenshot/accessibility checks as dev dependencies, plus a CI job that runs
  lint, build and vitest on every PR (today CI only runs `pytest`). This lands in the first implementation PR.
- Tests: the overview module is pure and fully unit-tested (including the "next school day" and stale rules,
  DST and weekends); component tests for the tab/filter state; the existing synthetic end-to-end test is
  extended to assert the new endpoint; screenshots are compared old versus new on synthetic data.
- Security: render all text as text; no new third-party scripts; keep the existing CSP posture.

## Rollout

1. Ship behind the same `/app` with `/app/classic` kept for two weeks (deploy per the usual process with a
   rollback image and the smoke test).
2. The installed home-screen app updates on next load; the service worker cache must be versioned so people do
   not see a stale shell.

## Open decisions (the parents')

1. **Grades tab.** The scraper does not collect class grades yet, so a Grades tab means extending the
   PowerSchool scraper. Default if unanswered: **ship v1 without Grades** and add it in a follow-up.
2. **The older "St. Mark CYO Track & Field" team** is currently labelled `PennJack`. Default if unanswered:
   it is a team **both Penn and Jack** are on, so events show both kids.

## What is left to the builder

Component library choice (or none), state management library, file layout, exact breakpoints beyond the five
tested, chart/sparkline treatment, and the exact copy for empty states.
