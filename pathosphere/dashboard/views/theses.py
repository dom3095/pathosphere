"""Theses page — human-in-the-loop approve/reject flow.

Mirrors `pathos thesis approve/reject` exactly: approval also creates the
auto economic prediction (create_thesis_prediction), matching the CLI so the
geopolitical→thesis→trade→prediction chain stays consistent regardless of
which interface is used. Opening the actual paper trade is a separate,
explicit action (own button) — it hits yfinance and touches portfolios.
"""

from __future__ import annotations

import sqlite3

import streamlit as st

from pathosphere.agent.approval import (
    approve_thesis,
    format_causal_chain,
    get_thesis,
    get_watchlist_items,
    list_theses,
    reject_thesis,
    validate_ticker,
)
from pathosphere.agent.predictions import create_thesis_prediction


def _has_open_trade(conn: sqlite3.Connection, thesis_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM trades WHERE thesis_id = ? LIMIT 1", (thesis_id,)
    ).fetchone()
    return row is not None


def _render_thesis_card(conn: sqlite3.Connection, thesis: sqlite3.Row) -> None:
    with st.container(border=True):
        st.markdown(f"**#{thesis['id']} — {thesis['title']}**")
        c1, c2, c3, c4 = st.columns(4)
        c1.write(f"Strumento: `{thesis['instrument'] or '—'}`")
        c2.write(f"Direzione: {thesis['direction'] or '—'}")
        c3.write(f"Orizzonte: {thesis['horizon_days'] or '—'} gg")
        c4.write(f"Confidenza: {thesis['confidence']:.0%}" if thesis["confidence"] is not None else "Confidenza: —")

        full = get_thesis(conn, thesis["id"])
        chain = format_causal_chain(full["causal_chain"]) if full["causal_chain"] else {}
        if chain:
            st.json(chain, expanded=False)
        elif full["causal_chain"]:
            st.text(full["causal_chain"])

        if full["invalidation"]:
            st.caption(f"Invalidazione: {full['invalidation']}")

        watchlist = get_watchlist_items(conn, thesis["id"])
        if watchlist:
            st.caption("Watchlist:")
            for w in watchlist:
                st.write(f"- [{w['status']}] {w['label']}")

        if thesis["status"] == "pending":
            b1, b2 = st.columns([1, 3])
            with b1:
                if st.button("✅ Approva", key=f"approve_{thesis['id']}"):
                    ticker = full["instrument"]
                    if ticker and not validate_ticker(ticker):
                        st.warning(f"Ticker {ticker} non trovato su yfinance — verificare prima di aprire il trade.")
                    try:
                        updated = approve_thesis(conn, thesis["id"])
                        try:
                            pred = create_thesis_prediction(conn, updated)
                            st.success(f"Approvata. Predizione economica #{pred['id']} creata.")
                        except (ValueError, sqlite3.Error) as exc:
                            st.warning(f"Approvata, ma predizione economica non creata: {exc}")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            with b2:
                reason = st.text_input("Motivo rifiuto", key=f"reason_{thesis['id']}")
                if st.button("❌ Rifiuta", key=f"reject_{thesis['id']}"):
                    if not reason.strip():
                        st.error("Motivo obbligatorio per il rifiuto.")
                    else:
                        try:
                            reject_thesis(conn, thesis["id"], reason)
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))

        elif thesis["status"] == "approved":
            st.write(f"Approvata: {full['approved_at']}")
            if _has_open_trade(conn, thesis["id"]):
                st.caption("Trade già aperto per questa tesi.")
            else:
                if st.button("📈 Apri trade (agent + random control)", key=f"trade_{thesis['id']}"):
                    from pathosphere.market.trading import open_agent_trade
                    from pathosphere.agent.predictions import link_thesis_prediction_to_trade

                    try:
                        result = open_agent_trade(conn, thesis["id"])
                        link_thesis_prediction_to_trade(conn, thesis["id"], result.agent_trade_id)
                        st.success(
                            f"Trade aperto: {result.ticker} {result.direction} "
                            f"qty={result.quantity:.4f} @ {result.price_open:.2f} "
                            f"(random control: {result.random_ticker})"
                        )
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

        elif thesis["status"] == "rejected":
            st.write(f"Rifiutata: {full['rejected_at']}")
            st.caption(f"Motivo: {full['rejection_reason']}")


def render(conn: sqlite3.Connection) -> None:
    st.header("Tesi")

    tab_pending, tab_approved, tab_rejected = st.tabs(["In attesa", "Approvate", "Rifiutate"])

    with tab_pending:
        rows = list_theses(conn, "pending")
        st.caption(f"{len(rows)} tesi in attesa")
        if not rows:
            st.info("Nessuna tesi in attesa. Generarle con `pathos thesis generate`.")
        for r in rows:
            _render_thesis_card(conn, r)

    with tab_approved:
        rows = list_theses(conn, "approved")
        st.caption(f"{len(rows)} tesi approvate")
        for r in rows:
            _render_thesis_card(conn, r)

    with tab_rejected:
        rows = list_theses(conn, "rejected")
        st.caption(f"{len(rows)} tesi rifiutate")
        for r in rows:
            _render_thesis_card(conn, r)
