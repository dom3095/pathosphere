# Handoff Document — Pathosphere

*Aggiornato: 2026-07-13 ~ 19:30 — CP-022 aperto e validato (notebook), Fase 4 Dashboard implementata*

## CP-022 — Geolocalizzazione eventi RSS (2026-07-13, investigazione + validazione, non implementato)

Usando la dashboard, l'utente ha notato Cuba/Venezuela mostrare solo terremoti USGS sulla mappa, mai
notizie politiche. Causa: nessuno step deriva `location_name` per eventi `origin='rss'` (0/1996).
Dettaglio completo in `CRITICAL_POINTS.md` CP-022.

**Regola richiesta**: relazione bilaterale grandi-potenze → no geoloc; 1 paese → geoloc lì; attore→
bersaglio *via* terzo paese → geoloc sul bersaglio (non attore, non mezzo). Task di ruolo semantico,
non deducibile da conteggio entità.

**Validato in `notebooks/study_19_rss_event_geolocation.ipynb`** (solo lettura, nessuna scrittura DB):
euristica risolve 38% del volume, 59% resta ambiguo; Qwen3 4B locale (Ollama, installato ex-novo
questa sessione via `brew install ollama` + `ollama pull qwen3:4b`) corretto sui 2 casi reali testati
a mano, ma **90-113s/chiamata** sotto la pressione di memoria di questa sessione (8GB M1, IDE+Jupyter+
Ollama insieme) — va ri-misurato a macchina scarica prima di un backfill storico (~1000 eventi
ambigui, va fatto come batch notturno offline, non interattivo).

**Ollama ora installato e attivo sulla macchina** (`ollama serve` avviato manualmente, non via `brew
services` — non persiste al riavvio finché non deciso altrimenti).

**Prossimo passo** (non fatto): `geolocate_rss_events()` in `extract.py` (euristica + fallback batch
Qwen), chiamata da `pathos extract`, poi `geocode_events()` esistente invariato fa il resto.

---

*Sessione precedente — Fase 4 Dashboard Streamlit:*

## Fase 4 — Dashboard Streamlit (2026-07-12, sera, branch `feat/streamlit-dashboard`)

**Cosa**: `pathos serve [--host] [--port]` avvia dashboard Streamlit
(`pathosphere/dashboard/app.py` + `views/*.py`, 8 pagine: Overview, Mappa,
Narrazioni, Grafo entità, Tesi, Portafogli, Predizioni, Brief). Dettaglio
completo in `docs/wiki.md` sezione 8b. Dipendenze aggiunte: `streamlit`,
`plotly`, `folium`, `streamlit-folium`.

**Decisioni chiave**:
- Connessione DB **non cachata** (`st.cache_resource`) — `sqlite3.Connection`
  non è thread-safe e la cache di Streamlit è condivisa tra sessioni/thread;
  si apre una connessione fresca a ogni rerun (costo trascurabile, file locale).
- Navigazione **non multipage nativo** Streamlit (niente cartella `pages/`) —
  un `st.sidebar.radio` in `app.py` seleziona la vista, un solo processo/URL.
- Grafo entità: layout **circolare manuale** (non force-directed) per non
  aggiungere una dipendenza di layout grafi — sufficiente per il sottografo
  indotto top-N hub mostrato (default 30, slider 10-80).
- Pagina Tesi: bottoni Approva/Rifiuta/Apri-trade **replicano esattamente**
  il comportamento CLI (`approve_thesis` + `create_thesis_prediction` su
  approvazione, `open_agent_trade` + `link_thesis_prediction_to_trade` su
  apertura trade) — nessuna logica di business duplicata, solo UI sopra le
  funzioni già testate in `agent/approval.py`, `agent/predictions.py`,
  `market/trading.py`.
- Curva equity portafogli: nessuna tabella di snapshot storici in schema —
  ricostruita come `INITIAL_CASH + cumsum(pnl)` sui trade chiusi in ordine
  cronologico, più un punto finale "live" con `get_portfolio_status()`
  (include unrealized dei trade aperti, no-lookahead-safe: non riscrive mai
  `price_open`).

**Verificato**: `streamlit.testing.v1.AppTest` — caricato `app.py`, simulato
click su tutte le 8 voci sidebar, **nessuna eccezione** contro il DB reale
(8241 eventi, 9142 entità, 75912 link, 749 divergenze — Tesi/Portafogli/
Predizioni/Brief vuote perché Fase 3 non ha ancora prodotto dati reali,
mostrano correttamente stato vuoto invece di errore). `pathos serve --help`
verificato. Ruff pulito su `pathosphere/dashboard/`. 498 test pytest
pre-esistenti ancora tutti verdi (nessuna regressione, nessun test nuovo:
interfaccia pura sopra logica già coperta).

**Non fatto in questa sessione**: nessun dato reale di tesi/trade/predizioni
esiste ancora sul DB — le pagine corrispondenti sono verificate solo in
stato vuoto. Prima esecuzione reale di `pathos brief` → `pathos thesis
generate` → approvazione via dashboard darà la prima verifica end-to-end
con dati veri.

**Prossimo**: aprire PR da `feat/streamlit-dashboard` → review → merge.
Poi, primo giro reale del ciclo agent (brief/thesis/approvazione) per
popolare Tesi/Portafogli/Predizioni e verificare la dashboard con dati veri.

---

## CP-021 fix: story-linking, ordine merge per similarità a parità di gap (2026-07-12, sera)

**Fix**: `link_related_events` in `story.py` ordina le coppie candidate per
`(gap temporale crescente, similarità decrescente)` invece di solo gap crescente. A parità
di gap (comune con un'entità quasi-hub: Trump in 149/2000 eventi → centinaia di coppie a
gap=0), la coppia con similarità più alta viene processata per prima invece di lasciare
l'ordine a un dettaglio implementativo (iterazione di un `set` Python). Nessuna modifica ai
gate di accettazione (finestra temporale, complete-linkage gruppo-vs-gruppo) — solo all'ordine
in cui le coppie vengono provate.

