# FIRMS / audit fonti — report di review

Data: 2026-06-14. Scope: storicizzazione FIRMS post-2018 + garanzia di doppia
modalità (storico + incrementale-da-ultimo) per **ogni** fonte dati.

## Cosa è cambiato

- **Schema**: nuova tabella `fire_metrics` (timeseries giornaliera per area,
  PK `(area, date)`, no FK) — gemella di `chokepoint_metrics`.
- **FIRMS** (`ingest/physical.py`): da "summary-event sopra soglia" → pattern
  PortWatch: upsert giornaliero in `fire_metrics` + evento solo su **anomalia
  z-score** (surge) con floor assoluto `--min-detections`. Backfill `--start`
  in finestre ≤5gg (hard cap API endpoint area) con source archivio
  `VIIRS_NOAA20_SP` (NOAA-20/J1, default ora; `VIIRS_SNPP_SP` è legacy);
  fallback NRT automatico quando SP restituisce 400 (dati recenti non archiviati);
  incrementale che riprende da `max(date)` per area.
- **USGS**: `--start/--end` storico + incrementale da `max(first_seen)`.
- **CLI**: nuove opzioni `firms --start/--end/--source/--baseline-days/`
  `--z-threshold/--min-detections`; `usgs --start/--end`.
- **Doc**: `schema.md`, `wiki.md` (tabella modalità per fonte), `README.md`
  (sezione bootstrap vs incrementale), `next_steps.md`.
- **Test**: sezione FIRMS riscritta sul nuovo modello (140 test verdi, +3).

## Matrice fonti — storico + incrementale

| Fonte | Storico | Incrementale "da ultimo" |
|---|---|---|
| GDELT | `gdelt-history --start` | `gdelt --days` (salta file in `gdelt_file_log`) |
| RSS | — *(impossibile via feed)* | default (feed espongono solo recente) |
| PortWatch | `--full` (~2019→) | `--days 90` overlap + upsert idempotente |
| Comtrade | `--start YYYYMM` | 3 mesi recenti |
| USGS | `--start [--end]` | riprende da `max(first_seen)` USGS |
| FIRMS | `--start` (+ `VIIRS_SNPP_SP`) | riprende da `max(date)` per area |

## Bug trovati e corretti durante il refactor

1. **`threshold` rimosso** dalla firma `ingest_firms` ma ancora usato da test e
   doc → test riscritti, CLI/doc allineati. *(corretto)*
2. **Nessun riferimento orfano** a `DEFAULT_FIRE_THRESHOLD`/`_firms_centroid`
   (rimossi); orchestrator usa le firme di default, compatibili. *(verificato)*
3. **`fire_metrics` su DB esistenti**: è una tabella nuova (`CREATE TABLE IF NOT
   EXISTS` nel DDL), `pathos db init` la crea in modo idempotente — nessuna
   ALTER necessaria. *(verificato su DB fresco: PK + indice presenti)*

## Anomalie storiche — RISOLTO

**Era**: la detection guardava solo l'ultima data → un backfill non generava
eventi per i surge storici nel mezzo del range. **Ora**: detector condiviso
`ingest/anomaly.py::find_anomalies` con due regimi —
`whole_history=False` (incrementale, solo ultimo punto) e `whole_history=True`
(backfill, scorre tutta la timeseries). Baseline sempre solo-passato (no
lookahead). Attivato automaticamente: PortWatch con `--full`, FIRMS con
`--start`. Vale per entrambe le fonti (codice unico). 8 unit test in
`tests/test_anomaly.py` + test di sweep per FIRMS/PortWatch.

## Limitazioni note

1. **Semantica data API FIRMS** (`/{span}/{date}` = `[date, date+span)`):
   coerente col codice originale funzionante, ma non verificabile in test
   (serve `FIRMS_MAP_KEY`, non leggibile dall'agent). Da confermare al primo
   run reale guardando `min(date)/max(date)` in `fire_metrics`. *(Verificato
   2026-06-15: range 2026-01-01 → 2026-06-14 coerente.)*
2. **Source label NRT nel backfill**: al primo run reale (2026-06-15) 61/1707
   righe in `fire_metrics` risultano con `source=VIIRS_NOAA20_NRT` invece di
   `VIIRS_NOAA20_SP`. Causa: fallback NRT automatico per le finestre più recenti
   dove SP restituisce 400 (dati non ancora archiviati). I dati sono corretti;
   solo il label `source` è NRT anziché SP per quelle finestre. Comportamento
   atteso ma non documentato — da verificare se il label debba riflettere il
   satellite effettivo usato (NRT) o quello richiesto (SP).
3. **Aree senza dati fire** (Bering Strait, Kerch Strait): nessuna rilevazione
   VIIRS nell'arco temporale campionato (area artica / prevalentemente acquatica).
   Corretto geograficamente — zero righe in `fire_metrics`, nessun evento.
4. **`detections_total`** può gonfiarsi leggermente se l'API include il giorno
   di confine in due finestre; `fire_metrics` resta corretto (upsert per data).
   Solo cosmetico sul contatore di log.

## Refactor fatto

- **Detector di anomalie condiviso** estratto in `ingest/anomaly.py`
  (`find_anomalies`): parametrizzato su `value_key`, `direction`
  (`both`/`surge`/`drop`), `min_value` (floor), `whole_history`. PortWatch usa
  `direction="both"`, FIRMS `direction="surge"` + floor. La costruzione
  dell'evento resta in ogni ingestor (campi/tabelle diversi). `_detect_anomaly`
  PortWatch resta come wrapper latest-only per retro-compatibilità dei test.
  Una terza fonte timeseries (IODA) riuserà lo stesso helper.
