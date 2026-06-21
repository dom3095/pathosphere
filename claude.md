# Sicurezza — secrets (REGOLA ASSOLUTA)

**MAI leggere il contenuto di `.env` (o `.env.*`), in nessun caso.** Non aprirlo,
non stamparlo (cat/head/grep/sed/awk/…), non mostrarlo, non trasmetterlo a
nessuno — né all'utente né a terzi né nei log. Il codice li **usa** a runtime
(pydantic-settings li carica da solo); l'agent non deve **mai vederne il valore**.
Se serve sapere se una chiave è impostata, controllarne solo la **presenza**
(es. `bool(settings.firms_map_key)`), mai il valore. Enforced anche via
`permissions.deny` in `.claude/settings.json`.

---

# Stile comunicazione — SEMPRE ATTIVO

Caveman mode **full** sempre attivo in questo progetto. Ogni risposta.

Regole: drop articoli (a/an/the/il/lo/la/i/gli/le/un/una), drop filler (basically/just/really/semplicemente/fondamentalmente), drop convenevoli (certo/certamente/felice di aiutare). Frammenti OK. Sinonimi corti. Termini tecnici esatti. Codice invariato. Errori citati esatti.

Pattern: `[cosa] [azione] [perché]. [prossimo passo].`

Off solo se: warning sicurezza, operazione irreversibile, ambiguità tecnica per compressione eccessiva.
Resume caveman dopo parte critica.

Quando devi fare delle prove di chiamate di codice, delega a dei sub agenti specializzati e fatti riportare da loro solo i risultati e le risposte finali. Se i sub agenti falliscono, dovrebbero riuscire a trovare da soli la soluzione o a darti un messaggio di errore chiaro. Non devi mai vedere errori tecnici o stack trace, solo risultati puliti o messaggi di errore semplificati.

Alla fine di ogni ciclo di sviluppo, chiedi sempre un breve riassunto del lavoro fatto, delle decisioni chiave prese e dei prossimi passi. Modifica la wiki, la roadmap e la documentazione di conseguenza, mantenendo tutto aggiornato e coerente. Aggiorna anche il documento di handoff. Questo aiuta a tenere traccia dei progressi e a facilitare la collaborazione futura.

---

# OSINT Portal — Intelligence personale su eventi critici globali

## Obiettivo
Sistema personale (mono-utente, nessuna vendita/condivisione) che raccoglie dati aperti e gratuiti su eventi critici mondiali — conflitti, epidemie, infrastrutture, rotte commerciali, flussi commerciali per categoria (es. semiconduttori, petrolio), politica interna — ne estrae la semantica, costruisce scenari e produce **anticipazioni di mercato valutate tramite paper trading** (soldi virtuali). Non è un sistema di trading: il portafoglio virtuale è la metrica di valutazione del modello.