**Verificato sul DB reale**: backup pre-fix
(`pathosphere_backup_20260712_183828_pre_cp021_reorder.db`), reset completo `story_id` +
riesecuzione da zero. Risultato: 125 storie (199 eventi), distribuzione sana (max 8, media
2.6, **nessun mega-blob**). Il caso Iran-deal segnalato ora unisce correttamente 121960+122131
(+2 altri eventi coerenti). Ispezionate a campione altre 2 storie da 6 eventi — coerenti.

**Non completamente risolto**: 122059 (Hormuz) e 122072 (sticking points) restano separati
dal gruppo Iran-deal — plausibilmente sotto-angolazioni sotto soglia 0.82 contro l'intero
gruppo, comportamento conservativo accettabile (non forza angolazioni diverse insieme).

**Test**: 1 nuovo (`test_ties_on_time_gap_prefer_higher_similarity_pair`), 498 totali verdi.

**Status**: CP-018/019/020/021 tutti chiusi. Pipeline entity+clustering+story ora verificata
end-to-end su dati reali. Nessun blocco noto per Fase 4 Dashboard.

---

## CP-021: story-linking, ordine greedy sub-ottimale con entità quasi-hub (2026-07-12, sera)

**Contesto**: ispezionando `study_17` (sezione cluster, aggiunta su richiesta utente che
notava assenza dei cluster di notizie), l'utente ha notato che 4-5 dei top-10 cluster
sono palesemente la stessa storia (trattativa Iran-USA) mai unita da `pathos story`.

**Verifica**: `Trump` compare in 149/~2000 eventi (quasi-hub). Il caso specifico
(eventi 121960+122131, Iran-deal) supera **entrambi** i gate di `story.py` individualmente
(similarità diretta 0.847 > soglia 0.82, span combinato 3gg < finestra 10gg) eppure resta
non unito — causa: ~13700 coppie-candidate totali (chiunque condivida una persona),
processate greedy per gap temporale crescente; un merge sbagliato con gap più piccolo,
elaborato prima, può allargare un gruppo abbastanza da bloccare il merge corretto quando
arriva il suo turno (union-find irreversibile).

**Scala misurata con cautela**: audit isolato (coppia-vs-coppia) trova 683 coppie che
passano entrambi i gate, di cui solo 298 unite nella storia finale — **ma il numero è
sovrastimato**: l'audit isolato replica esattamente il punto cieco (coppia-vs-coppia,
non gruppo-vs-gruppo) che `story.py` è già stato costruito per evitare. Campionando le
"mancate", molte sono correttamente respinte dal vero algoritmo (coppie che condividono
Trump ma sono topicamente slegate). Solo il caso Iran-deal è **confermato** come bug reale.

**Non fatto**: nessun fix — l'utente ha scelto di investigare prima di decidere, e i
risultati (rischio di sovrastimare + rischio di introdurre nuovi problemi con un
riordino affrettato su un algoritmo già a v3 dopo 2 iterazioni fallite) supportano
rimandare a sessione dedicata. Loggato come CP-021, **aperto, non bloccante** (non è
una regressione: 27 storie nuove formate correttamente in questo stesso run).

**Direzioni future possibili**: ordinare merge per similarità decrescente invece che per
gap crescente; oppure passata iterativa (ri-tentare coppie respinte dopo stabilizzazione)
invece di greedy single-pass.

---

## CP-020: due classi sistemiche aggiuntive (2026-07-12, tardo pomeriggio)

**Contesto**: dopo CP-018/CP-019, l'utente ha ricontrollato il grafo e corretto
l'inquadramento — *"non sono segnalazioni puntuali, sono classi di errore"* — vedendo
ancora `EU`/`European`/`Europe` (3 nodi) e `China`/`Chinese` (2 nodi) separati.

**Classe A — asimmetria demonimo↔paese**: `_location_country_key()` riconosceva "Chinese"
→ "China" via `DEMONYM_TO_COUNTRY`, ma **non riconosceva "China" stessa** come appartenente
allo stesso gruppo (il suo `canonical_name` Wikidata era "People's Republic of China", non
"China" — match esatto falliva). Fix: la chiave ora riconosce anche il **nome letterale**
dell'entità (`_KNOWN_PLACE_VALUES_LOWER`), non solo `canonical_name` — si applica a
qualunque paese nei dizionari esistenti, non solo Cina.

**Classe B — aggettivi continentali non coperti**: "European" non era in nessuna tabella
curata → restava `entity_type='other'`, invisibile alla canonicalizzazione. "Europe" (il
continente) aveva un'**altra istanza della stessa collisione Wikidata di CP-019**:
`wbsearchentities("Europe")` matchava "Europe PubMed Central" (database bibliografico), non
il continente. Fix: aggiunte `europe`/`european`, `asia`/`asian`, `africa`/`african` a
`LOCATION_ALIAS_TO_COUNTRY` — copre sia lo skip-Wikidata (CP-019) sia il backfill (esteso
per iterare anche questo dizionario, non solo `DEMONYM_TO_COUNTRY`).

**Verificato sul DB reale**: `China`/`Chinese` uniti; `Europe`/`European` uniti (canonical_name
corretto da "Europe PubMed Central" a "Europe"); `EU` resta `organization` distinta — 3 nodi
confusi → 2 nodi corretti. Bonus: `Asia`/`Asian`, `Africa`/`African` uniti preventivamente.

**Test**: 6 nuovi in `test_extract.py` (497 totali verdi). `pathos graph` rieseguito.

**Nota**: stessa lista curata, non rilevamento generale — probabile che emergano altre
coppie non ancora osservate (Oceania/Antartide non coperte, "American" resta ambiguo
US-vs-continente, non toccato).

---

## CP-018 + CP-019: fix canonicalizzazione entità (2026-07-12, pomeriggio)

**Contesto**: dopo la catena entity→canonicalizzazione→story-linking (sezione sotto),
l'utente ha ispezionato visivamente `study_15_visual_tour.ipynb` e trovato 4 problemi
nel grafo entità (loggati come CP-018, bloccante prima di Fase 4). Sessione ripresa per
risolverli, con richiesta esplicita di **verificare empiricamente sul DB reale**, non
fidarsi solo dei test unitari — e l'utente ha poi avvertito: *"non sono solo punti, sono
segnalazioni/esempi — ci saranno altre incongruenze che non ho visto"*, il che ha guidato
un audit più ampio invece di trattare i 4 punti come lista chiusa.

