<div align="center">

# 🛰️ CROON RFQ

### An autonomous, recurring-demand engine for the CROO Agent Protocol

**Every standing order is recurring demand. Every CROO agent is my supply.**
**The owner owns an autonomous revenue engine — not rented software.**

`Python 3.11+` · `FastAPI` · `SQLModel` · `croo-sdk` · `USDC on Base`

</div>

---

## What it is

**CROON RFQ** turns a recurring need into a *standing order*: a budgeted, recurring
job. On **every single run**, CROON does not just re-hire the same agent — it
**re-opens the market**:

> **discover 2–3 CROO agents → request quotes → score (price × reputation × speed)
> → select the best under budget → hire the winner via CAP → pay in USDC on Base →
> emit a signed, proof-bundled receipt → append to run history.**

This is the core differentiator, and it is visible in both the code and the demo:
**this is not a cron job and not a plain orchestrator.** A cron job re-runs the
same command. CROON re-runs a *competitive procurement*.

### ✅ Proof it works — real on-chain settlements

CROON has executed **real, paid runs on Base** against **3 distinct live CROO
counterparties** (clears the anti-sybil ≥3 unique-counterparty bar):

| # | Counterparty | Amount | BaseScan tx |
|---|--------------|--------|-------------|
| 1 | Wallet Tracker | 0.10 USDC | `0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f` |
| 2 | Polymind | 0.10 USDC | `0xf4bfa32db4d25ddec835b7570cdf36bdd7851192c6c6b514fbb265872bb1c2fd` |
| 3 | Polymarket Broker | 0.10 USDC | `0x387a240ff9ab48178c313f7558e10cacc438cdf82847dd9d10bb40ad803d0ed7` |

> Each is a genuine `pay_order` USDC transfer on Base, negotiated + settled through
> the real `croo-sdk`. Open any hash at `https://basescan.org/tx/<hash>`.

---

## Why this matters (the "not a cron job" section)

| A cron job / orchestrator | **CROON RFQ** |
|---------------------------|---------------|
| Re-runs the same command | **Re-opens a market** every run |
| Hard-wired to one provider | **Competitively selects** among 2–3 agents |
| No price discipline | **Hard budget rule** — over-budget bids excluded |
| Stateless | **Stateful**: budgets, spend, full run history in SQLite |
| Crashes on provider failure | **Budget-protecting fallback ladder**, never crashes |
| No proof of work/spend | **Signed receipt**: every bidder, the winner + *why*, tx hash, output hash, receipt hash |

The standing order is a **historical commercial relationship**, not a script. The
owner accrues run history, spend accounting, and a portfolio of on-chain receipts —
an asset they own, not a SaaS subscription they rent.

---

## Architecture at a glance

Four thin layers, one dependency direction, and a single door to CAP. Full detail
(with diagrams) in **[`ARCHITECTURE.md`](./ARCHITECTURE.md)**.

```
HTTP API + Demo UI  (croon/api.py, croon/static/)
        │
   ┌────┴─────────────┐         ┌──────────────────────────────┐
   │ B: Scheduler     │◀───────▶│ A: Standing Order Store        │
   │ cadence + run_now│  state  │ StandingOrder · Run (SQLite)   │
   └────┬─────────────┘         └──────────────────────────────┘
        │ execute_run(order)
        ▼
   C+D: Mini-RFQ Engine + Settlement  (croon/engine.py, scoring.py)
   discover → quote → score → select → hire+pay → delivery → receipt
        │  (the ONLY door to CAP)
        ▼
   CapClient boundary  (croon/cap_client.py)
   ├─ MockCapClient   deterministic fake market (no keys/network)
   └─ LiveCapClient   real croo-sdk → USDC on Base
```

- **Layer A — Standing Order Store** (`models.py`): budgets, cadence, run history.
- **Layer B — Scheduler** (`scheduler.py`): in-process cadence loop + `run_now`.
- **Layer C — Mini-RFQ Engine** (`engine.py`, `scoring.py`): the differentiator.
- **Layer D — Settlement + Receipt** (`engine.py`, `LiveCapClient`): pay + prove.

---

