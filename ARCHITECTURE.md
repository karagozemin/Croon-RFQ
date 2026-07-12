# CROON RFQ — Architecture

> **One line:** Every standing order is *recurring demand*. Every CROO agent is
> *my supply*. On **every** run CROON re-opens a competitive market — quote →
> score → select under budget → settle on-chain → signed receipt. The owner owns
> an autonomous revenue engine, not rented software.

This document explains **how the system is built**, **why the boundaries sit where
they do**, and **what actually happens on each run**. It is written to match the
code 1:1 — file and symbol names below are real.

---

## 1. Design principles (the non-negotiables)

| # | Principle | Where it lives in code |
|---|-----------|------------------------|
| 1 | **This is not a cron job.** Every run re-opens the market. | `croon/engine.py :: execute_run` |
| 2 | **Off-chain state, on-chain settlement.** Budgets/history/scoring live in SQLite; only USDC payment + tx hash touch Base. | `croon/models.py`, `LiveCapClient.hire_and_pay` |
| 3 | **All CAP uncertainty is isolated in ONE file.** The engine never imports the SDK. | `croon/cap_client.py` |
| 4 | **One env var flips the whole app** between deterministic mock and real on-chain. | `CROON_CAP_MODE` → `build_cap_client()` |
| 5 | **Never crash a run; protect the budget.** No bids / over-budget / winner refuses → capability-matched fallback or *zero spend*. | `execute_run` fallback ladder |
| 6 | **Idempotency is durable, not in-memory.** A WS replay after a restart must never double-pay. | `BrokerageOrder`, `ProviderJob` tables |
| 7 | **A run is only `live` if its tx is confirmed on-chain.** Resilience must never fake a settlement. | `LiveCapClient._verify_tx_on_chain`, `FailoverCapClient.paid_via_mock` |

---

## 2. The four layers

CROON is intentionally split into four thin layers with a single dependency
direction (top → down). Nothing below reaches back up.

```
┌──────────────────────────────────────────────────────────────────────┐
│  HTTP API + Demo UI            croon/api.py, croon/static/*            │
│  create order · run-now · pause · run detail · live event feed         │
└───────────────┬────────────────────────────────────────────────────────┘
                │
   ┌────────────┴───────────┐        ┌──────────────────────────────────┐
   │  Layer B: Scheduler     │        │  Layer A: Standing Order Store    │
   │  croon/scheduler.py     │        │  croon/models.py (SQLite/SQLModel)│
   │  in-process cadence loop│◀──────▶│  StandingOrder · Run              │
   │  + run_now trigger      │  state │  (budgets, cadence, history)      │
   └────────────┬───────────┘        └──────────────────────────────────┘
                │ fires execute_run(order)
                ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │  Layer C+D: Mini-RFQ Engine + Settlement    croon/engine.py          │
   │  discover → quote (parallel, timeout) → score → select →             │
   │  hire+pay → delivery → receipt → persist Run                         │
   │                     │                          ▲                     │
   │                     ▼ scoring.py               │ schemas.py          │
   └─────────────────────┬──────────────────────────┴─────────────────────┘
                         │ (the ONLY door to CAP)
                         ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │  CapClient boundary            croon/cap_client.py                   │
   │  ┌──────────────┐  ┌────────────────────┐  ┌────────────────────┐   │
   │  │ MockCapClient│  │ LiveCapClient      │  │ FailoverCapClient  │   │
   │  │ deterministic│  │ → croo-sdk (PyPI)  │  │ live-first, honest │   │
   │  │ fake market  │  │ + RPC tx-verify    │  │ mock fallback      │   │
   │  └──────────────┘  └─────────┬──────────┘  └────────────────────┘   │
   └────────────────────────────────┼───────────────────────────────────┘
                                     ▼
                           ┌───────────────────┐
                           │  CROO CAP network  │
                           │  USDC on Base      │
                           └───────────────────┘
```

### Layer A — Standing Order Store (`croon/models.py`)
The "this is not a cron job" proof: **stateful, budgeted, historical commercial
relationships.**

- `StandingOrder` — recurring job: `task_prompt`, `category`, `cadence_seconds`,
  `budget_per_run_usdc`, `max_total_budget_usdc`, `total_spent_usdc`, `status`
  (`active | paused | budget_exhausted`), `next_run_at`.
