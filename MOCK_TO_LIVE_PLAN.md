# CROON-RFQ — Mock → Live Geçiş Planı (Hackathon 1.’lik için)

> Kural hatırlatması: **commit/push YOK.** Tüm çalışma lokal `.env` + lokal
> `croon.db` + demo videosu üzerinden. Repo state’i değişmez.

## Jüri ne istiyor?
> “En az 3 **gerçekten bağımsız** provider, **gerçek teklifler**, **kazanan
> seçimi** ve **zincir üstü settlement** gösterilmeli. Provider’ların tamamı
> aynı ekibe aitse 1.’likten düşer.”

Yani tek bir run içinde şu zincir **gözle görünür** olmalı:
**3 bağımsız provider → 3 teklif → skorlama ile kazanan → on-chain USDC tx (BaseScan).**

---

## Mevcut durum (kod okundu, doğrulandı)

| Gereksinim | Durum | Kanıt |
|---|---|---|
| 3 bağımsız provider | ✅ VAR | `.env → CROON_LIVE_CANDIDATES_JSON`: Polymarket Smart Wallet Tracker (`b6c8cc34…`), Polymarket Broker (`062d6f26…`), Polymind (`49373b68…`) — 3 farklı agent_id, 3 farklı service_id, **hepsi harici Store agent’ı** (bizim değil). |
| Gerçek zincir-üstü settlement | ✅ VAR | `croon.db` içinde 3 provider için de `mode=live` + gerçek `0x…` tx (ör. `0xc09e8eab…`, `0xf4bfa32d…`, `0x387a240f…`). |
| Gerçek engine akışı (mock değil) | ✅ KODLU | `engine.execute_run`: discover → parallel quote → `score_quotes` → `hire_and_pay` (on-chain) → `get_delivery` → imzalı receipt. `LiveCapClient` gerçek `croo-sdk` çağırıyor. |
| **Tek run’da 3 teklif + kazanan seçimi görünür** | ❌ **EKSİK — asıl boşluk** | DB’deki live run’lar `scripts/live_order.py` (tekil hire CLI) ile oluşmuş; her run’ın `quotes_json`’ı **yalnız kazananı** içeriyor. Rakip teklifler / skor tablosu görünmüyor. `backfill_live_run.py` de tek-teklifli run yazıyor. |
| Mock kirliliği | ⚠️ VAR | DB’de 10 mock run (`agent_alpha/beta/gamma`, `base_listing_copy`). Bunlar “self-trade / sahte” izlenimi verip 1.’liği düşürebilir. |

**Sonuç:** Kod zaten canlı. Sorun *mock’tan live’a çevirmek* değil — 
**gerçek engine’i canlı modda 1 kez uçtan uca çalıştırıp** ortaya “3 teklif →
kazanan → tx” içeren TEK bir kanıt-run çıkarmak ve **mock satırları demodan
temizlemek.**

---

## Nerede hâlâ “mock” var ve ne yapılacak

### 1. `FailoverCapClient` sessiz mock’a düşme (integrity riski)
`build_cap_client` live modda `LiveCapClient`’ı `FailoverCapClient` ile sarıyor.
Herhangi bir canlı hata → sessizce `MockCapClient` (sahte tx). Engine bunu
`mode="mock"`/`"unverified"` olarak dürüstçe etiketliyor (iyi), **ama demo
run’ının gerçekten `mode=live` + `tx_verified=True` çıktığını mutlaka
doğrulamalıyız.** Aksi halde jüri “sahte demo” bayrağına takılır.
- **Aksiyon:** Demo run’ı sonrası DB’de `mode='live' AND tx_verified` kontrol et.
  Düşerse önce nedenini çöz (aşağıya bak), asla mock’la video çekme.

### 2. Cüzdan fonlaması (canlı ön-koşul)
`pay_order` için AA cüzdanı Base üzerinde USDC ile fonlanmış olmalı
(`is_insufficient_balance`). Bu olmadan live hire başarısız → failover → mock.
- **Aksiyon:** Demo öncesi cüzdanda ≥ ~0.5 USDC (3 provider × 0.10 + tampon) olduğunu doğrula.

### 3. Mock seed satırları (`scripts/seed.py`)
Demo DB’sindeki `agent_alpha/beta/gamma` + `base_listing_copy` run’ları jüriyi
yanıltır (bunlar bizim/sanal agent’lar → “aynı ekip / self-trade”).
- **Aksiyon:** Demo için mock run’ları gizle/temizle (aşağıdaki B adımı).

### 4. `MockCapClient` roster’ı
Kod olarak kalması sorun değil (offline test için) — sadece **live modda devreye
girmediğinden** emin ol (`CROON_CAP_MODE=live` + failover’a düşülmedi).

---

## Uygulama adımları (lokal, commit’siz)

### A. Ön-koşul doğrulama
1. `.env`: `CROON_CAP_MODE=live`, `CROON_CROO_SDK_KEY=croo_sk_…` dolu.
2. Roster’daki 3 provider’ın da `service_id`’si dolu ve **bize ait değil** →
   `python -m scripts.validate_candidates` (self-trade / duplicate uyarısı vermemeli).
