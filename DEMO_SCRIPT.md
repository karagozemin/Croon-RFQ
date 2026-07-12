# CROON RFQ — 5-Minute Demo Script

Target: **≤ 5:00**. Every second earns a point. Don't explain code — show the
market clearing and the money moving. Have `PROOF_BUNDLE.md` open in a tab.

**Before you hit record:**
- `python run.py` up, UI at http://127.0.0.1:8000
- One standing order seeded with run history (`python -m scripts.seed`)
- `PROOF_BUNDLE.md` open; a BaseScan tx tab pre-loaded
- Screen clean, font large, no secrets/keys visible

---

## Scene 1 — The problem (0:00–0:30) · *the 30 seconds that matter most*

> "Thousands of AI agents exist. Almost none earn. A one-off agent call is a
> dead end — no recurring revenue, no on-chain proof, no composability.
>
> CROON RFQ fixes the demand side. You define a **standing order** — a budgeted,
> recurring job. And here's the twist: on **every single run**, CROON doesn't
> re-hire the same agent. It **re-opens a competitive market** among CROO agents,
> picks the best under budget, pays in USDC on Base, and files a signed receipt.
>
> It's not a cron job. It's an autonomous procurement engine — and the owner
> **owns** it."

*(Say this over the dashboard already on screen. No slides.)*

## Scene 2 — The standing order (0:30–1:15)

- Show the standing order: name, **budget per run**, cadence, spend-to-date.
- "This is a recurring commercial relationship, not a script — it accrues history
  and a portfolio of on-chain receipts."
- Point at the run history list. "Every row is a market that already cleared."

## Scene 3 — Run now: the market clears live (1:15–2:45) · *the money shot*

- Click **Run now**. Narrate the live event stream as it renders:
  1. `candidates_discovered` — "2–3 CROO agents enter."
  2. `quote_received × N` — "each quotes a price and an SLA."
  3. `quotes_scored` — "price × reputation × speed. **Over-budget bids are
     excluded** — see the exclusion reason right here."
  4. `winner_selected` — read the **selection_reason** aloud.
  5. `payment_completed` — "USDC settles on Base."
  6. `receipt_generated` — "every bidder, the winner and *why*, tx hash, output
     hash, receipt hash — all signed."
- "One click. A market opened, cleared, paid, and proved itself."

## Scene 4 — On-chain proof (2:45–3:45)

- Open the run detail → copy the **tx hash**.
- Switch to the pre-loaded **BaseScan** tab, paste, show the confirmed tx.
- "Real USDC, Base mainnet, ERC-4337 settlement. Not a testnet, not a mock."
- Cut to `PROOF_BUNDLE.md`: "And we've done this against **three independent
  providers** — Smart Wallet Tracker, Broker, Polymind — three separate teams,
  three confirmed transactions. That clears the anti-sybil bar."

## Scene 5 — A2A composability + the base agents (3:45–4:30)

- "CROON also ships **two standalone CROO agents** — a Listing Copy agent and a
  Base Gas Oracle — each callable, each USDC-priced, each Store-listable."
- "They double as the **fallback pool**: if the market stalls or every bid is
  over budget, CROON routes to a capability-matched fallback — it **never
  crashes**, never wrong-routes work, never overspends."
- "This is A2A: our engine hires other agents, and other agents can hire ours."

## Scene 6 — Close (4:30–5:00)

> "CROON RFQ: recurring demand for the agent economy. Every run is a real,
> competitive, on-chain transaction. Open source, MIT, live on Base today.
>
> Three independent counterparties, three settled payments, one button. Thanks."

*(End on the PROOF_BUNDLE table with the three BaseScan links visible.)*

---

## Recording checklist

- [ ] Total runtime ≤ 5:00
- [ ] `Run now` shows the full event stream end-to-end (no error toast)
- [ ] A real tx opened on BaseScan on camera
- [ ] PROOF_BUNDLE table (3 counterparties) shown on camera
- [ ] No SDK key / `.env` / private data ever on screen
- [ ] Audio clear; UI zoomed enough to read event labels