**I 4 fix di CP-018** (`pathosphere/semantic/extract.py`):
1. **Wikidata QID conflict, tipo-aware**: `link_wikidata` ora chiede P31 a Wikidata
   quando due entità in conflitto QID hanno `entity_type` diverso, e scambia il canonico
   verso quella col tipo corretto (`_wikidata_instance_of_hint` + `WIKIDATA_TYPE_HINTS`),
   invece di "chi arriva primo vince". `repair_wikidata_type_conflicts()` per riparare
   conflitti già mal risolti nel DB esistente (rete, opt-in).
2. **Organization come tipo separato**: `INTERGOVERNMENTAL_ORGS` (EU/NATO/UN/WHO/IMF/
   World Bank/WTO/OPEC/G7/G20/ASEAN/African Union/Arab League/BRICS) → `entity_type=
   'organization'` invece di `company`. `backfill_organization_entities()` per righe esistenti.
3. **Location canonicalization**: nuova `canonicalize_location_entities()` (stesso pattern
   non distruttivo di `canonicalize_person_entities`) — England/British/Britain/UK ora
   un solo canonico "United Kingdom".
4. **Noise stoplist**: `NOISE_ENTITY_STOPLIST` (video/watch/photo/gallery/live/breaking/...)
   escluso a livello di **creazione** entità (non solo skip Wikidata). `purge_noise_entities()`
   per righe legacy.

**CP-019 (bonus, trovato durante la verifica)**: `UK` risultava alias di un'entità
"Ukrainian" con QID Q8798 — che è la **lingua** ucraina (codice ISO 639 `uk`), non il
paese. Collisione fuzzy-search Wikidata su stringa corta ambigua, non ipotetica: trovata
sui dati reali. Fix generale (non solo per UK): `CURATED_ALIAS_TO_LABEL` (demonimi + alias
location + org intergovernative) escluso dalla ricerca Wikidata — stesso meccanismo di
`GENERIC_ENTITY_STOPLIST` ma **preservando** il canonical_name corretto invece di azzerarlo.
Audit successivo su parole-paese ambigue non ancora linkate (Turkey/uccello, Georgia/stato
USA, Jordan/persona, Chad, Guinea, Niger, Congo, Mali, Jersey) — nessuna ancora corrotta,
ma aggiunta verifica P31 **proattiva** (`AMBIGUOUS_ENTITY_NAMES`) prima di accettare un
match, non solo dopo il fatto.

**Verificato empiricamente sul DB reale** (backup pre-fix:
`data/db/pathosphere_backup_20260712_163720_pre_cp018.db`):
- `FRANCE` (company) ora alias corretto di `France` (location, QID Q142)
- `EU`/`NATO` ora `organization` con canonical_name corretto
- `England`/`British`/`Britain` ora alias di `UK`, canonical_name="United Kingdom"
- `Ukrainian` bug riparato + bonus: ora unita anche all'entità paese "Ukraine" (id 1320)
- `VIDEO` (22 mention) eliminata
- `pathos graph` rieseguito: 77516 link scritti (da 83808 pre-canonicalizzazione)

**Comando CLI aggiornato**: `pathos extract [--backfill-orgs] [--repair-wikidata-types]`
(oltre ai flag già esistenti `--backfill-demonyms`). Canonicalizzazione location e purge
rumore girano sempre (locale, no rete, come già la canonicalizzazione persone).

**Test**: 53 nuovi in `test_extract.py` (494 totali verdi), ruff pulito.

**Nota per il futuro**: CP-019 non è garanzia di completezza — 9 nomi ambigui curati,
non un rilevatore generale. L'utente ha segnalato esplicitamente che ci sono probabilmente
altre incongruenze non ancora osservate; i controlli aggiunti oggi vanno letti come
*classi di difesa* generalizzate, non come lista chiusa.

**Status**: CP-018 e CP-019 chiusi. Pipeline entity/graph pronta per Fase 4 Dashboard
(nessun altro blocco noto aperto).

---

## Precedente: Catena entity extraction → canonicalizzazione → story-linking (2026-07-12)

**Contesto**: dopo il fix complete-linkage (11 luglio), l'utente ha chiesto: "è il caso
che ci sia un cap ai cluster?" — questa domanda ha aperto un'indagine a cascata che ha
richiesto sistemare 3 problemi collegati prima di passare a Fase 4 Dashboard, su esplicita
richiesta dell'utente: "prima risolviamo i problemi che abbiamo, non ha senso andare in
dashboard se dati/algoritmi sono di bassa qualità."

**Problema radice trovato**: complete-linkage (fix precedente) frammenta sistematicamente
eventi reali grandi e multi-angolo — 88 documenti sul funerale di Khamenei erano sparsi
su 33 micro-eventi invece di formare un'unica storia forte. Causa: nessuna soglia di
similarità embedding regge su copertura genuina multi-giorno/multi-angolo (breaking news
vs pezzo di colore vs analisi geopolitica).

### 1. Fix HTML boilerplate in embedding + extract (commit `d5dc724`, `510aa1a` parziale)

`extract.py` aveva già bleach.clean per il body (fix CP-015) ma **mai applicato a
`embedder.py`** — stesso bug, due file. Inoltre bleach pulisce i TAG ma non le ENTITY
REFERENCE (`&nbsp;`, `&ldquo;`, `&rdquo;`) — serviva anche `html.unescape()`. Applicato
a entrambi i file. Ri-eseguito su DB reale: 2972 doc RSS re-embedded + re-extracted,
`pathos graph` ricostruito, 2969 entità stale (leak HTML pre-fix) purgate. Risultato:
**0 entità con leak HTML residuo** (era 12%, 1264/10581).

### 2. Canonicalizzazione entity person (commit `510aa1a`)

