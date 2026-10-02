"""M11 — Personal dashboard (token-based, no login required).

Routes:
  GET  /d/{token}           → full HTML single-page app
  GET  /api/d/{token}       → JSON data, ?month=YYYY-MM
"""

from __future__ import annotations

import uuid
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import day_start, month_name, to_local, today_local
from alfred.dashboard_i18n import normalize_lang, payload, ui
from alfred.db import get_session
from alfred.labels import category_label
from alfred.models import SETTLED, Expense, Goal, HabitLog, HealthLog, Member, Note, Task
from alfred.panel_tokens import ensure_panel_token as ensure_dashboard_token  # noqa: F401
from alfred.panel_tokens import token_expired
from alfred.web_security import limit_dashboard

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["dashboard"])


# ── helpers ───────────────────────────────────────────────────────────────────


async def _get_member(token_str: str, session: AsyncSession) -> Member:
    try:
        token = uuid.UUID(token_str)
    except ValueError:
        raise HTTPException(status_code=404, detail="Dashboard not found") from None
    result = await session.execute(select(Member).where(Member.dashboard_token == token))
    member = result.scalar_one_or_none()
    if member is None or token_expired(member):
        raise HTTPException(status_code=404, detail="Dashboard not found") from None
    return member


def _parse_month(raw: str | None, today: date) -> tuple[int, int]:
    """``YYYY-MM`` → (year, month); anything malformed or out of range → current month."""
    if raw:
        try:
            year_s, month_s = raw.split("-")
            year, month = int(year_s), int(month_s)
            if 2000 <= year <= today.year + 1 and 1 <= month <= 12:
                return year, month
        except ValueError:
            pass
    return today.year, today.month


# ── JSON API ──────────────────────────────────────────────────────────────────


