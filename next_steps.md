# Next steps

Aggiornato 2026-06-14.

## 🤝 Handoff — leggere per primo

- **Branch**: `feat/numeric-detail-tables-rss-tor`, 3 commit pushati. Tutto il
  lavoro di questa sessione (sotto) è **in locale non committato** — l'utente
  dice quando committare. `git status` per il diff.
- **Test**: 150 verdi (`uv run pytest`, ~8s). Eseguirli dopo ogni modifica.
- **DB attuale** `data/db/pathosphere.db` (campione fresco 2026-06-15): 6,967 doc
  (gdelt 5742, rss 1081, comtrade 144), 3,766 eventi (gdelt 2884, portwatch 4,
  usgs 832, firms 46), 2,520 chokepoint_metrics, 1,707 fire_metrics. Tutti i 9
  check di validazione passati. Backup storico 8 anni GDELT in
  `pathosphere.db.bak-20260614` (2.5G, file separato — tenere).
- **Drop+rebuild** eseguito 2026-06-15 — campione pulito disponibile.
- **Sicurezza**: mai leggere `.env`/secrets (CLAUDE.md + deny in
  `.claude/settings.json`). Per FIRMS controllare solo `bool(settings.firms_map_key)`.
- **Gli ingest li lancia l'utente** dal terminale (rete + chiavi), non l'agent.

### Comandi per ricostruire un campione (li lancia l'utente)

```bash
uv run pathos db init && uv run pathos sources seed
uv run pathos ingest gdelt --days 2            # campione recente conflitti
uv run pathos ingest rss --max-age-days 3
uv run pathos ingest portwatch                 # 90gg + anomalie (latest)
uv run pathos ingest comtrade --start 202401 --end 202403
uv run pathos ingest usgs --start 2026-01-01
uv run pathos ingest firms --start 2026-01-01  # serve FIRMS_MAP_KEY; usa VIIRS_NOAA20_SP (finestre 5gg)
```

Poi i 9 + check di validazione qui sotto, e l'analisi (Fase 2).

## ✅ Fatto in questa sessione

- **Schema**: colonna `origin` su `raw_documents`/`events`; dettaglio numerico
  `gdelt_events` (Goldstein/tone/quad_class/mentions) e `comtrade_flows`
  (USD/kg); **`fire_metrics`** (timeseries giornaliera FIRMS, PK `(area,date)`);
  fix ordine migrazioni in `init_db`.
- **GDELT**: `DATEADDED` come data canonica (SQLDATE inaffidabile — rollover
  −100 e off-by-1yr); scrittura `gdelt_events`.
- **Comtrade**: backoff su 429 + backfill `--start/--end`; popola `comtrade_flows`.
- **PortWatch**: 28 chokepoint + paginazione FeatureServer (`--full`).
- **FIRMS**: riscritto sul pattern PortWatch — `fire_metrics` + anomalia surge
  z-score (floor `--min-detections`); backfill storico `--start` (auto source
  `VIIRS_NOAA20_SP`, finestre ≤5gg, fallback NRT automatico su 400); incrementale
  che riprende da `max(date)`/area.
- **USGS**: backfill storico `--start/--end` + incrementale da `max(first_seen)`.
- **Anomalie storiche**: detector condiviso `ingest/anomaly.py::find_anomalies`
  (PortWatch + FIRMS). Backfill (`--full`/`--start`) **scorre tutta la
  timeseries** e recupera le anomalie storiche, non solo l'ultimo giorno.
- **Audit fonti**: ogni fonte ha storico + incrementale-da-ultimo (RSS resta
  solo-incrementale). README: sezione bootstrap vs incrementale.
- **RSS**: Tor per RT (daemon effimero); header browser completi; catalogo
  pulito (8 feed morti commentati) + 8 fonti nuove.
- **Doc**: `data-semantics.md`, `embeddings-caveat.md`, **`firms-refactor-report.md`**
  (review/bug/limitazioni); allineati `schema.md`/`wiki.md`/`README.md`.

---