Trovato: "Khamenei" esisteva come 10+ righe diverse in `entities` (Khamenei/Ali Khamenei/
Ayatollah Khamenei/Ayatollah Ali Khamenei...) — NER estrae ogni variante onorifica come
entità distinta, nessun dedup automatico. `canonicalize_person_entities()` in
`extract.py`: due passate, non distruttive (pointer `canonical_entity_id`, stessa
convenzione già usata per gli alias Wikidata in `graph.py`):
1. Match esatto dopo strip onorifici (lista curata) su nomi multi-token — sicuro
2. Cognomi nudi ambigui ("Khamenei" da solo) uniti solo se un candidato domina
   nettamente (≥3× menzioni) — altrimenti lasciati separati (evita di fondere
   Ali Khamenei col figlio Mojtaba Khamenei, persona diversa)

Agganciato a `pathos extract` (gira sempre, locale, nessun costo di rete). Applicato al
DB reale: 2 gruppi esatti + 215 cognomi nudi uniti, 48 ambigui correttamente lasciati separati.

### 3. Story-linking a due stadi (commit `0237389`, `05e34e4`)

**Schema**: `events.story_id` (self-referenziale, stessa convenzione COALESCE di
`canonical_entity_id`) — non distruttivo, `event_documents` intatti, ogni micro-evento
resta ispezionabile singolarmente.

**Algoritmo** (`pathosphere/semantic/story.py`): unisce micro-eventi che condividono
un'entità PERSONA canonica entro una finestra temporale, **e** superano una soglia di
similarità embedding (0.82) calcolata come **vero complete-linkage gruppo-vs-gruppo**
(non solo la coppia-ponte che ha scatenato il tentativo di merge).

**Due iterazioni per arrivare al fix corretto** (documentate perché istruttive):
- **v1** (solo entità+tempo): un politico onnipresente (Trump, menzionato di striscio
  in decine di articoli slegati nella stessa settimana) ha agito da hub universale →
  mega-storia da **244 eventi** slegati (World Cup, NATO, mercati petroliferi...).
  Stesso identico chain-collapse già visto per gli embedding, spostato di un livello.
- **v2** (+ check embedding solo sulla coppia-ponte, soglia 0.82): mega-blob ridotto ma
  ancora presente a **206 eventi** — perché controllare solo i due eventi-innesco è
  esattamente il punto cieco dell'average-linkage: un evento-ponte può passare il check
  contro DUE gruppi diversi individualmente, pur essendo quei gruppi incoerenti tra loro.
- **v3 (fix vero)**: track dell'intero insieme documenti per gruppo union-find; il gate
  richiede la **similarità minima sull'intero cross-product** documenti-gruppo-A ×
  documenti-gruppo-B, non solo doc-ponte vs doc-ponte — esattamente lo stesso principio
  del fix complete-linkage di `cluster.py`, replicato un livello sopra.

**Fix aggiuntivo tempo**: per la dimensione temporale (1D, ordinata), basta controllare
che lo **span totale** del gruppo risultante (max-min) resti entro finestra — equivale
matematicamente a un vero controllo complete-linkage (in 1D, span limitato ⟺ ogni coppia
è vicina), niente approssimazione necessaria a differenza del caso embedding.

**Risultato finale su DB reale**: max micro-eventi uniti in una storia = **8** (era 206
nel mega-blob v2). Caso Khamenei: 22 micro-eventi frammentati → **12 macro-gruppi**
(due cluster genuini da 6 e 5 eventi) — risultato conservativo e sicuro: angolature
davvero diverse restano separate invece di essere forzate in un blob artificiale.

**Comando CLI**: `pathos story [--time-window-days N]` (default 10).

**Test**: 476 verdi (9 nuovi in `test_story.py`, inclusi test di regressione specifici
per entrambi i bug di chain-collapse trovati — coppia-ponte ed hub temporale).

**Status**: pipeline clustering→embedding→entity→story ora solida end-to-end. Pronto
per Fase 4 Dashboard.

---

## Precedente: Fix HTML boilerplate in embedding (2026-07-11 ~ 15:00)

**Contesto**: study_14 (complete-linkage fix) trovò un residuo — cluster Folha (12 doc,
portoghese) che mescolava temi slegati (G7, Tesla, Taliban, elezioni Peru, ponte Brooklyn)
pur superando la soglia 0.88.

**Root cause**: `raw_documents.body` conteneva HTML grezzo mai ripulito prima dell'embedding,
incluso un footer boilerplate ripetuto (`<a href="...redir.folha.com.br/redir/.../rss091/...">
Leia mais</a> (data)`). Per teaser brevi (366-1047 char), questo blocco condiviso può essere
il 20-40% del testo — domina il segnale embedding più del contenuto reale. `extract.py` aveva
già questo fix (CP-015, bleach prima del NER) ma **mai applicato a `embedder.py`** — stesso
bug, due file diversi.

**Fix** — `pathosphere/semantic/embedder.py::_build_text`: `bleach.clean(body, tags=[], strip=True)`
prima di costruire il testo per l'embedding. Verificato: dopo pulizia, le 12 coppie Folha
scendono da similarità ≥0.88 a max 0.855 — nessuna più supera la soglia.

**Test nuovo**: `test_build_text_strips_html_boilerplate`.

**Applicato al DB reale** (backup preventivo: `data/db/pathosphere_backup_20260711_144947.db`):
- Reset embedded=0 + vec_documents cleared + eventi RSS/Comtrade cleared (2972 RSS doc,
  Comtrade 252 doc esclusi per design, dati strutturati non prosa)
- Ri-eseguito `pathos embed` + `pathos cluster --time-window-hours 2160`
- Risultato: max cluster size 12→8, cluster Folha misto **sparito del tutto**. Tutti i 7
  cluster ≥5 doc rimasti sono genuinamente coerenti (Iran-USA deal multi-dominio, NATO
  Turchia, Argentina-Egitto World Cup; TASS/PressTV monodominio ma coerenti — media di stato
  su storia nazionale, non bug).

**Commit**: `6b90804`. **Test**: 460 verdi.

**Status**: bias fonte/lingua residuo **chiuso**. Clustering pipeline (average-linkage→
complete-linkage→HTML strip) ora solida end-to-end.

---

## Precedente: Complete-linkage fix (2026-07-11 ~ 14:30)

