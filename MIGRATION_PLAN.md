# CROON RFQ — Mock → Live Geçiş Planı (Hackathon 1.’lik için)

> Amaç: Jüri kriterlerini eksiksiz karşılamak →
> **(1)** en az 3 GERÇEKTEN BAĞIMSIZ provider,
> **(2)** gerçek teklifler,
> **(3)** kazanan seçimi,
> **(4)** zincir-üstü (Base mainnet) settlement.
>
> ⚠️ **Diskalifiye riski:** Provider’ların tamamı aynı ekibe/cüzdana aitse 1.’likten düşülür.
> Bu yüzden kritik iş, kendi base-agent’larımızı değil, **3. taraf CROO Agent Store agent’larını** aday listesine koymaktır.

---

## 0. Şu an neyin “mock”, neyin “live-ready” olduğu

Kod mimarisi zaten temiz: tüm CAP belirsizliği tek sınırda (`croon/cap_client.py`) izole edilmiş.
`CROON_CAP_MODE` env değişkeni tüm uygulamayı tek satırda çevirir.

| Katman | Şu anki durum | Live için ne gerekiyor |
|---|---|---|
| **Discovery** (`discover_agents`) | Mock: 5 sahte agent (`agent_alpha/beta/gamma` + 2 base). Live: `CROON_LIVE_CANDIDATES_JSON` boş `[]`. | **3+ gerçek 3. taraf Store agent’ı** JSON roster’a girilecek (service_id’li). |
| **Quote** (`request_quote`) | Live client zaten hazır: fiyat/SLA’dan **türetilmiş teklif** (spec §4, CAP’te native quote yok). | Aday listesindeki `listed_price_usdc` / `listed_eta_seconds` gerçek Store değerleriyle doldurulacak. |
| **Scoring / Winner** (`scoring.py`, `engine.py`) | **Zaten gerçek** — mock’a bağlı değil. `0.4·price + 0.35·rep + 0.25·speed`, over-budget hariç. | Değişiklik YOK. Olduğu gibi geçerli. |
| **Settlement** (`hire_and_pay`) | Mock: sahte `0x…` hash → UI’da **SIMULATED** rozeti. Live: `negotiate_order → pay_order` + `eth_getTransactionByHash` ile RPC doğrulaması **hazır**. | `CAP_MODE=live` + **USDC ile fonlanmış CROO cüzdanı** + geçerli `croo_sk_...` anahtarı. |
| **Delivery** (`get_delivery`) | Live client hazır (async polling, graceful degrade). | Değişiklik YOK. |
| **Provider worker** (`provider_worker.py`) | Kendi 2 base-agent’ımızı CAP provider olarak çalıştırır (fallback). | İsteğe bağlı; ama bağımsız provider sayısına **dahil edilmez**. |

**Özet:** Kod tarafında yeniden yazılacak neredeyse hiçbir şey yok. İş büyük ölçüde **konfigürasyon + cüzdan fonlama + gerçek Store agent’larını bulma**.

---

## 1. En kritik adım — 3 GERÇEKTEN BAĞIMSIZ provider (DQ’den kaçış)

Jüri “aynı ekibe ait provider” tespit ederse 1.’likten düşürüyor. Dolayısıyla:

- ❌ `base_listing_copy`, `base_gas_oracle` (bizim agent’larımız) → **bağımsız sayılmaz**, sadece FALLBACK olarak kalsın.
- ✅ CROO Agent Store’dan **farklı ekiplere ait en az 3 canlı agent** seçilecek. Kriterler:
  - Farklı cüzdan / farklı yayıncı (publisher) — self-trade paterni olmasın.
  - `service_id`’si olan, USDC fiyatı listelenmiş, gerçekten hire edilebilir servisler.
  - Task kategorimize uyan (ör. `risk`, `research`) veya kategori filtresi geniş tutulacak.

**Yapılacak:** `CROON_LIVE_CANDIDATES_JSON`’ı gerçek Store verisiyle doldur:

```json
[
  {"agent_id":"third_party_1","name":"<Store Agent A>","service_id":"svc_...","category":"risk","listed_price_usdc":"0.10","listed_eta_seconds":30,"reputation":0.80},
  {"agent_id":"third_party_2","name":"<Store Agent B>","service_id":"svc_...","category":"risk","listed_price_usdc":"0.12","listed_eta_seconds":25,"reputation":0.75},
  {"agent_id":"third_party_3","name":"<Store Agent C>","service_id":"svc_...","category":"research","listed_price_usdc":"0.08","listed_eta_seconds":45,"reputation":0.70}
]
```

> Not: `< 3 unique counterparty agents` ve `< 5 unique buyer wallets` reward-eligibility flag’leri var.
> En az 3 farklı counterparty’ye ödeme yapan çalıştırma; ve mümkünse 5 farklı buyer cüzdanından talep üret.

---

## 2. Ortam değişkenleri (`.env`)

`.env.example`’ı `.env`’e kopyala, şunları doldur:

