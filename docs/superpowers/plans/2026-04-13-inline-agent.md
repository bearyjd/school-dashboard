# Inline Agent Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a contextual LLM chip to item rows (Child view) and sync source rows (Sync view) so the AI can answer questions and take actions without leaving the current screen.

**Architecture:** New `POST /api/agent/inline` Flask endpoint builds a compact system prompt from the specific item's data, calls LiteLLM, and returns `{reply, action?}`. Frontend `useInlineChat` hook + `InlineAgent` component wire into existing views. Actions (mark done, reschedule, trigger sync) are executed via existing API functions.

**Tech Stack:** Python/Flask (backend), React 18 + TypeScript + TanStack Query (frontend), existing LiteLLM proxy.

---

## File Map

| File | Change |
|------|--------|
| `web/app.py` | Add `_build_inline_context()` helper + `POST /api/agent/inline` route |
| `tests/test_inline_agent.py` | New — 5 tests for the endpoint |
| `web/spa/src/api/types.ts` | Add `InlineAgentAction` + `InlineAgentResponse` types |
| `web/spa/src/api/index.ts` | Add `askInlineAgent()` function |
| `web/spa/src/hooks/useInlineChat.ts` | New — hook managing loading/reply/action/error state |
| `web/spa/src/components/InlineAgent.tsx` | New — collapsible AI chip component |
| `web/spa/src/views/Child.tsx` | Wire `InlineAgent` into each item row |
| `web/spa/src/views/Sync.tsx` | Wire `InlineAgent` into each source row |

---

### Task 1: Backend endpoint + tests

**Files:**
- Modify: `web/app.py` (add after the `/api/chat` route, around line 182)
- Create: `tests/test_inline_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inline_agent.py`:

```python
"""Tests for /api/agent/inline endpoint."""
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from web.app import app as flask_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "school.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            child TEXT NOT NULL,
            title TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'assignment',
            source TEXT NOT NULL DEFAULT 'manual',
            due_date TEXT,
            notes TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO items (id, child, title, type, source, due_date, completed)"
        " VALUES (1, 'Ford', 'Math HW', 'assignment', 'sgy', '2026-04-15', 0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SCHOOL_DB_PATH", str(db))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(tmp_path / "sync_meta.json"))
    monkeypatch.setenv("LITELLM_URL", "http://mock-litellm:8080")
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _mock_litellm(reply: str) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = lambda: None
    mock.json.return_value = {"choices": [{"message": {"content": reply}}]}
    return mock


def test_inline_agent_missing_fields_returns_400(client):
    r = client.post("/api/agent/inline", json={"context_type": "item"})
    assert r.status_code == 400
    assert "required" in r.get_json()["error"]


def test_inline_agent_unknown_context_type_returns_400(client):
    r = client.post("/api/agent/inline", json={
        "context_type": "bogus", "context_id": "1", "message": "hi"
    })
    assert r.status_code == 400


def test_inline_agent_item_reply_no_action(client):
    with patch("requests.post", return_value=_mock_litellm("It looks done already.")):
        r = client.post("/api/agent/inline", json={
            "context_type": "item", "context_id": "1", "message": "Is this done?"
        })
    assert r.status_code == 200
    body = r.get_json()
    assert body["reply"] == "It looks done already."
    assert body["action"] is None


def test_inline_agent_item_reply_with_action(client):
    reply = 'Sure, marking it done.\nACTION: mark_item_done {"id": 1}'
    with patch("requests.post", return_value=_mock_litellm(reply)):
        r = client.post("/api/agent/inline", json={
            "context_type": "item", "context_id": "1", "message": "Mark it done"
        })
    assert r.status_code == 200
    body = r.get_json()
    assert "marking it done" in body["reply"]
    assert body["action"] == {"type": "mark_item_done", "payload": {"id": 1}}
    assert "ACTION:" not in body["reply"]


def test_inline_agent_sync_source(client, tmp_path, monkeypatch):
    meta_path = tmp_path / "sync_meta.json"
    meta_path.write_text(json.dumps({
        "ixl": {"last_run": "2026-04-13T06:00:00", "last_result": "ok"}
    }))
    monkeypatch.setenv("SCHOOL_SYNC_META_PATH", str(meta_path))
    with patch("requests.post", return_value=_mock_litellm("IXL synced this morning.")):
        r = client.post("/api/agent/inline", json={
            "context_type": "sync_source", "context_id": "ixl", "message": "Status?"
        })
    assert r.status_code == 200
    assert "IXL" in r.get_json()["reply"]
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
python -m pytest tests/test_inline_agent.py -v
```

