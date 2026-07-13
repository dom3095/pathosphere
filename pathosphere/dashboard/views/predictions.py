"""Predictions page — Tetlock-style calibration + open/resolved lists."""

from __future__ import annotations

import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pathosphere.agent.predictions import get_calibration


def render(conn: sqlite3.Connection) -> None:
    st.header("Predizioni")

    open_df = pd.read_sql_query(
        """
        SELECT id, description, probability, horizon_date, macro_area, prediction_type, created_at
        FROM predictions WHERE resolved = 0 ORDER BY horizon_date
        """,
        conn,
    )
    resolved_df = pd.read_sql_query(
        """
        SELECT id, description, probability, outcome_eventual, outcome_on_time,
               brier_score, time_adjusted_score, resolved_date, macro_area, prediction_type
        FROM predictions WHERE resolved = 1 ORDER BY resolved_date DESC
        """,
        conn,
    )

    c1, c2 = st.columns(2)
    c1.metric("Predizioni aperte", len(open_df))
    c2.metric("Predizioni risolte", len(resolved_df))

    if open_df.empty and resolved_df.empty:
        st.info("Nessuna predizione ancora generata (`pathos thesis generate` → approvazione).")
        return

    calib = get_calibration(conn)
    if calib["overall"]["count"] > 0:
        st.subheader("Calibrazione (Tetlock)")
        m1, m2 = st.columns(2)
        mb = calib["overall"]["mean_brier_score"]
        mt = calib["overall"]["mean_time_adjusted_score"]
        m1.metric("Brier score medio", f"{mb:.3f}" if mb is not None else "—")
        m2.metric("Time-adjusted score medio", f"{mt:.3f}" if mt is not None else "—")

        buckets = pd.DataFrame(calib["buckets"])
        buckets = buckets[buckets["count"] > 0]
        if not buckets.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=buckets["label"], y=buckets["accuracy"], name="Accuratezza osservata"))
            fig.add_trace(
                go.Scatter(
                    x=buckets["label"],
                    y=[(lo + hi) / 2 for lo, hi in zip(buckets["min"], buckets["max"])],
                    mode="lines+markers", name="Probabilità dichiarata (atteso)",
                    line=dict(dash="dash", color="red"),
                )
            )
            fig.update_layout(
                title="Curva di calibrazione — accuratezza osservata vs probabilità dichiarata",
                yaxis_title="Frazione risolta vera", height=400,
            )
            st.plotly_chart(fig, width="stretch")

    if not open_df.empty:
        st.subheader("Aperte")
        st.dataframe(open_df, width="stretch", hide_index=True)

    if not resolved_df.empty:
        st.subheader("Risolte")
        st.dataframe(resolved_df, width="stretch", hide_index=True)
