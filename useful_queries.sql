-- ============================================================
-- PATHOSPHERE — Query utili
-- ============================================================
-- Aprire con:
--   sqlite3 data/db/pathosphere.db
--   .mode column
--   .headers on
-- Oppure con DB Browser for SQLite / DBeaver.
-- ============================================================


-- ─────────────────────────────────────────────────────────────
-- 1. PANORAMICA DATABASE
--    Prima cosa da eseguire per capire lo stato generale.
--    Mostra quante righe ci sono in ogni tabella principale.
-- ─────────────────────────────────────────────────────────────
SELECT 'sources'              AS tabella, COUNT(*) AS righe FROM sources
UNION ALL
SELECT 'raw_documents',                   COUNT(*) FROM raw_documents
UNION ALL
SELECT 'events',                          COUNT(*) FROM events
UNION ALL
SELECT 'event_documents',                 COUNT(*) FROM event_documents
UNION ALL
SELECT 'entities',                        COUNT(*) FROM entities
UNION ALL
SELECT 'entity_links',                    COUNT(*) FROM entity_links
UNION ALL
SELECT 'narrative_divergences',           COUNT(*) FROM narrative_divergences
UNION ALL
SELECT 'watchlist_items',                 COUNT(*) FROM watchlist_items
UNION ALL
SELECT 'theses',                          COUNT(*) FROM theses
UNION ALL
SELECT 'trades',                          COUNT(*) FROM trades
UNION ALL
SELECT 'portfolios',                      COUNT(*) FROM portfolios
UNION ALL
SELECT 'predictions',                     COUNT(*) FROM predictions
UNION ALL
SELECT 'gdelt_file_log',                  COUNT(*) FROM gdelt_file_log;


-- ─────────────────────────────────────────────────────────────
-- 2. STATISTICHE INGESTIONE GDELT
--    Verifica quanto materiale è stato scaricato, quanti file
--    sono stati processati vs errori, e il tasso di filtraggio
--    (rows_raw → rows_stored). Un tasso basso indica filtri
--    aggressivi; un tasso alto suggerisce di alzare min_mentions.
-- ─────────────────────────────────────────────────────────────
SELECT
    status,
    COUNT(*)                                          AS file_count,
    SUM(rows_raw)                                     AS totale_righe_raw,
    SUM(rows_stored)                                  AS totale_righe_inserite,
    ROUND(100.0 * SUM(rows_stored) / NULLIF(SUM(rows_raw), 0), 1) AS pct_conservato
FROM gdelt_file_log
GROUP BY status
ORDER BY file_count DESC;


-- ─────────────────────────────────────────────────────────────
-- 3. INGESTIONE GDELT PER GIORNO
--    Utile per identificare giorni con dati mancanti o anomalie
--    nel download. Un giorno dovrebbe avere ~24 file con
--    sample_hours=1, o fino a ~96 con sample_hours=0.
-- ─────────────────────────────────────────────────────────────
SELECT
    substr(filename, 1, 8)  AS giorno,         -- YYYYMMDD
    COUNT(*)                AS file_scaricati,
    SUM(rows_stored)        AS eventi_inseriti,
    MIN(rows_stored)        AS min_per_file,
    MAX(rows_stored)        AS max_per_file
FROM gdelt_file_log
WHERE status = 'ok'
GROUP BY 1
ORDER BY 1 DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────
-- 4. ULTIMI EVENTI (più recenti per last_seen)
--    Prima esplorazione dopo un ciclo di ingestione.
--    Mostra title, tipo, severity e posizione.
-- ─────────────────────────────────────────────────────────────
SELECT
    id,
    substr(last_seen, 1, 10)   AS data,
    event_type,
    severity,
    location_name,
    substr(title, 1, 80)       AS titolo
FROM events
ORDER BY last_seen DESC
LIMIT 20;


-- ─────────────────────────────────────────────────────────────
-- 5. EVENTI AD ALTA SEVERITÀ
--    Identifica rapidamente gli eventi più critici nel periodo.
--    severity 4-5 = conflitti materiali o crisi gravi.
-- ─────────────────────────────────────────────────────────────
SELECT
    id,
    substr(first_seen, 1, 10)  AS primo_avvistamento,
    event_type,
    severity,
    location_name,
    title
