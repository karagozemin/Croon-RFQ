# CROON RFQ

**A recurring-demand engine for CROO agents.**

> Every standing order is recurring demand. Every CROO agent is my supply.
> The owner owns an autonomous revenue engine — not rented software.

Users (humans or agents) create a **standing order** — a recurring job with a
budget and a cadence. On **every run**, CROON re-opens the market: it runs a
lightweight competitive selection (**mini-RFQ**) across 2–3 candidate CROO
agents, scores them, picks the best under budget, hires the winner via **CAP**,
pays in **USDC on Base**, and returns a proof-bundled receipt. It keeps a full
run history.

---

## This is NOT a cron job

A cron job blindly re-invokes the same fixed endpoint. CROON does something
fundamentally different: **on every single run it re-opens a competitive
market.**

- It **discovers** candidate agents fresh each run.
- It **requests quotes** (price, ETA, confidence) from each candidate.
- It **scores** them (price × reputation × speed) and **selects a winner under
  budget**.
- It **settles on-chain** via CAP and emits a **signed receipt**.
- It maintains **stateful, budgeted, historical commercial relationships** — not
  a stateless timer.

The differentiator lives in the code: see `croon/engine.py` (the run pipeline)
and `croon/scoring.py` (the transparent scoring function). The demo UI shows the
mini-RFQ moment live.

---

## Architecture (4 layers, kept separate)

```
┌──────────────────────────────────────────────────────────────────┐
│                        CROON RFQ (off-chain)                        │
│                                                                     │
│  Layer A — Standing Order Store      croon/models.py + db.py        │
│     Persists standing orders + run history (SQLite/SQLModel).       │
│     Budgets & order state live HERE, never in a contract.           │
│                                                                     │
│  Layer B — Scheduler / Trigger       croon/scheduler.py             │
│     In-process interval loop + run_now (demo-grade, not prod cron). │
│                                                                     │
│  Layer C — Mini-RFQ Engine           croon/engine.py + scoring.py   │
│     discover → quote → score → select winner under budget.          │
│     (THE differentiator — kept thin.)                               │
│                                                                     │
│  Layer D — Settlement + Receipt      croon/engine.py                │
│     hire+pay winner → assemble receipt → append to run history.     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ ALL CAP calls go through ONE boundary
                                ▼
                    ┌───────────────────────────┐
                    │   croon/cap_client.py       │
                    │   CapClient (interface)     │
                    │   ├─ MockCapClient (now)    │
                    │   └─ LiveCapClient (step 4) │  ── ON-CHAIN ──▶ USDC on Base
                    └───────────────────────────┘
```

**Off-chain (our DB/logic):** scheduling, budget accounting, candidate
discovery, mini-RFQ, scoring, selection, run history, receipts.

**On-chain (via CAP only):** the USDC payment/settlement to the winning agent,
and anchoring the receipt hash. No standing-order state or budgets on-chain; no
custom escrow contract.

---

## Current status

This repo currently implements **Build Order steps 1–3** (spec §11):

- [x] **Step 1** — Repo scaffold: FastAPI app, SQLModel models, `.env` loading,
      README, MIT LICENSE.
- [x] **Step 2** — `MockCapClient` + full pipeline against mocks: create
      standing order → `run_now` → collect mock quotes → score → select → pay
      (mock) → build receipt → store run.
- [x] **Step 3** — Demo UI rendering the mini-RFQ moment + run history.
- [~] **Step 4** — Real `CapClient` (`LiveCapClient`) wired to the official
      CROO CAP Python SDK (`croo-sdk`). Method mapping is confirmed and coded
      (see *The CapClient boundary* below). Remaining: an SDK key
      (`croo_sk_...`), a USDC-funded AA wallet, and one real end-to-end paid run
      on Base to capture a BaseScan tx hash — these need live credentials from
      the operator.
- [ ] **Steps 5–7** — Fallback timeout mechanism, the two base agents, hardening.

Everything below runs **without any network or keys** thanks to `MockCapClient`.

---

## Prerequisites

