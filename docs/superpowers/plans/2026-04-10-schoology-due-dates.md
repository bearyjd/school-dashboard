# Schoology Due Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Schoology dashboard tab to show urgency-colored dots, due date labels, sorted order, and a time filter bar — mirroring the Email Items tab.

**Architecture:** Two edits to `web/templates/index.html` only. `renderSchoology()` is rewritten to flatten assignments into a sortable list using the existing `dashDiff`/`dashDot`/`dashDueLabel` helpers and `dashTime` state. `renderDashList()` gets a one-character condition change to show the time bar for the schoology mode.

**Tech Stack:** Vanilla JS in a Flask/Jinja2 HTML template. No Python changes. No new dependencies.

---

### Task 1: Rewrite `renderSchoology()` and wire the time bar

**Files:**
- Modify: `web/templates/index.html` (lines ~304-322 for `renderSchoology`, line ~438 for `renderDashList`)

There is no pytest-testable Python in this change. Verification is done by running the Flask server and inspecting the Schoology tab in a browser.

---

- [ ] **Step 1: Read the current function to confirm line numbers**

Run:
```bash
grep -n "renderSchoology\|timeBar.style.display" web/templates/index.html
```

Expected output will include lines like:
```
304:function renderSchoology(){
438:  timeBar.style.display=(dashMode==='email')?'flex':'none';
```

Confirm the exact line numbers before editing.

---

- [ ] **Step 2: Replace `renderSchoology()` (lines ~304-322)**

Find this exact block in `web/templates/index.html`:

```javascript
function renderSchoology(){
  const data=dashChild==='all'?dashData.schoology:{[dashChild]:dashData.schoology[dashChild]||[]};
  let html='';
  for(const [child,asgns] of Object.entries(data)){
    if(!asgns||!asgns.length)continue;
    html+='<div class="dash-section">'+escHtml(child)+'</div>';
    html+=asgns.map(a=>{
      const inner='<span class="dash-dot" style="background:#3b82f6"></span>'+
        '<div class="dash-body">'+
          '<div class="dash-summary">'+escHtml(a.title)+'</div>'+
          (a.course?'<div class="dash-meta"><span class="dash-source">'+escHtml(a.course)+'</span></div>':'')+
        '</div>';
      return a.url
        ?'<a class="dash-item dash-item-link" href="'+escHtml(a.url)+'" target="_blank" rel="noopener">'+inner+'</a>'
        :'<div class="dash-item">'+inner+'</div>';
    }).join('');
  }
  return html||'<div class="dash-empty">No Schoology assignments.</div>';
}
```

Replace it with:

```javascript
function renderSchoology(){
  let items=[];
  for(const [child,asgns] of Object.entries(dashData.schoology)){
    if(!asgns)continue;
    asgns.forEach(a=>items.push(Object.assign({},a,{_child:child})));
  }
  if(dashChild!=='all') items=items.filter(i=>i._child===dashChild);
  if(dashTime==='today') items=items.filter(i=>{const d=dashDiff(i.due_date);return d!==null&&d<=0;});
  else if(dashTime==='week') items=items.filter(i=>{const d=dashDiff(i.due_date);return d!==null&&d<=7;});
  else if(dashTime==='month') items=items.filter(i=>{const d=dashDiff(i.due_date);return d!==null&&d<=31;});
  items=items.slice().sort((a,b)=>{
    const da=dashDiff(a.due_date),db=dashDiff(b.due_date);
    if(da===null&&db===null)return 0;
    if(da===null)return 1;if(db===null)return -1;
    return da-db;
  });
  if(!items.length)return '<div class="dash-empty">No Schoology assignments for this filter.</div>';
  return items.map(i=>{
    const diff=dashDiff(i.due_date);
    const inner='<span class="dash-dot" style="background:'+dashDot(diff)+'"></span>'+
      '<div class="dash-body">'+
        '<div class="dash-summary">'+escHtml(i.title)+'</div>'+
        '<div class="dash-meta">'+
          (i.course?'<span class="dash-source">'+escHtml(i.course)+'</span>':'')+
          dashDueLabel(i.due_date)+
        '</div>'+
      '</div>';
    return i.url
      ?'<a class="dash-item dash-item-link" href="'+escHtml(i.url)+'" target="_blank" rel="noopener">'+inner+'</a>'
      :'<div class="dash-item">'+inner+'</div>';
  }).join('');
}
```

---

- [ ] **Step 3: Update the time bar condition in `renderDashList()` (line ~438)**

Find this exact line:

```javascript
  timeBar.style.display=(dashMode==='email')?'flex':'none';
```

Replace with:

```javascript
  timeBar.style.display=(dashMode==='email'||dashMode==='schoology')?'flex':'none';
```

---

- [ ] **Step 4: Verify the Flask server is running and reload**

The live server is at `http://192.168.1.14:5000` (LXC deployment) or start locally with:

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
set -a && source config/env && set +a
python -m flask --app web/app.py run --port 5000
```

**Note:** Flask caches Jinja2 templates — after saving `index.html`, you must restart the Flask process for changes to appear. A hard browser refresh alone is not enough.

---

- [ ] **Step 5: Manual verification checklist**

Open `http://localhost:5000` (or `http://192.168.1.14:5000`) and click the **Schoology** tab.

Check each of the following:

1. **Time filter bar is visible** — the Today / Week / Month chips appear below the mode tabs
2. **Urgency dots are colored** — overdue items have red dots, due today/tomorrow amber, later blue/gray (not all flat blue)
3. **Due date labels appear** — each item shows "Overdue", "Today", "Tomorrow", "Fri", etc. in the meta line
4. **Sort order** — overdue items appear first, then soonest-due, no-due-date items at the bottom
5. **Filter works** — click "Today": only items due today or overdue remain; click "Week": items within 7 days; click "Month": items within 31 days
6. **Child filter still works** — select a child chip; only that child's assignments appear
7. **Links still work** — clicking an item with a URL opens it in a new tab
8. **No JS errors** — open browser DevTools console, confirm no errors on load or tab switch

---

- [ ] **Step 6: Commit**

```bash
git add web/templates/index.html
git commit -m "feat: show due dates and urgency on Schoology tab

- flatten assignments into sortable list (mirrors Email Items tab)
- urgency-colored dots via dashDot(dashDiff(due_date))
- due date labels via dashDueLabel(due_date)
- sort by due date ascending, no-due-date items last
- show time filter bar (Today/Week/Month) for schoology mode"
```
