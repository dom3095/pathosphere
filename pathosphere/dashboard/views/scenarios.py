"""Scenari page — conflict scenario sets with probability distributions."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import streamlit as st


def render(conn: sqlite3.Connection) -> None:
    st.header("Scenari di conflitto")

    try:
        sets_df = pd.read_sql_query(
            """
            SELECT id, country, country_name, status, created_date,
                   horizon_date, last_reviewed_at, summary
            FROM scenario_sets ORDER BY id DESC
            """,
            conn,
        )
    except pd.errors.DatabaseError:
        st.info("Tabelle scenari non ancora migrate (`pathos db init`).")
        return

    if sets_df.empty:
        st.info("Nessun set di scenari (`pathos scenario generate`).")
        return

    active = sets_df[sets_df["status"] == "active"]
    c1, c2 = st.columns(2)
    c1.metric("Set attivi", len(active))
    c2.metric("Set risolti", len(sets_df) - len(active))

    for _, s in sets_df.iterrows():
        title = (
            f"Set {s['id']} — {s['country_name'] or s['country']} "
            f"[{s['status']}] · orizzonte {s['horizon_date']}"
        )
        with st.expander(title, expanded=s["status"] == "active"):
            if s["summary"]:
                st.markdown(f"*{s['summary']}*")

            scen_df = pd.read_sql_query(
                """
                SELECT label, title, probability, invalidation,
                       market_implications, is_outcome, prediction_id
                FROM scenarios WHERE set_id = ? ORDER BY label
                """,
                conn,
                params=(int(s["id"]),),
            )
            if scen_df.empty:
                st.caption("Nessuno scenario nel set.")
                continue

            chart_df = scen_df.set_index("label")[["probability"]]
            st.bar_chart(chart_df, horizontal=True)
            st.dataframe(
                scen_df[["label", "title", "probability", "invalidation",
                         "market_implications", "is_outcome"]],
                use_container_width=True,
                hide_index=True,
            )

            items = pd.read_sql_query(
                """
                SELECT s.label AS scenario, w.label, w.indicator_query, w.status, w.triggered_at
                FROM watchlist_items w JOIN scenarios s ON s.id = w.scenario_id
                WHERE s.set_id = ? ORDER BY s.label
                """,
                conn,
                params=(int(s["id"]),),
            )
            if not items.empty:
                st.caption("Indicatori (watchlist vivente)")
                st.dataframe(items, use_container_width=True, hide_index=True)

            row = conn.execute(
                "SELECT dossier_json FROM scenario_sets WHERE id = ?", (int(s["id"]),)
            ).fetchone()
            dossier = json.loads(row["dossier_json"] or "{}") if row else {}
            evidence = dossier.get("evidence", [])
            if evidence:
                with st.popover("Dossier di evidenze (congelato alla generazione)"):
                    for e in evidence:
                        st.markdown(f"- **{e['id']}** ({e['source']}) {e['text']}")
