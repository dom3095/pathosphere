# Pathosphere — Riepilogo del progetto

*Documento di presentazione · Giugno 2026*

---

## Cos'è Pathosphere?

Pathosphere è un **sistema personale di intelligence geopolitica** che:

1. raccoglie automaticamente notizie e dati da fonti aperte (gratuite) in tutto il mondo
2. li elabora con intelligenza artificiale per estrarne pattern e significato
3. propone **tesi di investimento** basate su eventi geopolitici reali
4. valuta la qualità di queste tesi con un **portafoglio di carta** (soldi virtuali)

In parole semplici: è come avere un analista geopolitico automatico che legge ogni notte decine di fonti da Cina, Russia, Arabia Saudita, India, USA, Africa… capisce cosa sta succedendo nel mondo, e poi dice "secondo me, questo evento impatterà su questo strumento finanziario — vuoi aprire una posizione virtuale?". L'utente approva o rifiuta, e nel tempo si misura quanto l'analisi è davvero predittiva.

---

## Perché esiste?

Tre vincoli progettuali molto precisi:

| Vincolo | Scelta |
|---|---|
| Budget quasi zero | Solo dati gratuiti (GDELT, RSS pubblici, yfinance…) |
| MacBook Air M1 (8 GB RAM) | Un solo modello AI in RAM alla volta, nessun Docker |
| Human-in-the-loop | L'AI propone, l'utente decide — nessuna operazione autonoma |

Il **portafoglio virtuale** non è lo scopo finale: è la **metrica di valutazione**. Se l'AI batte un portafoglio casuale con le stesse dimensioni di trade, significa che l'analisi geopolitica contiene davvero del segnale utile.

---

## Architettura ad alto livello

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CICLO NOTTURNO                              │
│                                                                     │
│  FONTI DATI          ELABORAZIONE           STORAGE                 │
│  ──────────          ────────────           ───────                 │
│                                                                     │
│  GDELT ──────────►  dedup semantica ──────► SQLite (DB locale)     │
│  RSS 30+ feed ───►  NER + geocoding ──────► + sqlite-vec           │
│  PortWatch (IMF) ►  embedding e5-small ───►   (vettori)            │
│  Comtrade UN ────►  clustering eventi ────► Parquet                │
│  USGS + FIRMS ───►  grafo entità ─────────►   (archivio storico)   │
│  IODA (blackout) ►                                                  │
│  yfinance ───────►                                                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         CICLO MATTUTINO                             │
│                                                                     │
│  DB ──► AI locale (Qwen 4B) ──► AI cloud (Claude) ──► BRIEF        │
│                                                         │           │
│                 Claude ◄── grafo + divergenze ◄─────────┘           │
│                    │                                                │
│                    ▼                                                │
│               TESI DI TRADING  ◄── (opzionale: debate 6 personas)  │
│                    │                                                │
│                    ▼                                                │
│           👤 APPROVAZIONE UMANA                                     │
│            approve / reject (con motivazione)                       │
│                    │                                                │
│                    ▼                                                │
│          PORTFOLIO VIRTUALE (paper trading)                         │
│          + PREDIZIONI GEOPOLITICHE (calibrazione Tetlock)           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Fonti dati integrate

```
CONFLITTI / POLITICA          FISICA / INFRASTRUTTURE
────────────────────          ───────────────────────
GDELT (spina dorsale)         USGS — terremoti
ACLED, UCDP                   NASA FIRMS — incendi
ReliefWeb                     IODA — blackout internet

NARRAZIONI MULTI-BLOCCO       COMMERCIO / ECONOMIA
───────────────────────       ────────────────────
Xinhua / Global Times (🇨🇳)   UN Comtrade (semiconduttori)
TASS (🇷🇺)                    IMF PortWatch (Suez, Hormuz…)
Al Jazeera (🌍)               yfinance (prezzi EOD)
Press TV (🇮🇷)                FRED
The Hindu (🇮🇳)
Folha de São Paulo (🇧🇷)
+ testate occidentali
```

La divergenza narrativa **tra blocchi geopolitici** è essa stessa un segnale: se Xinhua e Reuters descrivono lo stesso evento in modo opposto, c'è qualcosa di interessante.

---

## Pipeline semantica (come i dati diventano intelligenza)

