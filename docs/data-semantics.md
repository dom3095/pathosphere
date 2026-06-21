# Semantica dei dati — come leggere il DB senza sbagliare

> **Avvertenza.** Documento scritto da Claude, verificato sul DB il **2026-06-14**.
> Riflette ciò che è stato **osservato nel codice e nei dati reali** a quella data,
> non ciò che lo schema *promette*. Dove non ho certezza, è scritto esplicitamente.
> Per la struttura (DDL, vincoli, ER) vedi [schema.md](schema.md); qui c'è il
> **significato** dei dati, che lo schema da solo non rivela.

## Principio chiave (l'errore da non rifare)

`raw_documents` ed `events` sono tabelle **eterogenee**: lo stesso campo significa
cose diverse a seconda dell'**ingestor** che ha scritto la riga. Non esiste una
"riga raw_documents" generica. Prima di interpretare un campo, chiediti **da quale
fonte viene la riga**. Trattare i dati GDELT come se fossero articoli giornalistici
è l'errore che questo documento esiste per prevenire.

**Provenance esplicita: la colonna `origin`.** Da 2026-06-14 sia `raw_documents`
che `events` hanno una colonna **`origin`** valorizzata dall'ingestor
(`gdelt | rss | comtrade | portwatch | usgs | firms`). **Usa quella** per sapere
da dove viene una riga — non più dedotta da `source_id IS NULL` o dal prefisso del
titolo. Gli indizi indiretti restano validi come conferma:

| Indizio | `origin` |
|---|---|
| `body` vuoto, `title` inizia con `GDELT:` | `gdelt` |
| `url` inizia con `comtrade://` | `comtrade` |
| `source_id` valorizzato + `body` reale + `published_at` con orario | `rss` |

---

## `raw_documents` — per sorgente

### GDELT Events (la stragrande maggioranza delle righe: ~3,2 M)

