# School Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-card-per-kid web app with a "Tonight" home page, per-question tables and a week calendar, in a Midnight (default) / Daylight theme, backed by one normalized server endpoint.

**Architecture:** A pure Python module builds one `GET /api/overview` payload (items, events, per-source freshness) from existing state, the items table, the GameChanger schedule and the `events` table. The React SPA stays Vite + React + TypeScript + react-router + TanStack Query and renders that payload through a small design system (CSS custom-property tokens, two themes). Old screens move to `/app/classic/*` in the same bundle for two weeks.

**Tech Stack:** Python 3.12/Flask, pytest; React 19, TypeScript, Vite, TanStack Query, react-router-dom; new dev deps vitest, @testing-library/react, @playwright/test.

**Spec:** `docs/superpowers/specs/2026-11-06-dashboard-redesign-design.md` (read it first; this plan only records decisions it does not).

## Global Constraints

- Run tests with `pytest tests/` only (never bare `pytest`); SPA checks are `npm --prefix web/spa run lint`, `run build` (`tsc -b && vite build`) and `npx vitest run`.
- No kid names or colours in code: kids and colour order come from the roster (`school_dashboard.state.get_roster()`); kid colour = palette index by roster position.
- Midnight tokens: bg `#0d1220`, surface `#151c31`, border `#232c4a`, text `#e8ecf7`, muted `#8c97b5`, selected tab `#2b3a73`; overdue `#ff6b6b`, missing `#ffb454`, tonight `#7aa2ff`, ok `#27c28a`; kids `#4d7cff`, `#27c28a`, `#b07cff`.
- Daylight tokens: bg `#faf7f2`, surface `#ffffff`, border `#e9e3d8`, text `#1f2430`, muted `#5b6070`; overdue `#c8402f`, missing `#b9770e`, tonight `#2a55c8`; kids `#2a55c8`, `#1c8a63`, `#8f4fc9`; hero block `#1f2430`.
- Default theme Midnight; choices Midnight | Daylight | System; persisted in localStorage key `school-theme`.
- Local day rolls over at **04:00** America/New_York; "tonight" = today or next weekday (Fri/Sat/Sun roll to Monday); holidays ignored in v1.
- Overdue = Schoology + manual items with `due < today`, not done. Missing/Late = PowerSchool flags only. IXL never overdue; one read-only "tonight" row per kid while `remaining > 0`. No cross-source matching.
- Events window 14 days (this week = next 7); Overdue and Missing returned in full, capped at 500 each with a warning; teams with no upcoming events hidden unless `past=1`.
- An event has `kids: [name]`; label `Penn+Jack` splits on `+`; a label that matches no roster kid gives `kids: []` (school-wide).
- All text is sanitised with the existing helper in `school_dashboard/powerschool.py` (strip control chars, 160-char cap) before it leaves the server and rendered as plain text, never HTML.
- App-page JS budget 300 kB gzipped, CSS 50 kB; no web fonts; animate `transform`/`opacity` only; honour `prefers-reduced-motion`; WCAG AA in both themes; meaning never colour-only.
- Screenshot widths 320, 375, 768, 1024, 1440, in both themes. Service worker cache name bumps `school-v1` to `school-v2`.

## Review Focus

1. Stale or failing source: the page must show a banner and tag affected rows, never a blank page. (Task 2 test, Task 7 test)
2. "Tonight" boundaries: Friday evening, Saturday, Sunday, 03:59 vs 04:01, DST change days. (Task 2)
3. Garbage input: items/events with missing, invalid or timezone-naive dates, 5,000-character titles, control characters. Skipped or capped, never a crash or horizontal overflow. (Tasks 2, 3, 8)
4. Roster size: 1 kid and 4 kids render correctly (colours cycle from the palette, filters adapt, no hardcoded names). (Tasks 2, 5, 7)
5. Stale shell after a deploy or offline: new service worker cache name, cached data labelled with its age, API failure shows a retry state. (Tasks 6, 7)

