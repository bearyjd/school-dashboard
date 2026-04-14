# Inline Agent Layer — Design Spec

**Date:** 2026-04-13
**Status:** Draft — awaiting user review

---

## Goal

Move the LLM from an isolated Chat tab into every data surface in the school dashboard PWA. Instead of switching context to ask the AI a question, you tap any item — digest card, homework task, sync row — and the assistant is right there with context-aware replies and the ability to take action.

## Architecture

### Approach

Add a shared `useInlineChat` hook and a lightweight `InlineAgent` UI component that any data surface can embed. The LLM interaction happens via a new focused Flask endpoint that receives only the relevant item's context rather than the full school state.

The existing General Chat tab remains for freeform queries but becomes secondary.

### New API Endpoint

```
POST /api/agent/inline
Body: { context_type: string, context_id: string, message: string }
Returns: { reply: string, action?: { type: string, payload: object } }
```

`context_type` values: `"digest_card"`, `"item"`, `"sync_source"`

The endpoint builds a tight system prompt from the specific item's data (not the full 30-day event dump) and returns an optional structured action alongside the reply.

**Actions the inline agent can return:**
| action.type | Effect |
|-------------|--------|
| `mark_item_done` | Sets item completed via existing PATCH /api/items/:id |
| `create_item` | Creates follow-up task via POST /api/items |
| `trigger_sync` | Triggers sync for one source via POST /api/sync |
| `reschedule_item` | Updates due_date on an item |

### New Frontend Components

**`InlineAgent.tsx`** — collapsible AI chip
- Renders as a small "✦ Ask" button in its collapsed state
- Expands to a single-turn mini-chat (question → reply → optional action button)
- Accepts `contextType`, `contextId`, optional `initialPrompt` props
- Uses `useInlineChat` hook for state

**`useInlineChat.ts`** — hook
- State: `{ loading, reply, action, error }`
- `ask(message)` → calls `/api/agent/inline`, updates state
- `executeAction()` → dispatches the returned action via existing API functions
- Resets on unmount

### Modified Surfaces

**DigestCard** (new): `InlineAgent` chip appears at bottom of each card. Suggested prompts pre-filled based on card type (e.g. for a homework card: "Already handled?" / "Add to Jack's tasks").

**ItemRow in Child view**: Long-press or swipe-right reveals `InlineAgent` chip. Suggested prompts: "Explain this", "Mark done", "Reschedule".

**SyncRow in Sync view**: Tap status badge opens `InlineAgent` for that source. Suggested prompts: "Why stale?", "Sync just this source".

### Flask Changes

1. New route: `POST /api/agent/inline` in `web/app.py`
2. Helper `_build_inline_context(context_type, context_id)` — fetches just the relevant item from DB/state
3. System prompt is compact: item data + current date + available actions list
4. Response JSON: `{ reply, action? }`

### No APK Required

This feature is fully implementable as a PWA enhancement. The existing TWA/APK wrapper remains compatible but is not required.

---

## What This Is NOT

- Not a redesign of the overall layout
- Not a persistent background agent
- Not a voice interface (that's a separate future feature)
- Not a replacement for the General Chat tab

---

## Testing

- Unit tests for `_build_inline_context()` in `tests/test_inline_agent.py`
- Mock LiteLLM responses in tests (same pattern as `tests/test_digest.py`)
- Frontend: `InlineAgent` renders collapsed by default, expands on tap, shows reply

---

## Open Questions (for user review)

1. Should action execution require confirmation (like the existing `needsConfirmation` in tools.ts) or execute immediately?
2. For digest cards: should the inline agent also be able to mark the whole card `done` via `patchDigestCard`?
3. Any surfaces missing from the initial list?