@router.get("/api/d/{token}", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def dashboard_api(
    token: str,
    month: str | None = Query(None, description="YYYY-MM"),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    member = await _get_member(token, session)
    lang = normalize_lang(member.language)

    today = today_local()
    sel_year, sel_month = _parse_month(month, today)

    month_start = date(sel_year, sel_month, 1)
    _, last_day = monthrange(sel_year, sel_month)
    month_end = date(sel_year, sel_month, last_day)
    # [start, end) in the user's timezone — index-friendly, unlike func.date(...)
    month_lo, month_hi = day_start(month_start), day_start(month_end + timedelta(days=1))

    # ── Financial ─────────────────────────────────────────────────────────────
    exp_q = await session.execute(
        select(Expense)
        .where(
            and_(
                Expense.member_id == member.id,
                Expense.status.in_(SETTLED),
                Expense.expense_date >= month_lo,
                Expense.expense_date < month_hi,
            )
        )
        .order_by(Expense.expense_date.desc())
    )
    month_txs = exp_q.scalars().all()

    total_expense = round(sum(e.amount for e in month_txs if e.transaction_type == "expense"), 2)
    total_income = round(sum(e.amount for e in month_txs if e.transaction_type == "income"), 2)
    balance = round(total_income - total_expense, 2)

    cat_totals: dict[str, float] = defaultdict(float)
    for e in month_txs:
        if e.transaction_type == "expense":
            cat_totals[e.category or "overig"] += e.amount

    # Monthly history — last 12 months
    history: list[dict] = []
    for i in range(11, -1, -1):
        m = sel_month - i
        y = sel_year
        while m <= 0:
            m += 12
            y -= 1
        ms = date(y, m, 1)
        _, ld = monthrange(y, m)
        me = date(y, m, ld)
        hq = await session.execute(
            select(Expense).where(
                and_(
                    Expense.member_id == member.id,
                    Expense.status.in_(SETTLED),
                    Expense.expense_date >= day_start(ms),
                    Expense.expense_date < day_start(me + timedelta(days=1)),
                )
            )
        )
        h = hq.scalars().all()
        history.append(
            {
                "month": f"{y:04d}-{m:02d}",
                "label": f"{month_name(ms, lang)[:3]} {ms.year % 100:02d}",
                "total_expense": round(
                    sum(e.amount for e in h if e.transaction_type == "expense"), 2
                ),
                "total_income": round(
                    sum(e.amount for e in h if e.transaction_type == "income"), 2
                ),
            }
        )

    recent_transactions = [
        {
            "date": to_local(e.expense_date).strftime("%d/%m") if e.expense_date else "",
            "merchant": e.merchant or "-",
            "category": category_label(e.category, lang),
            "amount": round(e.amount, 2),
            "type": e.transaction_type,
        }
        for e in month_txs[:20]
    ]

    # ── Goals ──────────────────────────────────────────────────────────────────
    gq = await session.execute(
        select(Goal)
        .where(Goal.member_id == member.id, Goal.active.is_(True))
        .order_by(Goal.deadline.asc().nullslast(), Goal.created_at.asc())
    )
    goals = [
        {
            "title": g.title,
            "target": (
                f"{g.target_value} {g.target_unit or ''}".strip() if g.target_value else None
            ),
            "deadline": g.deadline.isoformat() if g.deadline else None,
        }
        for g in gq.scalars().all()
    ]

    # ── Habits / streaks ───────────────────────────────────────────────────────
    thirty_ago = today - timedelta(days=30)
    hbq = await session.execute(
        select(HabitLog)
        .where(
            and_(
                HabitLog.member_id == member.id,
                HabitLog.log_date >= thirty_ago,
            )
        )
        .order_by(HabitLog.log_date.desc())
    )
    activity_dates: dict[str, set[date]] = defaultdict(set)
    for h in hbq.scalars().all():
        activity_dates[h.activity].add(h.log_date)

    habits: list[dict] = []
    for activity, dates_set in activity_dates.items():
        last7 = [(today - timedelta(days=i)) in dates_set for i in range(6, -1, -1)]
        streak = 0
        d = today
        while d in dates_set:
            streak += 1
            d -= timedelta(days=1)
        habits.append({"activity": activity, "streak": streak, "last7": last7})
    habits.sort(key=lambda x: -x["streak"])

    # ── Tasks ──────────────────────────────────────────────────────────────────
    tq = await session.execute(
        select(Task)
        .where(Task.member_id == member.id, Task.done_at.is_(None))
        .order_by(Task.due_date.asc().nullslast(), Task.created_at.asc())
        .limit(20)
    )
    tasks = [
        {
            "body": t.body,
            "due_date": t.due_date.isoformat() if t.due_date else None,
        }
        for t in tq.scalars().all()
    ]

    # ── Notes ──────────────────────────────────────────────────────────────────
    nq = await session.execute(
        select(Note).where(Note.member_id == member.id).order_by(Note.created_at.desc()).limit(6)
    )
    notes = [
        {"body": n.body, "date": to_local(n.created_at).strftime("%d/%m/%Y")}
        for n in nq.scalars().all()
    ]

    # ── Health ─────────────────────────────────────────────────────────────────
    hlq = await session.execute(
        select(HealthLog.log_type, func.count().label("cnt"))
        .where(
            and_(
                HealthLog.member_id == member.id,
                HealthLog.log_date >= month_start,
                HealthLog.log_date <= month_end,
            )
        )
        .group_by(HealthLog.log_type)
    )
    health = [{"type": row.log_type, "count": row.cnt} for row in hlq.all()]

    return JSONResponse(
        {
            "member_name": member.preferred_name or member.display_name or ui(lang)["user"],
            "month": f"{sel_year:04d}-{sel_month:02d}",
            "total_expense": round(total_expense, 2),
            "total_income": round(total_income, 2),
            "balance": round(balance, 2),
            "expenses_by_category": [
                {"category": category_label(k, lang), "total": round(v, 2)}
                for k, v in sorted(cat_totals.items(), key=lambda x: -x[1])
            ],
            "monthly_history": history,
            "recent_transactions": recent_transactions,
            "goals": goals,
            "habits": habits,
            "tasks": tasks,
            "notes": notes,
            "health": health,
        }
    )


@router.get(
    "/api/d/{token}/export", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def dashboard_export(
    token: str,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """GDPR access/portability: everything stored about the member, as a JSON download."""
    from alfred.privacy import export_member_data

    member = await _get_member(token, session)
    audit(session, "data_exported", member.id)
    return JSONResponse(
        await export_member_data(session, member),
        headers={"Content-Disposition": 'attachment; filename="alfred-data.json"'},
    )


# ── HTML page ─────────────────────────────────────────────────────────────────


@router.get("/d/{token}", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def dashboard_page(
    token: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    member = await _get_member(token, session)
    lang = normalize_lang(member.language)
    page = (
        _HTML_TEMPLATE.replace("__TOKEN__", token)
        .replace("__LANG__", lang)
        .replace("__I18N__", payload(lang))
        .replace("__TITLE__", ui(lang)["title"])
    )
    return HTMLResponse(page)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="__LANG__">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0,viewport-fit=cover"/>
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js" integrity="sha384-bs/nf9FbdNouRbMiFcrcZfLXYPKiPaGVGplVbv7dLGECccEXDW+S3zjqSKR5ZEaD" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<style>
:root{
  --bg:#0f1117;--surface:#1a1d27;--surface2:#232635;
  --accent:#6c63ff;--accent2:#48e5c2;
  --red:#f4647a;--green:#48e5c2;--yellow:#f9c74f;
  --text:#e8eaf0;--muted:#8b90a0;--border:#2c3050;
  --radius:12px;--gap:16px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg);color:var(--text);
  font-family:'Segoe UI',system-ui,sans-serif;
  font-size:15px;line-height:1.5;min-height:100vh;
  padding-bottom:env(safe-area-inset-bottom,0px);
}
header{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:12px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  position:sticky;top:env(safe-area-inset-top,0);z-index:10;
}
header h1{font-size:1.1rem;font-weight:700;color:var(--accent)}
header h1 span{color:var(--text);font-weight:400;font-size:.9rem;margin-left:4px}
.month-nav{margin-left:auto;display:flex;align-items:center;gap:6px}
.month-nav button{
  background:var(--surface2);border:1px solid var(--border);
  color:var(--text);border-radius:8px;padding:8px 16px;cursor:pointer;
  font-size:.9rem;min-height:40px;min-width:40px;touch-action:manipulation;
  -webkit-tap-highlight-color:transparent;
}
.month-nav button:hover,.month-nav button:active{
  background:var(--accent);border-color:var(--accent);
}
#month-label{font-weight:600;min-width:80px;text-align:center;font-size:.9rem}
main{max-width:1100px;margin:0 auto;padding:16px var(--gap)}
.grid{display:grid;gap:var(--gap)}
.row-3{grid-template-columns:repeat(3,1fr)}
.row-2{grid-template-columns:1fr 1fr}
.row-1{grid-template-columns:1fr}
@media(max-width:700px){.row-3,.row-2{grid-template-columns:1fr}}
.card{
  background:var(--surface);border-radius:var(--radius);
  border:1px solid var(--border);padding:16px;
}
.card-title{
  font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);margin-bottom:8px;
}
.stat-val{font-size:1.85rem;font-weight:700;word-break:break-all}
@media(max-width:400px){.stat-val{font-size:1.5rem}}
.stat-val.red{color:var(--red)}
.stat-val.green{color:var(--green)}
.stat-val.neutral{color:var(--accent)}
.stat-sub{font-size:.8rem;color:var(--muted);margin-top:2px}

/* Chart containers — height via CSS so Chart.js fills them */
.chart-wrap{position:relative;height:240px}
@media(max-width:700px){.chart-wrap{height:200px}}
@media(max-width:400px){.chart-wrap{height:175px}}

.tx-list{
  display:flex;flex-direction:column;gap:7px;margin-top:8px;
  max-height:340px;overflow-y:auto;
}
.tx-row{
  display:grid;grid-template-columns:36px 1fr auto;
  align-items:center;gap:8px;padding:9px 10px;
  background:var(--surface2);border-radius:8px;
}
@media(max-width:400px){
  .tx-row{grid-template-columns:30px 1fr auto;gap:6px;padding:7px 8px}
}
.tx-date{font-size:.72rem;color:var(--muted);text-align:center;line-height:1.3}
.tx-info{overflow:hidden;min-width:0}
.tx-merchant{
  font-weight:600;font-size:.88rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.tx-cat{font-size:.72rem;color:var(--muted)}
.tx-amt{font-weight:700;font-size:.9rem;white-space:nowrap}
.tx-amt.expense{color:var(--red)}
.tx-amt.income{color:var(--green)}
.goal-item,.task-item,.note-item{
  padding:10px 12px;background:var(--surface2);
  border-radius:8px;margin-top:8px;
}
.goal-title,.task-body{font-weight:600;font-size:.88rem}
.goal-meta,.task-due,.note-date{font-size:.75rem;color:var(--muted);margin-top:2px}
.habit-row{
  display:flex;align-items:center;gap:8px;padding:9px 0;
  border-bottom:1px solid var(--border);flex-wrap:wrap;
}
.habit-row:last-child{border-bottom:none}
.habit-name{flex:1;min-width:80px;font-size:.88rem;font-weight:600}
.habit-streak{font-size:.78rem;color:var(--accent2);font-weight:700;white-space:nowrap}
.habit-dots{display:flex;gap:4px;margin-left:auto}
@media(max-width:400px){
  .habit-name{width:100%}
  .habit-dots{margin-left:0}
}
.dot{
  width:13px;height:13px;border-radius:50%;
  background:var(--surface2);border:1px solid var(--border);
}
.dot.on{background:var(--accent2);border-color:var(--accent2)}
.health-chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{
  padding:5px 12px;border-radius:20px;
  background:var(--surface2);border:1px solid var(--border);font-size:.82rem;
}
.chip span{font-weight:700;color:var(--accent2)}
.empty{color:var(--muted);font-size:.85rem;padding:12px 0}
#loading{
  position:fixed;inset:0;background:var(--bg);
  display:flex;align-items:center;justify-content:center;
  font-size:1.1rem;color:var(--muted);z-index:100;
}
</style>
</head>
<body>
<div id="loading"></div>
<header>
  <h1>Alfred <span id="header-name"></span></h1>
  <div class="month-nav">
    <button id="prev-btn" data-aria="prev_month">&#8592;</button>
    <span id="month-label">—</span>
    <button id="next-btn" data-aria="next_month">&#8594;</button>
  </div>
</header>
<main>
  <div class="grid row-3" style="margin-bottom:var(--gap)">
    <div class="card">
      <div class="card-title" data-i18n="spent"></div>
      <div class="stat-val red" id="v-expense">—</div>
      <div class="stat-sub" id="v-expense-sub"></div>
    </div>
    <div class="card">
      <div class="card-title" data-i18n="income"></div>
      <div class="stat-val green" id="v-income">—</div>
    </div>
    <div class="card">
      <div class="card-title" data-i18n="balance"></div>
      <div class="stat-val neutral" id="v-balance">—</div>
    </div>
  </div>

  <div class="grid row-2" style="margin-bottom:var(--gap)">
    <div class="card">
      <div class="card-title" data-i18n="by_category"></div>
      <div class="chart-wrap"><canvas id="chart-donut"></canvas></div>
    </div>
    <div class="card">
      <div class="card-title" data-i18n="monthly_history"></div>
      <div class="chart-wrap"><canvas id="chart-bar"></canvas></div>
    </div>
  </div>

  <div class="grid row-2" style="margin-bottom:var(--gap)">
    <div class="card">
      <div class="card-title" data-i18n="recent_tx"></div>
      <div class="tx-list" id="tx-list"></div>
    </div>
    <div class="grid row-1" style="gap:var(--gap)">
      <div class="card">
        <div class="card-title" data-i18n="goals"></div>
        <div id="goals-list"></div>
      </div>
      <div class="card">
        <div class="card-title" data-i18n="health_month"></div>
        <div class="health-chips" id="health-chips"></div>
      </div>
    </div>
  </div>

  <div class="grid row-2" style="margin-bottom:var(--gap)">
    <div class="card">
      <div class="card-title" data-i18n="habits"></div>
      <div id="habits-list"></div>
    </div>
    <div class="card">
      <div class="card-title" data-i18n="pending_tasks"></div>
      <div id="tasks-list"></div>
    </div>
  </div>

  <div class="grid row-1">
    <div class="card">
      <div class="card-title" data-i18n="recent_notes"></div>
      <div id="notes-list"></div>
    </div>
  </div>
</main>

<script>
const TOKEN = "__TOKEN__";
let currentMonth = "";
let donutChart = null;
let barChart = null;
let lastData = null;

const I18N = __I18N__;
const T = I18N.t;
const MONTH_NAMES = I18N.months;
const EUR = new Intl.NumberFormat(I18N.locale, {style:"currency", currency:"EUR"});
document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = T[el.dataset.i18n]; });
document.querySelectorAll("[data-aria]").forEach(el => { el.setAttribute("aria-label", T[el.dataset.aria]); });
document.getElementById("loading").textContent = T.loading;
const CAT_COLORS = [
  "#6c63ff","#48e5c2","#f4647a","#f9c74f","#4cc9f0",
  "#7b2d8b","#ff9f43","#00b4d8","#06d6a0","#ef476f",
];

function fmtEur(v){
  return EUR.format(v);
}
function fmtMonth(ym){
  const [y,m] = ym.split("-");
  return MONTH_NAMES[parseInt(m)-1] + " " + y;
}
function addSubtractMonth(ym, delta){
  let [y,m] = ym.split("-").map(Number);
  m += delta;
  if(m > 12){m=1; y++;}
  if(m < 1){m=12; y--;}
  return `${y}-${String(m).padStart(2,"0")}`;
}
function isMobile(){ return window.innerWidth < 600; }

async function load(month){
  const url = `/api/d/${TOKEN}${month ? "?month="+month : ""}`;
  const res = await fetch(url);
  if(!res.ok){
    document.getElementById("loading").textContent = T.not_found;
    return;
  }
  const d = await res.json();
  currentMonth = d.month;
  lastData = d;
  render(d);
  document.getElementById("loading").style.display = "none";
}

function render(d){
  document.getElementById("header-name").textContent = "· " + d.member_name;
  document.getElementById("month-label").textContent = fmtMonth(d.month);

  document.getElementById("v-expense").textContent = fmtEur(d.total_expense);
  document.getElementById("v-expense-sub").textContent =
    d.expenses_by_category.length > 0 ? `${d.expenses_by_category.length} ${d.expenses_by_category.length === 1 ? T.category_one : T.categories}` : "";
  document.getElementById("v-income").textContent = fmtEur(d.total_income);
  const balEl = document.getElementById("v-balance");
  balEl.textContent = fmtEur(d.balance);
  balEl.className = "stat-val " + (d.balance >= 0 ? "green" : "red");

  renderDonut(d.expenses_by_category);
  renderBar(d.monthly_history);

  // Transactions
  const txList = document.getElementById("tx-list");
  if(d.recent_transactions.length === 0){
    txList.innerHTML = '<div class="empty">' + esc(T.empty_tx) + '</div>';
  } else {
    txList.innerHTML = d.recent_transactions.map(t => `
      <div class="tx-row">
        <div class="tx-date">${esc(t.date)}</div>
        <div class="tx-info">
          <div class="tx-merchant">${esc(t.merchant)}</div>
          <div class="tx-cat">${esc(t.category)}</div>
        </div>
        <div class="tx-amt ${t.type==="income"?"income":"expense"}">${t.type==="income"?"+":"−"}${fmtEur(t.amount)}</div>
      </div>`).join("");
  }

  // Goals
  const goalsList = document.getElementById("goals-list");
  if(d.goals.length === 0){
    goalsList.innerHTML = '<div class="empty">' + esc(T.empty_goals) + '</div>';
  } else {
    goalsList.innerHTML = d.goals.map(g => `
      <div class="goal-item">
        <div class="goal-title">${esc(g.title)}</div>
        <div class="goal-meta">${g.target ? esc(g.target) : ""}${g.deadline ? " · " + esc(g.deadline) : ""}</div>
      </div>`).join("");
  }

  // Habits
  const habitsList = document.getElementById("habits-list");
  if(d.habits.length === 0){
    habitsList.innerHTML = '<div class="empty">' + esc(T.empty_habits) + '</div>';
  } else {
    habitsList.innerHTML = d.habits.map(h => `
      <div class="habit-row">
        <div class="habit-name">${esc(h.activity)}</div>
        <div class="habit-streak">${h.streak}${esc(T.day_short)} 🔥</div>
        <div class="habit-dots">${h.last7.map(on=>`<div class="dot${on?" on":""}"></div>`).join("")}</div>
      </div>`).join("");
  }

  // Tasks
  const tasksList = document.getElementById("tasks-list");
  if(d.tasks.length === 0){
    tasksList.innerHTML = '<div class="empty">' + esc(T.empty_tasks) + '</div>';
  } else {
    tasksList.innerHTML = d.tasks.map(t => `
      <div class="task-item">
        <div class="task-body">${esc(t.body)}</div>
        ${t.due_date ? `<div class="task-due">📅 ${esc(t.due_date)}</div>` : ""}
      </div>`).join("");
  }

  // Notes
  const notesList = document.getElementById("notes-list");
  if(d.notes.length === 0){
    notesList.innerHTML = '<div class="empty">' + esc(T.empty_notes) + '</div>';
  } else {
    notesList.innerHTML = d.notes.map(n => `
      <div class="note-item">
        <div class="tx-merchant">${esc(n.body)}</div>
        <div class="note-date">${esc(n.date)}</div>
      </div>`).join("");
  }

  // Health
  const chips = document.getElementById("health-chips");
  if(d.health.length === 0){
    chips.innerHTML = '<div class="empty">' + esc(T.empty_health) + '</div>';
  } else {
    const labels = {medication:"💊 "+T.medication,mood:"😊 "+T.mood,sleep:"😴 "+T.sleep,water:"💧 "+T.water};
    chips.innerHTML = d.health.map(h => `
      <div class="chip">${esc(labels[h.type]||h.type)}: <span>${Number(h.count)}${esc(T.times)}</span></div>`).join("");
  }
}

function renderDonut(cats){
  const ctx = document.getElementById("chart-donut").getContext("2d");
  if(donutChart) donutChart.destroy();
  if(cats.length === 0){ ctx.clearRect(0,0,ctx.canvas.width,ctx.canvas.height); return; }
  const mob = isMobile();
  donutChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: cats.map(c => c.category),
      datasets: [{
        data: cats.map(c => c.total),
        backgroundColor: cats.map((_,i) => CAT_COLORS[i % CAT_COLORS.length]),
        borderWidth: 2,
        borderColor: "#1a1d27",
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: mob ? 4 : 0 },
      plugins: {
        legend: {
          position: mob ? "bottom" : "right",
          labels: {
            color: "#e8eaf0",
            font: { size: mob ? 10 : 11 },
            boxWidth: 10,
            padding: mob ? 8 : 12,
          }
        },
        tooltip: {
          callbacks: { label: ctx => " " + ctx.label + ": " + fmtEur(ctx.parsed) }
        }
      }
    }
  });
}