## PR slicing (merge on green CI after review; deploy after PR 4)

PR 1 docs (spec + plan, this work) · PR 2 Tasks 1-4 (tooling + backend; safe to deploy alone) · PR 3 Tasks 5-6 · PR 4 Tasks 7-9 · PR 5 Tasks 10-11 + rollout.

## File Structure

- `school_dashboard/overview_items.py`: item normalisation and status rules (pure).
- `school_dashboard/overview_events.py`: GameChanger + school-calendar events (pure except one DB read).
- `school_dashboard/overview.py`: assembler + freshness (reuses `watchdog.check_source`).
- `web/app.py`: add `GET /api/overview` only.
- `web/spa/src/design/`: `tokens.css`, `theme.ts`, `Tile.tsx`, `KidChip.tsx`, `StatusBadge.tsx`, `Row.tsx`, `Tabs.tsx`, `Banner.tsx`, `FreshnessTag.tsx`.
- `web/spa/src/lib/`: `overview.ts` (query hook), `urlState.ts`, `week.ts`.
- `web/spa/src/pages/`: `Today.tsx`, `ListPage.tsx`, `Calendar.tsx`; `web/spa/src/classic/` holds the moved old views.
- `web/spa/e2e/`: Playwright specs; `scripts/spa-qa-server.py`: synthetic-data server for e2e.

---

### Task 1: SPA test tooling and CI job

**Files:** Modify `web/spa/package.json`, `.github/workflows/tests.yml`; Create `web/spa/vitest.config.ts`, `web/spa/src/design/smoke.test.tsx`

**Interfaces:** Produces: `npx vitest run` works with jsdom + Testing Library; a CI job `spa` that runs lint, build, vitest and the bundle check `node web/spa/scripts/check-bundle.mjs` (fails if gzipped JS > 300 kB or CSS > 50 kB).

