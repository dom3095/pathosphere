"""Narrative divergences page — confronto narrazioni tra blocchi geopolitici."""

from __future__ import annotations

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st


def render(conn: sqlite3.Connection) -> None:
    st.header("Confronto narrazioni")

    df = pd.read_sql_query(
        """
        SELECT nd.id, nd.block_a, nd.block_b, nd.divergence_score, nd.summary,
               nd.computed_at, e.title AS event_title, e.first_seen
        FROM narrative_divergences nd
        JOIN events e ON e.id = nd.event_id
        ORDER BY nd.divergence_score DESC
        """,
        conn,
    )

    if df.empty:
        st.info("Nessuna divergenza narrativa calcolata ancora.")
        return

    df["pair"] = df["block_a"] + " ↔ " + df["block_b"]

    c1, c2 = st.columns(2)
    c1.metric("Coppie di eventi confrontate", len(df))
    c2.metric("Divergenza media", f"{df['divergence_score'].mean():.2f}")

    agg = (
        df.groupby("pair")["divergence_score"]
        .agg(["mean", "count"])
        .reset_index()
        .sort_values("mean", ascending=False)
    )
    fig = px.bar(
        agg,
        x="pair",
        y="mean",
        hover_data=["count"],
        labels={"pair": "Coppia di blocchi", "mean": "Divergenza media"},
        title="Divergenza media per coppia di blocchi geopolitici",
    )
    st.plotly_chart(fig, width="stretch")

    st.subheader("Top eventi per divergenza")
    top = df.head(25)[["event_title", "pair", "divergence_score", "summary", "first_seen"]]
    top.columns = ["Evento", "Blocchi", "Divergenza", "Sintesi", "Data"]
    st.dataframe(top, width="stretch", hide_index=True)