3. AA cüzdanı USDC ile fonlu (Base). `python -m scripts.readiness_check` ile
   API key + roster + hireability doğrula.

### B. Demo DB’sini temizle (mock’ı çıkar)
Amaç: jüri yalnız gerçek, bağımsız, on-chain run’ı görsün.
```bash
# Yedek al
cp croon.db croon.db.bak
# Mock run’ları ve sanal agent kazananlı satırları at
sqlite3 croon.db "DELETE FROM run WHERE mode!='live';"
sqlite3 croon.db "DELETE FROM run WHERE winner_agent_id IN \
  ('agent_alpha','agent_beta','agent_gamma','base_listing_copy','base_gas_oracle');"
```

### C. GERÇEK kanıt-run’ı üret (asıl iş — 3 teklif + kazanan + tx)
Tekil `live_order.py` yerine **tam engine’i** canlı çalıştır. Böylece
`quotes_json` 3 gerçek teklifi, `selection_reason` skorla seçimi ve `tx_hash`
on-chain settlement’ı içerir.

- **Yol 1 (tercih):** UI’dan standing order oluştur → “Run now”. UI mini-RFQ
  anını (quotes arriving → scoring → winner → payment → receipt) canlı gösterir;
  video için ideal.
- **Yol 2 (script):** Aşağıdaki tek-seferlik lokal runner ile engine’i tetikle
  (repo’ya eklenmez, sadece lokalde `/tmp`’de tutulabilir):
```bash
CROON_CAP_MODE=live PYTHONPATH="$PWD" .venv/bin/python - <<'PY'
import asyncio
from decimal import Decimal
from sqlmodel import Session
from croon.db import engine, init_db
from croon.models import StandingOrder
from croon.cap_client import build_cap_client
from croon.engine import execute_run

async def main():
    init_db()
    cap = build_cap_client()
    with Session(engine) as s:
        o = StandingOrder(
            name="Polymarket Signal Brief (live demo)",
            task_prompt="Notable Polymarket smart-wallet activity brief.",
            category="polymarket",
            cadence_seconds=300,
            budget_per_run_usdc=Decimal("0.50"),
            max_total_budget_usdc=Decimal("5.00"),
            max_agents_to_query=3, status="active",
        )
        s.add(o); s.commit(); s.refresh(o)
        run = await execute_run(o, s, cap)
        print("RUN", run.id, run.mode, run.winner_agent_id, run.tx_hash)

asyncio.run(main())
PY
```

Beklenen skorlama sonucu (fiyat 3’ünde de 0.10, eta 60 → reputation belirleyici):
`Tracker (0.99) > Broker (0.95) > Polymind (0.9)` → **kazanan Tracker**, sadece
o hire+pay edilir → tek gerçek tx.

### D. Doğrula (video çekmeden önce şart)
```bash
sqlite3 croon.db "SELECT mode, winner_agent_id, tx_hash, \
  json_array_length(quotes_json) AS n_quotes FROM run \
  ORDER BY started_at DESC LIMIT 1;"
```
Şunlar sağlanmalı:
- `mode = live` (mock/unverified DEĞİL),
- `n_quotes = 3` (üç bağımsız teklif kayıtlı),
- `tx_hash` gerçek `0x…` ve **BaseScan’de açılıyor** (tx_verified True),
- `selection_reason` skoru/ağırlıkları açıklıyor.

### E. Demo videosu (≤5 dk) senaryosu
1. Standing order göster → “Run now”.
2. UI’da 3 provider’dan **3 teklif** akışını göster.
3. Skorlama tablosu + **kazanan seçimi** (neden kazandı) göster.
4. `payment_completed` → **tx_hash’i BaseScan’de aç** (zincir üstü kanıt).
5. Receipt hash + delivery.
6. “3 farklı ekip/harici Store agent’ı” olduğunu README §CAP-mapping ile vurgula.

---

## Provider bağımsızlığı notu (1.’liği garantileyen kısım)
3 provider da **CROO Agent Store’daki harici üçüncü-taraf** servisler; bizim
requester agent’ımız (`2c61c35b…`) onlardan **farklı**. `validate_candidates`
zaten kendi service_id’lerimizle çakışmayı (self-trade) reddediyor. README’de bu
3 provider’ın Store linklerini ve sahiplik farkını açıkça belirt → “aynı ekip”
şüphesini baştan kapat.

## Özet checklist
- [ ] `.env` live + SDK key + 3 provider roster doğrulandı
- [ ] Cüzdan USDC fonlu (readiness_check yeşil)
- [ ] Mock run’lar demo DB’sinden temizlendi (yedekli)
- [ ] Tam engine 1 kez canlı çalıştırıldı (3 teklif + kazanan + tx)
- [ ] Run `mode=live`, `n_quotes=3`, tx BaseScan’de açılıyor
- [ ] Demo videosu bu zinciri gösteriyor