## ✔ Validazione post-ingestion (fare appena finito di popolare)

Tutti i check devono passare prima di procedere alla Fase 2.

```bash
DB=data/db/pathosphere.db

# 1. Row counts per tabella chiave (devono essere > 0 dove atteso)
sqlite3 $DB "SELECT 'raw_documents',count(*) FROM raw_documents
 UNION ALL SELECT 'events',count(*) FROM events
 UNION ALL SELECT 'gdelt_events',count(*) FROM gdelt_events
 UNION ALL SELECT 'comtrade_flows',count(*) FROM comtrade_flows
 UNION ALL SELECT 'chokepoint_metrics',count(*) FROM chokepoint_metrics;"

# 2. origin sempre valorizzato (atteso 0 NULL)
sqlite3 $DB "SELECT count(*) FROM raw_documents WHERE origin IS NULL;"
sqlite3 $DB "SELECT origin,count(*) FROM raw_documents GROUP BY origin;"

# 3. FK senza orfani (tutti attesi = 0)
sqlite3 $DB "SELECT count(*) FROM gdelt_events g LEFT JOIN events e ON g.event_id=e.id WHERE e.id IS NULL;"
sqlite3 $DB "SELECT count(*) FROM gdelt_events g LEFT JOIN raw_documents r ON g.document_id=r.id WHERE r.id IS NULL;"
sqlite3 $DB "SELECT count(*) FROM comtrade_flows c LEFT JOIN raw_documents r ON c.document_id=r.id WHERE r.id IS NULL;"

# 4. Date GDELT pulite (atteso 0: niente 1920/pre-2017 grazie a DATEADDED)
sqlite3 $DB "SELECT count(*) FROM raw_documents WHERE origin='gdelt' AND published_at < '2017';"
sqlite3 $DB "SELECT origin,min(published_at),max(published_at) FROM raw_documents GROUP BY origin;"

# 5. RSS = testo reale (body popolato) + source_id + lingua
sqlite3 $DB "SELECT count(*) tot, count(NULLIF(body,'')) con_body, count(source_id) con_src
 FROM raw_documents WHERE origin='rss';"

# 6. Copertura RSS per blocco (controllare che ogni blocco abbia ≥1 fonte con doc)
sqlite3 $DB "SELECT s.geopolitical_block, count(DISTINCT s.id), count(r.id)
 FROM sources s JOIN raw_documents r ON r.source_id=s.id AND r.origin='rss'
 GROUP BY 1 ORDER BY 3 DESC;"

# 7. PortWatch: 28 chokepoint + range date
sqlite3 $DB "SELECT count(DISTINCT portid), min(date), max(date) FROM chokepoint_metrics;"

# 7b. FIRMS: aree monitorate + range date + sorgenti usate
sqlite3 $DB "SELECT count(DISTINCT area), min(date), max(date) FROM fire_metrics;"
sqlite3 $DB "SELECT source, count(*) FROM fire_metrics GROUP BY source;"
sqlite3 $DB "SELECT count(*) FROM events WHERE origin='firms';"  -- anomalie surge

# 8. Comtrade: valori numerici sani (0 nulli) + range periodi
sqlite3 $DB "SELECT count(*), count(NULLIF(primary_value,0)), min(period), max(period) FROM comtrade_flows;"

# 9. gdelt_events: segnali numerici presenti + recupero date bug
sqlite3 $DB "SELECT count(*), round(avg(avg_tone),2), max(num_mentions) FROM gdelt_events;"
sqlite3 $DB "SELECT count(*) FROM gdelt_events WHERE substr(date_added,1,4)!=substr(sqldate,1,4);"  -- bug sqldate corretti via date_added
```

Cose da verificare a occhio sull'output:
- ogni `origin` (gdelt/rss/comtrade) presente con conteggi plausibili;
- nessun blocco geopolitico a 0 fonti (atteso `china` debole — Xinhua perso);
- date per origin coerenti (gdelt ~recente, comtrade 2018→, rss con orario);
- se GDELT è stato fatto `--days 7`: `published_at` tutto nell'ultima settimana;
- errori RSS nel log (feed 403/404/timeout) = manutenzione fonti, non bug dati.