FROM events
WHERE severity >= 4
ORDER BY severity DESC, last_seen DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────
-- 6. DISTRIBUZIONE EVENTI PER TIPO E GIORNO
--    Utile per vedere se l'ingestione produce distribuzione
--    attesa di tipi di evento nel tempo. Un spike improvviso
--    su conflict può indicare un evento reale rilevante.
-- ─────────────────────────────────────────────────────────────
SELECT
    substr(first_seen, 1, 10)  AS giorno,
    event_type,
    COUNT(*)                   AS n_eventi,
    ROUND(AVG(severity), 2)    AS severity_media
FROM events
GROUP BY 1, 2
ORDER BY 1 DESC, n_eventi DESC;


-- ─────────────────────────────────────────────────────────────
-- 7. DOCUMENTI PER BLOCCO GEOPOLITICO
--    Verifica la copertura per blocco geopolitico.
--    Se un blocco ha 0 documenti, manca quella fonte RSS.
--    Obiettivo: copertura plurale su tutti i 7+ blocchi.
-- ─────────────────────────────────────────────────────────────
SELECT
    s.geopolitical_block,
    COUNT(DISTINCT s.id)       AS n_fonti_attive,
    COUNT(rd.id)               AS n_documenti,
    MIN(substr(rd.published_at, 1, 10)) AS primo_doc,
    MAX(substr(rd.published_at, 1, 10)) AS ultimo_doc
FROM sources s
LEFT JOIN raw_documents rd ON rd.source_id = s.id
WHERE s.active = 1
GROUP BY s.geopolitical_block
ORDER BY n_documenti DESC;


-- ─────────────────────────────────────────────────────────────
-- 8. DOCUMENTI DA EMBEDDARE (coda Fase 2)
--    Quanti documenti non hanno ancora l'embedding calcolato.
--    Questo numero deve azzerarsi dopo ogni ciclo di embedding.
--    Se cresce troppo, la Fase 2 è in ritardo rispetto all'ingest.
-- ─────────────────────────────────────────────────────────────
SELECT
    COUNT(*)                                          AS da_embeddare,
    MIN(substr(fetched_at, 1, 10))                    AS piu_vecchio,
    MAX(substr(fetched_at, 1, 10))                    AS piu_recente
FROM raw_documents
WHERE embedded = 0;


-- ─────────────────────────────────────────────────────────────
-- 9. DIVERGENZA NARRATIVA PER EVENTO
--    Mostra gli eventi con la maggiore divergenza tra blocchi.
--    Un divergence_score alto (→1) segnala eventi dove le
--    narrazioni delle diverse potenze divergono significativamente.
--    Questi sono i segnali più preziosi per l'analisi geopolitica.
-- ─────────────────────────────────────────────────────────────
SELECT
    e.title                    AS evento,
    nd.block_a,
    nd.block_b,
    ROUND(nd.divergence_score, 3) AS divergenza,
    nd.summary
FROM narrative_divergences nd
JOIN events e ON e.id = nd.event_id
ORDER BY nd.divergence_score DESC
LIMIT 20;


-- ─────────────────────────────────────────────────────────────
-- 10. MAPPA ENTITÀ — Grafo relazioni
--     Mostra le relazioni tra entità (paesi, aziende, commodity).
--     Filtro su relation_type per vedere solo supply chain,
--     o solo sanzioni, o solo alleanze.
--     Utile per rispondere a "se chiude Hormuz, chi soffre?"
-- ─────────────────────────────────────────────────────────────
SELECT
    a.canonical_name           AS entita_a,
    a.entity_type              AS tipo_a,
    el.relation_type           AS relazione,
    ROUND(el.strength, 2)      AS forza,
    b.canonical_name           AS entita_b,
    b.entity_type              AS tipo_b