- **Python 3.11–3.13.** Use `python3.13` if it is available.
  > **Environment note:** Python **3.14** currently ships with a broken
  > `ensurepip`/wheel setup on some macOS installs (`python -m venv` fails at the
  > pip bootstrap step). This is an environment quirk, not a project limitation —
  > create the venv with `python3.13` and everything works.

## Quick start (single command)


```bash
# 1. Create venv + install deps
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Configure (mock mode needs no keys)
cp .env.example .env

# 3. (Optional) seed a standing order with 3 completed runs for history
.venv/bin/python scripts/seed.py

# 4. Run the server + demo UI
.venv/bin/python run.py
```

Then open **http://127.0.0.1:8000** and click **Run now** to watch a live
mini-RFQ: quotes arrive → scoring → winner → payment → receipt.

---

## Demo-day safety net: mock ↔ live via one env var

```
CROON_CAP_MODE=mock   # deterministic fake agents, no network, no keys (default)
CROON_CAP_MODE=live   # real CROO CAP SDK (needs croo_sk_ key + funded wallet)
```

The entire pipeline is identical in both modes because everything CAP-related is
isolated behind `CapClient` (`croon/cap_client.py`). Nothing else in the
codebase imports the SDK.

---

## Environment variables

See `.env.example` for the full list. Highlights:

| Var                          | Default              | Purpose                                        |
| ---------------------------- | -------------------- | ---------------------------------------------- |
| `CROON_CAP_MODE`             | `mock`               | `mock` \| `live` — the demo safety net.        |
| `CROON_DATABASE_URL`         | `sqlite:///croon.db` | Local state (SQLModel).                        |
| `CROON_HOST` / `CROON_PORT`  | `127.0.0.1` / `8000` | HTTP server bind.                              |
| `CROON_SCHEDULER_TICK_SECONDS` | `10`               | How often the in-process loop checks due orders. |
| `CROON_RFQ_TIMEOUT_SECONDS`  | `10`                 | Per-run RFQ timeout → triggers fallback.       |
| `CROON_W_PRICE/W_REP/W_SPEED`| `0.4/0.35/0.25`      | Scoring weights (documented below).            |

Live-only vars (used only when `CROON_CAP_MODE=live`):

| Var                          | Purpose                                                      |
| ---------------------------- | ----------------------------------------------------------- |
| `CROON_CROO_SDK_KEY`         | `croo_sk_...` key from the CROO Dashboard (sent as `X-SDK-Key`). |
| `CROON_CROO_API_URL`         | CROO CAP API base URL.                                       |
| `CROON_CROO_WS_URL`          | CROO CAP WebSocket URL (order/negotiation events).          |
| `CROON_BASE_RPC_URL`         | Base RPC endpoint for on-chain USDC settlement.             |
| `CROON_USDC_CONTRACT_ADDRESS`| Native USDC on Base (`0x8335…2913`). **Not** bridged USDbC — verify CAP settles in this token. |
| `CROON_LIVE_CANDIDATES_JSON` | JSON roster of Store service ids used as RFQ candidates (the SDK has no discovery). |

These are read by `LiveCapClient`; in mock mode they can stay blank.

---

## HTTP API (spec §8)

| Method | Path                              | Purpose                              |
| ------ | --------------------------------- | ------------------------------------ |
| POST   | `/standing-orders`                | Create a standing order.             |
| GET    | `/standing-orders`                | List standing orders.                |
| GET    | `/standing-orders/{id}`           | Detail + full run history.           |
| POST   | `/standing-orders/{id}/run-now`   | **Trigger a run immediately (demo).**|
| POST   | `/standing-orders/{id}/pause`     | Pause.                               |
| POST   | `/standing-orders/{id}/resume`    | Resume.                              |
| GET    | `/standing-orders/{id}/events`    | Live event feed (`?after=<seq>`).    |
| GET    | `/runs/{run_id}`                  | Full run detail incl. receipt bundle.|
| GET    | `/`                               | Demo UI.                             |

### How `run_now` works

1. `POST /standing-orders/{id}/run-now` delegates to the scheduler's `trigger()`
   so cadence bookkeeping stays in one place.
