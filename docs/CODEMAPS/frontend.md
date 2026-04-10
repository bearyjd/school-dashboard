<!-- Generated: 2026-04-10 | Files scanned: 34 | Token estimate: ~400 -->

# Frontend

## Template Tree

```
web/templates/index.html (114 lines)   ← Flask-served SPA
  ├─ Tab: Dashboard
  │    └─ <iframe src="/dashboard">   ← static HTML from state/
  │         └─ school_dashboard/templates/dashboard.html (248 lines)
  └─ Tab: Chat
       └─ Chat interface with marked.js markdown rendering
```

## `web/templates/index.html`

- **Tabs:** Dashboard (iframe) | Chat
- **Chat UI:** input box → POST `/api/chat` → streamed response
- **Markdown rendering:** `marked.js` (CDN, v9) — bot messages rendered as HTML
- **Styled for:** `.msg.bot` → p, h1-h3, ul/ol, strong, em, code, pre, blockquote, table

## `school_dashboard/templates/dashboard.html`

- Dark theme static dashboard (no JS framework)
- **Sections per child:** IXL diagnostics, SmartScore skills, Schoology assignments/grades
- **Action items:** overdue detection, due-date formatting
- **Data source:** school-state.json (rendered at sync time by `html.py`)

## Asset Dependencies

| Asset | Source | Purpose |
|-------|--------|---------|
| `marked.js` v9 | jsDelivr CDN | Markdown → HTML in chat |
| CSS | inline `<style>` | No external CSS framework |