FROM entity_links el
JOIN entities a ON a.id = el.entity_a
JOIN entities b ON b.id = el.entity_b
-- WHERE el.relation_type IN ('supplies', 'depends_on')  -- filtra per tipo
ORDER BY el.strength DESC NULLS LAST
LIMIT 50;


-- ─────────────────────────────────────────────────────────────
-- 11. TESI PENDENTI (in attesa di approvazione)
--     Lista delle tesi generate dall'agent non ancora revisionate.
--     Queste devono essere approvate o rifiutate manualmente.
--     Human-in-the-loop: nessuna operazione autonoma.
-- ─────────────────────────────────────────────────────────────
SELECT
    t.id,
    substr(t.created_at, 1, 10)  AS creata,
    t.title,
    t.instrument,
    t.direction,
    t.horizon_days               AS giorni,
    ROUND(t.confidence, 2)       AS confidenza,
    t.status
FROM theses t
WHERE t.status = 'pending'
ORDER BY t.created_at DESC;


-- ─────────────────────────────────────────────────────────────
-- 12. P&L PORTAFOGLI (performance comparativa)
--     Il confronto agent vs random vs benchmark è la metrica
--     principale del sistema. Se l'agent non batte il random,
--     il modello non ha valore predittivo.
-- ─────────────────────────────────────────────────────────────
SELECT
    p.name                                            AS portafoglio,
    COUNT(tr.id)                                      AS n_trade,
    COUNT(tr.closed_at)                               AS trade_chiusi,
    ROUND(SUM(CASE WHEN tr.pnl IS NOT NULL THEN tr.pnl ELSE 0 END), 2) AS pnl_totale,
    ROUND(AVG(CASE WHEN tr.pnl IS NOT NULL THEN tr.pnl END), 2)        AS pnl_medio,
    ROUND(100.0 * SUM(CASE WHEN tr.pnl > 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(CASE WHEN tr.pnl IS NOT NULL THEN 1 END), 0), 1) AS pct_win
FROM portfolios p
LEFT JOIN trades tr ON tr.portfolio_id = p.id
GROUP BY p.id, p.name
ORDER BY pnl_totale DESC NULLS LAST;


-- ─────────────────────────────────────────────────────────────
-- 13. CALIBRAZIONE TETLOCK (predizioni non finanziarie)
--     Misura quanto l'agent è calibrato nelle sue predizioni
--     geopolitiche. Brier score medio ideale < 0.25.
--     Score = 0: perfetto. Score = 1: peggio del caso.
--     Segmentare per bucket di probabilità svela overconfidence.
-- ─────────────────────────────────────────────────────────────
SELECT
    CASE
        WHEN probability < 0.2  THEN '0-20%'
        WHEN probability < 0.4  THEN '20-40%'
        WHEN probability < 0.6  THEN '40-60%'
        WHEN probability < 0.8  THEN '60-80%'
        ELSE                         '80-100%'
    END                             AS bucket_probabilita,
    COUNT(*)                        AS n_predizioni,
    SUM(resolved)                   AS risolte,
    ROUND(AVG(CASE WHEN resolved=1 THEN outcome END), 3)       AS tasso_realizzazione,
    ROUND(AVG(CASE WHEN resolved=1 THEN brier_score END), 4)   AS brier_score_medio
FROM predictions
GROUP BY 1
ORDER BY 1;


-- ─────────────────────────────────────────────────────────────
-- 14. WATCHLIST ATTIVA (indicatori osservabili)
--     Lista indicatori monitorati per verificare/invalidare
--     scenari aperti (Analysis of Competing Hypotheses).
--     Gli item 'triggered' sono segnali che una tesi si sta
--     concretizzando o falsificando.
-- ─────────────────────────────────────────────────────────────
SELECT
    id,
    status,
    label,
    description,
    triggered_at,
    indicator_query
FROM watchlist_items
WHERE status != 'expired'
ORDER BY
    CASE status WHEN 'triggered' THEN 0 WHEN 'active' THEN 1 END,
    created_at DESC;