Ogni riga del file GDELT 2.0 Events diventa **un** `raw_document`. Vedi
[`store_rows`](../pathosphere/ingest/gdelt.py) ([gdelt.py:264-277](../pathosphere/ingest/gdelt.py#L264-L277)).

- **`title`** = stringa **sintetica**, non un titolo giornalistico:
  `f"GDELT: {Actor1Name} → {Actor2Name} [{EventCode}]"`
  ([gdelt.py:272](../pathosphere/ingest/gdelt.py#L272)). Esempio reale:
  `GDELT:  → QUEENSLAND [171]` (Actor1Name vuoto, Actor2Name="QUEENSLAND",
  EventCode=171). Contiene frammenti di nomi-attore, **non il testo dell'articolo**.
- **`body`** = **vuoto**. GDELT Events non fornisce il testo dell'articolo, solo
  l'URL e i metadati. Non cercare contenuto testuale qui.
- **`source_id`** = **NULL**. I documenti GDELT **non sono attribuiti** a una
  fonte della tabella `sources`. Nessun paese / orientamento / blocco geopolitico
  è associato a un documento GDELT.
- **`language`** = vuoto.
- **`content_hash`** = `SHA-256 dell'URL`, **non del body**
  ([gdelt.py:265](../pathosphere/ingest/gdelt.py#L265)). ⚠️ Il commento nello
  schema dice "SHA-256 of body" — per GDELT è **falso**. La dedup esatta su GDELT
  è di fatto dedup per URL.
- **`published_at`** = data canonica = **DATEADDED** (quando GDELT ha osservato
  l'evento), formato `YYYY-MM-DDT00:00:00`. **Non** è SQLDATE: SQLDATE è
  inaffidabile (vedi sezione anomalia) e resta solo come fallback se DATEADDED
  manca ([`_event_date_iso`](../pathosphere/ingest/gdelt.py)). Lo stesso vale per
  `events.first_seen`/`last_seen`.
- **`embedded`** = 0 alla scrittura.
- **`origin`** = `gdelt`. I segnali numerici per-riga (Goldstein, tone, mentions,
  ecc.) stanno nella tabella di dettaglio **`gdelt_events`**, legata a questo doc
  (`document_id`) e al cluster `events` (`event_id`).

### RSS

- **`title`** = headline reale dell'articolo.
- **`body`** = testo reale dell'articolo.
- **`source_id`** = valorizzato → paese / blocco / orientamento da `sources`.
- **`language`** = valorizzato.
- **`published_at`** = timestamp completo con fuso (es. `2026-06-14T07:55:16+00:00`).

Questa è l'unica sorgente, ad oggi, con **testo giornalistico reale**.

### UN Comtrade

- Ogni **record commerciale** (reporter × HS × flusso × periodo) diventa un
  `raw_document` **sintetico** ([comtrade.py](../pathosphere/ingest/comtrade.py),
  `_synthesize_doc`).
- **`url`** = chiave sintetica `comtrade://{reporter}/{cmd}/{flow}/{period}` (serve
  solo da chiave di dedup, non è un link reale).
- **`title` / `body`** = testo **generato** dal record (es. "USA Import HS8542 …
  $X M"), **non una notizia**.
- **`source_id`** = riga `sources` "UN Comtrade".

---

## `events` — per sorgente

### GDELT

Un `event` è creato per ogni **chiave-evento univoca**, deduplicando le righe
([gdelt.py:282-335](../pathosphere/ingest/gdelt.py#L282-L335)).

- **`title`** = **chiave tecnica di dedup**, non leggibile:
  `Actor1CountryCode | Actor2CountryCode | EventRootCode | SQLDATE | ActionGeo_CountryCode`
  ([gdelt.py:283-289](../pathosphere/ingest/gdelt.py#L283-L289)). Esempio:
  `|AUS|19|20180101|UK` = Actor1Country vuoto, Actor2Country=AUS, EventRootCode=19
  (fight), data 2018-01-01, ActionGeoCountry=UK. **Non è un titolo**: non mostrarlo
  come tale.
- **`summary`** = qui sta il **contenuto semantico**, come testo
  ([gdelt.py:317-322](../pathosphere/ingest/gdelt.py#L317-L322)):
  `{Actor1Name} [{Actor1Country}] — {EventCode} — {Actor2Name} [{Actor2Country}] | Goldstein=… Tone=…`.
  Esempio reale: ` [] — 190 — SYDNEY [AUS] | Goldstein=-10.0 Tone=-2.51`.
- **`event_type`** = etichetta human-readable mappata dall'**EventRootCode CAMEO**
  ([EVENT_TYPE_MAP, gdelt.py:63-71](../pathosphere/ingest/gdelt.py#L63-L71)):
  `01 statement … 10 demand, 11 disapprove, 13 threaten, 14 protest, 17 coerce,
  18 assault, 19 fight, 20 mass_violence`. Codici non mappati → `other`.
- **`severity`** = derivata dal **GoldsteinScale**, scalata 1-5
  ([gdelt.py:302](../pathosphere/ingest/gdelt.py#L302)).
- **`lat`/`lon`/`location_name`** = da `ActionGeo_*`.
- **Dedup grossolana**: la chiave è la 5-tupla *giornaliera* sopra. Quindi **un
  `event` GDELT NON è un evento del mondo reale univoco**: è un raggruppamento
  per (paese attore 1, paese attore 2, tipo CAMEO, giorno, paese azione). Più
  articoli/righe distinte collassano nello stesso event; eventi reali diversi con
  la stessa 5-tupla collassano anche loro. Il clustering semantico in "eventi"
  veri (Fase 2) è un livello **successivo e separato**.

### PortWatch

- `event_type` = `infrastructure`. Generato **solo** come anomalia di transito
  (z-score) e **solo sull'ultima data** disponibile per chokepoint
  ([portwatch.py, `_detect_anomaly`](../pathosphere/ingest/portwatch.py)). Non è
  un backfill retroattivo di anomalie: la serie storica completa sta in
  `chokepoint_metrics`, non in `events`.

### USGS / FIRMS

- `event_type` = `hazard` (da codice ingestor `physical.py`). **Non verificato nel
  DB in questa sessione** — questi ingestor non risultavano ancora eseguiti al
  2026-06-14. Trattare con cautela finché non confermato sui dati reali.

---

## `chokepoint_metrics` (PortWatch)

Serie storica **giornaliera** dei transiti per i 28 chokepoint IMF. Copertura
upstream verificata: **dal 2019-01-01**. Campi `n_total`, `n_tanker`,
`n_container`, `n_dry_bulk`, `n_cargo`, `capacity`. Chiave `(portid, date)`,
upsert idempotente. Questa è la fonte da usare per analisi quantitative sui
transiti — **non** la tabella `events` (che ha solo le anomalie più recenti).

---

## Tabelle di dettaglio numerico (pattern satellite)

`events` è il livello **normalizzato cross-source**; i dati nativi e numerici di
ogni sorgente stanno in tabelle dedicate legate per FK. Oltre a
`chokepoint_metrics`:

### `gdelt_events` (dal 2026-06-14)

**Una riga per `GlobalEventID`** GDELT (granularità piena, più fine di `events`
che deduplica per 5-tupla → un `events.id` raccoglie molte righe `gdelt_events`).
Collegata a `events.id` e a `raw_documents.id`. Contiene i segnali numerici
per-riga: `goldstein` (−10..+10), `avg_tone`, `quad_class` (1-4),
`num_mentions`/`num_sources`/`num_articles`, più `event_code` (CAMEO pieno),
`date_added` (DATEADDED canonico) e `sqldate` grezzo (per audit, inaffidabile).
Per aggregare un segnale a livello evento: `GROUP BY event_id`.

### `comtrade_flows` (dal 2026-06-14)

**Una riga per record commerciale** (reporter × HS × flusso × periodo), legata al
`raw_documents.id` del doc sintetico. Valori **numerici** interrogabili:
`primary_value` (USD), `net_weight` (kg), `reporter_code`/`reporter_iso`,
`partner_code` (0=World), `cmd_code`, `flow_code` (M/X), `period` (YYYYMM). Usa
questa per analisi sui flussi — non il body testuale del documento.

---

## Trappole — elenco rapido

1. **`body` vuoto su GDELT.** Non cercare testo d'articolo nei documenti GDELT.
2. **`content_hash` GDELT = hash dell'URL**, non del body (a dispetto del commento
   nello schema).
3. **`source_id` NULL su GDELT** → nessuna attribuzione fonte/paese/blocco per i
   documenti GDELT.
4. **`events.title` GDELT non è un titolo** ma una chiave di dedup tecnica.
5. **I segnali numerici GDELT ora sono colonne** (dal 2026-06-14), nella tabella
   `gdelt_events` (Goldstein, AvgTone, QuadClass, NumMentions/Sources/Articles).
   `events.summary` continua a riportarne alcuni come testo, ma la fonte
   interrogabile è `gdelt_events`, non la stringa summary. ⚠️ Vale solo per i dati
   ingeriti dopo quella data.
6. **Un `event` GDELT non è un evento reale univoco** (dedup grossolana per
   5-tupla giornaliera).
7. **`events` PortWatch contiene solo le anomalie più recenti**, non la storia.

---

## Anomalia date GDELT — causa determinata e gestita

**Sintomo** (osservato 2026-06-14): `raw_documents.published_at` ha **5038 righe
con anno 1920**, più ~900 righe sparse tra 2008 e 2016.

**Causa del 1920 — confermata sui file sorgente.** Scaricando i file `export`
originali di inizio gennaio 2020, il campo SQLDATE vale **letteralmente**
`19200101`/`19200102` per il **98%** delle righe di quei file. È un bug di GDELT
al **cambio anno** (lo stamp dell'anno scivola di −100: 2020 → 1920). Il nostro
parser è corretto: `_sqldate_to_iso` fa solo slicing, quindi `1920` può venire
solo da un input `1920…`. La colonna **DATEADDED** (col 60, timestamp con cui
GDELT ha aggiunto l'evento) **resta corretta** (`20200101…`) e permette il
recupero.

**SQLDATE ha più modalità di bug, non solo il rollover.** Verificato su un file
normale (giugno 2020): ~1% delle righe ha SQLDATE con un valore fisso ~1 anno
prima (`20190616`) su articoli chiaramente del 2020 (URL con `/2020/06/15/`),
mentre DATEADDED è corretto. Sono **stamp errati**, non retrodatazioni reali (una
retrodatazione vera si spargerebbe su molte date; questo è un valore unico
sbagliato). Conclusione: **SQLDATE è inaffidabile in generale** — non usarlo come
data dell'evento. Di conseguenza anche le ~900 righe 2008-2016 sono di natura
incerta (possibile corruzione, non necessariamente retrodatazioni legittime).

**Gestione (decisione 2026-06-14): DATEADDED è la data canonica GDELT.**
[`_event_date_iso`](../pathosphere/ingest/gdelt.py) usa **DATEADDED** (quando GDELT
ha osservato l'evento) come data primaria; SQLDATE è solo fallback se DATEADDED
manca. Questo evita in un colpo tutte le modalità di bug di SQLDATE ed è anche la
scelta corretta per il no-lookahead (l'evento è datato a quando potevamo saperlo).

**Repair dei dati già in DB — eseguito 2026-06-14.** Il rollover è deterministico
(anno −100, mese/giorno preservati: SQLDATE `1920`0101 ↔ DATEADDED `2020`0101), e
tutte le 5038 righe erano in gennaio. Quindi recupero esatto in-DB con +100 anni,
senza re-fetch:

```sql
UPDATE raw_documents SET published_at = '2020' || substr(published_at, 5)
 WHERE published_at LIKE '1920-%';
UPDATE events SET first_seen = '2020' || substr(first_seen, 5),
                  last_seen  = '2020' || substr(last_seen, 5)
 WHERE first_seen LIKE '1920-%';
```

Residui 1920 dopo il repair: 0. Nota: `events.title` (chiave di dedup tecnica)
contiene ancora la sotto-stringa SQLDATE `19200101` — lasciata invariata perché è
una chiave opaca; i campi data semantici (`published_at`/`first_seen`/`last_seen`)
sono corretti.

Le ~900 righe **2008-2016** sono **lasciate intatte ma di natura incerta**
(probabile corruzione SQLDATE, vedi sopra). Sono ancora SQLDATE-derivate: la
nuova ingestione userà DATEADDED, ma queste righe scritte prima del cambio non
sono state re-fetchate, quindi per analisi sui dati attuali conviene filtrare
`published_at >= '2017'` oppure ri-scaricarle.
