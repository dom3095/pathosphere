"""Map page — geolocated events on a Folium map."""

from __future__ import annotations

import sqlite3

import folium
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

_TYPE_COLOR = {
    "conflict": "red",
    "epidemic": "purple",
    "trade": "orange",
    "infrastructure": "blue",
    "political": "green",
    "other": "gray",
}


def render(conn: sqlite3.Connection) -> None:
    st.header("Mappa eventi")

    event_types = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT event_type FROM events WHERE event_type IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]
    origins = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT origin FROM events WHERE origin IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        selected_types = st.multiselect("Tipo evento", event_types, default=event_types)
    with c2:
        selected_origins = st.multiselect("Fonte", origins, default=origins)
    with c3:
        days_back = st.number_input("Ultimi N giorni (0 = tutti)", min_value=0, value=30, step=10)

    query = """
        SELECT id, title, event_type, origin, severity, location_name, lat, lon, first_seen
        FROM events
        WHERE lat IS NOT NULL AND lon IS NOT NULL
    """
    params: list = []
    if selected_types:
        query += f" AND event_type IN ({','.join('?' * len(selected_types))})"
        params += selected_types
    if selected_origins:
        query += f" AND origin IN ({','.join('?' * len(selected_origins))})"
        params += selected_origins
    if days_back > 0:
        query += " AND first_seen >= datetime('now', ?)"
        params.append(f"-{int(days_back)} days")
    query += " ORDER BY first_seen DESC LIMIT 3000"

    rows = conn.execute(query, params).fetchall()
    st.caption(f"{len(rows)} eventi geolocalizzati (max 3000 mostrati)")

    if not rows:
        st.info("Nessun evento geolocalizzato per i filtri correnti.")
        return

    fmap = folium.Map(location=[20, 10], zoom_start=2, tiles="CartoDB positron")
    cluster = MarkerCluster().add_to(fmap)

    for r in rows:
        color = _TYPE_COLOR.get(r["event_type"], "gray")
        popup = folium.Popup(
            f"<b>{r['title']}</b><br>{r['event_type'] or '—'} · sev {r['severity'] or '—'}"
            f"<br>{r['location_name'] or ''}<br>{r['first_seen']}",
            max_width=300,
        )
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=4 + (r["severity"] or 1),
            color=color,
            fill=True,
            fill_opacity=0.7,
            popup=popup,
        ).add_to(cluster)

    st_folium(fmap, width=None, height=560, returned_objects=[])

    with st.expander("Legenda tipo evento"):
        for t, c in _TYPE_COLOR.items():
            st.markdown(f"- <span style='color:{c}'>●</span> {t}", unsafe_allow_html=True)