```
Migliaia di articoli grezzi
         │
         ▼
   [ DEDUP VETTORIALE ]
   KNN cosine ≥ 0.92, finestra 72h
   → elimina duplicati dello stesso evento
         │
         ▼
   [ NER + GEOCODING ]
   spaCy → entità (paesi, aziende, commodity)
   Nominatim → coordinate geografiche
   Wikidata → ID univoco ("TSMC" = "台積電" = Q319984)
         │
         ▼
   [ CLUSTERING → EVENTI ]
   union-find cosine ≥ 0.85
   → raggruppa articoli nello stesso evento geopolitico
         │
         ▼
   [ GRAFO ENTITÀ ]
   paese ──── azienda ──── commodity ──── infrastruttura
        co-occorrenza + divergenza narrativa
   → "se Hormuz chiude, chi soffre?" è una query sul grafo
         │
         ▼
   ~30-50 eventi al giorno → LLM (non migliaia di articoli grezzi)
```

---

## Strategia AI: ibrida e a budget zero

Il sistema usa due AI in modo complementare per tenere i costi vicino a zero:

```
┌─────────────────────┐         ┌─────────────────────┐
│   QWEN 3 — 4B q4    │         │   CLAUDE (Anthropic) │
│   (locale, gratis)  │         │   (cloud, credito    │
│                     │         │    abbonamento)      │
│  lavoro di massa:   │         │                      │
│  • classificazione  │         │  ragionamento alto:  │
│  • estrazione NER   │         │  • brief mattutino   │
│  • 6 personas nel   │         │  • sintesi debate    │
│    debate           │         │  • generazione tesi  │
│                     │         │  • catene causali    │
│  ~0 €/giorno       │         │  2-3 call/giorno     │
└─────────────────────┘         └─────────────────────┘
         │                               │
         └───────────────┬───────────────┘
                         ▼
              stessa API OpenAI-compatible
              (cambiare backend = 1 riga config)
```

---

## Il "debate" delle 6 personas

Quando si genera una tesi importante, il sistema simula un dibattito tra analisti di 6 prospettive geopolitiche diverse:

```
                    EVENTO GEOPOLITICO
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   📍 Beijing         📍 Washington     📍 Mosca
   (Qwen ricerca)    (Qwen ricerca)   (Qwen ricerca)
         │                 │                 │
   📍 Riyadh          📍 Gerusalemme    📍 Parigi
   (Qwen ricerca)    (Qwen ricerca)   (Qwen ricerca)
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
                   [ DIVERGENZA ] (Qwen)
                   cosa non concordano?
                           │
                   [ CRITICA ] (Qwen × 6)
                   ogni persona critica le altre
                           │
                           ▼
                   [ SINTESI ] (Claude)
                   tesi finale con catena causale
                   + condizione di invalidazione
```

---

## Tesi di trading: struttura

Ogni tesi proposta dal sistema contiene:

```yaml
evento_scatenante:
  descrizione: "Attacchi Houthi alle petroliere nel Mar Rosso"
  fonti: ["Al Jazeera", "Lloyd's List", "TASS"]

catena_causale:
  - "Rotte deviate via Capo di Buona Speranza (+10 giorni)"
  - "Costi assicurativi Suez ×3 in 2 settimane"
  - "Domanda tanker spot in aumento"
  - "Beneficiari: armatori con flotte fuori dalla zona"

strumento: "ZIM"        # ticker
direzione: "LONG"
orizzonte: "6 settimane"

condizione_invalidazione: "Cessate il fuoco o corridoio sicuro IMO"

prezzo_entrata: 14.23   # registrato al momento della decisione
                        # (mai retroattivo = no lookahead bias)
```

L'utente approva o rifiuta (con motivazione loggata). Se approva, viene aperta una posizione nel portafoglio virtuale.

---

## Portafogli di controllo

Il sistema mantiene **tre portafogli in parallelo** per misurare la qualità dell'analisi:

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   PORTAFOGLIO   │  │   PORTAFOGLIO   │  │   PORTAFOGLIO   │
│     AGENT       │  │     RANDOM      │  │   BENCHMARK     │
│                 │  │                 │  │                 │
│ Tesi approvate  │  │ Stesse size,    │  │ Buy & hold      │
│ dall'analisi    │  │ ticker casuali  │  │ SPY (S&P 500)   │
│ geopolitica     │  │                 │  │                 │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                   │                     │
         └───────────────────┼─────────────────────┘
                             ▼
                    Se Agent > Random:
                    l'analisi geopolitica
                    contiene segnale reale.
                    Se Agent ≤ Random:
                    lo si sa subito.