Expected: all 5 tests FAIL with `404` or import errors — the endpoint does not exist yet.

- [ ] **Step 3: Add `_build_inline_context()` and the route to `web/app.py`**

After the `api_chat` function (around line 182), insert:

```python
def _build_inline_context(context_type: str, context_id: str) -> tuple[str, list[str]]:
    """Return (context_description, available_actions) for the inline agent."""
    db_path = os.environ.get("SCHOOL_DB_PATH", "/opt/school/state/school.db")

    if context_type == "item":
        from school_dashboard.db import init_db, get_item
        init_db(db_path)
        item = get_item(db_path, int(context_id))
        if item is None:
            raise ValueError(f"item {context_id} not found")
        ctx = (
            f"Homework item for {item['child']}: '{item['title']}', "
            f"type={item['type']}, due={item.get('due_date') or 'unset'}, "
            f"completed={bool(item['completed'])}, notes={item.get('notes') or 'none'}"
        )
        return ctx, ["mark_item_done", "reschedule_item", "create_item"]

    if context_type == "sync_source":
        meta_path = os.environ.get("SCHOOL_SYNC_META_PATH", SYNC_META_DEFAULT_PATH)
        meta = read_sync_meta(meta_path)
        entry = meta.get(context_id, {})
        ctx = (
            f"Sync source '{context_id}': "
            f"last_run={entry.get('last_run') or 'never'}, "
            f"last_result={entry.get('last_result') or 'unknown'}"
        )
        return ctx, ["trigger_sync"]

    raise ValueError(f"unknown context_type: {context_type!r}")


@app.route("/api/agent/inline", methods=["POST"])
def api_agent_inline():
    data = request.get_json(silent=True) or {}
    context_type = (data.get("context_type") or "").strip()
    context_id = (data.get("context_id") or "").strip()
    message = (data.get("message") or "").strip()
    if not context_type or not context_id or not message:
        return jsonify({"error": "context_type, context_id, and message are required"}), 400

    try:
        context_str, available_actions = _build_inline_context(context_type, context_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    system = (
        "You are a focused assistant for a family school dashboard.\n"
        f"Context: {context_str}\n"
        f"Available actions: {', '.join(available_actions)}\n"
        "If you want to take an action, end your reply with exactly one line:\n"
        "ACTION: <action_type> <json_payload>\n"
        "Otherwise reply with plain helpful text. Be brief (1-3 sentences)."
    )

    try:
        resp = requests.post(
            f"{LITELLM_URL.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LITELLM_MODEL, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ], "max_tokens": 400},
            timeout=30,
        )
        resp.raise_for_status()
        reply_text = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Strip optional ACTION line from reply
    action: dict | None = None
    clean_lines = []
    for line in reply_text.strip().split("\n"):
        if line.strip().startswith("ACTION:"):
            try:
                rest = line.strip()[len("ACTION:"):].strip()
                action_type, payload_str = rest.split(" ", 1)
                action = {"type": action_type, "payload": json.loads(payload_str)}
            except Exception:
                pass
        else:
            clean_lines.append(line)

    return jsonify({"reply": "\n".join(clean_lines).strip(), "action": action})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_inline_agent.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Run full suite to check for regressions**

```bash
python -m pytest tests/ -q
```

Expected: 106 passed (101 original + 5 new).

- [ ] **Step 6: Commit**

```bash
git add web/app.py tests/test_inline_agent.py
git commit -m "feat: POST /api/agent/inline endpoint with item + sync_source context"
```

---

### Task 2: Frontend API types + function

**Files:**
- Modify: `web/spa/src/api/types.ts`
- Modify: `web/spa/src/api/index.ts`

- [ ] **Step 1: Add types to `web/spa/src/api/types.ts`**

Open `web/spa/src/api/types.ts` and append at the end:

```typescript
export interface InlineAgentAction {
  type: 'mark_item_done' | 'reschedule_item' | 'create_item' | 'trigger_sync'
  payload: Record<string, unknown>
}