```dotenv
CROON_CAP_MODE=live

# CROO Dashboard’dan alınan gerçek anahtar
CROON_CROO_SDK_KEY=croo_sk_...
CROON_CROO_REQUESTER_AGENT_ID=<bizim requester agent id>

# Endpoint’ler (varsayılanlar genelde yeterli)
CROON_CROO_API_URL=https://api.croo.network
CROON_CROO_WS_URL=wss://api.croo.network/ws
CROON_BASE_RPC_URL=https://mainnet.base.org   # kendi Base RPC’n (Alchemy/Infura önerilir, rate-limit için)

# Native USDC on Base (bridged USDbC DEĞİL)
CROON_USDC_CONTRACT_ADDRESS=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

# 1. adımdaki gerçek 3. taraf roster (tek satır)
CROON_LIVE_CANDIDATES_JSON=[...]

# Fallback = kendi agent’ımız (bağımsız provider sayısına dahil DEĞİL)
CROON_FALLBACK_SERVICE_ID=svc_<kendi servisimiz>
CROON_FALLBACK_AGENT_ID=base_listing_copy
```

---

## 3. Cüzdan fonlama (settlement’ın ön koşulu)

`pay_order` USDC on Base’de gerçek transfer yapar. Cüzdan boşsa `insufficient balance` hatası → otomatik mock fallback → **SIMULATED** rozeti geri gelir.

- [ ] CROO hesabına bağlı AA cüzdan adresini Dashboard’dan al.
- [ ] Base mainnet’te **native USDC** ile fonla (3 çalıştırma × ~0.10 USDC + gas payı; hackathonda 0% gas penceresi avantaj).
- [ ] Bakiyeyi doğrula (küçük bir test ödemesiyle).

---

## 4. Doğrulama akışı (commit/push YOK — hepsi yerel)

Repo’daki hazır scriptler tam da bunun için:

1. **Bağlantı/anahtar sağlığı:** `python scripts/readiness_check.py`
2. **Aday roster doğrula:** `python scripts/validate_candidates.py`
3. **Tek canlı sipariş (dry-run yerine gerçek küçük ödeme):** `python scripts/live_order.py`
4. **Zincir testi:** `python scripts/live_chain_test.py` → dönen `tx_hash`’i `eth_getTransactionByHash` ile teyit et.
5. **3 gerçek çalıştırmayı backfill/kayıt:** `python scripts/backfill_live_run.py`
6. UI’da her run detayında `MODE LIVE` + tıklanabilir **BaseScan** linki göründüğünü doğrula (SIMULATED rozeti KALKMALI).

> Kod, SDK’nın verdiği `tx_hash`’i RPC’de bağımsız doğrular (`_verify_tx_on_chain`). Bulunamazsa `UNVERIFIED` etiketler — yani UI asla sahte linki gerçek gibi göstermez. Bu dürüstlük jüri için artı.

---

## 5. Demo videosu için “kanıt” checklist (5 dk sınırı)

- [ ] `mode-pill` → yeşil **● LIVE** görünüyor.
- [ ] The Auction’da 3 **bağımsız** agent kart olarak açılıyor (yeni deal-in animasyonu), gerçek fiyatlarla teklif veriyor.
- [ ] Scoring bar’ları + over-budget exclusion görünüyor.
- [ ] Winner seçiliyor, `selection_reason` insan-okur.
- [ ] Settling paneli → **CONFIRMED**, ardından run detayında canlı **BaseScan** tx linki.
- [ ] En az 3 farklı counterparty’ye ödeme (landing’deki 3 receipt’i gerçek tx’lerle güncelle — `index.html` şu an 3 örnek hash içeriyor; bunları GERÇEK live tx’lerle değiştir).

---

## 6. Landing’deki 3 “PROOF” receipt’i (dikkat!)

`croon/static/index.html` içinde 3 sabit BaseScan linki var (`0xc09e8eab…`, `0xf4bfa32d…`, `0x387a240f…`).
Bunlar **gerçek live tx olmalı** — aksi halde jüri human spot-check’te ölü/uyduruk link görürse “fake demo” DQ riski.

- [ ] 3 gerçek live çalıştırma yap, dönen gerçek tx hash’lerini bu 3 receipt’e yaz.
- [ ] Counterparty isimlerini gerçek 3. taraf agent isimleriyle güncelle.

---

## 7. Öncelik sırası (5 saat kaldıysa)

1. **CROO Dashboard: anahtar + cüzdan fonlama** (blocker).
2. **3 bağımsız Store agent’ı bul → roster JSON** (DQ’den kaçışın kalbi).
3. `.env` → `CAP_MODE=live`, `readiness_check.py`.
4. 3 gerçek çalıştırma → tx’leri topla.
5. `index.html`’deki 3 receipt’i gerçek tx’lerle değiştir.
6. Demo videosu çek.

> Kodda yeni özellik yazmaya gerek yok; risk düşük. En büyük risk **dışsal**: cüzdan fonu ve gerçek bağımsız agent bulmak.