## Scoring (transparent, documented, `croon/scoring.py`)

```
score = w_price · price_score  +  w_rep · rep_score  +  w_speed · speed_score
```

- **Hard budget rule:** any quote with `price > budget_per_run` is **excluded
  before scoring** (still shown in the UI, with the exclusion reason).
- `price_score` / `speed_score`: normalized over the eligible set — cheaper /
  faster scores higher.
- `rep_score`: MVP placeholder = the quote's self-reported `confidence`.
  Deliberately **not** a reputation oracle (out of scope).
- Default weights `w_price=0.4, w_rep=0.35, w_speed=0.25` — configurable in `.env`.
- Every run stores a human-readable `selection_reason`, e.g.
  `"best score under budget: score 0.83, price 0.20 USDC, eta 20s, rep 0.91"`.

---

## The `CapClient` boundary + demo safety net

**All** CAP interaction goes through `croon/cap_client.py` — nowhere else. This is
the isolation contract that keeps SDK uncertainty in one place and lets us flip the
entire app between fake and real with **one env var**:

```bash
CROON_CAP_MODE=mock   # deterministic fake market — no keys, no network, no wallet
CROON_CAP_MODE=live   # real croo-sdk → negotiate + pay USDC on Base
```

### Exact CAP SDK methods used (`LiveCapClient`)

| Our interface | Real `croo-sdk` |
|---------------|-----------------|
| `hire_and_pay` | `AgentClient.negotiate_order(NegotiateOrderRequest)` → poll `get_negotiation` / `list_orders(role="buyer")` until order status `created` → `pay_order(order_id)` → `tx_hash` |
| `get_delivery` | `AgentClient.get_delivery(order_id)` (async; polled) |
| `discover_agents` | **No SDK primitive** — configured roster of Store `service_id`s |
| `request_quote` | **No native quote** — derived from listed price/SLA (no on-chain action) |

Auth: `AgentClient(Config(...), "croo_sk_...")`. Confirmed live behaviours the
adapter handles: the `creating → created` order lifecycle (paying too early 400s
`INVALID_STATUS`), negotiation→order resolution via `list_orders`, provider-enforced
`requirements` with no describe endpoint, and **asynchronous delivery** (a paid run
is valid on its tx hash even if the deliverable arrives later —
`paid_delivery_pending`).

---

## Fallback = risk management (spec §7)

CROON never crashes a run and never wrong-routes work:

- **< 1 valid quote** (network stall / no responders) → route to a
  **capability-matched** base agent, hire+pay normally, mark `fallback_used`.
- **All quotes over budget** → try a capable fallback once.
- **Winner refuses to transact** (negotiation REJECTED/EXPIRED/timeout) → route to
  a *different* capable fallback.
- **No capability-appropriate provider** → `no_provider_available`, **spend = 0**.

Capability matching means a "risk brief" is never routed to a gas oracle. This makes
our two base agents **real supply**, not dead hedges.

---

## Two base agents (also the fallback pool, spec §10)

Standalone, CAP-callable, USDC-settling, Store-listable providers:

1. **Listing Copy Agent** (`agents/listing_copy.py`) — repo/description →
   Agent Store listing copy (tagline + 3 bullets + category). ~0.05 USDC.
2. **Base Gas Oracle** (`agents/gas_oracle.py`) — current Base gas + estimated
   USDC-transfer / CAP-call cost. ~0.01 USDC.

Served as live CAP providers by `croon/provider_worker.py` (SDK WebSocket → accept
negotiation → deliver on `ORDER_PAID`).

---

## HTTP API (spec §8)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/standing-orders` | create a standing order |
| `GET`  | `/standing-orders` | list |
| `GET`  | `/standing-orders/{id}` | detail + run history |
| `POST` | `/standing-orders/{id}/run-now` | **trigger a run now (demo critical)** |
| `POST` | `/standing-orders/{id}/pause` | pause |
| `POST` | `/standing-orders/{id}/resume` | resume |
| `GET`  | `/standing-orders/{id}/events?after=<seq>` | live event feed for the UI |
| `GET`  | `/runs/{run_id}` | full run detail incl. receipt bundle |
| `GET`  | `/health` | mode + provider status |
| `GET`  | `/` | demo UI |

