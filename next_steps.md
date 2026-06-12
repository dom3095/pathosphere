# Next steps

## Pendente prima del nuovo branch

- Mergare `feature/rss_feed`: autenticati con `gh auth login --web`, poi:
  ```bash
  export PATH="/opt/homebrew/bin:$PATH"
  gh pr create \
    --title "feat: Phase 1 RSS ingestor + Phase 2 semantic pipeline" \
    --base main \
    --body "..."
  ```
  Scegli **Rebase and merge** su GitHub. Poi elimina il branch:
  ```bash
  git push origin --delete feature/rss_feed
  git branch -d feature/rss_feed
  ```

---

## Nuovo branch: `feature/ner-phase1-remaining`

### 1. Commit ottimizzazione GDELT (già pronta in working tree)
`build_lookup_caches` in `gdelt.py` + `cli.py` — pre-carica url/event-key in dict Python,
elimina ~2 SELECT per riga GDELT → ~17M query risparmiate sul download storico 5 anni.

### 2. NER + geocoding + Wikidata entity linking
Implementa `_phase_extract` in `cycle/orchestrator.py`:
- **spaCy** per entity extraction (persone, organizzazioni, luoghi, commodity)
- **Nominatim** per geocoding → `lat/lon` su `events`
- **Wikidata QID lookup** per entity linking → popola `entities` + `entity_links`
- Tabelle: `entities`, `entity_links` (già in schema)

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