---

## ✅ Fase 2 — Pipeline semantica (COMPLETATA 2026-06-15)

Opzione (a) scelta: GDELT escluso dall'embedding (body vuoto → titolo sintetico
non utile). `UPDATE raw_documents SET embedded=1 WHERE origin='gdelt'` applicato
prima di `pathos embed`. Risultati campione:

| Step | Risultato |
|---|---|
| Embedding (e5-small 384d) | 1,225 vettori (RSS 1081 + Comtrade 144) |
| Dedup semantica KNN ≥0.92 | 227 duplicati (~18% RSS) |
| Clustering (cosine ≥0.85, cap 30) | 329 eventi RSS (4,095 totali) |

**Fix cluster**: threshold default alzato 0.75→0.85 + `max_cluster_size=30` (prima
tutti 801 doc finivano in 1 mega-cluster per chain-linking single-linkage). Ora
cluster top per OSINT hanno copertura multi-blocco: Taiwan (western+china+russia+arab),
Iran drones (6 blocchi), Russia oil ban (4 blocchi). 10 cluster cappati a 30 = storie
più coperte. 268 singleton = notizie coperte da una sola fonte (normali).

**DB attuale**: 6,967 doc tutti `embedded=1`; 4,095 eventi; 7,858 `event_documents`;
1,225 `vec_documents`.

## ✅ Fase 2 — NER + entity extraction (COMPLETATA)

- `semantic/extract.py` implementato: NER spaCy `xx_ent_wiki_sm` + geocoding
  Nominatim (cached) + Wikidata `wbsearchentities`. `pathos extract` CLI wired.
- Orchestrator `_phase_extract` wired al modulo (non stub).
- Prerequisito una-tantum: `uv run python -m spacy download xx_ent_wiki_sm`

## ▶ Prossimo: Fase 2 restante

6. **Grafo entità** + **divergenza narrativa** per blocco (`narrative_divergences`)
   — confronto stesse notizie tra blocchi (ora c'è pluralità: Russia statale vs
   Moscow Times, ecc.).

---

## Fase 3 — Agent e valutazione (dopo Fase 2)

- Astrazione LLM (config `reasoning_model: claude | qwen-local`).
- Brief mattutino (`_phase_brief`, oggi stub) con Qwen locale.
- Generatore tesi con catene causali (Claude Agent SDK) → `theses`.
- Paper trading EOD: `trades`/`portfolios`, prezzi **yfinance** (da agganciare,
  ancora assente), no-lookahead; portafogli di controllo (agent/random/buy&hold).
- Flusso approvazione CLI (approve/reject con motivazione).
- Predizioni non finanziarie (`predictions`) + calibrazione Tetlock.

---

## Housekeeping / pendenti

- **Push branch + PR** quando pronto (`git push -u origin feat/numeric-detail-tables-rss-tor`).
- **Backup `data/db/pathosphere.db.bak-20260614`** (2.5G) = unica copia degli 8
  anni GDELT scaricati. Eliminare solo se si rifà `gdelt-history`, altrimenti
  tenere o ripartire da lì.
- **yfinance**: non ancora agganciato — prerequisito per il paper trading.
- **Catalogo RSS**: blocco `china` rimpolpato 2026-06-15 — aggiunte SCMP China
  section (`/4/feed/`), MERICS (DE, western), Taiwan MOFA (TW, western); SCMP
  aggiornato da world (`/5`) a All News (`/91`, volume ×4). Voce statale
  cinese assente: Xinhua/China Daily/Sixth Tone/People's Daily tutti stali o
  404; Global Times outbrain.xml live ma ~1 art/mese. Da ri-seedare il DB.
- **Segnali numerici GDELT / studio quant**: parcheggiati (non priorità —
  il cuore è l'agent semantico, non un modello quant).
- **GKG enrichment**: opzionale, abilita ricerca semantica sui doc GDELT.