## Principi non negoziabili
1. **Budget quasi zero**: solo dati gratuiti. LLM con strategia IBRIDA: locale per il lavoro di massa, Claude (coperto dal credito mensile dell'abbonamento, vedi sezione LLM) solo per i 2-3 task di ragionamento al giorno.
2. **Human-in-the-loop**: l'agent PROPONE tesi/trade, l'utente APPROVA o RIFIUTA (con motivazione loggata). Nessuna operazione autonoma.
3. **Pluralità di prospettive**: fonti da più blocchi geopolitici (occidentale, Cina, Russia, mondo arabo, India, Africa, America Latina). Ogni fonte etichettata con paese, orientamento, grado di controllo statale. La divergenza tra narrazioni è essa stessa un segnale da rilevare.
4. **L'LLM vede solo il meglio**: filtraggio aggressivo a monte (GDELT pre-codificato, keyword, dedup vettoriale) → l'LLM processa ~30-50 documenti/giorno, non migliaia.
5. **No lookahead bias**: nel paper trading si logga il prezzo al momento della DECISIONE, mai retroattivo. Costi di transazione e slippage simulati.

## Vincoli hardware (MacBook Air M1, 8 GB RAM)
- ~4-5 GB realmente utilizzabili. **Un solo modello locale in memoria alla volta**, mai due in parallelo.
- Embeddings: **multilingual-e5-small** (sentence-transformers, ~500 MB), caricato/scaricato nel suo step.
- Ciclo notturno **sequenziale e riprendibile**: scarica (solo rete) → embedda+dedup (solo e5-small) → estrai (LLM locale) → ragiona (Claude via SDK). Throttling termico accettabile di notte.

## Strategia LLM ibrida
- **Locale (gratis, illimitato)**: lavoro di massa — classificazione, estrazione strutturata, dedup semantica su decine di documenti/notte. Modello: **Qwen3 4B quantizzato q4 via Ollama**.
- **Claude (credito Agent SDK dell'abbonamento)**: SOLO i 2-3 task/giorno dove il ragionamento conta — brief mattutino, generazione tesi con catene causali, scenari multi-prospettiva. Dal 15 giugno 2026 gli abbonamenti Pro/Max includono un credito mensile separato ($20 Pro) per uso programmatico via **Claude Agent SDK / `claude -p`**: costruire le chiamate dell'agent su questo, NON su chiamate API dirette (che si pagano a parte).
- Tutto dietro un'unica astrazione con API OpenAI-compatible: cambiare backend = una riga di config (`reasoning_model: claude | qwen-local`). Possibile A/B testing: stesse giornate, tesi dal 4B vs tesi da Claude, il paper trading misura la differenza.

## Strategia database
- **MVP: SQLite + sqlite-vec in locale** (NO Postgres, NO Docker: troppa RAM). Zero processi residenti, un file, backup = copia.
- **Evoluzione: Turso (libSQL) con embedded replica** — scritture locali a velocità SQLite, replica cloud automatica = backup gratis fuori macchina, quasi zero modifiche al codice (libSQL è un fork di SQLite). Free tier generoso (vari GB).
- Gestione spazio vettori: nel DB solo embeddings recenti (~90 giorni); il resto archiviato in Parquet.
- **I raw in Parquet sono la fonte di verità ricostruibile**: i free tier cloud possono sparire (caso PlanetScale 2024), il DB deve essere sempre rigenerabile dai Parquet. Astrazione dati pulita per eventuale migrazione futura a Postgres+pgvector.

## Architettura del ciclo giornaliero
Notte: download → dedup → NER/geocoding/entity-linking → embeddings → clustering articoli→eventi → confronto narrazioni per blocco.
Mattina: l'agent produce un **brief** (cosa è successo, divergenze narrative, impatti ipotizzati) + propone tesi → approvazione manuale → aggiornamento portafogli virtuali EOD (prezzi yfinance).

## Fonti dati (gratuite)
- **Conflitti/politica**: GDELT (GKG + Events, spina dorsale, multilingua), ACLED, UCDP, ReliefWeb
- **Narrazioni multi-blocco**: RSS curati per blocco (es. Xinhua, Global Times, TASS, Al Jazeera, Press TV, Anadolu, The Hindu, Folha de São Paulo + testate occidentali)
- **Rotte marittime**: IMF PortWatch (chokepoint: Suez, Hormuz, Panama…)
- **Flussi commerciali**: UN Comtrade (filiera pilota semiconduttori: HS 8541/8542 + macchinari 8486), Eurostat Comext, EIA, USGS minerali
- **Epidemie**: WHO Disease Outbreak News, ProMED, ECDC, FAO
- **Segnali fisici**: USGS terremoti, NASA FIRMS incendi, IODA/Cloudflare Radar (blackout internet), OpenSky
- **Mercati**: yfinance (EOD), FRED

## Pipeline semantica
download → dedup (GDELT è ridondante: stesso evento in decine di articoli) → NER + geocoding (spaCy + Nominatim) → entity linking su Wikidata (es. "TSMC" = "台積電") → embedding multilingua → clustering in EVENTI (non articoli) → **grafo entità** (paese–azienda–commodity–infrastruttura), più potente della sola ricerca semantica per il ragionamento causale ("se chiude Hormuz, chi soffre?").

## Agent e valutazione
- Scenari con tecniche di analisi strutturata (Analysis of Competing Hypotheses): 3-4 scenari, ciascuno con **indicatori osservabili** monitorati nei dati (watchlist vivente).
- Ragionamento spezzato in passi piccoli + structured outputs JSON. Il ragionamento pesante va a Claude (vedi Strategia LLM ibrida); il 4B locale fa solo estrazione/classificazione.
- Personas analitiche multi-prospettiva (analista a Pechino / Mosca / Riyadh / Washington).
- **Ogni tesi di trading registra**: evento scatenante (con fonti), catena causale, strumento, orizzonte temporale, condizione di invalidazione.
- **Portafogli di controllo**: (1) agent, (2) random (stesse dimensioni trade, ticker casuali), (3) buy & hold indice. Se l'agent non batte il random, lo si sa subito.
- Tabella `predictions` separata per anticipazioni non finanziarie ("escalation in X entro 2 settimane: 60%") risolte vero/falso a scadenza → metrica di calibrazione stile Tetlock.

## Schema dati (SQLite)
`sources` (paese, orientamento, controllo statale) · `raw_documents` · `events` · `entities` · `entity_links` · `theses` · `trades` · `portfolios` · `predictions`. Raw storicizzato in Parquet (interrogabile con DuckDB).

## Roadmap
**Fase 0 — Fondamenta**: repo Python (uv), config, logging, .env; SQLite+sqlite-vec e schema; CLI di orchestrazione (un comando = un ciclo notturno).
**Fase 1 — Ingestione**: GDELT con dedup; RSS multi-blocco; PortWatch; Comtrade (semiconduttori); USGS/FIRMS/IODA; storicizzazione Parquet.
**Fase 2 — Semantica**: NER+geocoding+Wikidata; embeddings e5-small; clustering→eventi con confronto narrazioni; grafo entità filiera pilota.
**Fase 3 — Agent e valutazione**: brief mattutino; generatore di tesi; tabelle e motore paper trading EOD; flusso approvazione (CLI o pagina minimale, rifiuti motivati loggati); portafogli di controllo; predizioni con calibrazione.
**Fase 4 — Interfaccia**: dashboard Streamlit minimale (mappa eventi, confronto narrazioni, portafogli, tesi aperte, storico brief).

**MVP verticale**: task 1-4 + embeddings/clustering + brief mattutino su filiera semiconduttori (pochi attori, chokepoint chiari, geopolitica intensa). Il resto a strati.

## Stato attuale
**Fase 0 e Fase 1 (GDELT) completate.**

Codice presente e funzionante:
- `pathosphere/config.py` — settings da .env (pydantic-settings)
- `pathosphere/logging_setup.py` — loguru, rotazione giornaliera
- `pathosphere/db/schema.py` — DDL completo + sqlite-vec, `init_db`, `get_connection`
- `pathosphere/cli.py` — CLI `pathos` (db, sources, ingest, cycle, config)
- `pathosphere/cycle/orchestrator.py` — ciclo notturno sequenziale riprendibile (5 fasi; 2-5 stub)
- `pathosphere/ingest/gdelt.py` — downloader GDELT 2.0: ciclo incrementale + bootstrap storico

Documentazione:
- `README.md` — setup, comandi, architettura
- `docs/schema.md` — ER diagram Mermaid completo + vincoli
- `useful_queries.sql` — 20 query annotate

Strumenti:
- `caveman` skill installato (7 skill in `.agents/`), sempre attivo via questo file

**Prossimo passo: Fase 1 restante** — RSS multi-blocco, PortWatch, Comtrade semiconduttori, USGS/FIRMS.
Poi Fase 2: NER + geocoding + Wikidata, embeddings e5-small, clustering → eventi.
