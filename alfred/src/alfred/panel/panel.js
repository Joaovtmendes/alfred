(() => {
  "use strict";
  // Everything the user owns reaches the DOM through textContent / setAttribute; never as HTML.
  // Dynamic sizes go through the CSSOM (element.style.x = ...), which the nonce CSP allows.
  // The panel is read only: nothing here sends data back; filters only change the page address.
  const cfg = JSON.parse(document.getElementById("panel-config").textContent);
  const T = cfg.t;
  const LANG = { pt: "pt-BR", nl: "nl-NL", en: "en-GB", fr: "fr-FR", de: "de-DE" }[cfg.lang] || "en-GB";
  const fmtNum = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtInt = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
  const money = (v) => (v < 0 ? "− " : "") + "€ " + fmtNum.format(Math.abs(v));
  const money0 = (v) => (v < 0 ? "− " : "") + "€ " + fmtInt.format(Math.abs(v));
  const signed = (v) => (v > 0 ? "+ " + money(v) : money(v));
  const NS = "http://www.w3.org/2000/svg";
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // A sign, the euro mark and the number never split across lines: "− € 5,00" is one word.
  const nb = (t) => t.replace(/([−+]) €/g, "$1\u00a0€").replace(/€ (?=\d)/g, "€\u00a0");
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = nb(String(text));
    return n;
  }
  function svg(tag, attrs) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    return n;
  }
  const fmt = (tpl, vars) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  // singular text when the count is exactly 1 ("há 1 dia"): key + "_one", falling back to the plural
  const fmtN = (key, n, vars) => fmt(n === 1 && T[key + "_one"] ? T[key + "_one"] : T[key], { n, ...vars });
  const quote = (chat) => "“" + String(chat).replace(/^"|"$/g, "") + "”";
  const lc = (s) => (cfg.lang === "de" ? s : s.toLowerCase());
  const pctText = (v) => fmtInt.format(Math.abs(Math.round(v))) + "%";

  // ── dates (days arrive as local ISO days; they are never shifted by a time zone) ──
  const day = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const dtf = (opts) => new Intl.DateTimeFormat(LANG, { timeZone: "UTC", ...opts });
  const F_SHORT = dtf({ month: "short" }), F_LONG = dtf({ month: "long" }), F_MY = dtf({ month: "long", year: "numeric" });
  const cap1 = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const monthShort = (iso) => F_SHORT.format(day(iso)).replace(/\.$/, "");
  const monthWord = (iso) => { const w = F_LONG.format(day(iso)); return cfg.lang === "de" || cfg.lang === "en" ? w : w.toLowerCase(); };
  const monthTitle = (ym) => cap1(F_MY.format(day(ym + "-01")));
  const dm = (iso) => iso.slice(8, 10) + "/" + iso.slice(5, 7);
  const dayShort = (iso) => String(Number(iso.slice(8, 10))) + " " + monthShort(iso);
  const shiftDay = (iso, by) => { const d = day(iso); d.setUTCDate(d.getUTCDate() + by); return d.toISOString().slice(0, 10); };
  const fmt1 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmtL = (v) => fmt1.format(v) + " L";
  const fmtKg = (v) => new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 }).format(v) + " kg";

  // ── building blocks ──
  function chatIcon() {
    const s = svg("svg", { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    s.append(svg("path", { d: "M21 11.5a8.4 8.4 0 0 1-12.4 7.4L3 21l2.1-5.4A8.4 8.4 0 1 1 21 11.5z" }));
    return s;
  }
  function chip(parent, chat) {
    if (!chat) return;
    const c = el("div", "chip");
    c.append(chatIcon(), document.createTextNode(T.ask_in_chat + ": " + quote(chat)));
    parent.append(c);
  }

  function cardShell(card, label, span) {
    const c = el("section", "card " + (span || "s12"));
    c.dataset.card = card.id;
    const l = el("div", "label");
    if (label instanceof Node) l.append(label); else l.textContent = String(label);
    c.append(l);
    return c;
  }

  function heroMoney(parent, value, big) {
    const [whole, cents] = money(value).split(",");
    const h = el("div", "hero" + (big ? " xl" : ""), whole);
    h.append(el("small", "", "," + cents));
    parent.append(h);
    return h;
  }

  function barRow(parent, { label, value, pct, tone }) {
    const row = el("div", "row");
    const head = el("div", "");
    head.append(el("span", "", label + " · "), el("span", "num", value));
    row.append(head);
    const bar = el("div", "bar");
    const fill = el("span", tone === "warm" ? "warm" : "");
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    bar.append(fill);
    row.append(bar);
    parent.append(row);
  }

  function sparkline(parent, values, label) {
    if (!values.length) return;
    const w = 120, h = 30, pad = 3;
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => [
      pad + (i * (w - 2 * pad)) / Math.max(values.length - 1, 1),
      h - pad - ((v - lo) / span) * (h - 2 * pad),
    ]);
    const s = svg("svg", { viewBox: "0 0 " + w + " " + h, width: w, height: h, role: "img", "aria-label": label + ": " + values.join(", ") });
    s.append(svg("polyline", { points: pts.map((p) => p.join(",")).join(" "), fill: "none", stroke: "var(--accent)", "stroke-width": 2 }));
    parent.append(s);
  }

  // parts: [{label, value, text, tone: ""|"warm"|"hatch"|"good"}]; opts.total sits UNDER the legend.
  function stackedBar(parent, parts, opts) {
    const sum = parts.reduce((a, p) => a + Math.abs(p.value), 0) || 1;
    const bar = el("div", "bar stacked");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", parts.map((p) => p.label + " " + p.text).join(", "));
    for (const p of parts) {
      const seg = el("span", p.tone || "");
      seg.style.flex = Math.abs(p.value) / sum + " 1 0";
      bar.append(seg);
    }
    parent.append(bar);
    const legend = el("ul", "legend");
    for (const p of parts) {
      const li = el("li");
      li.append(el("b", p.tone || ""), el("span", "", p.label), el("span", "num", p.text));
      legend.append(li);
    }
    parent.append(legend);
    if (opts && opts.total) {
      const t = el("div", "total " + (opts.total.cls || ""));
      const l = el("span", "", opts.total.label);
      if (opts.total.note) l.append(document.createElement("br"), document.createTextNode(opts.total.note));
      t.append(l, el("span", "num", opts.total.text));
      parent.append(t);
    }
  }

  // Small column chart of {date, value}: one bar per point, the biggest one warm unless ``flat``;
  // ``max`` fixes the scale (mood is out of 10).
  function miniBars(parent, pts, label, max, flat) {
    const chart = el("div", "daily mini");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", label + ": " + pts.map((p) => dm(p.date) + " " + p.value).join(", "));
    const top = max || Math.max(...pts.map((p) => p.value), 0) || 1;
    for (const p of pts) {
      const b = el("i", "");
      b.style.height = (p.value > 0 ? Math.max(4, (p.value / top) * 100) : 0) + "%";
      chart.append(b);
    }
    parent.append(chart);
    const axis = el("div", "axis");
    axis.setAttribute("aria-hidden", "true");
    if (pts.length) {
      axis.append(el("span", "", dm(pts[0].date)));
      const last = el("span", "last", dm(pts[pts.length - 1].date));
      axis.append(last);
    }
    parent.append(axis);
  }

  function phraseBlock(parent, phrase) {
    if (!phrase) return;
    const p = el("div", "phrase", phrase.text);
    p.dataset.severity = phrase.severity || "info";
    chip(p, phrase.chat);
    parent.append(p);
  }

  function track(pct, cls, thick) {
    const t = el("div", "track" + (thick ? " thick" : ""));
    t.setAttribute("aria-hidden", "true");
    const f = el("i", cls || "");
    f.style.width = Math.max(0, Math.min(100, pct)) + "%";
    t.append(f);
    return t;
  }

  // "▲ 8%" (rising spending is the warm one), "▼ 11%", "= igual", "novo"
  function deltaText(it) {
    if (it.trend === "new") return T.cat_new;
    if (it.trend === "flat" || !it.delta) return T.cat_equal;
    return (it.trend === "up" ? "▲ " : "▼ ") + (it.delta_pct === null || it.delta_pct === undefined ? "" : pctText(it.delta_pct));
  }
  function deltaSpan(it) {
    const cls = it.trend === "up" && it.delta ? "up" : (it.trend === "down" && it.delta ? "down" : "flat");
    return el("span", cls, deltaText(it));
  }

  function cmpText(cmp, kind) {
    if (!cmp || !cmp[kind]) return "";
    const month = monthWord(cmp.start), c = cmp[kind];
    if (c.pct === null || c.pct === undefined) return fmt(T.cmp_none, { month });
    if (Math.abs(c.pct) < 0.5 || !c.delta) return fmt(T.cmp_same, { month });
    return fmt(c.delta > 0 ? T.cmp_above : T.cmp_below, { pct: fmtInt.format(Math.abs(Math.round(c.pct))), month });
  }

  // ── cards ──
  const TITLES = {
    balance: "title_balance", upcoming: "title_upcoming", categories: "title_categories", budgets: "title_budgets",
    owed: "title_owed", transactions: "title_tx", top_expenses: "title_top",
    month_vs_month: "title_mom", fixed_variable: "title_fixed", daily: "title_daily",
    recurring: "title_recurring",
    week: "title_week", tasks: "title_tasks", reminders: "title_reminders", month_map: "title_map", notes: "title_notes",
    water: "title_water", workouts: "title_workouts",
    goals: "title_goals", trip: "title_trip",
    training: "title_training", itinerary: "title_itinerary", packing: "title_packing", plan_budget: "title_planbudget",
  };
  const SPANS = {
    summary: { balance: "s7", categories: "s5", goals: "s5", week: "s7", upcoming: "s12" },
    agenda: { week: "s7", tasks: "s5", month_map: "s7", reminders: "s5", notes: "s12" },
    health: { goals: "s7", workouts: "s5", training: "s12", water: "s12" },
    trips: { trip: "s7", packing: "s5", itinerary: "s7", plan_budget: "s5", trips_past: "s12" },
    money: { transactions: "s7", month_vs_month: "s5", top_expenses: "s5", categories: "s7", budgets: "s7", fixed_variable: "s5", owed: "s5", daily: "s7", recurring: "s12" },
  };

  function emptyCard(card, span) {
    const c = cardShell(card, T[TITLES[card.id]] || "", span);
    const h = el("div", "hint");
    h.append(el("div", "empty", (card.hint && card.hint.text) || T.empty));
    if (card.hint) chip(h, card.hint.chat);
    c.append(h);
    return c;
  }

  function prevMonthOf(body) {
    for (const c of body.cards) if (c.compare && c.compare.start) return c.compare.start;
    return null;
  }

  const renderers = {
    balance(card, span) {
      const v = card.values, proj = card.projection || { available: false };
      const withProj = proj.available;
      let label = T.title_balance;
      if (withProj) {
        label = document.createDocumentFragment();
        label.append(el("span", "dsk", T.title_balance_proj), el("span", "mob", T.title_balance));
      }
      const c = cardShell(card, label, span);
      const top = el("div", "balance-top");
      const left = el("div");
      heroMoney(left, v.balance, true);
      left.append(el("div", "sub dsk", T.balance_realized));
      top.append(left);
      const estimate = withProj && proj.sufficient && proj.projected !== null && proj.projected !== undefined;
      const range = estimate && proj.low !== null && proj.high !== null;
      const rangeText = range ? fmt(T.proj_range, { low: money0(proj.low), high: money0(proj.high) }) : "";
      if (estimate) {
        const side = el("div", "proj-side");
        side.append(el("div", "big-num", "~ " + money0(proj.projected)));
        side.append(el("div", "sub", fmt(range ? T.proj_estimate : T.proj_estimate_plain, { date: dm(proj.month_end), low: money0(proj.low), high: money0(proj.high) })));
        top.append(side);
      }
      c.append(top);

      const tiles = el("div", "tiles");
      for (const [kind, name, val, arrow, cls, pre] of [
        ["income", T.income, v.income, "↑", "in", T.income_received],
        ["expense", T.expense, v.expense, "↓", "out", T.expense_paid],
      ]) {
        const t = el("div", "tile");
        const k = el("div", "k", name + " ");
        const a = el("span", "arrow " + cls, arrow);
        a.setAttribute("aria-hidden", "true");
        k.append(a);
        const s = el("div", "s");
        const cmp = cmpText(card.compare, kind);
        s.append(el("span", "dsk", cmp ? pre + " · " : pre), document.createTextNode(cmp));
        t.append(k, el("div", "big-num", money(val)), s);
        tiles.append(t);
      }
      c.append(tiles);

      if (withProj) {
        const parts = [{ label: T.leg_realized, value: Math.max(v.balance, 0), text: money(proj.realized) }];
        if (proj.committed > 0) parts.push({ label: T.leg_committed, value: proj.committed, text: money(-proj.committed), tone: "warm" });
        if (proj.scheduled_in > 0) parts.push({ label: T.leg_incoming, value: proj.scheduled_in, text: "+ " + money(proj.scheduled_in), tone: "good" });
        if (estimate && proj.variable > 0) parts.push({ label: T.leg_variable, value: proj.variable, text: money(-proj.variable), tone: "hatch" });
        stackedBar(c, parts, estimate ? { total: { cls: "mob", label: T.proj_total, note: rangeText, text: "~ " + money0(proj.projected) } } : null);
      }
      if (card.phrase) {
        const box = el("div", "voice");
        box.append(el("div", "av", "A"));
        const txt = el("div", "", card.phrase.text);
        txt.dataset.severity = card.phrase.severity || "info";
        chip(txt, card.phrase.chat);
        box.append(txt);
        c.append(box);
      }
      return c;
    },

    upcoming(card, span) {
      const c = cardShell(card, T.title_upcoming, span);
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item bill");
        const recv = it.direction === "receive";
        const tile = el("div", "date-tile" + (recv ? " in" : (it.overdue || it.days <= 5 ? " hot" : "")));
        tile.append(el("b", "", monthShort(it.due)), el("span", "", String(Number(it.due.slice(8, 10)))));
        const body = el("div");
        body.append(el("div", "t", it.name));
        const s = el("div", "s", it.due_text);
        if (it.source === "recurring") s.append(el("span", "dsk", " · " + T.recurring));
        body.append(s);
        row.append(tile, body, el("div", "amt", recv ? "+ " + money(it.amount) : money(it.amount)));
        list.append(row);
      }
      c.append(list);
      if (card.more) c.append(el("div", "cap", fmt(T.more_items, { n: card.more })));
      phraseBlock(c, card.phrase);
      return c;
    },

    categories(card, span) {
      const month = card.compare && card.compare.start ? monthWord(card.compare.start) : null;
      let label = T.title_categories;
      if (month) {
        label = document.createDocumentFragment();
        label.append(el("span", "", T.title_categories), el("span", "note", fmt(T.cat_legend, { month })));
      }
      const c = cardShell(card, label, span);
      if (month) c.firstChild.classList.add("label-row");
      const rows = el("div", "rows");
      const top = Math.max(1, ...card.items.map((i) => i.amount), card.others ? card.others.amount : 0);
      for (const it of card.items) {
        const r = el("div", "cat");
        const v = el("span", "v", money0(it.amount) + " ");
        v.append(deltaSpan(it));
        r.append(el("span", "n", it.label), track((it.amount / top) * 100, "", true), v);
        rows.append(r);
      }
      if (card.others) {
        const r = el("div", "cat");
        r.append(el("span", "n", T.cat_others), track((card.others.amount / top) * 100, "", true), el("span", "v", money0(card.others.amount)));
        rows.append(r);
      }
      c.append(rows);
      const tr = card.items.map((it) => [it.label, money(it.amount), it.pct_of_total + "%", deltaText(it)]);
      if (card.others) tr.push([T.cat_others, money(card.others.amount), "", ""]);
      phraseBlock(c, card.phrase);
      return c;
    },

    budgets(card, span) {
      const v = card.values;
      const hasPct = v.pct !== null && v.pct !== undefined;
      const label = document.createDocumentFragment();
      label.append(el("span", "", T.title_budgets));
      if (hasPct) label.append(el("span", "mob", " · " + fmt(T.budget_used_short, { pct: v.pct })));
      const c = cardShell(card, label, span);
      if (hasPct) {
        const line = el("div", "hero-line dsk");
        const h = el("div", "hero", String(v.pct));
        h.append(el("small", "", "%"));
        line.append(h, el("div", "sub", fmt(T.budget_used, { day: v.day })));
        c.append(line);
      }
      const rows = el("div", "rows");
      for (const it of card.items) {
        const r = el("div", "brow");
        const h = el("div", "h");
        h.append(el("span", "n", it.label), el("span", "v" + (it.level >= 100 ? " over" : ""), fmt(T.budget_of, { spent: money0(it.spent), limit: money0(it.limit) }) + " · " + fmtInt.format(it.pct) + "%"));
        const t = track(it.pct, it.level >= 100 ? "warm" : (it.level >= 80 ? "hatch" : ""), false);
        t.dataset.level = String(it.level);
        for (const at of ["80%", "calc(100% - 2px)"]) {
          const tick = el("u");
          tick.style.left = at;
          t.append(tick);
        }
        r.append(h, t);
        rows.append(r);
      }
      c.append(rows, el("div", "cap", T.budget_marks));
      phraseBlock(c, card.phrase);
      return c;
    },


    owed(card, span) {
      const c = cardShell(card, T.title_owed, span);
      heroMoney(c, card.values.total);
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item person");
        row.append(el("div", "avatar", (Array.from(it.person)[0] || "?").toUpperCase()));
        const body = el("div");
        body.append(el("div", "t", it.person));
        const when = it.days === 0 ? T.owed_today : fmtN("owed_days", it.days);
        body.append(el("div", "s", it.note ? it.note + " · " + when : when));
        row.append(body, el("div", "amt", money(it.amount)));
        list.append(row);
      }
      c.append(list);
      phraseBlock(c, card.phrase);
      return c;
    },

    transactions(card, span, ctx) {
      const c = cardShell(card, T.title_tx, span);
      // Free text stays in the browser: it is never read into the address or sent anywhere.
      const box = el("div", "search");
      const input = el("input");
      input.type = "search";
      input.setAttribute("aria-label", T.search_label);
      input.setAttribute("placeholder", T.search_label);
      input.setAttribute("autocomplete", "off");
      box.append(input, el("div", "note", T.search_note));
      c.append(box);
      const groups = [];
      const wrap = el("div", "list-days");
      for (const d of card.days) {
        const g = el("div", "daygrp");
        const head = el("div", "day");
        head.append(el("span", "", (d.date === ctx.body.today ? T.today_word + ", " : "") + dayShort(d.date)));
        if (d.income || d.expense) head.append(el("span", "num", signed(d.net)));
        g.append(head);
        const list = el("div", "list");
        for (const e of d.entries) {
          const row = el("div", "item entry");
          row.dataset.search = (e.merchant || "").toLowerCase();
          const body = el("div");
          body.append(el("div", "t", e.merchant || T.no_merchant));
          body.append(el("div", "s", [e.label, lc(T["st_" + e.status] || e.status)].filter(Boolean).join(" · ")));
          row.append(body, el("div", "amt" + (e.settled ? "" : " pending"), e.kind === "income" ? "+ " + money(e.amount) : money(-e.amount)));
          list.append(row);
        }
        g.append(list);
        groups.push([g, list]);
        wrap.append(g);
      }
      c.append(wrap);
      const none = el("div", "empty", T.search_none);
      none.hidden = true;
      c.append(none);
      input.addEventListener("input", () => {
        const q = input.value.trim().toLowerCase();
        let shown = 0;
        for (const [g, list] of groups) {
          let any = 0;
          for (const row of list.children) {
            const hit = !q || row.dataset.search.includes(q);
            row.hidden = !hit;
            if (hit) any++;
          }
          g.hidden = !any;
          shown += any;
        }
        none.hidden = shown > 0 || !q;
      });
      const tot = el("div", "total");
      tot.append(el("span", "", fmtN("tx_total", card.values.count)), el("span", "num", signed(card.values.balance)));
      c.append(tot);
      if (card.page && card.page.pages > 1) {
        const pager = el("div", "pager");
        const prev = el("button", "", "‹ " + T.page_prev), next = el("button", "", T.page_next + " ›");
        prev.type = next.type = "button";
        prev.disabled = card.page.number <= 1;
        next.disabled = card.page.number >= card.page.pages;
        prev.addEventListener("click", () => setPage(card.page.number - 1));
        next.addEventListener("click", () => setPage(card.page.number + 1));
        pager.append(prev, el("span", "pg", fmt(T.page_of, { n: card.page.number, total: card.page.pages })), next);
        c.append(pager);
      }
      phraseBlock(c, card.phrase);
      c.append(el("div", "cap", T.copy_note));
      return c;
    },

    top_expenses(card, span) {
      const c = cardShell(card, T.title_top, span);
      const rows = el("div", "rows");
      for (const it of card.items) {
        const r = el("div", "brow");
        const h = el("div", "h");
        h.append(el("span", "n", it.merchant || T.no_merchant), el("span", "v", money(it.amount)));
        r.append(h, track(it.pct_of_top, "", true));
        rows.append(r);
      }
      c.append(rows);
      phraseBlock(c, card.phrase);
      return c;
    },

    month_vs_month(card, span) {
      const c = cardShell(card, T.title_mom, span);
      const month = card.compare && card.compare.start ? monthWord(card.compare.start) : "";
      const two = el("div", "two");
      for (const [key, name, upIsGood] of [["expense", T.mom_expenses, false], ["income", T.mom_income, true]]) {
        const v = card.values[key];
        const col = el("div");
        col.append(el("div", "k", name), el("div", "big-num", money0(v.current)));
        let d;
        if (!v.delta || (v.pct !== null && Math.abs(v.pct) < 0.5)) d = el("div", "d flat", "= " + fmt(T.cmp_same, { month }));
        else {
          const up = v.delta > 0;
          const cls = up === upIsGood ? (upIsGood ? "good" : "down") : "up";
          d = el("div", "d " + cls, (up ? "▲ " : "▼ ") + (v.pct === null ? "" : pctText(v.pct) + " ") + fmt(T.vs_month, { month }));
        }
        col.append(d);
        two.append(col);
      }
      c.append(two);
      if (card.drivers && card.drivers.length) {
        c.append(el("div", "sub", T.mom_drivers + ": " + card.drivers.map((d) => d.label + " " + signed(d.delta)).join(", ")));
      }
      const ev = card.values.expense, iv = card.values.income;
      phraseBlock(c, card.phrase);
      return c;
    },

    fixed_variable(card, span) {
      const v = card.values;
      const c = cardShell(card, T.title_fixed, span);
      const has = v.fixed_pct !== null && v.fixed_pct !== undefined;
      const line = el("div", "hero-line");
      const h = el("div", "hero", has ? String(v.fixed_pct) : "–");
      h.append(el("small", "", (has ? "% " : " ") + T.fixed_word));
      line.append(h);
      c.append(line);
      stackedBar(c, [
        { label: T.fixed_word, value: v.fixed, text: money0(v.fixed) },
        { label: T.variable_word, value: v.variable, text: money0(v.variable), tone: "warm" },
      ].filter((p) => p.value > 0));
      phraseBlock(c, card.phrase);
      return c;
    },

    daily(card, span) {
      const weekly = card.unit === "week";
      const title = weekly ? T.title_weekly : T.title_daily;
      const c = cardShell(card, title, span);
      const real = card.points, v = card.values;
      // A month still running keeps its full width: the days to come are empty slots, so the
      // bars stay as thin as on a finished month and the axis reads 1 .. last day.
      const pts = real.slice();
      if (!weekly && pts.length && pts[0].date.slice(8, 10) === "01") {
        const y = Number(pts[0].date.slice(0, 4)), m = Number(pts[0].date.slice(5, 7));
        const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
        const pad = (n) => String(n).padStart(2, "0");
        for (let d = pts.length + 1; d <= days && pts.length < days; d++) pts.push({ date: y + "-" + pad(m) + "-" + pad(d), amount: 0 });
      }
      const wrap = el("div", "daily-wrap");
      const chart = el("div", "daily");
      chart.setAttribute("role", "img");
      chart.setAttribute("aria-label", title + ": " + money(v.total) + (v.max_date ? "; " + T.daily_peak + ": " + dm(v.max_date) + " (" + money(v.max) + ")" : ""));
      const top = v.max || 1;
      for (const p of pts) {
        const b = el("i", p.date === v.max_date ? "peak" : "");
        b.style.height = (p.amount > 0 ? Math.max(2, (p.amount / top) * 100) : 0) + "%";
        chart.append(b);
      }
      wrap.append(chart);
      const axis = el("div", "axis");
      axis.setAttribute("aria-hidden", "true");
      const n = pts.length;
      pts.forEach((p, i) => {
        const d = Number(p.date.slice(8, 10));
        const edge = i === 0 || i === n - 1;
        if (!edge && (weekly || (d !== 10 && d !== 20) || i < 2 || i > n - 3)) return;
        const text = weekly ? dm(p.date) : (i === n - 1 && n > 1 ? d + " " + monthShort(p.date) : String(d));
        const lab = el("span", i === 0 ? "" : (i === n - 1 ? "last" : "mid"), text);
        if (!edge) lab.style.left = ((i + 0.5) / n) * 100 + "%";
        axis.append(lab);
      });
      wrap.append(axis);
      c.append(wrap);
      phraseBlock(c, card.phrase);
      return c;
    },


    // ── Agenda ──
    week(card, span, ctx) {
      const c = cardShell(card, T.title_week, span);
      const today = ctx.body.today, tomorrow = shiftDay(today, 1);
      const wd = dtf({ weekday: "long" });
      for (const d of card.days) {
        const head = el("div", "day-head");
        const name = d.date === today ? T.day_today : (d.date === tomorrow ? T.day_tomorrow : cap1(wd.format(day(d.date))));
        head.append(el("span", "", name), el("span", "", dayShort(d.date)));
        c.append(head);
        for (const it of d.items) {
          const row = el("div", "appt");
          const body = el("div");
          body.append(el("div", "t", it.title));
          if (it.notes) body.append(el("div", "s", it.notes));
          row.append(el("b", "num", it.time), body);
          c.append(row);
        }
      }
      if (card.more) c.append(el("div", "cap", fmt(T.more_items, { n: card.more })));
      const rows = [];
      for (const d of card.days) for (const it of d.items) rows.push([dm(d.date), it.time, it.title]);
      phraseBlock(c, card.phrase);
      return c;
    },

    tasks(card, span) {
      const v = card.values;
      const c = cardShell(card, T.title_tasks, span);
      const stats = el("div", "stats");
      for (const [n, label, warm] of [[v.open, T.tasks_open], [v.overdue, T.tasks_overdue_l, v.overdue > 0], [v.due_week, T.tasks_week_l], [v.done_week, T.tasks_done_l]]) {
        const s = el("div", "stat" + (warm ? " warm" : ""));
        s.append(el("b", "num", String(n)), el("span", "", label));
        stats.append(s);
      }
      c.append(stats);
      if (card.items.length) {
        const list = el("div", "list");
        for (const it of card.items) {
          const row = el("div", "item");
          const body = el("div");
          body.append(el("div", "t", it.body), el("div", "s", dayShort(it.due)));
          row.append(body, el("div", "amt" + (it.kind === "overdue" ? " late" : ""), it.kind === "overdue" ? "− " + fmtN("trip_days", it.days) : (it.days === 0 ? T.day_today : (it.days === 1 ? T.day_tomorrow : "+ " + fmtN("trip_days", it.days)))));
          list.append(row);
        }
        c.append(list);
      }
      phraseBlock(c, card.phrase);
      return c;
    },

    reminders(card, span) {
      const c = cardShell(card, T.title_reminders, span);
      const wd = dtf({ weekday: "short" });
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item");
        const body = el("div");
        const dayNames = it.every_day ? T.rem_every_day : it.days.map((i) => wd.format(new Date(Date.UTC(2024, 0, 1 + i)))).join(", ");
        body.append(el("div", "t", it.text || T["rem_kind_" + it.kind] || it.kind), el("div", "s", (it.text ? (T["rem_kind_" + it.kind] || "") + " · " : "") + dayNames));
        row.append(body, el("div", "amt num", it.time));
        list.append(row);
      }
      c.append(list);
      phraseBlock(c, card.phrase);
      return c;
    },

    month_map(card, span) {
      const c = cardShell(card, T.title_map, span);
      const wk = dtf({ weekday: "narrow" });
      const grid = el("div", "heat");
      grid.setAttribute("role", "img");
      const top = Math.max(1, ...card.weeks.flat().map((x) => x.count));
      grid.setAttribute("aria-label", T.title_map + ": " + card.weeks.flat().filter((x) => x.count).map((x) => dm(x.date) + " " + x.count).join(", "));
      for (let i = 0; i < 7; i++) grid.append(el("span", "hd", wk.format(new Date(Date.UTC(2024, 0, 1 + i)))));
      for (const w of card.weeks) for (const d of w) {
        const cell = el("span", "cell lv" + (d.count ? Math.min(4, Math.ceil((d.count / top) * 4)) : 0) + (d.today ? " today" : "") + (d.past ? " past" : ""), d.count ? String(d.count) : "");
        cell.title = dm(d.date);
        grid.append(cell);
      }
      c.append(grid, el("div", "cap", T.map_legend));
      phraseBlock(c, card.phrase);
      return c;
    },

    notes(card, span) {
      const c = cardShell(card, T.title_notes, span);
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item note");
        const body = el("div");
        body.append(el("div", "t clamp", it.body), el("div", "s", dayShort(it.date)));
        row.append(body);
        list.append(row);
      }
      c.append(list);
      return c;
    },

    // ── Hábitos ──
    water(card, span) {
      const v = card.values;
      const c = cardShell(card, T.title_water, span);
      const h = el("div", "hero", fmtL(v.today));
      c.append(h, el("div", "sub", T.water_today + " · " + fmtL(v.average) + " " + T.water_avg_l));
      miniBars(c, card.points.map((p) => ({ date: p.date, value: p.litres })), T.title_water);
      phraseBlock(c, card.phrase);
      return c;
    },

    workouts(card, span) {
      const v = card.values;
      const c = cardShell(card, T.title_workouts, span);
      c.append(el("div", "hero", String(v.this_week)), el("div", "sub", T.workouts_this + " · " + T.workouts_last + ": " + v.last_week + (v.km_week ? " · " + fmt(T.workouts_km, { km: fmt1.format(v.km_week) }) : "")));
      miniBars(c, card.weeks.map((w) => ({ date: w.start, value: w.count })), T.workouts_weeks_l, 0, true);
      if (card.activities.length) {
        const list = el("div", "list");
        for (const a of card.activities) {
          const row = el("div", "item");
          row.append(el("div", "t", cap1(a.activity)), el("div", "amt", "× " + a.count));
          list.append(row);
        }
        c.append(list);
      }
      phraseBlock(c, card.phrase);
      return c;
    },

    goals(card, span) {
      const c = cardShell(card, T.title_goals, span);
      const rows = el("div", "rows");
      for (const it of card.items) {
        const b = el("div", "brow");
        const h = el("div", "h");
        h.append(el("span", "n", it.title), el("span", "v", fmt(T.goal_days, { n: Math.min(it.logs_7d, 7) })));
        b.append(h, track((Math.min(it.logs_7d, 7) / 7) * 100, ""));
        const sub = [it.target, it.deadline ? fmt(T.goal_until, { date: dayShort(it.deadline) }) : null].filter(Boolean).join(" · ");
        if (sub) b.append(el("div", "cap", sub));
        rows.append(b);
      }
      c.append(rows);
      phraseBlock(c, card.phrase);
      return c;
    },

    training(card, span) {
      const c = cardShell(card, T.title_training, span);
      const wdF = dtf({ weekday: "long" });
      const wdName = (i) => cap1(wdF.format(new Date(Date.UTC(2026, 0, 5 + i))));
      const items = (d) => {
        const list = el("div", "list");
        for (const it of d.items) {
          const row = el("div", "item");
          const body = el("div");
          const bits = [it.sets && it.reps ? it.sets + "×" + it.reps : null, it.planned_kg !== null ? fmt(T.training_plan_kg, { kg: fmtKg(it.planned_kg) }) : null].filter(Boolean);
          body.append(el("div", "t", it.exercise));
          if (bits.length) body.append(el("div", "s", bits.join(" · ")));
          const right = el("div", "amt");
          if (it.last_kg !== null) {
            right.append(el("div", "num", fmtKg(it.last_kg)));
            if (it.delta_kg !== null) {
              const up = it.delta_kg > 0, down = it.delta_kg < 0;
              const txt = (up ? "↑ +" : (down ? "↓ −" : "= ")) + fmtKg(Math.abs(it.delta_kg)) + " · " + fmt(T.training_since, { date: dm(it.first_day) });
              right.append(el("div", "s" + (up ? " good" : ""), txt));
            } else {
              right.append(el("div", "s", T.training_first));
            }
          }
          row.append(body, right);
          list.append(row);
        }
        return list;
      };
      const moved = card.days.flatMap((d) => d.items).filter((it) => it.delta_kg !== null).sort((a, b) => Math.abs(b.delta_kg) - Math.abs(a.delta_kg)).slice(0, 4);
      if (moved.length) {
        c.append(el("div", "label sub-label", T.training_evolution));
        const list = el("div", "list");
        for (const it of moved) {
          const row = el("div", "item");
          const up = it.delta_kg > 0, down = it.delta_kg < 0;
          row.append(el("div", "t", it.exercise), el("div", "amt", fmtKg(it.last_kg)));
          const sub = el("div", "s" + (up ? " good" : ""), (up ? "↑ +" : (down ? "↓ −" : "= ")) + fmtKg(Math.abs(it.delta_kg)) + " · " + fmt(T.training_since, { date: dm(it.first_day) }));
          row.children[0].append(sub);
          list.append(row);
        }
        c.append(list);
      }
      const today = card.days.find((d) => d.today);
      if (today) {
        c.append(el("div", "sub", T.training_today + " · " + wdName(today.weekday) + (today.title ? " · " + today.title : "")));
        c.append(items(today));
      } else {
        c.append(el("div", "sub", T.training_rest));
      }
      for (const d of card.days.filter((x) => !x.today)) {
        const det = el("details", "plan-day");
        det.append(el("summary", "", wdName(d.weekday) + (d.title ? " · " + d.title : "")), items(d));
        c.append(det);
      }
      const rows = [];
      for (const d of card.days) for (const it of d.items) rows.push([wdName(d.weekday), it.exercise, it.last_kg !== null ? fmtKg(it.last_kg) : (it.planned_kg !== null ? fmtKg(it.planned_kg) : "–")]);
      phraseBlock(c, card.phrase);
      return c;
    },

    // ── Viagens ──
    trip(card, span) {
      const t = card.trip, v = card.values;
      const c = cardShell(card, T.title_trip, span);
      c.append(el("div", "hero small", t.destination));
      const when = dayShort(t.start) + (t.end ? " – " + dayShort(t.end) : "");
      const state = t.state === "active" ? T.trip_state_active : (t.state === "upcoming" ? fmtN("trip_state_upcoming", t.days_to_start) : T.trip_state_ended);
      c.append(el("div", "sub", when + " · " + state));
      const tiles = el("div", "tiles three");
      const tile = (k, val, cls) => { const x = el("div", "tile"); x.append(el("div", "k", k), el("div", "big-num" + (cls ? " " + cls : ""), val)); return x; };
      if (v.budget !== null) {
        tiles.append(tile(T.trip_budget_l, money0(v.budget)), tile(T.trip_spent_l, money0(v.spent)), v.remaining >= 0 ? tile(T.trip_left_l, money0(v.remaining)) : tile(T.trip_over_l, money0(-v.remaining), "up"));
      } else {
        tiles.append(tile(T.trip_spent_l, money0(v.spent)));
      }
      c.append(tiles);
      if (v.budget !== null) c.append(track(v.pct, v.pct >= 100 ? "warm" : "", true));
      else c.append(el("div", "cap", T.trip_no_budget));
      if (card.categories.length) {
        c.append(el("div", "label sub-label", T.trip_cats));
        const top = Math.max(1, ...card.categories.map((x) => x.amount));
        const rows = el("div", "rows");
        for (const x of card.categories) {
          const r = el("div", "cat");
          r.append(el("span", "n", x.label), track((x.amount / top) * 100, "", true), el("span", "v", money0(x.amount)));
          rows.append(r);
        }
        c.append(rows);
      }
      if (card.points.length > 1) {
        c.append(el("div", "label sub-label", T.trip_daily));
        miniBars(c, card.points.map((p) => ({ date: p.date, value: p.amount })), T.trip_daily);
      }
      phraseBlock(c, card.phrase);
      return c;
    },

    itinerary(card, span) {
      const c = cardShell(card, T.title_itinerary, span);
      const wd = dtf({ weekday: "long" });
      for (const d of card.days) {
        const head = el("div", "day-head");
        head.append(el("span", "", cap1(wd.format(day(d.date)))), el("span", "", dayShort(d.date)));
        c.append(head);
        for (const it of d.items) {
          const row = el("div", "appt");
          const body = el("div");
          body.append(el("div", "t", it.title));
          row.append(el("b", "num", it.time || "·"), body);
          c.append(row);
        }
      }
      const rows = [];
      for (const d of card.days) for (const it of d.items) rows.push([dm(d.date), it.time || "–", it.title]);
      phraseBlock(c, card.phrase);
      return c;
    },

    packing(card, span) {
      const v = card.values;
      const c = cardShell(card, T.title_packing, span);
      c.append(el("div", "sub", fmt(T.pack_count, { done: v.done, total: v.total })));
      c.append(track(v.total ? (v.done / v.total) * 100 : 0, "", true));
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item");
        row.append(el("div", "t", it.name), el("div", "s" + (it.packed ? " good" : ""), (it.packed ? "✓ " : "○ ") + (it.packed ? T.pack_yes : T.pack_no)));
        list.append(row);
      }
      c.append(list);
      phraseBlock(c, card.phrase);
      return c;
    },

    plan_budget(card, span) {
      const v = card.values;
      const c = cardShell(card, T.title_planbudget, span);
      c.append(el("div", "sub", fmt(T.planbudget_total, { spent: money0(v.spent), plan: money0(v.plan) })));
      const rows = el("div", "rows");
      for (const x of card.lines) {
        const r = el("div", "cat");
        r.append(el("span", "n", x.label), track(Math.min(100, x.pct), x.over ? "warm" : "", true), el("span", "v", money0(x.spent) + " / " + money0(x.plan)));
        rows.append(r);
      }
      c.append(rows);
      phraseBlock(c, card.phrase);
      return c;
    },

    trips_past(card, span) {
      const c = cardShell(card, T.title_past, span);
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item");
        const body = el("div");
        body.append(el("div", "t", it.destination), el("div", "s", [dayShort(it.start), fmtN("trip_days", it.days), it.budget !== null ? fmt(T.past_budget_of, { budget: money0(it.budget) }) : null].filter(Boolean).join(" · ")));
        const right = el("div", "amt");
        right.append(el("div", "num", money0(it.spent)));
        if (it.budget !== null) right.append(el("div", "s " + (it.within ? "good" : "up"), it.within ? T.past_within : T.past_over));
        row.append(body, right);
        list.append(row);
      }
      c.append(list);
      phraseBlock(c, card.phrase);
      return c;
    },

    recurring(card, span) {
      const c = cardShell(card, T.title_recurring, span);
      c.append(el("div", "sub", fmtN("rec_total", card.values.count, { total: money(card.values.monthly_total) })));
      const list = el("div", "list");
      for (const it of card.items) {
        const row = el("div", "item");
        const body = el("div");
        body.append(el("div", "t", it.name));
        const bits = [T["rec_" + it.kind], it.installment ? fmt(T.rec_part, { n: it.installment.number, total: it.installment.total }) : null, T["rec_" + it.frequency], it.due_text].filter(Boolean);
        body.append(el("div", "s", bits.join(" · ")));
        row.append(body, el("div", "amt", money(it.amount)));
        list.append(row);
      }
      c.append(list);
      phraseBlock(c, card.phrase);
      return c;
    },
  };

  // ── address-only filters: closed lists, written to the URL, never free text ──
  const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
  const KINDS = ["expense", "income"];
  const STATES = ["paid", "to_pay", "received", "to_receive"];
  const CAT_IDS = cfg.categories.map((x) => x.id);
  const FILTER_TABS = { summary: true, money: true };
  const KEYS = ["month", "categories", "kind", "state", "page"];

  function readFilters() {
    const p = new URLSearchParams(location.search);
    // the server answers 2000 .. next year only: anything else would label current data wrongly
    const m = p.get("month") || "", y = Number(m.slice(0, 4));
    const month = MONTH_RE.test(m) && y >= 2000 && y <= Number(cfg.today.slice(0, 4)) + 1 ? m : "";
    const cats = (p.get("categories") || "").split(",").filter((x) => CAT_IDS.includes(x));
    const kind = KINDS.includes(p.get("kind")) ? p.get("kind") : "";
    const st = (p.get("state") || "").split(",").filter((x) => STATES.includes(x));
    const pg = /^\d{1,5}$/.test(p.get("page") || "") ? Math.max(1, Number(p.get("page"))) : 1;
    return { month, cats, kind, st, page: pg };
  }
  function apiQuery(tabId) {
    const f = readFilters(), q = new URLSearchParams();
    if (f.month) q.set("month", f.month);
    if (f.cats.length) q.set("categories", f.cats.join(","));
    if (f.kind) q.set("kind", f.kind);
    if (f.st.length) q.set("state", f.st.join(","));
    if (tabId === "money" && f.page > 1) q.set("page", String(f.page));
    const s = q.toString();
    return s ? "?" + s : "";
  }
  function writeUrl(p) {
    for (const k of [...p.keys()]) if (!KEYS.includes(k)) p.delete(k);
    const q = p.toString();
    history.pushState(null, "", location.pathname + (q ? "?" + q : ""));
  }
  function pushFilters(mutate) {
    const p = new URLSearchParams(location.search);
    mutate(p);
    p.delete("page");
    writeUrl(p);
    refresh();
  }
  function setPage(n) {
    const p = new URLSearchParams(location.search);
    if (n > 1) p.set("page", String(n)); else p.delete("page");
    writeUrl(p);
    refresh();
    window.scrollTo({ top: 0, behavior: REDUCED ? "auto" : "smooth" });
  }
  const thisMonth = () => cfg.today.slice(0, 7);
  const shiftMonth = (ym, by) => {
    const [y, m] = ym.split("-").map(Number), t = y * 12 + (m - 1) + by;
    return Math.floor(t / 12) + "-" + String((t % 12) + 1).padStart(2, "0");
  };

  function pill(cls) { return el("label", "pill" + (cls ? " " + cls : "")); }
  function dropdown(name, options, value, onChange) {
    const s = el("select");
    s.setAttribute("aria-label", name);
    for (const [v, t] of options) { const o = el("option", "", t); o.value = v; s.append(o); }
    s.value = value;
    s.addEventListener("change", () => onChange(s.value));
    return s;
  }

  const filterBar = document.getElementById("filter-bar"), filtersEl = document.getElementById("filters"), noteEl = document.getElementById("filters-note");
  filtersEl.setAttribute("aria-label", T.filters_label);

  function renderFilters(tabId) {
    filterBar.hidden = !FILTER_TABS[tabId];
    filtersEl.replaceChildren();
    if (!FILTER_TABS[tabId]) return;
    const f = readFilters(), cur = f.month || thisMonth(), isMoney = tabId === "money";
    const setMonth = (ym) => pushFilters((p) => { if (!ym || ym === thisMonth()) p.delete("month"); else p.set("month", ym); });
    if (isMoney) {
      const months = [];
      for (let i = 0; i < 13; i++) months.push(shiftMonth(thisMonth(), -i));
      if (!months.includes(cur)) months.push(cur);
      const pm = pill("soft");
      pm.append(dropdown(T.f_month, months.map((m) => [m, monthTitle(m)]), cur, setMonth));
      filtersEl.append(pm);
    } else {
      const pm = el("div", "pill month");
      const prev = el("button", "", "‹"), next = el("button", "", "›");
      prev.type = next.type = "button";
      prev.setAttribute("aria-label", T.f_month_prev);
      next.setAttribute("aria-label", T.f_month_next);
      next.disabled = cur >= thisMonth();
      prev.addEventListener("click", () => setMonth(shiftMonth(cur, -1)));
      next.addEventListener("click", () => setMonth(shiftMonth(cur, 1)));
      pm.append(prev, el("span", "cur", monthTitle(cur)), next);
      filtersEl.append(pm);
    }
    const soft = isMoney ? "soft" : "";
    const catOpts = [["", T.f_category], ...cfg.categories.map((x) => [x.id, x.label])];
    let catVal = f.cats[0] || "";
    if (f.cats.length > 1) { catOpts.push(["__multi", fmt(T.f_multi, { n: f.cats.length })]); catVal = "__multi"; }
    const pc = pill("soft");
    pc.append(dropdown(T.f_category, catOpts, catVal, (v) => { if (v !== "__multi") pushFilters((p) => { if (v) p.set("categories", v); else p.delete("categories"); }); }));
    const pk = pill(soft);
    pk.append(dropdown(T.f_kind, [["", T.f_kind_all], ["expense", T.f_kind_expense], ["income", T.f_kind_income]], f.kind, (v) => pushFilters((p) => { if (v) p.set("kind", v); else p.delete("kind"); })));
    const stOpts = [["", T.f_state_all], ...STATES.map((s) => [s, T["st_" + s]])];
    let stVal = f.st[0] || "";
    if (f.st.length > 1) { stOpts.push(["__multi", f.st.map((s) => T["st_" + s]).join(", ")]); stVal = "__multi"; }
    const ps = pill(soft);
    ps.append(dropdown(T.f_state, stOpts, stVal, (v) => { if (v !== "__multi") pushFilters((p) => { if (v) p.set("state", v); else p.delete("state"); }); }));
    filtersEl.append(pc, pk, ps);
    if (f.month || f.cats.length || f.kind || f.st.length) {
      const clear = el("button", "clear", T.f_clear);
      clear.type = "button";
      clear.addEventListener("click", () => pushFilters((p) => { for (const k of ["month", "categories", "kind", "state"]) p.delete(k); }));
      filtersEl.append(clear);
    }
    noteEl.textContent = isMoney ? T.f_note_money : T.f_note_summary;
  }

  // ── tabs and loading ──
  const loaded = new Map();
  const panel = document.getElementById("panel");
  let latest = 0; // only the most recent selection may draw (a slow answer must not win)
  let current = null;

  function failure(status) {
    if (status === 404) return T.expired; // unknown or expired link: only a new one helps
    if (status === 429) return T.rate_limited;
    return T.error;
  }

  async function load(tabId) {
    const mine = ++latest;
    const tab = cfg.tabs.find((x) => x.id === tabId);
    panel.setAttribute("aria-labelledby", "tab-" + tabId);
    panel.replaceChildren(el("div", "empty", T.loading));
    let message = null;
    try {
      const qs = apiQuery(tabId), key = tabId + qs;
      if (!loaded.has(key)) {
        const r = await fetch(tab.path.replace("{token}", cfg.token) + qs, { headers: { Accept: "application/json" } });
        if (!r.ok) { message = failure(r.status); throw new Error(String(r.status)); }
        loaded.set(key, await r.json());
      }
      if (mine !== latest) return;
      const body = loaded.get(key);
      const pm = prevMonthOf(body);
      const ctx = { body, tab: tabId, prevMonth: pm ? monthWord(pm) : null };
      const grid = el("div", "grid");
      for (const card of body.cards) {
        const draw = renderers[card.id];
        if (!draw) continue;
        const span = (SPANS[tabId] && SPANS[tabId][card.id]) || "s12";
        grid.append(card.empty ? emptyCard(card, span) : draw(card, span, ctx));
      }
      if (tabId === "health" && grid.children.length) grid.append(el("p", "cap grid-note", T.health_note));
      panel.replaceChildren(grid.children.length ? grid : el("div", "empty", T.soon));
    } catch (_) {
      if (mine === latest) panel.replaceChildren(el("div", "empty", message || T.error));
    }
  }

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const tablist = document.querySelector('[role="tablist"]');
  const tabsWrap = document.querySelector(".tabs-wrap");
  function fade() {
    tabsWrap.classList.toggle("fade-l", tablist.scrollLeft > 4);
    tabsWrap.classList.toggle("fade-r", tablist.scrollLeft + tablist.clientWidth < tablist.scrollWidth - 4);
  }
  tablist.addEventListener("scroll", fade, { passive: true });
  window.addEventListener("resize", fade);

  function refresh() {
    renderFilters(current);
    load(current);
  }
  function selectTab(i, focus) {
    tabs.forEach((t, k) => { t.setAttribute("aria-selected", String(k === i)); t.tabIndex = k === i ? 0 : -1; });
    if (focus) tabs[i].focus();
    tabs[i].scrollIntoView({ block: "nearest", inline: "nearest", behavior: REDUCED ? "auto" : "smooth" });
    current = tabs[i].dataset.tab;
    document.body.dataset.tabActive = current;
    refresh();
  }
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(i, false));
    t.addEventListener("keydown", (e) => {
      const last = tabs.length - 1;
      const to = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i + last) % tabs.length, Home: 0, End: last }[e.key];
      if (to !== undefined) { e.preventDefault(); selectTab(to, true); }
    });
  });
  window.addEventListener("popstate", () => { if (current) refresh(); });

  // ── theme: follows the system until the member picks one (kept only in this browser) ──
  const root = document.documentElement, toggle = document.getElementById("theme-toggle");
  function effective() {
    if (root.hasAttribute("data-theme-locked")) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  function paintToggle() {
    const dark = effective() === "dark";
    toggle.setAttribute("aria-label", dark ? T.theme_to_light : T.theme_to_dark);
    toggle.setAttribute("title", dark ? T.theme_to_light : T.theme_to_dark);
    const s = svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    if (dark) {
      s.append(svg("circle", { cx: 12, cy: 12, r: 4 }));
      for (const [a, b, c, d] of [[12, 2, 12, 4], [12, 20, 12, 22], [2, 12, 4, 12], [20, 12, 22, 12], [4.9, 4.9, 6.3, 6.3], [17.7, 17.7, 19.1, 19.1], [4.9, 19.1, 6.3, 17.7], [17.7, 6.3, 19.1, 4.9]]) s.append(svg("line", { x1: a, y1: b, x2: c, y2: d }));
    } else {
      s.append(svg("path", { d: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" }));
    }
    toggle.replaceChildren(s);
  }
  function applyTheme(t) { root.dataset.theme = t; root.setAttribute("data-theme-locked", "1"); }
  try { const saved = localStorage.getItem("alfred-theme"); if (saved === "light" || saved === "dark") applyTheme(saved); } catch (_) { /* storage may be blocked */ }
  toggle.addEventListener("click", () => {
    const next = effective() === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("alfred-theme", next); } catch (_) { /* storage may be blocked */ }
    paintToggle();
  });
  paintToggle();

  // Drawing primitives, exposed read-only for the screenshot script and the tab PRs.
  window.AlfredPanel = Object.freeze({ barRow, sparkline, stackedBar, cardShell, money, el });
  selectTab(0, false);
  fade();
})();
