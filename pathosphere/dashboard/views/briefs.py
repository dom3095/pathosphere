"""Briefs page — morning brief history."""

from __future__ import annotations

import sqlite3

import streamlit as st


def render(conn: sqlite3.Connection) -> None:
    st.header("Brief mattutini")

    rows = conn.execute(
        "SELECT id, date, event_count, entity_count, generated_at FROM briefs ORDER BY date DESC"
    ).fetchall()

    if not rows:
        st.info("Nessun brief ancora generato. Eseguire `pathos brief`.")
        return

    dates = [r["date"] for r in rows]
    selected = st.selectbox("Data", dates)

    row = conn.execute(
        "SELECT content, event_count, entity_count, generated_at FROM briefs WHERE date = ?",
        (selected,),
    ).fetchone()

    st.caption(f"{row['event_count']} eventi · {row['entity_count']} entità · generato {row['generated_at']}")
    st.markdown(row["content"])

    with st.expander("Storico brief"):
        for r in rows:
            st.write(f"- {r['date']} — {r['event_count']} eventi, {r['entity_count']} entità")