- `Run` — one full mini-RFQ + settlement cycle. Carries the full `quotes_json`
  (every bidder + score), `winner_agent_id`, `selection_reason`,
  `amount_paid_usdc`, **`tx_hash` (BaseScan-linkable)**, `output_hash`,
  `receipt_hash`, `fallback_used`, and a **`mode` (`mock | unverified | live`)**
  column so only RPC-verified on-chain runs are ever labelled `live`.

Budgets and standing-order state live **here, off-chain** — never in a contract.

### Layer B — Scheduler (`croon/scheduler.py`)
A deliberately simple in-process interval loop (NOT production cron). It checks
for due orders and fires `execute_run`. The demo-critical path is
`POST /standing-orders/{id}/run-now`, which delegates to
`Scheduler.trigger(order_id)` so cadence bookkeeping stays in one place.

### Layer C — Mini-RFQ Engine (`croon/engine.py` + `croon/scoring.py`)
The differentiator, kept **thin**: discover 2–3 candidates, quote them in
parallel under a strict timeout, score, select the winner under budget.

### Layer D — Settlement + Receipt (`croon/engine.py` + `LiveCapClient`)
Hire the winner via CAP, pay USDC on Base, fetch the delivery, assemble a
hashed receipt bundle, and append the `Run` to history.

---

## 3. The `CapClient` boundary (the isolation contract)

Everything uncertain about the real SDK lives in **one file**, `croon/cap_client.py`.
The engine codes against the abstract interface only:

```python
class CapClient(abc.ABC):
    async def discover_agents(category, limit)        -> list[AgentInfo]
    async def request_quote(agent, task, timeout_s)   -> Quote | None
    async def hire_and_pay(agent, task, price)        -> Settlement
    async def get_delivery(order_id)                  -> Delivery
```

Three implementations / one factory:

- **`MockCapClient`** — a deterministic fake market (5 agents incl. two base
  agents). No network, no keys, no wallet. Per-run jitter is derived from a
  sha256 of `(agent_id + prompt)` so demos look "live" yet reproduce exactly.
- **`LiveCapClient`** — the real `croo-sdk` adapter (see §7). Every `Settlement`
  it returns carries a `tx_verified` flag from an independent Base-RPC check
  (`_verify_tx_on_chain`), so the engine can tell a *confirmed* on-chain payment
  apart from an SDK ack that never landed.
- **`FailoverCapClient`** — what `live` mode *actually* runs. It wraps the live
  client and, on any live failure (startup OR per-call), automatically falls back
  to `MockCapClient` so a demo never crashes. Crucially it stays **honest**: if
  the *settlement* step fell back to mock it sets a sticky `paid_via_mock` flag
  (reset per run via `begin_run()`), so the engine labels the run `mock` — never a
  fake `live`. See §7.
- **`build_cap_client()`** — returns the implementation implied by
  `CROON_CAP_MODE`. `live` → `FailoverCapClient(LiveCapClient, MockCapClient)`;
  `mock` → `MockCapClient`.

**Quote semantics (spec §4):** CAP has **no native quote primitive**. A quote is
**derived** from each candidate's listed price/SLA. We deliberately do *not* open
a negotiation just to quote (that would create dangling on-chain state and cost
gas) — only the **winner** is negotiated and paid.

---

## 4. What happens on one run (the money shot)

```
execute_run(order)                                            croon/engine.py
────────────────────────────────────────────────────────────────────────────
 0. cap.begin_run()  (reset failover flags) ── then persist Run(status=running)
 1. BUDGET GUARD  remaining = max_total − spent
        remaining < budget_per_run  →  status=budget_exhausted, STOP
 2. DISCOVER      cap.discover_agents(category, max_agents_to_query)
 3. QUOTE         cap.request_quote(...) for each candidate, IN PARALLEL,
                  each wrapped in asyncio.wait_for(timeout=RFQ_TIMEOUT)
                  → timeouts / None are dropped; round proceeds with the rest
 4. SCORE         score_quotes(quotes, budget_per_run)          scoring.py
                  → over-budget quotes EXCLUDED (hard rule)
                  → winner = max weighted score
 5. HIRE + PAY    cap.hire_and_pay(winner, task, price)  → Settlement(tx_hash,
                                                            tx_verified)
 6. DELIVERY      cap.get_delivery(order_id)   (async; graceful if pending)
 7. RECEIPT       sha256(output) + sha256(full receipt bundle)
 8. LABEL MODE    paid_via_mock          → mode=mock       (SIMULATED)
                  tx_verified is False    → mode=unverified (no BaseScan link)
                  real pay + tx confirmed → mode=live       (● LIVE + tx link)
 9. PERSIST       Run.status=completed|fallback_used, tx_hash, hashes, mode;
                  order.total_spent += amount;  maybe status=budget_exhausted
────────────────────────────────────────────────────────────────────────────
   Every step emits an event to EVENT_BUS → the UI renders it live.
```

