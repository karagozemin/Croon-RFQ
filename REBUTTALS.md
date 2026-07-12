# CROON RFQ — Judge Attack Sheet (Q&A war room)

_Think like a hostile judge trying to knock this out of first place. 20 attacks,
each with a tight answer and where the proof lives. If a judge can ask it, the
answer should already be on screen before they finish asking._

---

## Category 1 — "This isn't novel"

**1. "This is just a cron job."**
A cron job re-runs the *same command* against the *same provider*. CROON re-runs a
*competitive procurement*: every run re-discovers agents, re-collects quotes,
re-scores, and can pick a *different* winner. Different providers were in fact paid
across runs — see PROOF_BUNDLE. Cron has no quotes, no scoring, no budget rule, no
receipt. → README "not a cron job" table; DEMO scene 3.

**2. "This is just an orchestrator / LangChain wrapper."**
An orchestrator is hard-wired to a chosen tool. CROON has no fixed provider — the
provider is *selected competitively at runtime under a budget constraint*, then paid
on-chain. The output is a signed economic receipt, not just a function result.

**3. "Why not use an existing scheduler (Airflow/cron/Celery)?"**
The scheduler is the least interesting 5% — deliberately demo-grade and swappable
(`scheduler.py`). The value is layers C+D: the mini-RFQ market + on-chain
settlement + receipt. An existing scheduler would just call `execute_run()`; it
adds nothing to the thesis and we call this out under Known Limitations.

**4. "This is just a marketplace feature CROO will build anyway."**
CROO Store is *supply-side* (agents list themselves). CROON is *demand-side*: it
manufactures recurring, budgeted demand and routes it competitively. We're a buyer
that makes the marketplace liquid, not a competing marketplace.

---

## Category 2 — "The economics/design are questionable"

**5. "Why is recurring necessary? Why not one call?"**
One-off calls don't compound. Recurring demand creates: price discovery over time,
a portfolio of on-chain receipts the owner *owns*, spend accounting, and a reason
for agents to compete for a durable customer. That's the whole thesis — the first
line of the README.

**6. "Why 3 providers? Feels arbitrary / just to pass anti-sybil."**
3 is the hackathon's own genuine-independence bar, but it's also the minimum that
makes a *market* rather than a duel — you need enough bidders that price/quality
competition is real and the winner can change. We proved 3 *independent teams*, not
3 wallets of ours (that would be self-trade — see #9).

**7. "Quotes aren't real — you derive them from listed price."**
Correct and documented (Known Limitations). CAP has no native quote primitive, so a
quote = the candidate's listed price/SLA. This is a *deliberate* design: quoting
stays free and side-effect-free, and only the winner is actually negotiated + paid.
The competition (scoring, budget exclusion, winner+reason) is genuine and per-run
auditable via `quotes_json`.

**8. "Reputation is fake."**
Acknowledged: `rep_score` is a placeholder (quote confidence / prior successful
runs), not an oracle — intentionally out of scope. It's isolated in `scoring.py` so
a real reputation source drops in without touching the engine. We'd rather ship an
honest placeholder than fake an oracle.

---

## Category 3 — "The on-chain proof is weak"

**9. "This is self-trade / sybil."**
Payments route to **3 distinct counterparty agents from independent teams**
(Smart Wallet Tracker, Broker, Polymind) — money leaves our wallet to *them*, not
back to us. That's the opposite of the concentrated self-trade pattern the rules
flag. → PROOF_BUNDLE table + "Not self-trade" criterion row.

**10. "The transactions are faked / hardcoded hashes."**
Every hash is verifiable *right now* against Base mainnet via public RPC —
`eth_getTransactionByHash` shows distinct blocks and the EntryPoint recipient. We
give the exact curl command. You don't have to trust us; run it. → PROOF_BUNDLE §Reproduce.

**11. "Testnet, not mainnet."**
`eth_chainId` returns `0x2105` = 8453 = Base **mainnet**. Command included.

**12. "Status is `paid_delivery_pending` — did work actually happen?"**
The *payment* is the on-chain fact and it's final (tx mined). CAP delivery is
*asynchronous* by design — the deliverable is re-fetchable via `get_delivery` and
may arrive after the tx. The spend proof (tx hash) is independent of delivery
latency. Documented under Known Limitations + the CapClient section.

**13. "Amounts are tiny (0.10 USDC) — is this real?"**
Amount size doesn't change validity; a real ERC-4337 USDC transfer is a real
transfer at any size. Small amounts are responsible hackathon spend, not a
limitation of the system — budgets are configurable per standing order.

---

## Category 4 — "Engineering / robustness"

**14. "What happens when a provider fails or no one bids?"**
The fallback ladder (spec §7): <1 valid quote → capability-matched base agent;
all-over-budget → one capable fallback; winner refuses → a *different* fallback;
nothing capable → `no_provider_available` with **spend = 0**. It never crashes and
never wrong-routes (a risk brief never goes to a gas oracle).

**15. "Could it double-pay on a restart / crash mid-run?"**
No — durable idempotency is unit-tested across a simulated restart
(`tests/test_brokerage.py`): a paid order is not paid twice. The order lifecycle
also guards against paying in the wrong state (`creating→created`).

**16. "Is the CAP integration real or a shim?"**
Real `croo-sdk`. We list the exact methods (`negotiate_order`, `get_negotiation`,
`list_orders`, `pay_order`, `get_delivery`) and the real lifecycle quirks we handle
(early-pay 400 `INVALID_STATUS`, negotiation→order resolution, provider-enforced
`requirements` with no describe endpoint). All isolated in `cap_client.py`.

**17. "How do I run it without keys / risking money?"**
`CROON_CAP_MODE=mock` — deterministic fake market, no keys, no network, no wallet.
One env var flips to `live`. Judges can explore the entire product risk-free, then
verify the on-chain proof separately.

**18. "Discovery is just a hardcoded list."**
Yes, in live mode — because the SDK exposes no search primitive (documented). It's
isolated in `LiveCapClient` so a real discovery endpoint drops in without touching
the engine. We chose to be explicit rather than pretend we have discovery.

---

## Category 5 — "Presentation / process"

**19. "Judges won't read a long README."**
They won't — so the first screen is a one-sentence hook + a 30-second flow diagram +
links straight to PROOF_BUNDLE and DEMO. Architecture is *below* proof, on purpose.

**20. "The demo could be luck / cherry-picked."**
The demo is scripted (DEMO_SCRIPT.md) *and* backed by independently reproducible
on-chain proof. Even if the live click hiccups, the three mainnet transactions stand
on their own and anyone can re-verify them. The claim survives without the demo.

---

## The 3 lines to memorize

1. **"It's not a cron job — every run re-opens a competitive market and pays the
   winner on-chain."**
2. **"Three independent counterparties, three verified Base-mainnet transactions —
   run our curl command yourself."**
3. **"Demand-side infrastructure: we make CROO agents earn *repeatedly*, and the
   owner owns the engine."**