export interface InlineAgentResponse {
  reply: string
  action: InlineAgentAction | null
}
```

- [ ] **Step 2: Add `askInlineAgent` to `web/spa/src/api/index.ts`**

Open `web/spa/src/api/index.ts`. The first line already reads:
```typescript
import type { Item, Dashboard, SyncStatus, SyncMeta, ChatMessage, Digest } from './types';
```

Change that line to:
```typescript
import type { Item, Dashboard, SyncStatus, SyncMeta, ChatMessage, Digest, InlineAgentResponse } from './types';
```

Then append at the bottom of the file:
```typescript
export const askInlineAgent = (contextType: string, contextId: string, message: string) =>
  apiFetch<InlineAgentResponse>('/api/agent/inline', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context_type: contextType, context_id: contextId, message }),
  })
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd web/spa && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/spa/src/api/types.ts web/spa/src/api/index.ts
git commit -m "feat: add InlineAgentResponse type + askInlineAgent API function"
```

---

### Task 3: `useInlineChat` hook

**Files:**
- Create: `web/spa/src/hooks/useInlineChat.ts`

- [ ] **Step 1: Create the hook**

Create `web/spa/src/hooks/useInlineChat.ts`:

```typescript
import { useState, useCallback } from 'react'
import { askInlineAgent } from '../api'
import type { InlineAgentAction } from '../api/types'

interface State {
  loading: boolean
  reply: string | null
  action: InlineAgentAction | null
  error: string | null
}

const INITIAL: State = { loading: false, reply: null, action: null, error: null }

export function useInlineChat(contextType: string, contextId: string) {
  const [state, setState] = useState<State>(INITIAL)

  const ask = useCallback(async (message: string) => {
    setState({ loading: true, reply: null, action: null, error: null })
    try {
      const res = await askInlineAgent(contextType, contextId, message)
      setState({ loading: false, reply: res.reply, action: res.action, error: null })
    } catch (e: unknown) {
      setState({
        loading: false, reply: null, action: null,
        error: e instanceof Error ? e.message : 'request failed',
      })
    }
  }, [contextType, contextId])

  const reset = useCallback(() => setState(INITIAL), [])

  return { ...state, ask, reset }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd web/spa && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/spa/src/hooks/useInlineChat.ts
git commit -m "feat: useInlineChat hook for inline agent state"
```

---

### Task 4: `InlineAgent` component

**Files:**
- Create: `web/spa/src/components/InlineAgent.tsx`

- [ ] **Step 1: Create the component**

Create `web/spa/src/components/InlineAgent.tsx`:

```typescript
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useInlineChat } from '../hooks/useInlineChat'
import { updateItem, createItem, triggerSync } from '../api'
import type { InlineAgentAction } from '../api/types'

interface InlineAgentProps {
  contextType: string
  contextId: string
  suggestions?: string[]
}

const SYNC_TOKEN = (window as unknown as { __SYNC_TOKEN__?: string }).__SYNC_TOKEN__ ?? ''