**Domanda utente**: "è il caso che ci sia un cap ai cluster? se ci sono 120 articoli su Hormuz come vengono trattati?"

**Test empirico (study_13)**: cap NON frammenta eventi genuini — è rete di sicurezza necessaria.
Ogni valore di cap testato (30→uncapped) satura il proprio tetto: senza cap un cluster arriva a
**1370 doc** (>50% del corpus), fondendo 25 storie diverse (Hormuz, Iran-USA, Israele-Libano, FIFA,
G7, finanza giapponese) via centroid drift.

**Root cause trovato**: il fix precedente (average-linkage, sessione 2026-07-10) controllava solo
il doc-ponte contro il centroide di ogni cluster target, singolarmente. Un doc D coerente sia con
A sia con B salda A e B interamente, senza mai verificare che i membri di A siano coerenti con
quelli di B — stesso pattern di transitività del bug originale single-linkage, un livello più
in profondità.

**Fix** — `pathosphere/semantic/cluster.py`:
- Pre-filtro economico (centroide, soglia 0.75) — scarta candidati lontani, O(1)
- Gate vero: **complete-linkage cluster-vs-cluster** — prima di fondere due cluster, verifica
  distanza massima tra ogni coppia di membri A×B, non solo doc-ponte vs centroide — O(|A|×|B|)
- Cap 30 resta come rete di sicurezza aggiuntiva (zero costo osservato)

**Test regressione**: `test_cluster_rejects_bridging_doc_welding_unrelated_clusters` — embedding
costruiti a mano (cos(D,A)=cos(D,B)=0.90, cos(A,B)=0.62), verifica che vecchio bug avrebbe fuso
A+B tramite D, nuovo fix li mantiene separati.

**Verifica empirica (study_14)**:
- Cap ha **zero effetto** ora (12/20/30/100/uncapped → risultato identico, 1977 eventi, max 12 doc)
- Singleton 88.8%→78.0% (il gate severo impedisce merge sbagliati che rubavano doc a cluster corretti)
- 9/10 top cluster genuinamente coerenti (Khamenei funeral, Argentina-Egitto World Cup, Putin-Trump,
  NATO Ankara, piogge Mumbai)
- 1 residuo: cluster Folha (12 doc, portoghese) mix temi — bias fonte/lingua, scala molto minore
  (12 vs 1370 del bug originale)
- Same-domain ≠ automaticamente bug: TASS/PressTV monodominio ma coerenti (media di stato)

**Commit**: `779363d`. **Test**: 459 verdi.

**Status**: clustering **strutturalmente solido**. Nessun tuning ulteriore cap necessario.

---

## Precedente: Fix GDELT titles in clustering (2026-07-10 ~ 20:05)

**Problem discovered**: Grandi cluster (69+ docs) avevano titoli **grezzi GDELT** come `||11|20251021|US` (event ID numerici, non testo umano).

**Root cause**: `cluster_documents()` includeva doc con `origin='gdelt'` che non hanno titoli leggibili.

**Fix**: Filter `(r.origin IS NULL OR r.origin != 'gdelt')` in clustering query:
- Esclude GDELT (che ha già gdelt_events nel DB)
- Preserva test doc (origin=NULL) per unit test
- RSS events adesso hanno titoli puliti da headline vere

**Verification**:
- Top 5 cluster titles: "Why the economics make this the craziest World Cup ever", "India summons US diplomat…", "No final agreement on deal with US – Iran", etc.
- Zero titoli GDELT grezzi
- 458 test passed

**Commit**: `b4588a5` — "fix(clustering): exclude GDELT docs from RSS event clustering"

---

## Clustering fix: chain-collapse prevented via average-linkage (2026-07-10 ~ 19:30)

**Criticità audit** (study_09_criticality_audit.ipynb eseguito):
1. Study_08 mai eseguito (execution_count: null su tutte le celle) — numeri in HANDOFF precedente non verificabili da notebook
2. **Clustering RSS rotto**: 79% singleton + 26 eventi al cap 30 doc (chain-collapse single-linkage)
3. Event_type: 90%+ codici CAMEO (disapprove/fight/coerce), 0% target vocab (conflict/political/...)
4. Wikidata: <1% entità con QID, 99%+ mai controllate (rate-limited)
5. Entity_type rumoroso: città classificate come company (WASHINGTON, FRANCE, NASA, BERLIN)

**Fix clustering** — pathosphere/semantic/cluster.py refactorizzato:
- Algoritmo: single-linkage → average-linkage con centroide coherence check
- KNN threshold: 0.85 (neighbors, unchanged)
- Coherence threshold: 0.88 (new, more stringent per centroid check)
- Implementazione: load embeddings in memoria, track centroids dinamicamente
- Test su 2564 RSS doc (720h window): 1258 eventi, 1117 singleton (88.8%), 0 chain-collapse
- Cluster grandi (20-30 doc) verificati **genuinamente coerenti** (World Cup cluster tutte su WC 2026, non mescolato)

**Commit**: `d14aeb4` — "fix(clustering): prevent single-linkage chain-collapse via average-linkage coherence"

**Interpretazione**: I 88% singleton sono **reali**, non artefatto — dataset RSS è intrinsecamente disperso (molti topic singolari, pochi argomenti forti con molti doc). Cluster grandi rimangono topicamente omogenei per costruzione.

**Status**: Clustering **solido per produzione**. Prossima fase: Fase 4 Dashboard.

---

## Re-ingest GDELT da zero, pipeline pulita (2026-07-10 — completato)

**GDELT history**: ✅ COMPLETO
- 8760 file scaricati, 223.077 events, 334.301 docs ingestati
- CP-016 (gdelt_anomaly): anomalie Goldstein promosse direttamente a events, NER/graph bypassato
- CP-015: HTML strippato da body prima di NER (dipendenza `bleach`)
- Canonicalizzazione: Wikidata QID, demonimi (Israeli/Russian/Chinese→location)