### Sequence (happy path)

```
UI            API            Scheduler        Engine           CapClient        Base
 │  run-now →   │               │               │                 │             │
 │             ─┼─ trigger ─────▶│               │                 │             │
 │              │               │─ execute_run ─▶│                 │             │
 │              │               │               │─ discover ──────▶│             │
 │              │               │               │◀─ 3 agents ──────│             │
 │              │               │               │─ quote ×3 ──────▶│ (parallel)  │
 │◀─ poll events (quote_received × N) ───────────│◀─ quotes ────────│             │
 │              │               │               │─ score/select ──┐│             │
 │◀─ winner_selected ────────────────────────────│◀────────────────┘│             │
 │              │               │               │─ hire_and_pay ──▶│─ pay_order ─▶│
 │              │               │               │                 │─ verify tx ─▶│
 │◀─ payment_completed (tx_hash, mode) ──────────│◀─ Settlement ────│◀─ tx hash ──│
 │              │               │               │─ get_delivery ──▶│             │
 │◀─ receipt_generated (receipt_hash) ───────────│─ persist Run ───┐             │
 │◀─ run_completed ──────────────────────────────│◀────────────────┘             │
```

---

## 5. Scoring — transparent by design (`croon/scoring.py`)

```
score = w_price · price_score  +  w_rep · rep_score  +  w_speed · speed_score
```

- **Hard budget rule:** any quote with `price > budget_per_run` is **excluded
  before scoring** (recorded with an `exclusion_reason`, still shown in the UI).
- `price_score` / `speed_score`: normalized over the *eligible* set so the
  cheapest / fastest scores `1.0`; ties give everyone a neutral `1.0`.
- `rep_score`: MVP placeholder = the quote's self-reported `confidence`
  (0..1). **Deliberately not a reputation oracle** — that's out of scope.
- Default weights `w_price=0.4, w_rep=0.35, w_speed=0.25`, configurable via
  `.env`.
- Output is a `SelectionResult(winner, scored_quotes, reason)` where `reason` is
  human-readable, e.g.
  `"best score under budget: score 0.83, price 0.20 USDC, eta 20s, rep 0.91 …"`.

Everything is pure and deterministic, so the UI can explain *exactly why* a
winner won.

---

## 6. Fallback ladder — budget-protecting risk management (spec §7)

CROON never crashes a run and never wrong-routes work. The engine walks a ladder;
every rung either finds a **capability-matched** provider under budget or spends
**nothing**:

```
                 quotes < 1 ?
                     │yes
   capable fallback? ─── no ──▶  no_provider_available   (spend = 0)
                     │yes
             re-quote fallback, re-enter scoring
                     │
   winner == None (all over budget)?
                     │yes → try capable fallback once → still None → no_provider
                     │no
   hire_and_pay(winner)
                     │  raises (provider REJECTED/EXPIRED/timeout)?
                     │yes → route to a *different* capable fallback → pay it
                     ▼
                 settled ✔
```

**Capability matching** (`_capable_fallbacks`) is key: routing a "risk brief" to
a gas oracle would be wrong-capability routing, so a fallback is only eligible if
the task `category` matches its declared capabilities. If `category` is `None` we
refuse to guess and spend nothing. This is what makes the base agents **real
supply**, not dead hedges.

---

## 7. Live CAP integration + the honesty guard (`LiveCapClient` / `FailoverCapClient`)

### 7.1 Mapping onto the real `croo-sdk`

