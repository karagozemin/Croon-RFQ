# CROON RFQ — DoraHacks Submission Text

_Paste this into the BUIDL description. Order is deliberate: hook → problem →
what → proof → how to verify. Never open with "We built..."._

---

## One-liner

**CROO agents can earn from one-off jobs. CROON makes them earn *repeatedly* —
every recurring job is re-auctioned to independent agents and the winner is paid
in real USDC on Base.**

## The problem

Today a CROO agent earns from a single call and then the relationship ends. There's
no recurring demand, no price discipline, no on-chain track record a buyer can own.
The agent economy has supply but no durable, composable demand.

## What CROON is

CROON RFQ is an **autonomous recurring-demand engine** on the CROO Agent Protocol.
You define a **standing order** — a budgeted, recurring job. On **every run** CROON
re-opens a competitive market:

**discover 2–3 independent CROO agents → collect quotes → score (price × reputation
× speed) → drop over-budget bids → hire the winner via CAP → pay USDC on Base →
emit a signed receipt → append to run history.**

It is not a cron job (which repeats one command) and not a plain orchestrator (which
is hard-wired to one provider). Every run is a fresh, competitive, on-chain
procurement — and the owner **owns** the resulting engine and receipt portfolio.

## Proof (real, on-chain, reproducible)

Three genuinely **independent** counterparties discovered, selected, hired and paid
on **Base mainnet** — clearing the anti-sybil ≥3 unique-counterparty bar:

| Counterparty | Amount | Base tx |
|--------------|--------|---------|
| Polymarket Smart Wallet Tracker | 0.10 USDC | `0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f` |
| Polymarket Broker | 0.10 USDC | `0x387a240ff9ab48178c313f7558e10cacc438cdf82847dd9d10bb40ad803d0ed7` |
| Polymind | 0.10 USDC | `0xf4bfa32db4d25ddec835b7570cdf36bdd7851192c6c6b514fbb265872bb1c2fd` |

Each is a real `pay_order` USDC transfer via the real `croo-sdk`, mined into a
distinct block through ERC-4337 EntryPoint v0.7. Verify any hash at
`https://basescan.org/tx/<hash>`. Full evidence + reproduce commands in
`PROOF_BUNDLE.md`.

## CAP SDK methods used

`AgentClient.negotiate_order` → poll `get_negotiation` / `list_orders(role="buyer")`
→ `pay_order(order_id)` → `tx_hash`; `get_delivery(order_id)` (async). Auth via
`AgentClient(Config(...), "croo_sk_...")`. All CAP access is isolated behind one
`CapClient` boundary (`croon/cap_client.py`), flippable mock↔live with one env var.

## A2A composability

CROON also ships **two standalone CAP agents** (Listing Copy, Base Gas Oracle) —
each callable, USDC-priced, Store-listable — which double as the capability-matched
fallback pool. Our engine hires other agents; other agents can hire ours.

## Links

- Repo: (public GitHub, MIT)
- Demo video: (≤ 5 min — follows `DEMO_SCRIPT.md`)
- Proof bundle: `PROOF_BUNDLE.md`
- Architecture: `ARCHITECTURE.md`
