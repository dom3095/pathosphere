"""Entity graph page — hub entities and induced co-occurrence subgraph.

Layout is a simple circular placement (nodes ordered by entity_type then
degree) rather than a force-directed layout — no extra graph-layout
dependency needed, and legible enough for the top-N hub subgraph shown here.
"""

from __future__ import annotations

import math
import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_TYPE_COLOR = {
    "country": "#1f77b4",
    "company": "#ff7f0e",
    "commodity": "#2ca02c",
    "infrastructure": "#9467bd",
    "person": "#d62728",
    "location": "#17becf",
    "organization": "#bcbd22",
    "other": "#7f7f7f",
}


def _hub_degrees(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT entity_id, SUM(deg) AS degree FROM (
            SELECT entity_a AS entity_id, COUNT(*) AS deg FROM entity_links GROUP BY entity_a
            UNION ALL
            SELECT entity_b AS entity_id, COUNT(*) AS deg FROM entity_links GROUP BY entity_b
        ) t
        GROUP BY entity_id
        ORDER BY degree DESC
        """,
        conn,
    )


def render(conn: sqlite3.Connection) -> None:
    st.header("Grafo entità")

    degrees = _hub_degrees(conn)
    if degrees.empty:
        st.info("Nessun link entità ancora calcolato (`pathos graph`).")
        return

    top_n = st.slider("Numero di entità hub mostrate", min_value=10, max_value=80, value=30, step=5)
    top_ids = degrees.head(top_n)["entity_id"].tolist()

    placeholders = ",".join("?" * len(top_ids))
    ent_df = pd.read_sql_query(
        f"SELECT id, canonical_name, name, entity_type, wikidata_qid FROM entities WHERE id IN ({placeholders})",  # noqa: S608
        conn,
        params=top_ids,
    ).set_index("id")
    ent_df["display"] = ent_df["canonical_name"].fillna(ent_df["name"])
    degrees = degrees.set_index("entity_id").loc[top_ids]

    st.subheader("Top entità per grado")
    table = ent_df.join(degrees)[["display", "entity_type", "degree", "wikidata_qid"]]
    table.columns = ["Entità", "Tipo", "Grado", "Wikidata QID"]
    st.dataframe(table.sort_values("Grado", ascending=False), width="stretch", hide_index=True)

    edges = pd.read_sql_query(
        f"""
        SELECT entity_a, entity_b, relation_type, strength
        FROM entity_links
        WHERE entity_a IN ({placeholders}) AND entity_b IN ({placeholders})
        """,  # noqa: S608
        conn,
        params=top_ids + top_ids,
    )

    ordered = ent_df.join(degrees).sort_values(["entity_type", "degree"], ascending=[True, False])
    n = len(ordered)
    angle_step = 2 * math.pi / max(n, 1)
    pos = {
        eid: (math.cos(i * angle_step), math.sin(i * angle_step))
        for i, eid in enumerate(ordered.index)
    }

    edge_x, edge_y = [], []
    for _, e in edges.iterrows():
        if e["entity_a"] not in pos or e["entity_b"] not in pos:
            continue
        x0, y0 = pos[e["entity_a"]]
        x1, y1 = pos[e["entity_b"]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(width=0.5, color="#999"), hoverinfo="none"))

    for etype, group in ordered.groupby("entity_type"):
        xs = [pos[i][0] for i in group.index]
        ys = [pos[i][1] for i in group.index]
        sizes = [8 + min(g, 40) for g in group["degree"]]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="markers+text", name=etype,
                text=group["display"], textposition="top center",
                marker=dict(size=sizes, color=_TYPE_COLOR.get(etype, "#7f7f7f")),
                hovertext=[f"{d} · grado {g}" for d, g in zip(group["display"], group["degree"])],
                hoverinfo="text",
            )
        )

    fig.update_layout(
        showlegend=True, height=650,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        title=f"Sottografo indotto — top {top_n} entità hub",
    )
    st.plotly_chart(fig, width="stretch")
