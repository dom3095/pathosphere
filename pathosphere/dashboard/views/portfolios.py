"""Portfolios page — equity curves for agent / random / benchmark.

Equity curve = INITIAL_CASH + cumulative realized P&L of closed trades over
time, with a final "now" point that adds live unrealized P&L for open
positions (one get_portfolio_status() call, no-lookahead-safe since it only
reprices currently open trades at the current moment, never rewrites
price_open).
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pathosphere.market.trading import INITIAL_CASH, get_portfolio_status


def _equity_curve(conn: sqlite3.Connection, portfolio_id: int) -> pd.DataFrame:
    closed = pd.read_sql_query(
        "SELECT closed_at AS t, pnl FROM trades "
        "WHERE portfolio_id = ? AND closed_at IS NOT NULL ORDER BY closed_at",
        conn,
        params=(portfolio_id,),
    )
    closed["equity"] = INITIAL_CASH + closed["pnl"].cumsum()
    return closed[["t", "equity"]]


def render(conn: sqlite3.Connection) -> None:
    st.header("Portafogli")

    portfolios = conn.execute("SELECT id, name FROM portfolios ORDER BY name").fetchall()
    if not portfolios:
        st.info(
            "Nessun portafoglio inizializzato ancora. "
            "Eseguire `pathos portfolio init` dopo la prima approvazione di tesi."
        )
        return

    statuses = {s.name: s for s in get_portfolio_status(conn)}

    cols = st.columns(len(portfolios))
    for col, p in zip(cols, portfolios):
        s = statuses.get(p["name"])
        with col:
            st.metric(
                p["name"].capitalize(),
                f"${INITIAL_CASH + (s.total_pnl if s else 0):,.0f}",
                delta=f"{s.return_pct:+.2f}%" if s else None,
            )
            if s:
                st.caption(f"{s.open_trades} aperti · {s.closed_trades} chiusi")

    fig = go.Figure()
    has_any = False
    for p in portfolios:
        curve = _equity_curve(conn, p["id"])
        s = statuses.get(p["name"])
        if not curve.empty:
            has_any = True
            fig.add_trace(go.Scatter(x=curve["t"], y=curve["equity"], mode="lines+markers", name=p["name"]))
        if s is not None:
            last_t = curve["t"].iloc[-1] if not curve.empty else "start"
            fig.add_trace(
                go.Scatter(
                    x=[last_t, "ora"],
                    y=[curve["equity"].iloc[-1] if not curve.empty else INITIAL_CASH,
                       INITIAL_CASH + s.total_pnl],
                    mode="lines+markers", name=f"{p['name']} (live)",
                    line=dict(dash="dot"),
                )
            )
            has_any = True

    if has_any:
        fig.update_layout(title="Curva equity", yaxis_title="Equity ($)", height=450)
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Nessun trade ancora registrato — le curve equity appariranno dopo il primo `pathos trade open`.")

    st.subheader("Trade aperti")
    open_trades = pd.read_sql_query(
        """
        SELECT p.name AS portfolio, t.ticker, t.direction, t.quantity, t.price_open, t.opened_at
        FROM trades t JOIN portfolios p ON p.id = t.portfolio_id
        WHERE t.closed_at IS NULL ORDER BY t.opened_at DESC
        """,
        conn,
    )
    if open_trades.empty:
        st.caption("Nessun trade aperto.")
    else:
        st.dataframe(open_trades, width="stretch", hide_index=True)