### How `run_now` works

`POST /standing-orders/{id}/run-now` → `Scheduler.trigger(id)` → `execute_run(order)`.
The engine persists a `Run` immediately, then streams events
(`run_started → candidates_discovered → quote_received × N → quotes_scored →
winner_selected → payment_pending → payment_completed → receipt_generated →
run_completed`) to an in-memory bus. The UI polls `/events` (~1s) and renders the
mini-RFQ moment live. Judges click one button and watch the market clear.

---

## Quickstart (single command)

```bash
# 1. install
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. configure (defaults to safe MOCK mode)
cp .env.example .env

# 3. run — serves API + demo UI on http://127.0.0.1:8000
python run.py
```

Open **http://127.0.0.1:8000**, create/seed a standing order, hit **Run now**, and
watch quotes arrive → scoring → winner → payment → receipt.

Seed a few runs for a populated history:

```bash
python -m scripts.seed
```

### Going live (real USDC on Base)

1. Get a `croo_sk_...` key from the CROO Dashboard and **fund CROON's AA wallet
   with USDC on Base** (payment precondition — the SDK raises insufficient-balance
   otherwise).
2. In `.env`:
   ```bash
   CROON_CAP_MODE=live
   CROON_CROO_SDK_KEY=croo_sk_...
   CROON_LIVE_CANDIDATES_JSON='[{"agent_id":"...","service_id":"...","name":"...","listed_price_usdc":"0.10","listed_eta_seconds":30,"reputation":0.7}]'
   CROON_FALLBACK_SERVICE_ID=...      # our base agent's Store service id
   CROON_FALLBACK_AGENT_ID=...
   ```
3. Execute one real end-to-end paid run (probe first — free, no spend):
   ```bash
   # dry run: negotiate + create order, stop BEFORE paying
   CROON_CAP_MODE=live python -m scripts.live_order --agent-id <id> \
     --requirements '{"market_id":"..."}' --probe-only

   # real run: negotiate → create → pay USDC on Base → persist idempotently
   CROON_CAP_MODE=live python -m scripts.live_order --agent-id <id> \
     --requirements '{"market_id":"..."}'
   ```

Inspect on-chain runs:

```bash
sqlite3 croon.db -header -column \
  "SELECT substr(id,1,8) run, winner_agent_id, amount_paid_usdc amt, tx_hash \
   FROM run WHERE mode='live' ORDER BY started_at;"
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Covers the brokerage cycle (spend guard + **durable idempotency**: no double-pay
across a simulated restart) and the provider worker.

---

## Known limitations (honest)

- **`discover_agents` is roster-based in live mode.** The SDK exposes no
  search/discovery, so live candidates come from a configured list of Store
  `service_id`s. Documented and isolated in `LiveCapClient`.
- **Quotes are derived, not negotiated.** CAP has no native quote primitive; a
  quote is the candidate's listed price/SLA. Only the winner is actually
  negotiated + paid (quoting stays cheap and side-effect free).
- **Reputation is a placeholder** (quote confidence / prior successful runs), not a
  real reputation oracle — intentionally out of scope.
- **Delivery is asynchronous.** A freshly paid run may show
  `paid_delivery_pending`; the deliverable is re-fetchable later. The tx hash is
  the proof of spend.
- **USDC token:** targets canonical native USDC on Base
  (`0x8335…2913`), not bridged USDbC. Verify before large spends.
- **Scheduler is demo-grade** — a simple in-process interval loop, not production
  cron.

---

## Project layout

```
croon/            app package — api, scheduler, engine, scoring, cap_client,
                  brokerage, provider_worker, models, schemas, config, events, db
croon/static/     single-page demo UI (index.html, app.js, style.css)
agents/           two standalone base agents (listing_copy, gas_oracle) + provider
scripts/          live_order, seed, readiness_check, probes, and other tooling
tests/            brokerage + provider_worker unit tests
ARCHITECTURE.md   deep-dive with diagrams
```

---

## License

[MIT](./LICENSE) © 2026 CROON RFQ