-- ─────────────────────────────────────────────────────────────
-- 15. DETAIL TRADE CON TESI
--     Vista join trades + tesi per analisi post-mortem.
--     Utile per capire quali catene causali hanno prodotto
--     profit e quali no — input per migliorare il modello.
-- ─────────────────────────────────────────────────────────────
SELECT
    p.name                          AS portafoglio,
    tr.ticker,
    tr.direction,
    tr.quantity,
    tr.price_open,
    tr.price_close,
    ROUND(tr.pnl, 2)                AS pnl,
    substr(tr.opened_at, 1, 10)     AS aperto,
    substr(tr.closed_at, 1, 10)     AS chiuso,
    substr(th.title, 1, 60)         AS tesi
FROM trades tr
JOIN portfolios p  ON p.id  = tr.portfolio_id
LEFT JOIN theses th ON th.id = tr.thesis_id
ORDER BY tr.opened_at DESC
LIMIT 50;


-- ─────────────────────────────────────────────────────────────
-- 16. DOCUMENTI RECENTI CON FONTE
--     Ispezione rapida dei documenti più freschi, con blocco
--     geopolitico e controllo statale della fonte.
--     Utile per verificare che l'ingestione RSS funzioni.
-- ─────────────────────────────────────────────────────────────
SELECT
    substr(rd.fetched_at, 1, 16)   AS scaricato,
    s.geopolitical_block           AS blocco,
    s.state_control                AS ctrl_statale,
    s.name                         AS fonte,
    substr(rd.title, 1, 80)        AS titolo
FROM raw_documents rd
JOIN sources s ON s.id = rd.source_id
ORDER BY rd.fetched_at DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────
-- 17. FONTI CONFIGURATE (catalogo)
--     Lista completa delle fonti attive con metadati geopolitici.
--     state_control: 0=indipendente, 1=parziale, 2=forte, 3=totale.
-- ─────────────────────────────────────────────────────────────
SELECT
    geopolitical_block,
    country,
    name,
    orientation,
    state_control,
    language,
    active
FROM sources
ORDER BY geopolitical_block, state_control DESC, name;


-- ─────────────────────────────────────────────────────────────
-- 18. EFFICIENZA FILTRO GDELT (rows_raw vs rows_stored)
--     Monitora il tasso di conservazione nel tempo per
--     rilevare cambiamenti nell'output di GDELT o nel filtro.
--     Un calo improvviso di pct_conservato può indicare
--     un bug nel filtro o un cambiamento nel formato GDELT.
-- ─────────────────────────────────────────────────────────────
SELECT
    substr(filename, 1, 8)          AS giorno,
    SUM(rows_raw)                   AS raw,
    SUM(rows_stored)                AS stored,
    ROUND(100.0 * SUM(rows_stored) / NULLIF(SUM(rows_raw), 0), 2) AS pct_conservato
FROM gdelt_file_log
WHERE status = 'ok'
GROUP BY 1
ORDER BY 1 DESC
LIMIT 14;


-- ─────────────────────────────────────────────────────────────
-- 19. EVENTI ATTIVI (non ancora risolti)
--     Gli eventi in corso da monitorare. Un evento "risolto"
--     ha resolved_at NOT NULL. Questa query mostra la finestra
--     di crisi aperta corrente.
-- ─────────────────────────────────────────────────────────────
SELECT
    id,
    substr(first_seen, 1, 10)   AS inizio,
    event_type,
    severity,
    location_name,
    substr(title, 1, 80)        AS titolo
FROM events
WHERE resolved_at IS NULL
ORDER BY severity DESC, first_seen DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────
-- 20. ENTITÀ PIÙ CITATE NEL GRAFO
--     Le entità con più relazioni sono i nodi centrali del
--     grafo geopolitico-economico. Utile per identificare
--     gli attori sistemicamente rilevanti (hub).
-- ─────────────────────────────────────────────────────────────
SELECT
    e.canonical_name,
    e.entity_type,
    COUNT(el.id)    AS n_relazioni,
    e.wikidata_qid
FROM entities e
JOIN entity_links el ON (el.entity_a = e.id OR el.entity_b = e.id)
GROUP BY e.id
ORDER BY n_relazioni DESC
LIMIT 20;
