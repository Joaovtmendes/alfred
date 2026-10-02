(() => {
  "use strict";
  // Everything the user owns reaches the DOM through textContent / setAttribute; never as HTML.
  const cfg = JSON.parse(document.getElementById("panel-config").textContent);
  const T = cfg.t;
  const fmtNum = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money = (v) => (v < 0 ? "− " : "") + "€ " + fmtNum.format(Math.abs(v));
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  function cardShell(card, label, span) {
    const c = el("section", "card " + (span || "s12"));
    c.dataset.card = card.id;
    c.append(el("div", "label", label));
    return c;
  }

  function heroMoney(parent, value) {
    const [whole, cents] = money(value).split(",");
    const h = el("div", "hero", whole);
    h.append(el("small", "", "," + cents));
    parent.append(h);
  }

  function barRow(parent, { label, value, pct, tone }) {
    const row = el("div", "row");
    const head = el("div", "");
    head.append(el("span", "", label + " · "), el("span", "num", value));
    row.append(head);
    const bar = el("div", "bar");
    const fill = el("span", tone === "warm" ? "warm" : "");
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%"; // CSSOM: allowed under the nonce CSP
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
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label + ": " + values.join(", "));
    const line = document.createElementNS(NS, "polyline");
    line.setAttribute("points", pts.map((p) => p.join(",")).join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "var(--accent)");
    line.setAttribute("stroke-width", "2");
    svg.append(line);
    parent.append(svg);
  }

  // parts: [{label, value, text, tone: ""|"warm"|"hatch"}]; opts.total: {label, text} sits UNDER the legend.
  function stackedBar(parent, parts, opts) {
    const sum = parts.reduce((a, p) => a + Math.abs(p.value), 0) || 1;
    const bar = el("div", "bar stacked");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", parts.map((p) => p.label + " " + p.text).join(", "));
    for (const p of parts) {
      const seg = el("span", p.tone || "");
      seg.style.width = (Math.abs(p.value) / sum) * 100 + "%";
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
      const t = el("div", "total");
      t.append(el("span", "", opts.total.label), el("span", "num", opts.total.text));
      parent.append(t);
    }
  }

  function tableView(parent, rows) {
    const d = el("details", "table");
    d.append(el("summary", "", T.table_view));
    const t = el("table");
    for (const [a, b] of rows) {
      const tr = el("tr");
      tr.append(el("td", "", a), el("td", "", b));
      t.append(tr);
    }
    d.append(t);
    parent.append(d);
  }

  function phraseBlock(parent, phrase) {
    if (!phrase) return;
    const p = el("div", "phrase", phrase.text);
    if (phrase.chat) p.append(el("div", "chip", T.ask_in_chat + ": " + phrase.chat));
    parent.append(p);
  }

  const renderers = {
    balance(card) {
      const c = cardShell(card, T.tab_balance, "s7");
      if (card.empty) { c.append(el("div", "empty", T.empty)); return c; }
      const v = card.values;
      heroMoney(c, v.balance);
      const top = Math.max(v.income, v.expense, 0); // one scale for both bars
      const pct = (x) => (top ? (x / top) * 100 : 0);
      barRow(c, { label: T.income, value: money(v.income), pct: pct(v.income) });
      barRow(c, { label: T.expense, value: money(v.expense), pct: pct(v.expense), tone: "warm" });
      tableView(c, [[T.income, money(v.income)], [T.expense, money(v.expense)], [T.tab_balance, money(v.balance)]]);
      phraseBlock(c, card.phrase);
      return c;
    },
  };

  const loaded = new Map();
  const panel = document.getElementById("panel");
  let latest = 0; // only the most recent tab selection may draw (a slow answer must not win)

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
      if (!loaded.has(tabId)) {
        const r = await fetch(tab.path.replace("{token}", cfg.token) + location.search, { headers: { Accept: "application/json" } });
        if (!r.ok) { message = failure(r.status); throw new Error(String(r.status)); }
        loaded.set(tabId, await r.json());
      }
      if (mine !== latest) return;
      const body = loaded.get(tabId);
      const grid = el("div", "grid");
      for (const card of body.cards) {
        const draw = renderers[card.id];
        if (draw) grid.append(draw(card));
      }
      panel.replaceChildren(grid.children.length ? grid : el("div", "empty", T.soon));
    } catch (_) {
      if (mine === latest) panel.replaceChildren(el("div", "empty", message || T.error));
    }
  }

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function select(i, focus) {
    tabs.forEach((t, k) => { t.setAttribute("aria-selected", String(k === i)); t.tabIndex = k === i ? 0 : -1; });
    if (focus) tabs[i].focus();
    load(tabs[i].dataset.tab);
  }
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(i, false));
    t.addEventListener("keydown", (e) => {
      const last = tabs.length - 1;
      const to = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i + last) % tabs.length, Home: 0, End: last }[e.key];
      if (to !== undefined) { e.preventDefault(); select(to, true); }
    });
  });
  // Drawing primitives, exposed read-only for the screenshot script and the tab PRs.
  window.AlfredPanel = Object.freeze({ barRow, sparkline, stackedBar, cardShell, tableView, money, el });
  select(0, false);
})();
