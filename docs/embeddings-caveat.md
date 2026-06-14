# Caveat embeddings — perché i vettori GDELT non rappresentano gli articoli

> **Stato al 2026-06-14: il problema PERSISTE** per i documenti GDELT.
> Verificato su codice e dati reali. Leggere prima di far girare embed/dedup/
> clustering sull'intero corpus. Contesto sui dati: [data-semantics.md](data-semantics.md).

## Cosa fa l'embedder

`embed_documents` seleziona `title, body` da `raw_documents WHERE embedded = 0`
([embedder.py:73](../pathosphere/semantic/embedder.py#L73)) e costruisce il testo
da embeddare concatenando **titolo + body**
([`_build_text`, embedder.py:42-47](../pathosphere/semantic/embedder.py#L42-L47)).
Se entrambi sono vuoti il documento è saltato; altrimenti si embedda ciò che c'è.

## Il problema

Per i documenti **GDELT** (oggi ~3,2 M, la quasi totalità del corpus):

- `body` è **vuoto** (vedi [data-semantics.md](data-semantics.md));
- `title` è la stringa **sintetica** `GDELT: {Actor1Name} → {Actor2Name} [{EventCode}]`.

Quindi l'embedder, su un documento GDELT, embedda **solo la stringa sintetica del
titolo** — es. `GDELT:  → QUEENSLAND [171]`. Il vettore risultante rappresenta
**quella stringa di metadati** (frammenti di nomi-attore + codice evento), **non
il contenuto dell'articolo**, che non è mai stato scaricato.

### Conseguenze a valle

- **Dedup semantica** e **clustering in eventi** (Fase 2), girati su questi
  vettori, raggruppano i documenti per **similarità della stringa nome-attore**,
  non per significato reale dell'evento. Il risultato è semanticamente poco
  affidabile.
- La firma di prossimità tra due documenti GDELT diversi dipende da quanto si
  somigliano i loro `Actor*Name`/`EventCode`, non dai fatti.

## Evidenza che persiste

Al 2026-06-14 risultano **3872 documenti già embeddati** (`vec_documents`), prodotti
prima dell'ingestione RSS → quasi certamente documenti GDELT, cioè vettori di
titoli sintetici. Con ~3,2 M documenti GDELT a `embedded = 0`, proseguire
l'embedding **as-is** propaga il problema su tutto il corpus.

## Dove il problema NON c'è

- **RSS**: `title` reale + `body` reale → embeddings semanticamente validi.
- **Comtrade**: `title`/`body` sono sintetici ma **descrittivi del record**
  (reporter, merce, flusso, valore); l'embedding è coerente col contenuto del
  documento (un flusso commerciale), quindi accettabile per quel dominio.

## Raccomandazioni (decisione aperta, non ancora presa)

Una di queste, da concordare col proprietario del progetto:

1. **Escludere GDELT dall'embedding** finché i documenti non sono arricchiti con
   testo reale. Far girare embed/dedup/clustering solo su RSS (e Comtrade per il
   suo dominio).
2. **Arricchire GDELT prima di embeddare**, tramite GKG (lo stesso timestamp dei
   file `export` già in `gdelt_file_log` ha un file `gkg`): il GKG fornisce
   `PAGE_TITLE` (titolo reale), temi ed entità pre-codificate — superficie
   semantica sufficiente per un embedding sensato senza scraping.

In ogni caso: **non assumere che i vettori attualmente in `vec_documents` siano
significativi**. Vanno considerati sospetti e probabilmente ricalcolati dopo aver
risolto la sorgente del testo.
