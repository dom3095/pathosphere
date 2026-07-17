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

# Workflow di sviluppo — REGOLE OBBLIGATORIE

## 1. Ciclo di modifica codice

Ogni modifica segue questo ordine senza eccezioni:

```
1. Fix/implementazione
2. Test (uv run pytest — tutti verdi prima di procedere)
3. Refactor se necessario (ruff check, nessun codice morto)
4. Aggiorna documentazione: wiki.md, roadmap.md, schema.md se cambia DB
5. Aggiorna LOOP_STATE.md (task completati, prossima azione)
6. Aggiorna HANDOFF.md (stato esatto, punti critici, comandi utili)
```

Mai committare con test rossi. Mai aggiornare solo il codice senza aggiornare la doc.

## 2. Branch policy — REGOLA ASSOLUTA

**Mai committare direttamente su `main`.** Sempre:

```
git checkout -b feat/<nome>   # o fix/<nome>, refactor/<nome>, chore/<nome>
# ... lavoro ...
git push -u origin feat/<nome>
gh pr create ...              # PR → review → merge
```

Il branch viene creato PRIMA di toccare qualsiasi file. Se ci si trova su `main` con modifiche, stashare e creare il branch prima di procedere.

## 3. Pull Request — Conventional Commits

Il messaggio PR usa **Conventional Commits**:

```
<type>(<scope>): <descrizione breve>

[body opzionale: cosa cambia e perché]

[footer: breaking changes, issue refs]
```

Tipi: `feat` · `fix` · `refactor` · `test` · `docs` · `chore` · `perf`  
Scope: nome modulo o fase (es. `predictions`, `trading`, `schema`, `cli`, `3f`)

Esempi:
```
feat(predictions): add time-adjusted scoring with timing penalty
fix(trading): correct slippage calculation on close
refactor(schema): extract prediction_domains to junction table
```

Il titolo PR = primo commit significativo. Body = lista bullet delle modifiche principali.

## 4. Punti critici aperti

Quando emerge un punto critico (bug noto, decisione tecnica rimandato, rischio potenziale), annotarlo **immediatamente** in `CRITICAL_POINTS.md`. Formato:

```
- **Nome problema**: descrizione. Workaround: ... Impatto: ...
```

## 5. Fine sessione — obbligatorio

Prima di chiudere ogni sessione di sviluppo:

1. `LOOP_STATE.md` — aggiornare: fase corrente, subtask completati, ultima azione, prossima azione, note tecniche rilevanti
2. `HANDOFF.md` — aggiornare: cosa è stato fatto, stato esatto al cut-off, prossima azione raccomandata, punti critici aperti, comandi utili aggiornati
3. Chiedere all'utente un breve riassunto verbale per confermare allineamento

Il collega che riprende il lavoro (o Claude nella sessione successiva) deve poter partire da HANDOFF.md senza rileggere tutta la conversazione.

---

# OSINT Portal — Intelligence personale su eventi critici globali

## Obiettivo
Sistema personale (mono-utente, nessuna vendita/condivisione) che raccoglie dati aperti e gratuiti su eventi critici mondiali — conflitti, epidemie, infrastrutture, rotte commerciali, flussi commerciali per categoria (es. semiconduttori, petrolio), politica interna — ne estrae la semantica, costruisce scenari e produce **anticipazioni di mercato valutate tramite paper trading** (soldi virtuali). Non è un sistema di trading: il portafoglio virtuale è la metrica di valutazione del modello.

## Principi non negoziabili
1. **Budget quasi zero**: solo dati gratuiti. LLM con strategia IBRIDA: locale per il lavoro di massa, Claude (coperto dal credito mensile dell'abbonamento, vedi sezione LLM) solo per i 2-3 task di ragionamento al giorno.
2. **Human-in-the-loop, con auto-open a soglia** (rivisto 2026-07-14): l'agent PROPONE tesi; per confidence ≥ `settings.auto_open_confidence_threshold` (default 0.6) il **paper trade si apre in autonomia** (soldi virtuali, rischio zero) — l'umano rivede/rifinisce/chiude *dopo*, non approva *prima*. Sotto soglia, resta il flusso originale: pending → `pathos thesis approve`/`reject` manuale (con motivazione loggata su rifiuto). Disattivabile per singolo run (`--no-auto-open`). Vale solo per il paper trading (soldi finti) — non è mai autonomia su denaro reale, che il progetto non tocca.
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
**Fasi 0, 1, 2, 3, 4 completate** (Fase 4 Dashboard Streamlit su branch `feat/streamlit-dashboard`, non ancora mergiata — vedi HANDOFF).

Vedi `LOOP_STATE.md` per stato esatto e `HANDOFF.md` per dettaglio sessione corrente.

Strumenti:
- `caveman` skill installato (7 skill in `.agents/`), sempre attivo via questo file
