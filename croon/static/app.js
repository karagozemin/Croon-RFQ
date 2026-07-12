// CROON RFQ — demo UI controller.
// Renders the mini-RFQ money shot (quotes -> scoring -> winner -> payment ->
// receipt) by polling the per-standing-order event feed, plus run history.

const $ = (sel) => document.querySelector(sel);
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.status === 204 ? null : res.json();
};

const state = {
  selectedId: null,
  lastSeq: 0,
  pollTimer: null,
};

// --- Standing orders --------------------------------------------------------

async function loadOrders() {
  const orders = await api("/standing-orders");
  const ul = $("#order-list");
  ul.innerHTML = "";
  orders.forEach((o) => {
    const li = document.createElement("li");
    if (o.id === state.selectedId) li.classList.add("active");
    li.innerHTML = `
      <div class="o-name">${escapeHtml(o.name)}</div>
      <div class="o-meta">
        <span>${o.budget_per_run_usdc} USDC/run</span>
        <span class="status-pill status-${o.status}">${o.status}</span>
      </div>`;
    li.onclick = () => selectOrder(o.id);
    ul.appendChild(li);
  });
  if (!state.selectedId && orders.length) selectOrder(orders[0].id);
}

async function selectOrder(id) {
  state.selectedId = id;
  state.lastSeq = 0;
  $("#rfq-stream").innerHTML = "";
  await loadOrders();
  await refreshDetail();
  $("#run-now-btn").disabled = false;
  $("#pause-btn").disabled = false;
  startPolling();
}

async function refreshDetail() {
  if (!state.selectedId) return;
  const o = await api(`/standing-orders/${state.selectedId}`);
  $("#rfq-order-meta").classList.remove("muted");
  $("#rfq-order-meta").innerHTML = `
    <b>${escapeHtml(o.name)}</b> · budget/run <b>${o.budget_per_run_usdc} USDC</b>
    · category ${o.category || "—"} · policy <code>${o.selection_policy}</code>`;
  const nextRun = new Date(o.next_run_at).toLocaleTimeString();
  $("#order-summary").classList.remove("muted");
  $("#order-summary").innerHTML = `
    Total spent <b>${o.total_spent_usdc}</b> / ${o.max_total_budget_usdc} USDC
    · status <b>${o.status}</b> · next run ~${nextRun}`;
  renderRuns(o.runs || []);
  const pb = $("#pause-btn");
  pb.textContent = o.status === "paused" ? "▶ Resume" : "⏸ Pause";
}

function renderRuns(runs) {
  const tb = $("#run-rows");
  tb.innerHTML = "";
  runs.forEach((r, i) => {
    const n = runs.length - i;
    const tx = r.tx_hash
      ? `<a href="https://basescan.org/tx/${r.tx_hash}" target="_blank" rel="noopener">${short(r.tx_hash)}</a>`
      : "—";
    const fb = r.fallback_used ? ` <span class="tag-fallback">⚠ fallback</span>` : "";
    const mode = r.mode === "live"
      ? `<span class="mode-badge mode-live">● LIVE</span>`
      : `<span class="mode-badge mode-mock">mock</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${n}</td>
      <td>${mode}</td>
      <td>${escapeHtml(r.winner_agent_id || "—")}${fb}</td>
      <td>${r.amount_paid_usdc}</td>
      <td>${r.status}</td>
      <td>${tx}</td>`;
    tb.appendChild(tr);
  });
}

// --- Run now / pause --------------------------------------------------------

$("#run-now-btn").onclick = async () => {
  if (!state.selectedId) return;
  const btn = $("#run-now-btn");
  btn.disabled = true;
  $("#rfq-stream").innerHTML = "";
  state.lastSeq = 0;
  try {
    // Fire and forget — events + history update via polling.
    await api(`/standing-orders/${state.selectedId}/run-now`, { method: "POST" });
  } catch (e) {
    addEvent({ type: "run_failed", reason: e.message });
  } finally {
    btn.disabled = false;
    await refreshDetail();
  }
};

$("#pause-btn").onclick = async () => {
  if (!state.selectedId) return;
  const o = await api(`/standing-orders/${state.selectedId}`);
  const action = o.status === "paused" ? "resume" : "pause";
  await api(`/standing-orders/${state.selectedId}/${action}`, { method: "POST" });
  await refreshDetail();
  await loadOrders();
};

// --- Live event polling -----------------------------------------------------

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(pollEvents, 1000);
  pollEvents();
}

