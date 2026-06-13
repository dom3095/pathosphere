# Next steps

## ✅ Fatto

- `feature/rss_feed` merged in main (rebase), contenuto identico — branch eliminabile.
- **1. Ottimizzazione GDELT** committata: `build_lookup_caches` elimina ~2 SELECT/riga.
- **2. NER + geocoding + Wikidata** implementati in `semantic/extract.py`:
  spaCy `xx_ent_wiki_sm` → `entities` + `document_entities`; Nominatim con
  `geocode_cache` (1 req/s) → `events.lat/lon`; `wbsearchentities` → QID +
  canonical_name. Wired in `_phase_extract` + comando `pathos extract`.
  Nota: dopo pull eseguire `uv sync` e `pathos db init` (migrazioni additive).
- **3. PortWatch** implementato in `ingest/portwatch.py`: ArcGIS FeatureServer
  `Daily_Chokepoints_Data`, 7 chokepoint strategici → tabella `chokepoint_metrics`
  (timeseries giornaliera). Anomaly detection z-score vs baseline trailing 30g
  (latest escluso, no lookahead) → `events` `event_type=infrastructure` con
  `location_name`=portname (geocode phase riempie lat/lon). Comando
  `pathos ingest portwatch` + wired in `_phase_ingest`. 15 test.
- **4. Comtrade** implementato in `ingest/comtrade.py`: API preview gratuita
  (`public/v1/preview/C/M/HS`, no key, ≤500 rec/call). HS 8541/8542/8486,
  9 reporter pilota vs World, import+export mensili. Filtri ai totali
  (`motCode=0`, `customsCode=C00`, `partner2Code=0`) per evitare il troncamento
  a 500 dovuto alla disaggregazione. Ogni record → `raw_document` sintetico
  (source "UN Comtrade", url `comtrade://...` per dedup) così la pipeline
  semantica tratta i flussi come documenti. Comando `pathos ingest comtrade`
  + wired in `_phase_ingest`. 11 test.
- **5. USGS + FIRMS** implementati in `ingest/physical.py`, `event_type=hazard`:
  - USGS earthquakes (FDSNWS GeoJSON, no key): sismi significativi
    (`min_magnitude` default 5.0) → un evento per sisma con lat/lon/depth,
    dedup (title, first_seen), severity da magnitudo. `pathos ingest usgs`.
  - NASA FIRMS (richiede `FIRMS_MAP_KEY` gratuito in .env): rilevazioni fuoco
    attive per 4 aree strategiche, evento di sintesi solo se conteggio > soglia
    (default 50) — non scarica i singoli pixel. Skip pulito senza key.
    `pathos ingest firms`. config: `firms_map_key`.
  - Entrambi wired in `_phase_ingest`. 12 test.

**→ Fase 1 COMPLETATA. Tutti gli ingestor pronti.** Prossimo: Fase 3 (agent).

---

## Fase 3 (dopo phase1-remaining)

- Brief mattutino con Qwen3 4B → `_phase_brief`
- Generatore tesi con catene causali (Claude Agent SDK)
- Paper trading EOD: `trades`, `portfolios`, aggiornamento prezzi via yfinance
- Flusso approvazione CLI: proposta → approve/reject con motivazione loggata
- Portafogli di controllo: agent vs random vs buy&hold indice
- Predizioni non finanziarie con calibrazione Tetlock (`predictions`)