**Stato pipeline semantica** (2026-07-10 ~12:30 UTC):
- 🔄 `uv run pathos embed` — in progress, ~20 min (monitorare: `tail -f data/logs/embed_post_reingest.log`)
- 🔄 `uv run pathos extract` — in progress, ~1 ora (monitorare: `tail -f data/logs/extract_post_reingest.log`)
- ⬜ `uv run pathos cluster` — da eseguire dopo extract (~5 min)
- ⬜ `uv run pathos graph` — da eseguire dopo cluster (~10 min)

**Notebook study_08**: ✅ CREATO E COMMITATO
- Replica metodologia study_04-07 su GDELT pulito da zero
- Metriche: hairball (componente gigante), entità generiche ALL CAPS, Wikidata linkage, demonimi
- Pronto per esecuzione: `jupyter notebook notebooks/study_08_gdelt_post_reingest.ipynb`

**Completato** (2026-07-10 ~ 01:57 UTC):
1. ✅ GDELT history: 8760 file, 223.077 events, 334.301 docs
2. ✅ Embed: RSS 2.972 docs (Comtrade+GDELT per design escusi da NLP)
3. ✅ Extract: NER 0 (RSS pre-processato), geocoding 731 events, Wikidata 7 QID (rate-limited)
4. ✅ Cluster: 0 events creati (metadati cluster_id aggiunti a events esistenti)
5. ✅ Graph: 94.678 entity_links processati

**Notebook study_08**: ✅ ESEGUITO — metriche finali

### Risultati verifica post-re-ingest

| Metrica | Valore | Baseline (study_07) | Diff |
|---------|--------|-------------------|------|
| Nodo GDELT | ❌ NON ESISTE | grado 3.962 | ✓ rimosso |
| Gigante (nodi) | 8.744/9.359 (93.4%) | 12.664/13.278 (95.4%) | ↓ 2.0pp |
| Entity links | 94.678 | 123.047 | ↓ 27% |
| Entities totali | 10.581 | 13.278 | ↓ 20% |
| Wikidata QID | 29/10.581 (0.3%) | n/a | rate-limited |
| Demonimi | 9 trovati | n/a | ✓ reclassificati |

**Interpretazione**:
- ✅ **CP-016 fix funziona**: GDELT anomaly bypass NER/graph. Nessun nuovo nodo GDELT introdotto.
- ✅ **Hairball ridotto** 2pp vs pre-reset (95.4% → 93.4%). Link totali -27%.
- ❌ **Wikidata linkage bassissimo** (0.3%) — rate-limited a "Beijing", retry prossimo ciclo con delay ↑
- ✅ **Demonimi canonicalizzati** (Israeli/Russian/Chinese→location)

**Cosa non osserviamo**:
- Nodo GDELT pre-fix storico: non pulito (CI-016 previene NUOVI, non rimuove vecchi)
- Link storici gdelt_linked: restano intatti (nessun cleanup retroattivo)
→ Per dataset storico pulito serve `gdelt-reset --yes` esplicito (fatto in sessione 2026-07-09)

**Study 08b** ✅ — Event aggregation coherent (20/20 top eventi ≥3 docs, 231K eventi totali)

**Extract Wikidata Retry** ✅ — Delay ↑ (1.0 → 2.0 sec)
- 15 QID linkati (2x baseline 7)
- 20 entities checked (2x baseline 10)
- Rate-limited dopo "America"
- Progresso significativo nonostante rate limiting

**Stato finale**:
- ✓ GDELT re-ingest: 8.760 file, 223K eventi, 334K docs
- ✓ Pipeline semantica: embed/extract/cluster/graph completi
- ✓ Study 08 + 08b: hairball ↓2pp, event coherence OK
- ✓ Wikidata: 15 QID (2x improvement after retry)
- ✓ Branch `feat/study-08-gdelt-verification`: 2 commits, 458 test verdi

**Prossimi step**:
1. Merge PR `feat/study-08-gdelt-verification` → main
2. Fase 4 Dashboard Streamlit 

---

## Reset GDELT + backfill demonimi su DB reale (2026-07-09)

**Contesto**: sessione precedente (branch `refactor/gdelt-numeric-split`, ora eliminato) aveva diagnosticato CP-016 (documenti sintetici GDELT trattati come prosa dalla pipeline NLP) e scritto il fix. In parallelo, un'altra sessione ha ramificato da quel branch aggiungendo canonicalizzazione entità via Wikidata QID (nuova colonna `entities.canonical_entity_id`) + fix CP-015 (strip HTML dal body prima del NER, dipendenza `bleach`) + una propria implementazione di reset GDELT in `cli.py`, mergiando tutto su `main` con squash (PR #8, #9, #10). Il branch originale è risultato ridondante (contenuto già in `main`) ed è stato eliminato.

**Azioni eseguite in questa sessione su `main`**:
1. `pathos ingest gdelt-reset --yes` sul DB reale (`data/db/pathosphere.db`, 494MB) — cancellati 177.281 `raw_documents` origin=gdelt, 234.502 `gdelt_events`, 118.166 `events` origin=gdelt, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità rimaste orfane (usate solo da doc gdelt), 27.734 `entity_links` coinvolti, 4.836 righe `gdelt_file_log` (per permettere ri-scaricamento pulito). RSS/Comtrade/PortWatch/USGS/FIRMS/IODA verificati intatti. Operazione confermata con l'utente via preview prima dell'esecuzione (comando supporta dry-run di default, `--yes` per eseguire davvero).
2. `pathos extract --backfill-demonyms --limit 0 --skip-geocode --skip-wikidata` — 49 entità (Israeli, Russian, Chinese, American, Ukrainian…) riclassificate da `entity_type='other'` a `location` con `canonical_name` = nome paese.
3. Costruito un artifact visivo (HTML standalone, canvas force-directed graph + card cluster + mappa) usando dati catturati PRIMA del reset — quindi rappresenta lo stato GDELT-contaminato "as-is", utile come confronto storico. Include un esempio onesto di topic-drift nel clustering (evento 122013 "Armenia's top court…" i cui documenti sono in realtà quasi tutti su Netanyahu/Israele — sintomo di chain-collapse, non ancora fixato).

