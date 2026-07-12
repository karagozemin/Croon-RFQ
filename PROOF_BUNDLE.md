# CROON RFQ — Live On-Chain Settlement Proof Bundle

_Judge-facing evidence. Everything below is independently verifiable with the
commands at the bottom. Local evidence only — no commit/push required to check._

**Chain:** Base mainnet (chainId `0x2105` = 8453)
**Settlement path:** ERC-4337 EntryPoint v0.7 (`0x0000000071727de22e5e9d8baf0edac6f37da032`) — real Account-Abstraction USDC payments, not simulated hashes.
**SDK:** real `croo-sdk` (`negotiate_order` → poll → `pay_order` → `tx_hash`).

---

## TL;DR for judges

- **3 genuinely independent CROO counterparties** were discovered, selected, hired and **paid on Base mainnet** — clears the anti-sybil **≥3 unique-counterparty** bar.
- Each payment is a **real on-chain tx** (verified live via `eth_getTransactionByHash`, mined into distinct blocks).
- Each run persists the **full receipt**: quotes, winner, selection reason, output hash, receipt hash.

| # | Provider (independent team) | agent_id | Paid | BaseScan |
|---|------------------------------|----------|------|----------|
| 1 | Polymarket Smart Wallet Tracker | `b6c8cc34` | 0.10 USDC | [tx](https://basescan.org/tx/0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f) |
| 2 | Polymarket Broker | `062d6f26` | 0.10 USDC | [tx](https://basescan.org/tx/0x387a240ff9ab48178c313f7558e10cacc438cdf82847dd9d10bb40ad803d0ed7) |
| 3 | Polymind | `49373b68` | 0.10 USDC | [tx](https://basescan.org/tx/0xf4bfa32db4d25ddec835b7570cdf36bdd7851192c6c6b514fbb265872bb1c2fd) |

---

## Run #1 — Polymarket Smart Wallet Tracker

- **run_id:** `4ab7a900`
- **Providers discovered / quoted:** Polymarket Smart Wallet Tracker (`b6c8cc34`), price 0.10 USDC, eta 60s, confidence 0.90
- **Winner:** `b6c8cc34` (score **1.0**, under budget)
- **Selection reason:** _"first real on-chain run: hired Polymarket Smart Wallet Tracker at 0.10 USDC"_
- **Amount paid:** 0.10 USDC
- **Base tx:** `0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f`
- **BaseScan:** https://basescan.org/tx/0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f
- **Block:** `0x2e442ed` · **to:** EntryPoint `0x0000…da032`
- **Output hash:** `d699a1a80ab2597a7d95abf94d00bdabcfec77ad2321e3660ceaaed75d3e30f5`
- **Receipt hash:** `bd6dc0186e66629e15453b996e4c640fa785c3db93aad9a2fa6cd4c98a54c5da`
- **Status:** `paid_delivery_pending` (spend proven by tx; deliverable async — see §async delivery in README)

## Run #2 — Polymarket Broker

- **run_id:** `afa044c5`
- **Providers discovered / quoted:** Polymarket Broker (`062d6f26`), price 0.10 USDC, eta 60s, confidence 0.90
- **Winner:** `062d6f26` (score **1.0**, under budget)
- **Selection reason:** _"real on-chain run: hired Polymarket Broker at 0.10 USDC"_
- **Amount paid:** 0.10 USDC
- **Base tx:** `0x387a240ff9ab48178c313f7558e10cacc438cdf82847dd9d10bb40ad803d0ed7`
- **BaseScan:** https://basescan.org/tx/0x387a240ff9ab48178c313f7558e10cacc438cdf82847dd9d10bb40ad803d0ed7
- **Block:** `0x2e447fd` · **to:** EntryPoint `0x0000…da032`
- **Output hash:** `ad36384ebd386a8417668bd812f7f456900ab5a044400e771484c9fd65bdd320`
- **Receipt hash:** `74d9cb398dd6b48f10661952ec7b3753927fa41349874edb45a538fb22bd6ace`
- **Status:** `paid_delivery_pending`

## Run #3 — Polymind

- **run_id:** `58286c2e`
- **Providers discovered / quoted:** Polymind (`49373b68`), price 0.10 USDC, eta 60s, confidence 0.90
- **Winner:** `49373b68` (score **1.0**, under budget)
- **Selection reason:** _"real on-chain run: hired Polymind at 0.10 USDC"_
- **Amount paid:** 0.10 USDC
- **Base tx:** `0xf4bfa32db4d25ddec835b7570cdf36bdd7851192c6c6b514fbb265872bb1c2fd`
- **BaseScan:** https://basescan.org/tx/0xf4bfa32db4d25ddec835b7570cdf36bdd7851192c6c6b514fbb265872bb1c2fd
- **Block:** `0x2e44713` · **to:** EntryPoint `0x0000…da032`
- **Output hash:** `92c971285c408bc36fc38bf3adb6d06c88232be4d850d2568b8f6476278d166e`
- **Receipt hash:** `35b5c1799976a70a44b4e10b308ec42498adb6d0c05c3c1500769c714b3096af`
- **Status:** `paid_delivery_pending`

---

## Multi-quote competition (visible in-app)

Live diversity runs above were executed one counterparty at a time to prove
**breadth** (3 independent teams paid). The **competitive clearing** itself —
2–3 quotes scored side-by-side, over-budget bids excluded, winner + reason —
is shown live in the demo UI on every `Run now` (see `DEMO_SCRIPT.md`, scene 3).
Every run persists its full `quotes_json`, so the scoring is auditable per run.

---

## Judging-criterion mapping

| Criterion | Evidence |
|-----------|----------|
| **≥3 genuinely independent providers** | 3 distinct agent_ids, separate CROO Store listings, independent teams (table above) |
| **Real quotes** | each run stores `quotes_json` (price, eta, confidence, score, exclusion reason) |
| **Winner selection** | `winner_agent_id` + human-readable `selection_reason` per run |
| **On-chain settlement** | 3 verified Base mainnet ERC-4337 USDC payments (BaseScan links) |
| **Not self-trade** | payments route to 3 different counterparty agents, not back to owner |

---

## Reproduce (anyone can run this)

**A. Confirm the 3 tx are live on Base mainnet:**

```bash
for tx in \
  0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f \
  0x387a240ff9ab48178c313f7558e10cacc438cdf82847dd9d10bb40ad803d0ed7 \
  0xf4bfa32db4d25ddec835b7570cdf36bdd7851192c6c6b514fbb265872bb1c2fd ; do
  curl -s -X POST https://mainnet.base.org -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_getTransactionByHash\",\"params\":[\"$tx\"]}" \
    | jq '{tx: .result.hash, block: .result.blockNumber, to: .result.to}'
done
```

**B. Read the runs straight from the app's DB:**

```bash
sqlite3 croon.db -header -column \
  "SELECT substr(id,1,8) run, winner_agent_id, amount_paid_usdc amt, \
          substr(tx_hash,1,12) tx, status \
   FROM run WHERE mode='live' AND tx_hash IS NOT NULL ORDER BY started_at;"
```

**C. Confirm chain identity:**

```bash
curl -s -X POST https://mainnet.base.org -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' | jq -r '.result'
# 0x2105 == 8453 == Base mainnet
```
