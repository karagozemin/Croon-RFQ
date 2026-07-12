/* CROON RFQ — frontend runtime
   Landing → Intro → App (The Floor). Vanilla JS, no build step.
   Views are swapped in-place; the app talks to the FastAPI backend and
   renders live runs from the polling event bus. */

"use strict";

/* ============================== utilities ============================== */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (h, n = 10) => (h && h.length > n * 2 ? `${h.slice(0, n)}…${h.slice(-6)}` : h || "");
const usdc = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : String(v ?? "—");
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function copyStamp(x, y, text = "COPIED") {
  const el = document.createElement("div");
  el.className = "copied-stamp";
  el.textContent = text;
  el.style.left = `${x}px`;
  el.style.top = `${y - 24}px`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1000);
}

/* ============================== router ============================== */

const views = { landing: $("#view-landing"), intro: $("#view-intro"), app: $("#view-app") };

function show(view) {
  Object.entries(views).forEach(([k, el]) => el.classList.toggle("hidden", k !== view));
  window.scrollTo(0, 0);
}

function go(hash) {
  if (location.hash !== hash) location.hash = hash;
  else route();
}

function route() {
  const h = location.hash;
  if (h === "#/app") {
    show("app");
    App.boot();
  } else if (h === "#/intro") {
    show("intro");
    Intro.play();
  } else {
    show("landing");
  }
}

window.addEventListener("hashchange", route);

/* ============================== landing ============================== */

(function initLanding() {
  const enter = () => {
    if (sessionStorage.getItem("croon_intro_seen")) go("#/app");
    else go("#/intro");
  };
  $("#cta-enter").addEventListener("click", enter);
  $("#cta-launch").addEventListener("click", enter);

  // scroll-reveal for anatomy steps
  const io = new IntersectionObserver(
    (entries) =>
      entries.forEach((e, ) => {
        if (e.isIntersecting) {
          const step = e.target;
          const idx = Number(step.dataset.step || 1);
          setTimeout(() => step.classList.add("in"), (idx - 1) * 90);
          io.unobserve(step);
        }
      }),
    { threshold: 0.25 }
  );
  $$(".step").forEach((el) => io.observe(el));
})();

/* ============================== intro ============================== */

const Intro = (() => {
  let playing = false;
  let cancelled = false;

  const LINES = [
    { t: "$ croon open-market --order wallet-risk-brief", cls: "" },
    { t: "discovering live agents on CROO ……… <span class='ok'>3 found</span>", cls: "" },
    { t: "broadcasting RFQ · timeout 8s ……… <span class='ok'>quotes in</span>", cls: "" },
    { t: "scoring 0.4·price + 0.35·rep + 0.25·speed", cls: "" },
    { t: "winner: <span class='amber'>WALLET TRACKER</span> — 0.10 USDC under budget", cls: "" },
    { t: "settling on BASE ……… <span class='ok'>tx confirmed</span>", cls: "" },
  ];

  function skip() {
    cancelled = true;
  }

  function onKey(e) {
    if (e.key === "Escape") skip();
  }

  async function typeLine(term, html) {
    // type the plain-text prefix char by char, then swap in the rich html
    const plain = html.replace(/<[^>]+>/g, "");
    const row = document.createElement("div");
    term.appendChild(row);
    for (let i = 0; i <= plain.length; i++) {
      if (cancelled) break;
      row.innerHTML = esc(plain.slice(0, i)) + '<span class="cursor">▌</span>';
      await sleep(plain.length > 50 ? 8 : 14);
    }
    row.innerHTML = html;
  }

  function buildRing() {
    const g = $("#ring-nodes");
    g.innerHTML = "";
    const names = ["WALLET-TRACKER", "POLYMIND", "GAS-ORACLE"];
    names.forEach((name, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / names.length;
      const x = 200 + 140 * Math.cos(a);
      const y = 200 + 140 * Math.sin(a);
      const node = document.createElementNS("http://www.w3.org/2000/svg", "g");
      node.setAttribute("class", "ring-node");
      node.innerHTML =
        `<circle cx="${x}" cy="${y}" r="6"></circle>` +
        `<text x="${x}" y="${y - 16}">${name}</text>`;
      g.appendChild(node);
    });
    const center = document.createElementNS("http://www.w3.org/2000/svg", "g");
    center.setAttribute("class", "ring-node");
    center.innerHTML =
      `<circle cx="200" cy="200" r="9"></circle>` +
      `<text x="200" y="235" style="fill:var(--amber)">CROON</text>`;
    g.appendChild(center);
    return $$(".ring-node", g);
  }

  async function play() {
    if (playing) return;
    playing = true;
    cancelled = false;
    document.addEventListener("keydown", onKey);

    const term = $("#intro-term");
    term.innerHTML = "";
    const ring = $("#intro-ring");
    ring.classList.remove("show");
    $("#ring-c").style.strokeDashoffset = "880";
    const nodes = buildRing();

    for (let i = 0; i < LINES.length; i++) {
      if (cancelled) break;
      await typeLine(term, LINES[i].t);
      if (i === 1 && !cancelled) {
        ring.classList.add("show");
        nodes.forEach((n, j) => setTimeout(() => n.classList.add("show"), j * 180));
      }
      if (i === 2 && !cancelled) $("#ring-c").style.strokeDashoffset = "0";
      await sleep(cancelled ? 0 : 340);
    }
    if (!cancelled) await sleep(700);

    document.removeEventListener("keydown", onKey);
    playing = false;
    sessionStorage.setItem("croon_intro_seen", "1");
    go("#/app");
  }

  return { play };
})();