**Stato DB reale dopo questa sessione**: `origin=gdelt` completamente vuoto (0 righe in tutte le tabelle derivate). Prossimo passo per chi riprende: rilanciare `pathos ingest gdelt-history --start <data>` per ripopolare da zero con la pipeline già pulita (CP-016+CP-015+canonicalizzazione tutti attivi dal primo giorno, niente contaminazione da smaltire questa volta), poi `pathos ingest gdelt-anomalies --backfill-country --full`.

**Punti di attenzione per la prossima sessione**:
- Se lavori in parallelo con un'altra sessione Claude sullo stesso repo, **verifica sempre `git log main` e `git branch -a` prima di assumere che il tuo branch sia l'unica fonte di verità** — in questa sessione un reset/backfill è stato lanciato mentre in background un `git checkout` cambiava branch, e serviva ricostruire la sequenza da `git reflog` per capire cosa fosse successo. Nessun danno (i processi in esecuzione non sono affetti da checkout successivi, il DB è file-based non branch-based), ma ha richiesto un giro di verifica non banale.
- File innocuo da ignorare se lo vedi in `git status`: `pathosphere.db` (0 byte, root del repo) — scarto di un comando lanciato dalla cwd sbagliata in una sessione precedente, non il DB vero (`data/db/pathosphere.db`).

Dettagli completi CP-016/CP-015/canonicalizzazione: vedi commit `3566dbc` e PR #8/#9/#10 su GitHub, più `CRITICAL_POINTS.md`.

---

## Fix Wikidata linking (2026-07-07)

Run `pathos extract` produceva 40 errori 429 su 50 lookups Wikidata (10 QIDs). Due cause, fixate in `pathosphere/semantic/extract.py` (`link_wikidata`):

1. **Sleep saltato su errore**: `continue` su exception bypassava `time.sleep(delay_s)` → dopo primo 429 richieste a raffica senza pausa (~8 req/s), 429 auto-amplificato. Ora delay a inizio iterazione, rispettato sempre. `WIKIDATA_DELAY_S` 0.2→1.0 (limite anonimo Wikimedia ~1 req/s).
2. **Budget bruciato su entità spazzatura**: top-mentioned erano nomi generici ALL CAPS (`CRIMINAL`, `MILITARY`, `MALE`…) → link inutili o sbagliati (`MALE`→Malé). Nuova `GENERIC_ENTITY_STOPLIST` (~110 nomi comuni/ruoli/demonimi, match case-insensitive): marcati `wikidata_checked=1` senza lookup a inizio run, contati in `WikidataResult.stoplisted`. La stessa UPDATE azzera QID sbagliati assegnati pre-fix (es. `PRESIDENT`→Q30461 trovato nel DB reale).

In più: su 429 il run si interrompe subito (`WikidataResult.rate_limited=True`), entità restanti restano `wikidata_checked=0` → ritentate ciclo successivo. Errori non-429 continuano come prima. Output CLI e orchestrator mostrano stoplisted + flag rate limited. +4 test (stoplist, strip QID legacy, abort su 429, errore non-429 continua). 423 test verdi.

Smoke test reale (subagent, DB di produzione): 146 generici ritirati, 3 lookups a ~1 req/s, ISRAEL→Q801, US→Q30, `rate_limited=False`.

Run `pathos extract` completo post-fix: 9 QIDs validi (PAKISTAN→Q843, UKRAINE→Q212, RUSSIA→Q159…), poi 429 dopo 10 lookups anche a 1 req/s → abort pulito (1 warning vs 40 pre-fix), 40 entità rimandate. Probabile penalità residua IP dal run storm mattutino; se 429 persiste a IP pulito nei cicli successivi, alzare `WIKIDATA_DELAY_S` o onorare `Retry-After`. `SCHOOL`→Q3914 sfuggito → aggiunto a stoplist (QID verrà azzerato automaticamente al prossimo run dallo strip legacy).

## Fix IODA (2026-07-06)

`pathos ingest ioda --start 2026-01-01` crashava con `JSONDecodeError`. Tre cause, tutte fixate in `pathosphere/ingest/ioda.py`:

1. **Base URL sbagliato**: `ioda.inetintel.cc.gatech.edu/api/v2` è frontend SPA → HTML con 200. Corretto: `https://api.ioda.inetintel.cc.gatech.edu/v2`
2. **Limite API <100 giorni** per query singola → chunking automatico 90gg (`IODA_MAX_CHUNK_DAYS`), delay 1s tra chunk
3. **Shape reale annidata** `{"data": [[{...}]]}` → flatten un livello (vecchie shape restano supportate)

In più: risposta non-JSON ora → `RuntimeError` pulito in `IODAResult.errors` invece di crash. +3 test (chunking, shape annidata, non-JSON). Smoke test reale: IR 2026-01-01→07-05, 185 metriche, 3 chunk, 0 errori, 5 eventi outage.

## Stato al momento del handoff

**Branch:** fix/wikidata-linking (da pushare + PR)
**Test:** 423 verdi (22 in test_extract.py)
**Docs:** complete e allineate (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md)

---

## Cosa è stato fatto in questa sessione

### Predictions v2 — implementazione completa

**Schema** (`pathosphere/db/schema.py`, migration idempotenti in `_MIGRATIONS`):
- 10 colonne nuove su `predictions`: `macro_area` (NOT NULL DEFAULT 'world'), `prediction_type` (NOT NULL DEFAULT 'geopolitical'), `outcome_eventual`, `outcome_on_time`, `resolved_date`, `time_adjusted_score`, `origin_scope`, `impact_scope`, `time_horizon_class`, `trade_id`
- Backfill legacy: `outcome_on_time = outcome` E `outcome_eventual = outcome` (guardie IS NULL, idempotenti)
- Tabelle nuove: `prediction_domains(prediction_id, domain, is_primary)`, `prediction_revisions(id, prediction_id, probability, rationale, revised_at)`
- `theses.prediction_id` FK opzionale (catena predizione world → tesi)

**Config:** `timing_penalty_alpha: float = 0.001`