2. The engine (`execute_run`) opens the mini-RFQ:
   `discover_agents` → `request_quote` on each candidate (bounded by
   `CROON_RFQ_TIMEOUT_SECONDS`).
3. Quotes are scored; anything over `budget_per_run` is hard-excluded.
4. The winner is hired and paid via `CapClient.hire_and_pay` (mock tx in mock
   mode; real USDC-on-Base tx in live mode).
5. A receipt bundle is assembled (quotes, winner, reason, tx hash, timestamps,
   output hash, receipt hash) and appended to run history.
6. Every step emits an event the UI polls to render the live money shot.

---

## Scoring function (spec §6)

Defined transparently in `croon/scoring.py`:

```
score = w_price * price_score + w_rep * reputation_score + w_speed * speed_score
```

- **price_score** — normalized so that a cheaper quote (still under budget)
  scores higher.
- **speed_score** — lower ETA scores higher.
- **reputation_score** — MVP placeholder: the quote's self-reported/derived
  confidence (a full reputation oracle is deliberately out of scope).
- **Hard budget rule** — any quote with `price > budget_per_run` is **excluded**
  before scoring.
- **Default weights** — `w_price=0.4`, `w_rep=0.35`, `w_speed=0.25`
  (configurable via env).

The winning score and a human-readable `selection_reason` are stored on every
run, e.g.:

> `best score under budget: score 0.6527, price 0.12 USDC, eta 40s, rep 0.72
> (weights price=0.4, rep=0.35, speed=0.25)`

---

## Fallback mechanism (spec §7 — planned, step 5)

On each run the RFQ is sent with a strict per-run timeout
(`CROON_RFQ_TIMEOUT_SECONDS`). If **fewer than 1 valid quote** returns, CROON
will **not crash**: it emits *"No live bids received — budget protection active —
routing to fallback provider"* and routes the job to one of **our own base
agents** (real, hireable CAP agents), marking `run.fallback_used = True`. The
`CapClient` interface and event plumbing already support this; the base agents
land in step 6.

---

## The CapClient boundary (spec §4)

`croon/cap_client.py` exposes the only surface the rest of the app depends on:

```python
class CapClient:
    async def discover_agents(self, category, limit) -> list[AgentInfo]
    async def request_quote(self, agent_id, task, timeout_s) -> Quote | None
    async def hire_and_pay(self, agent_id, task, agreed_price_usdc) -> Settlement
    async def get_delivery(self, order_id) -> Delivery
```

- **`MockCapClient`** — deterministic fake agents (Alpha, Beta, plus base-agent
  fallbacks). Lets us build and test the entire pipeline offline.
- **`LiveCapClient`** — wraps the real CROO CAP Python SDK (`croo-sdk`). All
  uncertainty about the SDK lives here and nowhere else; the SDK is imported
  **lazily** so mock mode never requires it to be installed.

### Exact CAP SDK methods used (live mode)

Auth: `AgentClient(Config(base_url, ws_url, rpc_url), "croo_sk_...")` — the key
is sent as the `X-SDK-Key` header. Our interface maps onto the SDK **as the
requester** like this:

| CROON interface (`CapClient`) | CROO CAP SDK call(s)                                   | Notes |
| ----------------------------- | ------------------------------------------------------ | ----- |
| `discover_agents()`           | *(none — not an SDK primitive)*                        | The SDK has **no** search/discovery; accounts & services are set up in the Agent Store. Candidates therefore come from a **configured roster** of Store service ids (`CROON_LIVE_CANDIDATES_JSON`). Documented as honest emulation (spec §4). |
| `request_quote()`             | *(none — derived)*                                     | CAP has **no native quote primitive**. A quote is **derived** from the candidate's listed price / SLA. We deliberately do **not** open a negotiation just to quote (that creates dangling on-chain state + gas). |
| `hire_and_pay()`              | `negotiate_order()` → *(provider accepts)* → `get_negotiation()` (poll for `order_id`) → `pay_order(order_id)` | The real negotiation + **USDC-on-Base settlement** happens here, for the winner only. SDK auto-handles the ERC-20 approve. |
| `get_delivery()`              | `get_delivery(order_id)`                               | Returns the hired agent's work product (`deliverable_text`). |