export function InlineAgent({ contextType, contextId, suggestions = [] }: InlineAgentProps) {
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const { loading, reply, action, error, ask, reset } = useInlineChat(contextType, contextId)
  const qc = useQueryClient()

  const send = (msg: string) => {
    setInput('')
    ask(msg)
  }

  const executeAction = async (act: InlineAgentAction) => {
    try {
      if (act.type === 'mark_item_done') {
        await updateItem(act.payload.id as number, { completed: true })
        qc.invalidateQueries({ queryKey: ['items'] })
        qc.invalidateQueries({ queryKey: ['dashboard'] })
      } else if (act.type === 'reschedule_item') {
        await updateItem(act.payload.id as number, { due_date: act.payload.due_date as string })
        qc.invalidateQueries({ queryKey: ['items'] })
      } else if (act.type === 'create_item') {
        await createItem({
          child: act.payload.child as string,
          title: act.payload.title as string,
          type: act.payload.type as string | undefined,
          due_date: act.payload.due_date as string | undefined,
        })
        qc.invalidateQueries({ queryKey: ['items'] })
      } else if (act.type === 'trigger_sync' && SYNC_TOKEN) {
        await triggerSync(act.payload.source as string, 'none', SYNC_TOKEN)
        qc.invalidateQueries({ queryKey: ['syncMeta'] })
      }
    } catch {
      // action failed; leave UI open so user sees current state
    }
    reset()
    setOpen(false)
  }

  if (!open) {
    return (
      <button
        className="btn btn--ghost"
        style={{ fontSize: '12px', padding: '4px 10px', marginTop: 4, alignSelf: 'flex-start' }}
        onClick={() => setOpen(true)}
      >
        ✦ Ask
      </button>
    )
  }

  return (
    <div style={{
      marginTop: 8,
      padding: '10px 12px',
      background: 'rgba(99,102,241,0.06)',
      borderRadius: 8,
      border: '1px solid var(--color-border)',
    }}>
      {/* Input state */}
      {!reply && !loading && (
        <>
          {suggestions.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
              {suggestions.map(s => (
                <button
                  key={s}
                  className="btn btn--secondary"
                  style={{ fontSize: '11px', padding: '3px 8px' }}
                  onClick={() => send(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              style={{
                flex: 1,
                fontSize: '13px',
                padding: '6px 10px',
                border: '1px solid var(--color-border)',
                borderRadius: 6,
                background: 'var(--color-surface)',
                color: 'var(--color-text)',
                outline: 'none',
              }}
              placeholder="Ask about this…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && input.trim() && send(input.trim())}
              autoFocus
            />
            <button
              className="btn btn--primary"
              style={{ padding: '6px 12px', fontSize: '13px' }}
              disabled={!input.trim()}
              onClick={() => send(input.trim())}
            >→</button>
            <button
              className="btn btn--ghost"
              style={{ padding: '6px 10px', fontSize: '13px' }}
              onClick={() => { setOpen(false); reset() }}
            >✕</button>
          </div>
        </>
      )}

      {/* Loading state */}
      {loading && (
        <div style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>Thinking…</div>
      )}

      {/* Error state */}
      {error && (
        <div style={{ fontSize: '13px', color: 'var(--color-error)' }}>
          {error}
          <button className="btn btn--ghost" style={{ marginLeft: 8, fontSize: '12px' }}
            onClick={() => { reset(); }}>Retry</button>
        </div>
      )}

      {/* Reply state */}
      {reply && (
        <div>
          <div style={{ fontSize: '13px', lineHeight: 1.5, marginBottom: action ? 8 : 0 }}>{reply}</div>
          {action && (
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
              <button
                className="btn btn--primary"
                style={{ fontSize: '12px', padding: '5px 12px' }}
                onClick={() => executeAction(action)}
              >
                {action.type === 'mark_item_done' ? '✓ Mark Done' :
                 action.type === 'reschedule_item' ? '📅 Reschedule' :
                 action.type === 'trigger_sync' ? '⟳ Sync Now' : 'Do It'}
              </button>
              <button className="btn btn--ghost" style={{ fontSize: '12px', padding: '5px 10px' }}
                onClick={() => reset()}>Dismiss</button>
            </div>
          )}
          {!action && (
            <button className="btn btn--ghost"
              style={{ fontSize: '12px', padding: '3px 8px', marginTop: 6 }}
              onClick={() => reset()}>✕ Close</button>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd web/spa && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/spa/src/components/InlineAgent.tsx
git commit -m "feat: InlineAgent collapsible AI chip component"
```

---

### Task 5: Wire into Child view (item rows)

**Files:**
- Modify: `web/spa/src/views/Child.tsx`

The current item row is a flat flex row. We need to change it to a column card so `InlineAgent` appears below the item content.

- [ ] **Step 1: Update the item card in `web/spa/src/views/Child.tsx`**

Add the import at the top:
```typescript
import { InlineAgent } from '../components/InlineAgent'
```

Replace the current item card `<div>` (the one with `className="card"` inside `items.map`) with:

```tsx
<div key={item.id} className="card" style={{ opacity: item.completed ? 0.5 : 1 }}>
  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
    <button
      onClick={() => patchMutation.mutate({ id: item.id, updates: { completed: !item.completed } })}
      style={{
        width: 24, height: 24, borderRadius: '50%',
        border: `2px solid ${item.completed ? 'var(--color-success)' : 'var(--color-border)'}`,
        background: item.completed ? 'var(--color-success)' : 'transparent',
        color: '#fff', flexShrink: 0,
      }}
    >{item.completed ? '✓' : ''}</button>
    <div style={{ flex: 1, minWidth: 0 }} onClick={() => setEditing(item)}>
      <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {item.title}
      </div>
      <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>
        {item.type} · {item.due_date ?? 'no due date'}
      </div>
    </div>
  </div>
  <InlineAgent
    contextType="item"
    contextId={String(item.id)}
    suggestions={['Explain this', 'Mark done', 'Reschedule']}
  />
</div>
```

- [ ] **Step 2: Build the SPA**

```bash
cd web/spa && npm run build
```

Expected: build completes with 0 errors (ignore any pre-existing dynamic import warnings).

- [ ] **Step 3: Commit**

```bash
git add web/spa/src/views/Child.tsx
git commit -m "feat: inline AI chip on homework item rows (Child view)"
```

---

### Task 6: Wire into Sync view (source rows)

**Files:**
- Modify: `web/spa/src/views/Sync.tsx`

The current source row is a flat flex row. We change it to a column card so `InlineAgent` appears below the row.

- [ ] **Step 1: Update the source card in `web/spa/src/views/Sync.tsx`**

Add the import at the top:
```typescript
import { InlineAgent } from '../components/InlineAgent'
```

Replace the current source card `<div>` (inside `SOURCES.map`) with:

```tsx
<div key={src.key} className="card">
  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
    <span style={{ fontSize: '24px' }}>{src.icon}</span>
    <div style={{ flex: 1 }}>
      <div style={{ fontWeight: 700 }}>{src.label}</div>
      <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>{src.description}</div>
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
      <span className={`badge ${badgeCls}`}>{age}</span>
      <button
        className="btn btn--secondary"
        style={{ padding: '6px 12px', fontSize: '12px' }}
        disabled={isRunning}
        onClick={() => handleSync(src.key)}
      >Pull</button>
    </div>
  </div>
  <InlineAgent
    contextType="sync_source"
    contextId={src.key}
    suggestions={['Why stale?', 'Sync now']}
  />
</div>
```

- [ ] **Step 2: Build the SPA**

```bash
cd web/spa && npm run build
```

Expected: build completes with 0 errors.

- [ ] **Step 3: Run full test suite**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
python -m pytest tests/ -q
```

Expected: 106 passed.

- [ ] **Step 4: Commit**

```bash
git add web/spa/src/views/Sync.tsx
git commit -m "feat: inline AI chip on sync source rows (Sync view)"
```

---

## Done

After Task 6, the inline agent layer is complete:
- `POST /api/agent/inline` handles `item` and `sync_source` context types
- `InlineAgent` component is live in Child view and Sync view
- Actions (mark done, reschedule, trigger sync) execute via existing API functions
- 106 tests passing