**`pathosphere/agent/predictions.py`** (riscritto):
- Costanti esportate: `VALID_MACRO_AREAS`, `VALID_PREDICTION_TYPES`, `TYPES_BY_MACRO_AREA`, `VALID_DOMAINS` (10), `VALID_SCOPES` (5)
- `add_prediction(...)` — valida coerenza macro_area/type, world richiede scope+domini, economic richiede thesis_id; inserisce prediction_domains; time_horizon_class derivato (breve ≤30gg, medio ≤180gg, lungo; UTC)
- `revise_prediction(id, probability, rationale)` — logga in prediction_revisions
- `resolve_prediction(id, outcome_eventual, resolved_date, alpha=None)` — brier su outcome_eventual; outcome_on_time derivato; legacy `outcome` specchia on_time; time_adjusted_score = 0 se mai accaduto, altrimenti (1−brier)×max(0, 1−alpha×|delta gg|)
- `get_calibration()` — dual metric, bucket con accuracy su outcome_eventual (fallback legacy), per-bucket mean_time_adjusted_score, breakdown by_macro_area/by_prediction_type
- `create_thesis_prediction(conn, thesis)` — auto-predizione economic per tesi approvata; clampa confidence a [0,1], default p=0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade(conn, thesis_id, trade_id)` — aggancia SOLO la più vecchia predizione economic aperta e non collegata

**CLI** (`pathosphere/cli.py`):
- `predict add` — flag v2 completi, click.Choice da costanti (inclusi --domain)
- `predict revise <id> --probability --rationale` — NUOVO
- `predict resolve <id> --outcome-eventual true|false --resolved-date YYYY-MM-DD`
- `predict list` — filtri --macro-area/--prediction-type/--domain; colonna Out con fallback legacy
- `predict calibration` — dual metric + breakdown per area e tipo
- `thesis approve` — auto-crea predizione economic (protetta: fallimento non maschera approvazione)
- `trade open` — aggancia predizione via domain function
- Gestione `sqlite3.IntegrityError` su FK inesistenti

### Review (8 angoli multi-agente) — 10 finding, 9 fixati

Fix principali: calibration accuracy usava `outcome` mentre brier usava `outcome_eventual` (metriche contraddittorie); backfill mancante di outcome_eventual (righe legacy mostravano '—'); auto-create non protetta dopo commit approvazione; UPDATE unbounded in trade open; business logic spostata da CLI a domain layer; timezone UTC coerente; alpha parametrico.

Non fixato (documentato): CP-010 — migration girano solo con `pathos db init`.

### Nuovi punti critici
- **CP-007**: headroom (compressione token) — opzione futura se credito Claude stretto
- **CP-008**: ruff F821 `sqlite3` undefined in 9 punti moduli ingest (pre-esistente, branch dedicato)
- **CP-009**: cambio timing_penalty_alpha invalida comparabilità score storici
- **CP-010**: dopo pull con modifiche schema serve `uv run pathos db init`

---

## Stato esatto al cut-off

- Codice + test: **COMPLETI**, 419 verdi
- Docs (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md): agent haiku in aggiornamento
- LOOP_STATE.md, CRITICAL_POINTS.md: aggiornati
- **Nessun commit ancora fatto** sul branch

---

## Prossima azione raccomandata

**Fase 4 — Dashboard Streamlit**

Scope:
- Mappa mondiale eventi (folium)
- Confronto narrazioni per blocco geopolitico
- Curva equity tre portafogli (agent/random/benchmark)
- Tesi aperte (status pending/approved/rejected)
- Storico brief mattutini
- Grafico calibrazione Tetlock (bucket vs accuracy)

CLI: `pathos serve` → `localhost:8501`

Dipende da Fase 3 (predictions v2) completa. DB popolo via:
```
uv run pathos cycle run           # ciclo notturno completo
uv run pathos brief              # brief mattutino
uv run pathos thesis generate    # tesi
uv run pathos thesis approve <id> # auto-crea economic prediction
```

---

## Setup automazione (launchd)

```bash
# Una volta sola: installa daemon che lancia loop ogni 12h
./scripts/setup_launchd.sh
# Opzioni:
#   --interval SECONDS    (default 43200 = 12h)
#   --uninstall           (disattiva e rimuovi)

# Monitor il daemon
tail -f data/logs/launchd.log
launchctl list | grep pathosphere

# Disattiva
./scripts/setup_launchd.sh --uninstall
```

## Comandi utili

```bash
# Stato / DB
uv run pytest tests/ -q                    # 498 verdi
uv run pathos db init                      # OBBLIGATORIO dopo pull con modifiche schema
uv run pathos db info                      # Row counts per tabella

# Loop autonomo manuale (CP-017) — corre il ciclo notturno forever con stato persistente
# Interruzione sicura: Ctrl+C salva state + esci
caffeinate -i uv run pathos loop --sleep-hours 1.0 --max-retries 3
# Monitor:
tail -f data/logs/*.log
tail -f data/cycle_state.json  # Stato ultimo ciclo + error log (ultimi 100)

# Ciclo una volta (per debug/test)
uv run pathos cycle
uv run pathos cycle --from-phase embed       # Resume da EMBED
uv run pathos cycle --dry-run                # Simula solo

# Fasi singole (tutte standalone ora)
uv run pathos ingest gdelt --max-goldstein 5
uv run pathos ingest gdelt-anomalies --backfill-country --full
uv run pathos ingest rss
uv run pathos ingest portwatch
uv run pathos embed
uv run pathos cluster
uv run pathos extract
uv run pathos graph
uv run pathos brief
# etc.

# Predictions v2
uv run pathos predict add "Desc" --macro-area world --prediction-type geopolitical \
  --probability 0.65 --horizon 2026-08-10 --domain conflitto_armato \
  --origin-scope regionale --impact-scope globale
uv run pathos predict revise <id> --probability 0.7 --rationale "..."
uv run pathos predict resolve <id> --outcome-eventual true --resolved-date 2026-08-05
uv run pathos predict list --open --macro-area world --domain commercio
uv run pathos predict calibration

# Thesis / trading (v2: approve auto-crea predizione economic, trade open la aggancia)
uv run pathos thesis list
uv run pathos thesis approve <id>
uv run pathos thesis reject <id> --reason "..."
uv run pathos trade open <thesis_id>
uv run pathos portfolio status
```
