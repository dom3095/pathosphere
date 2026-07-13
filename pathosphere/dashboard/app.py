"""Pathosphere dashboard — Streamlit entry point.

Launched via `pathos serve` (see pathosphere/cli.py), which shells out to
`streamlit run` against this file. Not meant to be imported for its side
effects — `streamlit run pathosphere/dashboard/app.py` is the only supported
invocation.
"""

from __future__ import annotations

import streamlit as st

from pathosphere.dashboard.db import get_connection
from pathosphere.dashboard.views import (
    briefs,
    entity_graph,
    map_view,
    narratives,
    overview,
    portfolios,
    predictions,
    theses,
)

st.set_page_config(page_title="Pathosphere", page_icon="🌐", layout="wide")

_PAGES = {
    "Overview": overview,
    "Mappa": map_view,
    "Narrazioni": narratives,
    "Grafo entità": entity_graph,
    "Tesi": theses,
    "Portafogli": portfolios,
    "Predizioni": predictions,
    "Brief": briefs,
}


def main() -> None:
    st.sidebar.title("🌐 Pathosphere")
    st.sidebar.caption("OSINT portal — intelligence personale")
    page_name = st.sidebar.radio("Sezione", list(_PAGES.keys()))

    conn = get_connection()
    try:
        _PAGES[page_name].render(conn)
    finally:
        conn.close()


main()