Minor field-name variance across SDK versions is absorbed by a single `_attr()`
helper in `cap_client.py` (again, isolated to this one file). The two spots that
still need version confirmation against the installed build are marked with
`TODO(verify)` — specifically the `negotiate_order` request shape.

> **The missing price-discovery layer (a feature, not a fake auction).**
> CAP today has **no native quote/discovery primitive** — agents publish a
> price/SLA in the Agent Store, and that's it. CROON's mini-RFQ *adds the
> layer CAP is missing*: it derives a quote from each candidate's listed
> price/SLA, scores them, and settles competitively under budget. We say this
> out loud in the demo. It is deliberately **not** a simulated auction with
> invented bids — every number traces back to a real Store listing, and the
> only on-chain action is the single winning payment.

### Payment precondition (live mode)

Before CROON can pay providers, **CROON's own agent (AA) wallet must be funded
with USDC on Base.** If it isn't, `pay_order` raises an insufficient-balance
error (`is_insufficient_balance`). Fund the AA wallet address shown in the CROO
Dashboard with USDC on Base before attempting a live run.

### Safe go-live sequence (run this before spending a cent)

The `croo_sk_...` mapping (`negotiate_order → get_negotiation → pay_order`) is
coded but must be verified against the real `croo-sdk` source before a live
payment. Follow this staged rollout:

1. **Read-only first.** With `CROON_CAP_MODE=live`, exercise only discovery/
   quote-emulation + `get_negotiation` polling. **No `pay_order` yet.** If auth
   and negotiation resolve cleanly, the key/SDK wiring is correct.
2. **One safe micro-run.** Trigger `run_now` with the smallest possible budget,
   routing to **your own base agent as the winner** (self-hire = a controlled
   first payment). Capture the tx and verify it on BaseScan.
3. **Only if that tx is clean** → enable the fallback timeout (step 5) and
   normal candidate runs.
4. **Keep `CROON_CAP_MODE` flippable.** If the live net stalls on demo day, flip
   back to `mock`. That safety net is the single most valuable asset here.

> V1 Pioneers note: completing CAP integration testing (the micro-run above) is
> reported to qualify for a $10 USDC reward — a free byproduct of go-live.

---

## Project layout

```
Croon-RFQ/
├─ croon/
│  ├─ __init__.py
│  ├─ config.py          # env/.env settings (pydantic-settings)
│  ├─ schemas.py         # Pydantic I/O contracts (SDK-agnostic)
│  ├─ models.py          # SQLModel tables: StandingOrder, Run (Layer A)
│  ├─ db.py              # SQLite engine + sessions
│  ├─ cap_client.py      # CapClient boundary: Mock + Live (all CAP lives here)
│  ├─ scoring.py         # transparent scoring function (Layer C)
│  ├─ engine.py          # mini-RFQ + settlement pipeline (Layers C+D)
│  ├─ events.py          # in-memory event bus for the live UI
│  ├─ scheduler.py       # in-process cadence loop + run_now (Layer B)
│  ├─ api.py             # FastAPI: HTTP API + serves UI
│  └─ static/            # single-page demo UI (html/css/js)
├─ scripts/seed.py       # seed a standing order + 3 runs for history
├─ run.py                # single-command entrypoint
├─ requirements.txt
├─ .env.example
├─ LICENSE               # MIT
└─ README.md
```

---

## Known limitations

- **Live CAP coded but not yet run on-chain.** `LiveCapClient` is wired to the
  real `croo-sdk`, but a real paid run needs an SDK key + USDC-funded AA wallet
  (operator-supplied). The two base agents and the fallback timeout path arrive
  in build-order steps 5–7. Mock mode is fully functional today.
- **Scheduler is demo-grade.** A single in-process interval loop, not durable
  production cron; jobs don't survive restarts mid-run.
- **Reputation is a placeholder** (quote confidence), not a real oracle — by
  design (out of scope).
- **Event bus is in-memory** and per-process; it resets on restart.


---

## License

MIT — see [`LICENSE`](./LICENSE).