async function pollEvents() {
  if (!state.selectedId) return;
  try {
    const events = await api(
      `/standing-orders/${state.selectedId}/events?after=${state.lastSeq}`
    );
    if (events.length) {
      events.forEach((e) => {
        state.lastSeq = Math.max(state.lastSeq, e.seq);
        addEvent(e);
      });
      // A terminal event means history changed — refresh it.
      if (events.some((e) => ["receipt_generated", "run_completed", "run_failed"].includes(e.type))) {
        refreshDetail();
      }
    }
  } catch (_) {
    /* transient — ignore */
  }
}

// --- Event rendering (the money shot) ---------------------------------------

function addEvent(e) {
  const li = document.createElement("li");
  li.className = `ev-${e.type}`;
  li.innerHTML = renderEvent(e);
  $("#rfq-stream").appendChild(li);
  li.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderEvent(e) {
  switch (e.type) {
    case "run_started":
      return title("▶ Run started", `${escapeHtml(e.order_name || "")} · budget/run ${e.budget_per_run} USDC`);
    case "candidates_discovered":
      return title("🔎 RFQ sent to", (e.agents || []).map((a) => escapeHtml(a.name)).join(", "));
    case "quote_received": {
      const q = e.quote;
      const base = q.is_base_agent ? `<span class="badge-base">BASE</span>` : "";
      return title(
        `💬 Quote — ${escapeHtml(q.agent_name)}${base}`,
        `<span class="quote-line">${q.price_usdc} USDC · ETA ${q.eta_seconds}s · conf ${q.confidence}</span>`
      );
    }
    case "quote_missed":
      return title("⌛ No response", `${escapeHtml(e.agent_id)} timed out / declined`);
    case "quotes_scored":
      return title("📊 Scoring", scoreTable(e.quotes || []));
    case "winner_selected":
      return title(
        `🏆 Winner — ${escapeHtml(e.winner.agent_name)}`,
        `${escapeHtml(e.reason)}`
      );
    case "payment_pending":
      return title("⏳ CAP payment", `hiring ${escapeHtml(e.agent_name)} · settling USDC on Base…`);
    case "payment_completed": {
      const tx = e.tx_hash
        ? `<a class="hash" href="https://basescan.org/tx/${e.tx_hash}" target="_blank" rel="noopener">${e.tx_hash}</a>`
        : "(no tx)";
      return title("✅ Payment completed", `${e.amount_usdc} USDC · tx ${tx}`);
    }
    case "receipt_generated":
      return title("🧾 Receipt generated", `hash <span class="hash">${e.receipt_hash}</span><br/>status ${e.status} · total spent ${e.total_spent} USDC`);
    case "fallback_triggered":
      return title("⚠ Fallback", escapeHtml(e.message || "routing to fallback provider"));
    case "run_completed":
      return title("🎉 Run complete", `status ${e.status}`);
    case "run_failed":
      return title("❌ Run failed", escapeHtml(e.reason || "unknown error"));
    default:
      return title(e.type, "");
  }
}

function scoreTable(quotes) {
  return quotes
    .map((q) => {
      const s = q.excluded
        ? `<span style="color:var(--danger)">excluded — ${escapeHtml(q.exclusion_reason || "over budget")}</span>`
        : `score <b>${(q.score ?? 0).toFixed(3)}</b>`;
      return `<div class="quote-line">${escapeHtml(q.agent_name)}: ${q.price_usdc} USDC / ${q.eta_seconds}s / conf ${q.confidence} → ${s}</div>`;
    })
    .join("");
}

function title(t, body) {
  return `<div class="ev-title">${t}</div>${body ? `<div class="ev-body">${body}</div>` : ""}`;
}

// --- Create form ------------------------------------------------------------

$("#create-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    name: f.name.value,
    task_prompt: f.task_prompt.value,
    category: f.category.value || null,
    budget_per_run_usdc: f.budget_per_run_usdc.value,
    max_total_budget_usdc: f.max_total_budget_usdc.value,
    cadence_seconds: parseInt(f.cadence_seconds.value, 10),
    max_agents_to_query: parseInt(f.max_agents_to_query.value, 10),
    acceptance_criteria: [],
  };
  const created = await api("/standing-orders", {
    method: "POST",
    body: JSON.stringify(body),
  });
  await selectOrder(created.id);
};

// --- Utils ------------------------------------------------------------------

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function short(h) {
  return h && h.length > 14 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;
}

// --- Boot -------------------------------------------------------------------

(async function boot() {
  try {
    const h = await api("/health");
    const badge = $("#cap-mode");
    badge.textContent = `${h.cap_mode} mode`;
    badge.classList.add(h.cap_mode);
  } catch (_) {}
  await loadOrders();
})();