function renderBar(history){
  const ctx = document.getElementById("chart-bar").getContext("2d");
  if(barChart) barChart.destroy();
  const mob = isMobile();
  // On mobile show last 6 months to avoid label cramping
  const data = mob ? history.slice(-6) : history;
  barChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map(h => h.label),
      datasets: [
        {
          label: T.spent,
          data: data.map(h => h.total_expense),
          backgroundColor: "rgba(244,100,122,0.7)",
          borderRadius: 4,
        },
        {
          label: T.income,
          data: data.map(h => h.total_income),
          backgroundColor: "rgba(72,229,194,0.7)",
          borderRadius: 4,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: {
            color: "#8b90a0",
            font: { size: mob ? 10 : 10 },
            maxRotation: mob ? 45 : 0,
            minRotation: mob ? 45 : 0,
          },
          grid: { color: "#2c3050" }
        },
        y: {
          ticks: {
            color: "#8b90a0",
            font: { size: mob ? 9 : 10 },
            callback: v => "€" + v,
          },
          grid: { color: "#2c3050" }
        }
      },
      plugins: {
        legend: {
          labels: { color: "#e8eaf0", font: { size: mob ? 10 : 11 } }
        },
        tooltip: {
          callbacks: {
            label: ctx => " " + ctx.dataset.label + ": " + fmtEur(ctx.parsed.y)
          }
        }
      }
    }
  });
}

function esc(s){
  if(!s) return "";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// Re-render charts on resize / orientation change
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if(lastData){
      renderDonut(lastData.expenses_by_category);
      renderBar(lastData.monthly_history);
    }
  }, 250);
});

document.getElementById("prev-btn").addEventListener("click", () => {
  load(addSubtractMonth(currentMonth, -1));
});
document.getElementById("next-btn").addEventListener("click", () => {
  const next = addSubtractMonth(currentMonth, 1);
  const today = new Date();
  const todayYM = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,"0")}`;
  if(next <= todayYM) load(next);
});

load();
</script>
</body>
</html>"""