```

---

## Predizioni geopolitiche (calibrazione Tetlock)

Oltre alle tesi finanziarie, il sistema registra predizioni geopolitiche pure:

```
"Escalation militare Cina-Taiwan entro 3 mesi: 35%"
         │
         │ (si aspetta la scadenza)
         ▼
   outcome: vero / falso
         │
         ▼
   Brier Score = (probabilità - outcome)²
   → 0 = perfetto, 1 = pessimo

   Calibrazione per bucket:
   [0-20%]  → quante volte si avvera?  (dovrebbe: ~10%)
   [20-40%] → quante volte si avvera?  (dovrebbe: ~30%)
   [40-60%] → quante volte si avvera?  (dovrebbe: ~50%)
   [60-80%] → quante volte si avvera?  (dovrebbe: ~70%)
   [80-100%]→ quante volte si avvera?  (dovrebbe: ~90%)
```

Questo è il metodo di [Philip Tetlock](https://en.wikipedia.org/wiki/Philip_E._Tetlock) per misurare quanto un analista è "calibrato" — non solo se indovina, ma se le sue probabilità riflettono la realtà.

---

## Stato di avanzamento

### Roadmap completa

```
Fase 0 ████████████ 100% — Fondamenta (repo, config, DB, CLI)
Fase 1 ████████████ 100% — Ingestione dati (GDELT, RSS, PortWatch, Comtrade, USGS, IODA)
Fase 2 ████████████ 100% — Semantica (NER, embeddings, clustering, grafo entità)
Fase 3 ████████████ 100% — Agent e valutazione (LLM, brief, tesi, debate, trading, predizioni)
Fase 4 ░░░░░░░░░░░░   0% — Dashboard Streamlit (in arrivo)
```

### Dettaglio Fase 3 (appena completata)

| Task | Cosa fa | Stato |
|---|---|---|
| 3a — LLM client | Astrazione Claude/Qwen, stessa API | ✅ |
| 3b — Brief mattutino | Riassunto giornaliero da eventi + anomalie | ✅ |
| 3c — Generatore tesi | Fast path (Claude) + debate 6 personas | ✅ |
| 3d — Flusso approvazione | `pathos thesis list/show/approve/reject` | ✅ |
| 3e — Paper trading EOD | Portfolio agent/random/benchmark, open/close trade | ✅ |
| 3f — Predizioni Tetlock | add/resolve/calibration Brier score | ✅ |

**375 test automatici — tutti verdi.**

---

## Come si usa (comandi principali)

```bash
# Ciclo notturno
uv run pathos cycle run

# Brief mattutino
uv run pathos brief

# Tesi (con o senza debate tra personas)
uv run pathos thesis generate
uv run pathos thesis debate

# Approvazione
uv run pathos thesis list
uv run pathos thesis show <id>
uv run pathos thesis approve <id>
uv run pathos thesis reject <id> --reason "Ticker sbagliato"

# Portfolio virtuale
uv run pathos portfolio init
uv run pathos portfolio status
uv run pathos trade open <thesis_id>
uv run pathos trade close <trade_id>

# Predizioni geopolitiche (v2)
uv run pathos predict add "Escalation Taiwan entro luglio" \
  --macro-area world --prediction-type geopolitical \
  --probability 0.35 --horizon 2026-07-31 \
  --domain conflitto_armato --origin-scope regionale --impact-scope globale
uv run pathos predict revise <id> --probability 0.4 --rationale "New intel from GDELT"
uv run pathos predict resolve <id> --outcome-eventual true --resolved-date 2026-07-15
uv run pathos predict calibration
```

---

## Cosa manca: Fase 4 — Dashboard

L'unica cosa ancora da costruire è un'interfaccia grafica (Streamlit) con:

- **Mappa mondiale** degli eventi geopolitici (folium)
- **Confronto narrazioni** per blocco geopolitico
- **Curva equity** dei tre portafogli nel tempo
- **Tesi aperte** con stato di approvazione
- **Storico brief** mattutini
- **Grafico calibrazione Tetlock** (bucket vs accuracy)

---

## In sintesi

```
Pathosphere = "Bloomberg dell'intelligence open-source,
               con AI multilingua e portafoglio virtuale
               come metrica di qualità"

Budget: quasi €0/mese
Hardware: MacBook Air M1 consumer
Dati: 100% open/gratuiti
Controllo umano: obbligatorio su ogni decisione
Scopo: capire se l'analisi geopolitica LLM è predittiva
```

---

*Documento generato il 27 giugno 2026*