- [ ] **Step 1: Write the failing test** `smoke.test.tsx::renders_a_component_in_jsdom`: render `<div data-testid="x">hi</div>` and assert `getByTestId('x')` has text `hi`.
- [ ] **Step 2:** Run `npx --prefix web/spa vitest run`; expected FAIL (vitest not installed).
- [ ] **Step 3:** Add dev deps `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, `@playwright/test`; add scripts `test` (`vitest run`) and `check-bundle`; write `check-bundle.mjs` (gzip every file in `dist/assets`, sum by extension, exit 1 over budget); add a `spa` job to `tests.yml` (Node 20, `npm ci`, lint, build, test, check-bundle).
- [ ] **Step 4:** Run `npm --prefix web/spa run test` and `run check-bundle`; expected PASS and "within budget".
- [ ] **Step 5: Commit** `chore: add vitest, playwright and an SPA CI job`.

### Task 2: Item normalisation and status rules

**Files:** Create `school_dashboard/overview_items.py`; Test `tests/test_overview_items.py`

**Interfaces:** Produces (used by Tasks 3-4):
- `local_today(now: datetime, tz: tzinfo, rollover_hour: int = 4) -> date`
- `next_school_day(today: date) -> date`
- `classify(due: str | None, today: date, nxt: date, *, flag: str | None = None, done: bool = False) -> str | None`: returns `"overdue" | "missing" | "late" | "tonight" | "upcoming" | None` (flag is `"missing"` or `"late"` for PowerSchool rows; `None` for undated or done).
- `build_items(state: dict, manual_items: list[dict], today: date, kids: list[str]) -> list[dict]`: each item `{id, kid, title, course, due, status, source, category, score, url, readonly}` where `source in {"powerschool","schoology","ixl","manual"}`; text sanitised; Overdue/Missing capped at 500 each (the caller adds the warning).

- [ ] **Step 1: Write failing tests** (all in `tests/test_overview_items.py`):
  - `test_local_today_rolls_over_at_0400`: 2026-11-07 03:59 NY is still 2026-11-06; 04:01 is 2026-11-07.
  - `test_next_school_day_rolls_weekends`: Fri 2026-11-06, Sat 11-07 and Sun 11-08 all give Mon 2026-11-09; Mon gives Tue.
  - `test_local_today_dst_days`: 2027-03-14 and 2026-11-01 give the correct local date at 03:30 and 05:00.
  - `test_classify_rules`: due Thu with today Fri is `overdue`; due Fri or Mon (today Fri) is `tonight`; due Tue is `upcoming`; `None` and `done=True` give `None`; `flag="missing"` gives `missing` regardless of date; `flag="late"` gives `late`.
  - `test_ixl_row_is_readonly_tonight_while_remaining`: remaining 3 gives one `tonight` row `source="ixl"` `readonly=True`; remaining 0 gives none; IXL is never `overdue`.
  - `test_powerschool_and_schoology_not_merged`: the same title from both sources yields two rows.
  - `test_garbage_dates_skipped`: due `"not-a-date"`, `""`, a naive datetime string, and `None` do not raise; undated rows are dropped; `5000`-char titles are capped at 160 with control characters stripped.
  - `test_caps_at_500`: 600 missing rows return 500.
- [ ] **Step 2:** Run `pytest tests/test_overview_items.py -v`; expected FAIL (module missing).
- [ ] **Step 3:** Implement the module using `zoneinfo` for the rollover, and the sanitiser from `school_dashboard/powerschool.py` (`_clean_text`/equivalent; check its exact name). Read the state shapes in `web/app.py::api_dashboard` (`schoology`, `ixl`, `powerschool` sections) rather than guessing keys.
- [ ] **Step 4:** Run `pytest tests/test_overview_items.py -v`; expected PASS.
- [ ] **Step 5: Commit** `feat: overview item normalisation and status rules`.

### Task 3: Event normalisation

**Files:** Create `school_dashboard/overview_events.py`; Test `tests/test_overview_events.py`

**Interfaces:** Produces:
- `split_kids(label: str, roster: list[str]) -> list[str]`
- `gc_events(gc_data: dict, roster: list[str], today: date, days: int, *, include_past_seasons: bool = False) -> list[dict]`: each `{kids, title, date, time, type, location, source: "gc", scheduleSource, team}`
- `school_events(db_path: str, today: date, days: int) -> list[dict]`: reads the `events` table (`date,title,type,child,source`); each `{kids, title, date, time: "", type, location: "", source: "school", scheduleSource: None, team: None}`; `child` null gives `kids: []`.

- [ ] **Step 1: Write failing tests:**
  - `test_split_kids`: `"Penn+Jack"` gives `["Penn","Jack"]`; `"jack"` matches roster `Jack` case-insensitively; `"St. Mark Lions"` gives `[]`.
  - `test_gc_events_window_and_order`: events inside `[today, today+days)` only, sorted by date then time.
  - `test_past_season_teams_hidden`: a team with no upcoming events is dropped unless `include_past_seasons=True`.
  - `test_schedule_source_passthrough`: `schedule_source="ics"` becomes `scheduleSource="ics"`; missing key gives `"api"`.
  - `test_school_events_from_db`: a tmp sqlite `events` table with a dated row (child `None`) is returned with `kids == []`; missing DB file returns `[]`.
  - `test_bad_event_rows_skipped`: invalid dates and non-dict entries are skipped without raising; titles are sanitised and capped.
- [ ] **Step 2:** Run `pytest tests/test_overview_events.py -v`; expected FAIL.
- [ ] **Step 3:** Implement. Reuse the parsing approach of `school_dashboard/digest.py::_load_gc_events` rather than calling it (it drops `schedule_source` and does not do multi-kid).
- [ ] **Step 4:** Run the tests; expected PASS.
- [ ] **Step 5: Commit** `feat: overview event normalisation (gc + school calendar)`.

### Task 4: `/api/overview` endpoint

**Files:** Create `school_dashboard/overview.py`; Modify `web/app.py` (new route only); Test `tests/test_overview.py`, `tests/test_overview_api.py`; Modify `tests/e2e/test_pipeline_synthetic.py`

**Interfaces:** Consumes Tasks 2-3 and `school_dashboard.watchdog.check_source`, `sync_meta.read_sync_meta`, `paths.state_path/db_path/gc_path/sync_meta_path`, `state.get_roster`.
Produces: `build_overview(state, manual_items, gc_data, school_events, meta, roster, now, tz, *, days=14, include_past_seasons=False) -> dict` returning `{generated_at, kids:[{name, colorIndex}], items, events, freshness:{source:{last_ok,last_run,result,stale,failing,age_hours}}, warnings}` and `GET /api/overview?days=14&past=0`.

- [ ] **Step 1: Write failing tests:**
  - `test_overview.py::test_shape_and_kid_colour_order`: three roster kids give `colorIndex` 0,1,2 in roster order; four kids give 0..3; one kid gives `[0]` (Review Focus 4).
  - `test_stale_source_flagged`: a source whose `last_ok` is 30 h old on a weekday after the 14:30 sync gives `freshness[src].stale == True`; a failing source gives `failing == True`; payload still contains items from that source (Review Focus 1).
  - `test_truncation_warning`: 501 missing rows yield 500 and a warning string mentioning "missing".
  - `test_overview_api.py::test_200_and_json_keys` (Flask test client with tmp state env as in `tests/test_config_roster.py`): status 200, keys present; `?days=3` narrows events; unknown query params ignored.
  - `test_empty_state_returns_empty_lists`: no state file gives 200 with empty lists and a warning, not 500.
  - Extend `tests/e2e/test_pipeline_synthetic.py`: after the existing pipeline, GET `/api/overview` and assert per-kid PowerSchool missing counts equal the fixture's.
- [ ] **Step 2:** Run `pytest tests/test_overview.py tests/test_overview_api.py -v`; expected FAIL.
- [ ] **Step 3:** Implement `build_overview` (pure; `now`/`tz` injected) and the route (load state/gc/meta/db via `paths`, call `build_overview`, `jsonify`); freshness uses `watchdog.check_source` and `SOURCES` (see its exact signature in `watchdog.py` before calling).
- [ ] **Step 4:** Run `pytest tests/` and expect all green.
- [ ] **Step 5: Commit** `feat: GET /api/overview`.

### Task 5: Design system and themes

**Files:** Create `web/spa/src/design/{tokens.css,theme.ts,Tile.tsx,KidChip.tsx,StatusBadge.tsx,Row.tsx,Tabs.tsx,Banner.tsx,FreshnessTag.tsx}` plus a `*.test.tsx` for each; Modify `web/spa/src/main.tsx` (apply theme before first paint), `web/spa/index.html` (inline theme bootstrap to avoid a flash)

**Interfaces:** Produces:
- `type ThemeChoice = 'midnight' | 'daylight' | 'system'`; `resolveTheme(choice: ThemeChoice, prefersDark: boolean): 'midnight' | 'daylight'`; `getStoredTheme(): ThemeChoice`; `setTheme(choice: ThemeChoice): void` (writes `school-theme`, sets `document.documentElement.dataset.theme`).
- `<KidChip name={string} index={number} wide?={boolean}/>` (initial + name when wide), `<StatusBadge status="overdue"|"missing"|"late"|"tonight"|"upcoming"/>` (icon + word + colour), `<Tile label value tone onClick?/>`, `<Row .../>`, `<Tabs items active onChange/>`, `<Banner tone>`, `<FreshnessTag ageHours stale failing/>`.

- [ ] **Step 1: Write failing tests:**
  - `theme.test.ts::resolveTheme`: `system`+dark gives `midnight`, `system`+light gives `daylight`, explicit choices win; `getStoredTheme()` defaults to `midnight` when storage is empty or holds junk.
  - `KidChip.test.tsx`: chip text is the first initial; with `wide` also the name; `index` 5 wraps to palette position 2 (colour class `kid-2`) (Review Focus 4).
  - `StatusBadge.test.tsx`: renders the word and an element with `aria-hidden` icon for each status (not colour-only).
  - `tokens.test.ts`: parse `tokens.css` and assert the exact hex tokens from Global Constraints exist for `[data-theme=midnight]` and `[data-theme=daylight]`, and that contrast of text-on-surface and each semantic colour on surface is at least 4.5:1 in both themes (use a small WCAG helper in the test).
  - `Banner.test.tsx`/`FreshnessTag.test.tsx`: stale shows "as of" age text; failing shows the failing wording.
- [ ] **Step 2:** Run `npx vitest run src/design`; expected FAIL.
- [ ] **Step 3:** Implement tokens as CSS custom properties per theme (no hard-coded colours in component CSS), semantic elements (buttons, `nav`, `table`), designed hover/focus-visible/active states, `prefers-reduced-motion` guards; headline serif only in Daylight, heavy sans in Midnight, tabular numerals for counts.
- [ ] **Step 4:** Run vitest, lint and build; expected PASS.
- [ ] **Step 5: Commit** `feat: design system tokens, themes and base components`.

### Task 6: App shell, routing, URL state, service worker

**Files:** Modify `web/spa/src/App.tsx`, `web/spa/src/components/BottomNav.tsx`, `web/spa/public/sw.js` (`school-v2`); Create `web/spa/src/lib/{overview.ts,urlState.ts}`, `web/spa/src/classic/` (move `views/Child.tsx`, `Home.tsx` etc. unchanged, lazy-loaded), tests

**Interfaces:** Produces:
- Routes: `/` (Today), `/tonight|overdue|missing|events` (ListPage), `/calendar`, `/chat`, `/sync`, `/settings`, `/classic/*` (lazy).
- `useOverview(): UseQueryResult<Overview>` hitting `GET /api/overview`, `staleTime` 60 s, keeps previous data on error.
- `useUrlState<T>(key: string, parse: (v: string | null) => T, fallback: T): [T, (v: T) => void]` (query-string backed).
- TS types `Overview`, `OverviewItem`, `OverviewEvent`, `Freshness` in `web/spa/src/api/types.ts` matching Task 4's payload.

- [ ] **Step 1: Write failing tests:** `urlState.test.ts` (set/get round-trips, junk falls back, other params preserved); `App.test.tsx` (each route renders its page heading; `/classic/home` renders the old Home; unknown path redirects to `/`); `sw.test.ts` (the file's cache constant equals `school-v2` and the activate handler deletes other caches) (Review Focus 5); `overview.test.tsx` (on fetch error with prior data, the hook still returns the old data plus an error flag).
- [ ] **Step 2:** Run `npx vitest run`; expected FAIL.
- [ ] **Step 3:** Implement the shell: desktop top bar with tab links, phone bottom bar (replace the old `buildTabs`), `Suspense` for the lazy classic group, theme applied at boot.
- [ ] **Step 4:** Run vitest, lint, build, `check-bundle`; expected PASS.
- [ ] **Step 5: Commit** `feat: new app shell, routes, URL state, sw cache v2`.

### Task 7: Today page (layout A)

**Files:** Create `web/spa/src/pages/Today.tsx` (+ `.css`), `Today.test.tsx`

**Interfaces:** Consumes `useOverview`, `useUrlState`, design components. Produces the home page: four tiles (Overdue, Missing, Due tonight, Events this week; each links to its tab), kid filter pills (`?kid=`), grouped list (Overdue, Due tonight; Missing collapsed with count; Coming up), "This week" panel linking to `/calendar`, freshness banner, loading/empty/error states.

- [ ] **Step 1: Write failing tests** (Testing Library with a fixture `Overview`):
  - `test_tiles_counts_and_links`: tile values equal the counts in the fixture and each tile's link target is `/overdue`, `/missing`, `/tonight`, `/events`.
  - `test_kid_filter_narrows_everything`: selecting a kid hides other kids' rows, changes tile counts, and updates `?kid=`; multi-kid events match either kid.
  - `test_missing_collapsed_by_default_and_expands`.
  - `test_stale_source_banner_and_row_tag_not_blank` (Review Focus 1): a stale `ixl` shows a banner naming IXL with its age and tags IXL rows "as of"; page still lists rows.
  - `test_empty_state_names_next_due`: no tonight items shows "Nothing due tonight" and the next upcoming item.
  - `test_error_state_shows_retry_and_cached_age`: query error with cached data shows the data plus its age; without data shows a retry button.
  - `test_one_and_four_kids` (Review Focus 4): 1-kid and 4-kid fixtures render the right number of filter pills.
  - `test_long_title_does_not_overflow`: a 160-char title wraps (assert the row has `overflow-wrap` styling class).
- [ ] **Step 2:** Run `npx vitest run src/pages/Today`; expected FAIL.
- [ ] **Step 3:** Implement with semantic markup (`main`, `section`, `ul`), the Midnight look from the approved mock (hero tile = "due tonight"), no per-kid cards.
- [ ] **Step 4:** Run vitest, lint, build; expected PASS.
- [ ] **Step 5: Commit** `feat: Today page`.

### Task 8: Question tabs as tables (layout B)

**Files:** Create `web/spa/src/pages/ListPage.tsx`, `ListPage.test.tsx`; Modify `web/spa/src/api/index.ts` (item PATCH helper if missing)

**Interfaces:** Consumes `useOverview`; item check-off uses the existing `PATCH /api/items/<id>`. Produces `/tonight|overdue|missing|events` pages: sortable table (who, what, class, due, source), kid and source filters, "Late" filter on Missing, check-off for rows with `readonly === false` (optimistic update, rollback and visible error on failure), read-only rows show no checkbox.

- [ ] **Step 1: Write failing tests:** `test_each_tab_shows_only_its_status`; `test_sort_by_due_and_by_kid`; `test_late_filter_on_missing`; `test_checkoff_optimistic_then_rollback_on_error` (mock a 500: row reappears and an error message shows); `test_readonly_rows_have_no_checkbox` (PowerSchool, IXL); `test_garbage_rows_do_not_break_table` (item with empty title and a 160-char title render; table scrolls horizontally on narrow widths via a scroll container) (Review Focus 3).
- [ ] **Step 2:** Run `npx vitest run src/pages/ListPage`; expected FAIL.
- [ ] **Step 3:** Implement with a real `<table>`, `scope` headers, keyboard-operable sort buttons.
- [ ] **Step 4:** Run vitest, lint, build; expected PASS.
- [ ] **Step 5: Commit** `feat: per-question table pages with check-off`.

### Task 9: Calendar page (layout C)

**Files:** Create `web/spa/src/pages/Calendar.tsx`, `web/spa/src/lib/week.ts`, tests

**Interfaces:** Produces `weekDays(start: Date, tz: string): { date: string; label: string; isToday: boolean }[]` (7 rolling days from `start`), `shiftWeek(start: Date, weeks: number): Date`, and the page: 7-day strip on wide screens, agenda list on phones, previous/next week (`?week=` offset), items due plus events together, colour-coded by kid, today highlighted, banner linking to the overdue/missing lists.

- [ ] **Step 1: Write failing tests:** `week.test.ts` (7 consecutive dates across a DST change 2027-03-14 and a month end; `isToday` only for the 04:00-rolled local day); `Calendar.test.tsx::test_items_and_events_land_on_their_day`, `test_week_navigation_updates_url_and_shifts_by_seven_days`, `test_agenda_on_narrow_viewport` (render with `matchMedia` mocked to 320 px: list, not grid), `test_multi_kid_event_shows_both_chips`.
- [ ] **Step 2:** Run `npx vitest run src/pages/Calendar src/lib/week`; expected FAIL.
- [ ] **Step 3:** Implement `week.ts` with `Intl.DateTimeFormat` in America/New_York (no date library) and the page.
- [ ] **Step 4:** Run vitest, lint, build; expected PASS.
- [ ] **Step 5: Commit** `feat: calendar page (week strip and agenda)`.

### Task 10: Settings, theme toggle and Classic

**Files:** Modify `web/spa/src/views/Settings.tsx` (becomes `pages/Settings.tsx`), header component for a "Classic view" link; tests

**Interfaces:** Consumes `setTheme`/`getStoredTheme`. Produces a Settings control with Midnight / Daylight / System, applied immediately and persisted; a header link to `/classic/home`; Sync and Chat keep working in the new shell.

- [ ] **Step 1: Write failing tests:** `test_theme_toggle_applies_and_persists` (choosing Daylight sets `data-theme="daylight"` and `localStorage['school-theme']`); `test_system_follows_matchmedia`; `test_classic_link_navigates`; `test_sync_view_still_lists_all_four_sources` (regression from today's Sync view).
- [ ] **Step 2:** Run `npx vitest run src/pages/Settings`; expected FAIL.
- [ ] **Step 3:** Implement; keep Sync/Chat components unchanged except for theme tokens.
- [ ] **Step 4:** Run vitest, lint, build; expected PASS.
- [ ] **Step 5: Commit** `feat: theme settings and classic view link`.

### Task 11: Quality gates, docs and rollout

**Files:** Create `scripts/spa-qa-server.py`, `web/spa/e2e/{screens.spec.ts,a11y.spec.ts}`, `web/spa/playwright.config.ts`; Modify `CLAUDE.md`, `README.md`, `.omc/STATE.md` note

**Interfaces:** `scripts/spa-qa-server.py` builds synthetic state through the real pipeline (roster of 3, PowerSchool fixture, GC events, school events, one stale source) and serves the built SPA on a given port.

- [ ] **Step 1: Write the specs:** `screens.spec.ts` visits `/`, `/tonight`, `/overdue`, `/missing`, `/events`, `/calendar`, `/settings` at 320, 375, 768, 1024 and 1440 in both themes, asserts no console errors, no failed requests and no horizontal overflow (`document.documentElement.scrollWidth <= innerWidth`), and saves screenshots; `a11y.spec.ts` runs `@axe-core/playwright` and fails on serious/critical violations in both themes and tabs through the page checking focus order.
- [ ] **Step 2:** Run `npx playwright test`; expected FAIL until the server script and config exist.
- [ ] **Step 3:** Implement the server script and config; add a non-blocking CI step that runs the specs on PRs touching `web/spa/**`; add a Lighthouse run (performance >= 90, LCP < 2.5 s, CLS < 0.1) as a documented manual gate.
- [ ] **Step 4:** Run the full suite: `pytest tests/`, `npm --prefix web/spa run lint`, `run build`, `run test`, `run check-bundle`, `npx playwright test`; expected all green.
- [ ] **Step 5:** Update `CLAUDE.md`/`README.md` (new routes, `/api/overview`, theme, `/app/classic` removal date = launch + 14 days). **Rollout checklist (ops, not code):** back up and tag a rollback image, `docker compose pull`, refresh the host `web/` from the image, `up -d`, run `scripts/smoke-live.sh --container school-dashboard-1`, load `/app/` on a phone and a desktop, confirm the installed app picks up `school-v2`; then ask the parents to relabel the GameChanger map entry `PennJack` to `Penn+Jack` in `config/env`.
- [ ] **Step 6: Commit** `test: SPA screenshots, accessibility and rollout notes`.