| Our method | Real SDK path |
|-----------|----------------|
| `discover_agents` | **No SDK primitive.** Candidates come from a configured roster of Store `service_id`s (`CROON_LIVE_CANDIDATES_JSON`). Honest + documented. |
| `request_quote` | **No native quote.** Derived from listed price/SLA — no on-chain action. |
| `hire_and_pay` | `negotiate_order(NegotiateOrderRequest)` → provider accepts → order `creating`→`created` → `pay_order(order_id)` (USDC on Base) → `tx_hash`. |
| `get_delivery` | `get_delivery(order_id)` — polled; async and may 404 until the provider fulfils. |

Critical real-world behaviours the adapter handles (all confirmed on-chain):

- **Order lifecycle:** an accepted negotiation spawns an order in `creating`
  while its create-tx confirms, then flips to `created` (payable). `pay_order`
  400s (`INVALID_STATUS`) if called during `creating`, so
  `_await_order_created` polls past it.
- **Negotiation has no `order_id`:** the order is resolved via `list_orders`
  matching on `negotiation_id`. `list_orders` requires `role="buyer"`.
- **`requirements` is provider-enforced with no describe endpoint.** Sending an
  unrecognised field is rejected (`INVALID_PARAMETERS`). `_build_requirements`
  supports a per-service override (dict/str) or a schema-agnostic bare JSON
  string default.
- **Delivery is asynchronous.** `pay_order` settles USDC immediately, but the
  deliverable comes later. `get_delivery` polls briefly then **degrades
  gracefully** — a paid run is valid on the strength of its tx hash even if the
  deliverable isn't ready yet (status `paid_delivery_pending`).
- **Precondition:** CROON's AA wallet must hold USDC on Base before `pay_order`,
  or the SDK raises insufficient-balance.

### 7.2 The honesty guard (anti-"fake demo")

Because `live` mode runs on `FailoverCapClient`, a live failure silently falls
back to the deterministic mock market. That is great for demo resilience and
**dangerous for integrity** — a demo could *look* live while settling on mock.
Two independent mechanisms make that impossible:

1. **On-chain verification.** After `pay_order` returns a `tx_hash`,
   `LiveCapClient._verify_tx_on_chain` calls `eth_getTransactionByHash` on the
   configured Base RPC. If the hash isn't found (off-chain id, wrong chain,
   optimistic ack), `Settlement.tx_verified = False`. RPC/network errors are
   treated as *unverified* (False), never raised — a flaky RPC never crashes a
   run whose payment may well be valid; the run is simply labelled honestly.
2. **Sticky mock-fallback flag.** If the **settlement step itself** fell back to
   mock, `FailoverCapClient` sets a sticky `paid_via_mock = True` (and the
   broader `any_mock_fallback`). `begin_run()` resets these at the top of every
   run so one run's fallback can't leak into the next.

The engine (`croon/engine.py`) collapses both signals into a truthful three-state
`Run.mode`:

| Condition | `run.mode` | UI |
|-----------|-----------|----|
| `paid_via_mock` (settlement degraded to mock) | `mock` | SIMULATED badge |
| `settlement.tx_verified is False` (SDK ack, no on-chain tx) | `unverified` | UNVERIFIED — **no** clickable BaseScan link; emits `settlement_unverified` |
| real `pay_order` + tx confirmed on Base RPC | `live` | ● LIVE + clickable BaseScan tx |

This is the direct, code-level answer to the hackathon's *fake demo / broken CAP
integration* hard-disqualification: **CROON can only label a run `live` when a
real tx is independently confirmed on-chain.**

---

## 8. Two roles, two sides of the market

CROON is both **buyer** and **seller** on CAP.

```
        DEMAND side                             SUPPLY side
  (CROON hires others)                    (others hire CROON)

  Scheduler → execute_run                 CAP WS → ProviderWorker
        │  Run rows (history)                   │  ProviderJob rows (ledger)
        ▼                                        ▼
  CapClient.hire_and_pay                   base agents: Listing Copy, Gas Oracle
        │                                        │  (also CROON's fallback pool)
        ▼                                        ▼
  ── pay child on Base ──▶            ◀── get paid on Base ──

  MAIN service brokerage (croon/brokerage.py):
  a buyer hires CROON RFQ itself → CROON RE-OPENS the market, hires+pays a
  CHILD agent, returns a PROOF-BUNDLED deliverable. Honours the pitch on the
  supply side too.
```

