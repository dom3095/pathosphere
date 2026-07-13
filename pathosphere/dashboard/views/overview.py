"""Overview page — pipeline health, data freshness, top-line counts."""

from __future__ import annotations

import sqlite3

import streamlit as st

_TABLES = [
    ("raw_documents", "Documenti grezzi"),
    ("events", "Eventi"),
    ("entities", "Entità"),
    ("entity_links", "Link entità"),
    ("narrative_divergences", "Divergenze narrative"),
    ("theses", "Tesi"),
    ("trades", "Trade"),
    ("predictions", "Predizioni"),
    ("briefs", "Brief"),
]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 — fixed whitelist above


def render(conn: sqlite3.Connection) -> None:
    st.header("Overview")

    counts = {table: _count(conn, table) for table, _ in _TABLES}

    cols = st.columns(4)
    for i, (table, label) in enumerate(_TABLES):
        with cols[i % 4]:
            st.metric(label, f"{counts[table]:,}")

    st.divider()

    latest_doc = conn.execute(
        "SELECT MAX(fetched_at) FROM raw_documents"
    ).fetchone()[0]
    latest_event = conn.execute(
        "SELECT MAX(created_at) FROM events"
    ).fetchone()[0]
    latest_brief = conn.execute(
        "SELECT MAX(date) FROM briefs"
    ).fetchone()[0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Ultimo documento ingerito", latest_doc or "—")
    c2.metric("Ultimo evento creato", latest_event or "—")
    c3.metric("Ultimo brief", latest_brief or "—")

    st.divider()
    st.subheader("Fasi")
    phases = [
        ("Fase 0 — Fondamenta", True),
        ("Fase 1 — Ingestione", counts["raw_documents"] > 0),
        ("Fase 2 — Semantica", counts["events"] > 0 and counts["entities"] > 0),
        ("Fase 3 — Agent & valutazione", counts["theses"] > 0 or counts["predictions"] > 0),
        ("Fase 4 — Dashboard", True),
    ]
    for label, done in phases:
        st.write(("✅ " if done else "⬜ ") + label)

    if counts["theses"] == 0 and counts["trades"] == 0 and counts["predictions"] == 0:
        st.info(
            "Nessuna tesi/trade/predizione ancora generata dal ciclo agent "
            "(`pathos brief` → `pathos thesis` → approvazione). Le pagine "
            "Tesi/Portafogli/Predizioni mostreranno dati vuoti finché il "
            "ciclo non gira."
        )
