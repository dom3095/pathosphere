# Next steps

## ✅ Fatto

- `feature/rss_feed` merged in main (rebase), contenuto identico — branch eliminabile.
- **1. Ottimizzazione GDELT** committata: `build_lookup_caches` elimina ~2 SELECT/riga.
- **2. NER + geocoding + Wikidata** implementati in `semantic/extract.py`:
  spaCy `xx_ent_wiki_sm` → `entities` + `document_entities`; Nominatim con
  `geocode_cache` (1 req/s) → `events.lat/lon`; `wbsearchentities` → QID +
  canonical_name. Wired in `_phase_extract` + comando `pathos extract`.
  Nota: dopo pull eseguire `uv sync` e `pathos db init` (migrazioni additive).

---

## Branch corrente: `feature/ner-phase1-remaining`

### 3. PortWatch ingestor
IMF PortWatch API — chokepoint: Suez, Hormuz, Panama, Bab el-Mandeb.
Segnale fisico su rotte marittime → `events` con `event_type=infrastructure`.

### 4. UN Comtrade ingestor (filiera semiconduttori)
HS codes: **8541/8542** (semiconduttori) + **8486** (macchinari produzione chip).
Dati mensili → `raw_documents` con `source_id` filiera pilota.

### 5. USGS + NASA FIRMS
- USGS earthquakes API (JSON) → eventi fisici in `events`
- NASA FIRMS MODIS (incendi) → eventi fisici in `events`

---

## Fase 3 (dopo phase1-remaining)

- Brief mattutino con Qwen3 4B → `_phase_brief`
- Generatore tesi con catene causali (Claude Agent SDK)
- Paper trading EOD: `trades`, `portfolios`, aggiornamento prezzi via yfinance
- Flusso approvazione CLI: proposta → approve/reject con motivazione loggata
- Portafogli di controllo: agent vs random vs buy&hold indice
- Predizioni non finanziarie con calibrazione Tetlock (`predictions`)