- **`croon/provider_worker.py`** — opens the SDK WebSocket, accepts negotiations
  for owned services, and delivers on `ORDER_PAID`.
- **`agents/provider.py`, `agents/listing_copy.py`, `agents/gas_oracle.py`** —
  the two standalone base agents (real, hireable CAP providers; also the
  fallback pool).
- **`croon/brokerage.py`** — the main-service brokerage cycle (see §9).

---

## 9. Durable idempotency (crash- and replay-safe)

Paying a child spends real USDC, and the CAP WebSocket can replay `ORDER_PAID` on
reconnect — **including across a process restart**. An in-memory cache cannot
survive that, so idempotency is anchored in SQLite.

**`BrokerageOrder`** (demand-side main service), keyed by `parent_order_id`:

```
 claimed ──▶ settled ──▶ completed
    │           │            │
    │           │            └─ replay → return stored deliverable (no re-pay)
    │           └───────────── replay → rebuild from paid child (no re-pay)
    └──── failed ───────────── replay → free to retry (no spend happened)
```

The linchpin: `_mark_settled` writes `child_tx_hash` **immediately after
`hire_and_pay` returns, before** assembling the deliverable — minimising the
crash window so a replay always recovers via `settled` instead of paying twice.
An in-process per-parent `asyncio.Lock` serialises concurrent replays within one
process.

**`ProviderJob`** (supply side) does the same for inbound orders: `negotiation_id`
is `UNIQUE`, so a replayed `order_negotiation_created` fails the insert fast and
is skipped — never accept/deliver the same negotiation twice.

---

## 10. Config & the demo safety net (`croon/config.py`)

All tunables are `CROON_`-prefixed env vars (Pydantic `Settings`). The most
important is `CROON_CAP_MODE`:

- `mock` → `MockCapClient` — build, test, and demo the **entire** pipeline with
  no network, no keys, no funded wallet.
- `live` → `FailoverCapClient(LiveCapClient, MockCapClient)` — real negotiation +
  USDC settlement on Base, with automatic (and *honestly labelled*) mock
  fallback if the live path fails.

Live-only vars (`CROON_CROO_SDK_KEY`, `CROON_LIVE_CANDIDATES_JSON`,
`CROON_FALLBACK_*`, `CROON_PROVIDER_*`, `CROON_BASE_RPC_URL`) also accept the
SDK's native names (`CROO_SDK_KEY`, `BASE_RPC_URL`, …) so an already-configured
CROO wallet works without duplicating vars. The
`min(buyer_budget, CROON_MAX_CHILD_SPEND_USDC)` spend guard means a single paid
order can never drain the wallet.

---

## 11. Module map (quick reference)

```
croon/
  api.py            FastAPI transport + UI mount + lifespan (starts B + provider)
  scheduler.py      Layer B — in-process cadence loop + run_now trigger
  engine.py         Layer C+D — mini-RFQ + settlement + fallback ladder + mode
  scoring.py        transparent weighted scoring + hard budget rule
  cap_client.py     THE boundary — CapClient / Mock / Live / FailoverCapClient
  brokerage.py      main-service brokerage (CROON is hired → re-opens market)
  provider_worker.py supply side — serve base agents as CAP providers
  models.py         Layer A — StandingOrder, Run, BrokerageOrder, ProviderJob
  schemas.py        SDK-agnostic contract types (Pydantic; incl. tx_verified)
  config.py         env-driven Settings + get_settings()
  events.py         in-memory event bus for the live UI feed
  db.py             SQLModel engine/session/init
  static/           single-page demo UI (index.html, app.js, style.css)
agents/
  provider.py       base-agent registry (BASE_AGENTS) + shared provider logic
  listing_copy.py   Base Agent #1 — Agent Store listing copy (~0.05 USDC)
  gas_oracle.py     Base Agent #2 — Base gas + cost estimate (~0.01 USDC)
scripts/            operational tooling (live_order, seed, probes, checks)
tests/              brokerage + provider_worker unit tests
```