/* ============================== app ============================== */

const App = (() => {
  let booted = false;
  let orders = [];
  let selectedId = null;
  let selectedDetail = null;
  let lastSeq = 0;
  let pollTimer = null;
  let running = false;
  const bidders = new Map(); // agent_id -> card element

  /* ---------- boot ---------- */

  async function boot() {
    if (booted) {
      refreshOrders();
      return;
    }
    booted = true;
    bindChrome();
    try {
      const h = await api("/health");
      const pill = $("#mode-pill");
      pill.textContent = `● ${String(h.cap_mode || "?").toUpperCase()}`;
      pill.classList.add(h.cap_mode === "live" ? "live" : "mock");
    } catch {
      $("#mode-pill").textContent = "● OFFLINE";
    }
    await refreshOrders();
  }

  function bindChrome() {
    $("#app-logo").addEventListener("click", () => go("#/"));
    $("#new-order-btn").addEventListener("click", openSheet);
    $("#sheet-close").addEventListener("click", closeSheet);
    $("#sheet-veil").addEventListener("click", closeSheet);
    $("#dialog-close").addEventListener("click", closeDialog);
    $("#dialog-veil").addEventListener("click", closeDialog);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeSheet();
        closeDialog();
      }
    });
    $("#create-form").addEventListener("submit", onCreate);
    $("#create-form").addEventListener("input", renderSheetPreview);
    $("#run-now-btn").addEventListener("click", runNow);
    $("#pause-btn").addEventListener("click", togglePause);
  }

  /* ---------- THE BOOK ---------- */

  async function refreshOrders() {
    try {
      orders = await api("/standing-orders");
    } catch {
      orders = [];
    }
    renderOrders();
    renderTicker();
    renderSpendMeter();
    if (!selectedId && orders.length) selectOrder(orders[0].id);
    else if (selectedId) {
      renderIdle();
      loadDetail();
    }
  }

  function renderOrders() {
    const box = $("#order-list");
    if (!orders.length) {
      box.innerHTML =
        `<div class="empty-note">THE BOOK IS EMPTY.<br/>File a standing order —<br/>or seed the demo:<br/><code>python scripts/seed.py</code></div>`;
      return;
    }
    box.innerHTML = orders
      .map((o) => {
        const spent = Number(o.total_spent_usdc);
        const max = Number(o.max_total_budget_usdc) || 1;
        const pct = Math.min(100, (spent / max) * 100);
        return `
        <div class="order-line ${o.id === selectedId ? "active" : ""}" data-id="${o.id}">
          <div class="ol-name"><span class="dot ${esc(o.status)}"></span>${esc(o.name)}</div>
          <div class="ol-meta mono"><span>${esc(o.category || "—")}</span><span>${usdc(o.budget_per_run_usdc)}/run</span></div>
          <div class="ol-spend"><i style="width:${pct}%"></i></div>
        </div>`;
      })
      .join("");
    $$(".order-line", box).forEach((el) =>
      el.addEventListener("click", () => selectOrder(el.dataset.id))
    );
  }

  function renderTicker() {
    const track = $("#app-ticker-track");
    if (!orders.length) {
      track.innerHTML = "";
      return;
    }
    const items = orders
      .map(
        (o) =>
          `<span>${esc(o.name).toUpperCase()} · <b>${usdc(o.total_spent_usdc)}</b>/${usdc(o.max_total_budget_usdc)} USDC · ${esc(o.status).toUpperCase()}</span>`
      )
      .join("");
    track.innerHTML = items + items;
  }

  function renderSpendMeter() {
    const total = orders.reduce((s, o) => s + (Number(o.total_spent_usdc) || 0), 0);
    $("#spend-meter").textContent = `Σ ${total.toFixed(2)} USDC SETTLED`;
  }

  function selectOrder(id) {
    if (running) return; // don't switch mid-auction
    selectedId = id;
    lastSeq = 0;
    renderOrders();
    renderIdle();
    loadDetail();
  }

  /* ---------- idle stage + ledger ---------- */

  function currentOrder() {
    return orders.find((o) => o.id === selectedId) || null;
  }

  function renderIdle() {
    const o = currentOrder();
    const idle = $("#stage-idle");
    const live = $("#stage-live");
    if (!running) {
      idle.classList.remove("hidden");
      live.classList.add("hidden");
    }
    const runBtn = $("#run-now-btn");
    const pauseBtn = $("#pause-btn");
    if (!o) {
      $("#auction-sub").textContent = "/ SELECT AN ORDER";
      $("#idle-meta").innerHTML = "";
      runBtn.disabled = true;
      pauseBtn.disabled = true;
      return;
    }
    $("#auction-sub").textContent = `/ ${o.name.toUpperCase()}`;
    $("#idle-meta").innerHTML = `
      <span class="task">“${esc(o.task_prompt)}”</span>
      BUDGET <b>${usdc(o.budget_per_run_usdc)} USDC</b> / RUN · CAP <b>${usdc(o.max_total_budget_usdc)}</b> ·
      SPENT <b>${usdc(o.total_spent_usdc)}</b><br/>
      CADENCE ${o.cadence_seconds}s · MAX ${o.max_agents_to_query} AGENTS · STATUS ${esc(o.status).toUpperCase()}`;
    runBtn.disabled = o.status === "budget_exhausted";
    pauseBtn.disabled = false;
    pauseBtn.textContent = o.status === "paused" ? "▶ RESUME" : "⏸ PAUSE";
  }

  async function loadDetail() {
    if (!selectedId) return;
    try {
      selectedDetail = await api(`/standing-orders/${selectedId}`);
    } catch {
      selectedDetail = null;
    }
    renderLedger();
  }

  function statusHtml(r) {
    const cls = r.status === "failed" ? "failed" : r.status === "completed" ? "settled" : "";
    const fb = r.fallback_used ? ` <span class="fb">FB</span>` : "";
    return `<span class="${cls}">${esc(r.status).toUpperCase()}</span>${fb}`;
  }

  function renderLedger() {
    const runs = (selectedDetail && selectedDetail.runs) || [];
    const box = $("#run-rows");
    const done = runs.filter((r) => r.status === "completed");
    const spent = done.reduce((s, r) => s + (Number(r.amount_paid_usdc) || 0), 0);
    $("#ledger-summary").innerHTML = runs.length
      ? `${runs.length} RUNS · ${done.length} SETTLED · <b>${spent.toFixed(2)} USDC</b> PAID`
      : "—";
    if (!runs.length) {
      box.innerHTML = `<div class="empty-note">NO RUNS YET.<br/>OPEN THE MARKET →</div>`;
      return;
    }
    box.innerHTML = runs
      .map(
        (r, i) => `
      <div class="run-line" data-rid="${r.id}">
        <div class="rl-n">#${String(runs.length - i).padStart(3, "0")}</div>
        <div class="rl-mid">
          <div class="rl-winner">${esc(r.winner_agent_id ? nameFromRun(r) : "—")}</div>
          <div class="rl-status">${statusHtml(r)}</div>
        </div>
        <div class="rl-amt">${r.status === "completed" ? usdc(r.amount_paid_usdc) : "·"}</div>
      </div>`
      )
      .join("");
    $$(".run-line", box).forEach((el) =>
      el.addEventListener("click", () => openRunDialog(el.dataset.rid))
    );
  }

  function nameFromRun(r) {
    const q = (r.quotes || []).find(
      (q) => (q.agent_id || q.agentId) === r.winner_agent_id
    );
    return (q && (q.agent_name || q.agentName)) || short(r.winner_agent_id, 8);
  }

  /* ---------- run-now: the auction ---------- */

  async function runNow() {
    const o = currentOrder();
    if (!o || running) return;
    running = true;
    lastSeq = 0;
    bidders.clear();
    $("#stage-idle").classList.add("hidden");
    const live = $("#stage-live");
    live.classList.remove("hidden");
    $("#bidders").innerHTML = "";
    $("#stage-log").innerHTML = "";
    $("#budget-val").textContent = usdc(o.budget_per_run_usdc);
    startPolling();
    try {
      await api(`/standing-orders/${o.id}/run-now`, { method: "POST" });
    } catch (e) {
      log("err", "error", String(e.message || e));
    }
    // let the final events land, then settle back to idle state
    await sleep(2200);
    stopPolling();
    running = false;
    await refreshOrders();
    await loadDetail();
    renderIdle();
  }

  function startPolling() {
    stopPolling();
    const tick = async () => {
      if (!selectedId) return;
      try {
        const events = await api(`/standing-orders/${selectedId}/events?after=${lastSeq}`);
        events.forEach((ev) => {
          lastSeq = Math.max(lastSeq, ev.seq || 0);
          handleEvent(ev);
        });
      } catch {
        /* transient */
      }
    };
    tick();
    pollTimer = setInterval(tick, 700);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  /* ---------- event → stage ---------- */

  function log(cls, ev, text) {
    const box = $("#stage-log");
    const t = new Date().toLocaleTimeString("en-GB", { hour12: false });
    const line = document.createElement("div");
    line.className = `log-line ${cls}`;
    line.innerHTML = `<span class="t">${t}</span><span class="ev">${esc(ev)}</span>${text}`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  function bidderCard(agentId, name, isFallback) {
    if (bidders.has(agentId)) return bidders.get(agentId);
    const el = document.createElement("div");
    el.className = "bidder";
    el.dataset.agent = agentId;
    el.innerHTML = `
      <div class="b-name">${esc(name)}${isFallback ? ' <span class="b-base">FALLBACK</span>' : ""}</div>
      <div class="b-price">·</div>
      <div class="b-eta">AWAITING QUOTE…</div>
      <div class="b-score-track"><div class="b-score-bar"></div></div>
      <div class="b-score-label"></div>`;
    $("#bidders").appendChild(el);
    bidders.set(agentId, el);
    requestAnimationFrame(() => setTimeout(() => el.classList.add("dealt"), 30));
    return el;
  }

  function handleEvent(ev) {
    const d = ev;
    switch (ev.type) {
      case "run_started":
        log("", "run_started", "the market re-opens");
        break;

      case "candidates_discovered": {
        const cands = d.candidates || d.agents || [];
        log("", "candidates_discovered", `${cands.length || "?"} live agents on the floor`);
        cands.forEach((c, i) => {
          const id = c.agent_id || c.id || `c${i}`;
          const nm = c.agent_name || c.name || short(id, 8);
          setTimeout(() => bidderCard(id, nm, false), i * 200);
        });
        break;
      }

      case "quote_received": {
        const id = d.agent_id || (d.quote && d.quote.agent_id);
        const nm = d.agent_name || (d.quote && d.quote.agent_name) || short(id, 8);
        const price = d.price_usdc || (d.quote && d.quote.price_usdc);
        const eta = d.eta_seconds ?? (d.quote && d.quote.eta_seconds);
        const conf = d.confidence ?? (d.quote && d.quote.confidence);
        const el = bidderCard(id, nm, !!d.is_fallback);
        $(".b-price", el).textContent = `${usdc(price)} USDC`;
        $(".b-eta", el).textContent =
          `ETA ${eta ?? "?"}s${conf != null ? ` · CONF ${Number(conf).toFixed(2)}` : ""}`;
        log("", "quote_received", `<b>${esc(nm)}</b> bids ${usdc(price)} USDC`);
        break;
      }

      case "quote_missed":
        log("err", "quote_missed", `${esc(short(d.agent_id, 8))} — timeout, dropped`);
        break;

      case "quotes_scored": {
        (d.quotes || []).forEach((q) => {
          const id = q.agent_id || q.agentId;
          const el = bidders.get(id);
          if (!el) return;
          const score = Number(q.score ?? q.total_score ?? 0);
          $(".b-score-bar", el).style.width = `${Math.min(100, score * 100)}%`;
          const over = q.excluded || q.over_budget;
          $(".b-score-label", el).textContent = over
            ? "OVER BUDGET — EXCLUDED"
            : `SCORE ${score.toFixed(3)}`;
          if (over) {
            el.classList.add("excluded");
            el.insertAdjacentHTML("beforeend", '<span class="b-tag exc">EXCLUDED</span>');
          }
        });
        log("", "quotes_scored", "0.4·price + 0.35·rep + 0.25·speed");
        break;
      }

      case "fallback_triggered":
        log("err", "fallback_triggered", esc(d.reason || "primary market failed — fallback ladder"));
        break;

      case "no_provider_available":
        log("err", "no_provider", esc(d.reason || "no provider available"));
        break;

      case "winner_selected": {
        const w = d.winner || d;
        const id = w.agent_id || d.winner_agent_id;
        const nm = w.agent_name || short(id, 8);
        bidders.forEach((el, aid) => {
          if (aid === id) {
            el.classList.add("winner");
            if (!$(".b-tag.won", el))
              el.insertAdjacentHTML("beforeend", '<span class="b-tag won">HAMMER ↓ WON</span>');
          } else {
            el.classList.add("loser");
          }
        });
        log("ok", "winner_selected", `<b>${esc(nm)}</b>${d.reason ? " — " + esc(d.reason) : ""}`);
        break;
      }

      case "payment_pending":
        log("", "payment_pending", `settling with ${esc(d.agent_name || "winner")} · USDC on BASE`);
        break;

      case "payment_completed": {
        const tx = d.tx_hash || d.txHash || "";
        log(
          "ok",
          "payment_completed",
          tx
            ? `tx <span class="hash" data-h="${esc(tx)}">${esc(short(tx))}</span>`
            : "settled"
        );
        bindHashCopy();
        break;
      }

      case "receipt_generated":
        log("ok", "receipt_generated", d.receipt_hash ? `sha256 ${esc(short(d.receipt_hash))}` : "signed receipt filed");
        break;

      case "run_completed":
        log("ok", "run_completed", `status ${esc(d.status || "completed")} — the floor closes`);
        break;

      case "run_failed":
        log("err", "run_failed", esc(d.reason || "unknown"));
        break;

      default:
        log("", ev.type || "event", "");
    }
  }

  function bindHashCopy() {
    $$(".stage-log .hash").forEach((el) => {
      if (el.dataset.bound) return;
      el.dataset.bound = "1";
      el.addEventListener("click", (e) => {
        navigator.clipboard?.writeText(el.dataset.h || el.textContent);
        copyStamp(e.clientX, e.clientY);
      });
    });
  }

  /* ---------- pause / resume ---------- */

  async function togglePause() {
    const o = currentOrder();
    if (!o) return;
    const action = o.status === "paused" ? "resume" : "pause";
    try {
      await api(`/standing-orders/${o.id}/${action}`, { method: "POST" });
      await refreshOrders();
    } catch {
      /* noop */
    }
  }

  /* ---------- new order sheet ---------- */

  function openSheet() {
    $("#sheet-veil").classList.remove("hidden");
    $("#order-sheet").classList.remove("hidden");
    renderSheetPreview();
  }

  function closeSheet() {
    $("#sheet-veil").classList.add("hidden");
    $("#order-sheet").classList.add("hidden");
  }

  function closeDialog() {
    $("#dialog-veil").classList.add("hidden");
    $("#run-dialog").classList.add("hidden");
  }

  function renderSheetPreview() {
    const f = new FormData($("#create-form"));
    const per = Number(f.get("budget_per_run_usdc")) || 0;
    const max = Number(f.get("max_total_budget_usdc")) || 0;
    const cad = Number(f.get("cadence_seconds")) || 0;
    const runs = per > 0 ? Math.floor(max / per) : 0;
    $("#sheet-preview").innerHTML =
      `THIS ORDER FUNDS UP TO <b>${runs} RUNS</b> · ONE MARKET EVERY <b>${cad}s</b> · ` +
      `HARD CAP <b>${max.toFixed(2)} USDC</b> — OVER-BUDGET BIDS ARE EXCLUDED BEFORE SCORING.`;
  }

  async function onCreate(e) {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      name: f.get("name"),
      task_prompt: f.get("task_prompt"),
      category: f.get("category") || null,
      budget_per_run_usdc: String(f.get("budget_per_run_usdc")),
      max_total_budget_usdc: String(f.get("max_total_budget_usdc")),
      cadence_seconds: Number(f.get("cadence_seconds")),
      max_agents_to_query: Number(f.get("max_agents_to_query")),
    };
    try {
      const created = await api("/standing-orders", {
        method: "POST",
        body: JSON.stringify(body),
      });
      closeSheet();
      await refreshOrders();
      selectOrder(created.id);
    } catch (err) {
      alert(`Could not file the order:\n${err.message || err}`);
    }
  }

  /* ---------- run detail dialog ---------- */

  async function openRunDialog(runId) {
    let r;
    try {
      r = await api(`/runs/${runId}`);
    } catch {
      return;
    }
    const quotes = r.quotes || [];
    const rows = quotes
      .map((q) => {
        const id = q.agent_id || q.agentId;
        const won = id === r.winner_agent_id;
        const over = q.excluded || q.over_budget;
        return `<tr class="${won ? "won" : ""}${over ? " exc" : ""}">
          <td>${esc(q.agent_name || q.agentName || short(id, 8))}${won ? " ◂ WINNER" : ""}</td>
          <td>${usdc(q.price_usdc)}</td>
          <td>${q.eta_seconds ?? "—"}s</td>
          <td>${(q.reputation ?? q.confidence) != null ? Number(q.reputation ?? q.confidence).toFixed(2) : "—"}</td>
          <td>${q.score != null ? Number(q.score).toFixed(3) : over ? "EXC" : "—"}</td>
        </tr>`;
      })
      .join("");
    $("#run-dialog-body").innerHTML = `
      <div class="d-section">
        <div class="d-label">STATUS · ${esc(r.status).toUpperCase()} ${r.fallback_used ? "· FALLBACK USED" : ""} · MODE ${esc(r.mode || "?").toUpperCase()}</div>
        ${r.selection_reason ? `<div class="d-reason">${esc(r.selection_reason)}</div>` : ""}
      </div>
      <div class="d-section">
        <div class="d-label">EVERY BIDDER, EVERY PRICE</div>
        <table class="d-table">
          <thead><tr><th>AGENT</th><th>PRICE</th><th>ETA</th><th>REP</th><th>SCORE</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5">no quotes recorded</td></tr>'}</tbody>
        </table>
      </div>
      <div class="d-section">
        <div class="d-label">SETTLEMENT</div>
        <div class="d-hash">PAID ${usdc(r.amount_paid_usdc)} USDC${r.tx_hash ? ` · TX <a href="https://basescan.org/tx/${esc(r.tx_hash)}" target="_blank" rel="noopener">${esc(r.tx_hash)}</a>` : ""}</div>
      </div>
      ${r.receipt_hash || r.output_hash ? `
      <div class="d-section">
        <div class="d-label">PROOF BUNDLE</div>
        ${r.output_hash ? `<div class="d-hash">OUTPUT sha256:${esc(r.output_hash)}</div>` : ""}
        ${r.receipt_hash ? `<div class="d-hash">RECEIPT sha256:${esc(r.receipt_hash)}</div>` : ""}
      </div>` : ""}`;
    $("#dialog-veil").classList.remove("hidden");
    $("#run-dialog").classList.remove("hidden");
  }

  return { boot };
})();

/* ============================== start ============================== */

route();
