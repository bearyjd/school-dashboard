# Schoology Due Dates — Design Spec

## Overview

The Schoology tab currently shows assignment titles and course names but ignores the `due_date` field already present in the `/api/dashboard` response. This spec upgrades `renderSchoology()` to display urgency-colored dots, due date labels, sorted order, and a time filter bar — mirroring the existing Email Items tab behavior exactly.

## What Changes

Two edits to `web/templates/index.html`. No backend changes.

### 1. `renderSchoology()` rewrite

**Current behavior:** Iterates children as groups, renders a hardcoded blue dot (`#3b82f6`), no due date label, no sort, no filter.

**New behavior:**

1. Flatten `dashData.schoology` into a single array; each item gets a `_child` tag.
2. Filter to `dashChild` if not `'all'`.
3. Apply `dashTime` filter using `dashDiff(a.due_date)`:
   - `'today'` → `diff <= 0`
   - `'week'` → `diff <= 7`
   - `'month'` → `diff <= 31`
4. Sort ascending by `dashDiff(a.due_date)`; items with no due date (`null`) go to the end.
5. Render each item:
   - Dot: `dashDot(dashDiff(a.due_date))`
   - Body: title + meta row with course name + `dashDueLabel(a.due_date)`
   - Wrap in `<a>` if `a.url` present, otherwise `<div>`
6. Empty state: `'No Schoology assignments for this filter.'`

All helpers (`dashDiff`, `dashDot`, `dashDueLabel`) already exist and are used by the Email Items and Calendar tabs unchanged.

### 2. `renderDashList()` — one-line change

```js
// before
timeBar.style.display = (dashMode === 'email') ? 'flex' : 'none';

// after
timeBar.style.display = (dashMode === 'email' || dashMode === 'schoology') ? 'flex' : 'none';
```

## What Does Not Change

- `/api/dashboard` backend — `due_date` is already returned
- CSS — no new classes needed; existing `dash-dot`, `dash-body`, `dash-meta`, `dash-item-link` apply
- All other tabs (IXL, Calendar, Email Items, Readiness)
- `dashTime` state variable and `setDashTime()` function
- Child chip bar behavior

## Urgency Color Reference (from existing `dashDot`)

| Condition | Color | Meaning |
|-----------|-------|---------|
| `diff < 0` | `#ef4444` red | Overdue |
| `diff === 0` | `#f59e0b` amber | Due today |
| `diff <= 2` | `#f59e0b` amber | Due tomorrow/soon |
| `diff <= 7` | `#3b82f6` blue | Due this week |
| `diff > 7` | `#6b7280` gray | Due later |
| `null` | `#6b7280` gray | No due date |

## Scope

- **In scope:** `renderSchoology()` rewrite, `renderDashList()` condition
- **Out of scope:** Backend changes, new CSS, new state variables, other tabs
