"""CycleBites: a small campus surplus food exchange."""

from __future__ import annotations

import base64
import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import requests
import streamlit as st

try:
    import folium
    from streamlit_folium import st_folium
except ModuleNotFoundError:
    folium = None
    st_folium = None

try:
    from streamlit_searchbox import st_searchbox
except ModuleNotFoundError as error:
    if error.name != "streamlit_searchbox":
        raise
    st_searchbox = None

try:
    from streamlit_geolocation import streamlit_geolocation
except ModuleNotFoundError as error:
    if error.name != "streamlit_geolocation":
        raise
    streamlit_geolocation = None

if tuple(map(int, st.__version__.split(".")[:2])) >= (1, 60):
    try:
        import streamlit_shadcn_ui as ui
    except ModuleNotFoundError as error:
        if error.name != "streamlit_shadcn_ui":
            raise
        ui = None
else:
    ui = None


DB_PATH = Path(__file__).with_name("cyclebites.db")
LOGO_PATH = Path(__file__).parent / "assets" / "cyclebites-logo.svg"
LOGO_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
POUNDS_PER_PORTION = 0.75  # A transparent planning estimate, not a measured weight.
KG_CO2E_PER_POUND = 0.25  # EPA WARM v16: 0.50 metric tons / short ton landfilled.
PHOTON_API = "https://photon.komoot.io/api/"
WALKING_ROUTE_API = "https://routing.openstreetmap.de/routed-foot/route/v1/driving/"
HTTP_HEADERS = {"User-Agent": "CycleBites/1.0 (https://github.com/shaypat112/cyclebites-campus)"}
_route_lock = threading.Lock()
_last_route_request = 0.0


def fetch_json(url: str, params: dict, timeout: int) -> dict | None:
    try:
        response = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def format_place(properties: dict) -> str:
    """Turn a Photon result into a concise, unambiguous pickup address."""
    street = " ".join(str(properties.get(key, "")).strip() for key in ("housenumber", "street")).strip()
    parts = [properties.get("name"), street, properties.get("city") or properties.get("district"), properties.get("state")]
    return ", ".join(str(part).strip() for part in parts if part and str(part).strip())


@st.cache_data(ttl=300, show_spinner=False)
def search_places(searchterm: str) -> list[tuple[str, dict]]:
    """Query Photon's public search-as-you-type demo conservatively."""
    query = searchterm.strip()
    if len(query) < 3:
        return []
    response = fetch_json(PHOTON_API, {"q": query, "limit": 5, "lang": "en"}, timeout=4)
    features = response.get("features", []) if response else []
    places = {}
    for feature in features:
        label = format_place(feature.get("properties", {}))
        coordinates = feature.get("geometry", {}).get("coordinates", [])
        if label and len(coordinates) >= 2 and label not in places:
            places[label] = {"label": label, "lat": coordinates[1], "lon": coordinates[0]}
    return list(places.items())


def miles_between(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Straight-line distance for ranking, not a travel-distance estimate."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*start, *end))
    arc = 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    ))
    return 3958.8 * arc


def directions_url(destination: tuple[float, float] | str, origin: tuple[float, float] | None = None) -> str:
    target = f"{destination[0]},{destination[1]}" if isinstance(destination, tuple) else destination
    parameters = {"api": 1, "destination": target, "travelmode": "walking"}
    if origin:
        parameters["origin"] = f"{origin[0]},{origin[1]}"
    return "https://www.google.com/maps/dir/?" + urlencode(parameters)


@st.cache_data(ttl=300, show_spinner=False)
def nearby_food_banks(lat: float, lon: float) -> list[dict]:
    """Search a bounded local area for mapped food banks and pantries."""
    lat_delta = 15 / 111.0
    lon_delta = lat_delta / max(math.cos(math.radians(lat)), 0.1)
    bbox = f"{lon - lon_delta},{lat - lat_delta},{lon + lon_delta},{lat + lat_delta}"
    found = {}
    for term in ("food pantry", "food bank"):
        response = fetch_json(PHOTON_API, {"q": term, "bbox": bbox, "limit": 30, "lang": "en"}, timeout=6)
        features = response.get("features", []) if response else []
        for feature in features:
            tags = feature.get("properties", {})
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            name = tags.get("name", "")
            if len(coordinates) < 2 or not any(word in name.lower() for word in ("food bank", "food pantry", "food shelf")):
                continue
            if any(word in name.lower() for word in ("animal", "pet food")):
                continue
            point = (coordinates[1], coordinates[0])
            if miles_between((lat, lon), point) > 10:
                continue
            key = (tags.get("osm_type"), tags.get("osm_id"))
            found[key] = {"name": name, "lat": point[0], "lon": point[1]}
    return sorted(found.values(), key=lambda bank: miles_between((lat, lon), (bank["lat"], bank["lon"])))[:12]


@st.cache_data(ttl=300, show_spinner=False)
def walking_route(start: tuple[float, float], end: tuple[float, float]) -> dict | None:
    """Return a road-network route; limit requests to the public demo server."""
    global _last_route_request
    url = WALKING_ROUTE_API + f"{start[1]},{start[0]};{end[1]},{end[0]}"
    with _route_lock:
        delay = 1.0 - (time.monotonic() - _last_route_request)
        if delay > 0:
            time.sleep(delay)
        _last_route_request = time.monotonic()
        response = fetch_json(url, {"overview": "full", "geometries": "geojson", "steps": "false"}, timeout=8)
        routes = response.get("routes", []) if response else []
    return routes[0] if routes else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                food_name TEXT NOT NULL,
                portions INTEGER NOT NULL CHECK (portions > 0),
                pickup_location TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                expires_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available', 'claimed')),
                created_at TEXT NOT NULL,
                claimed_at TEXT
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(listings)")}
        for column in ("latitude", "longitude"):
            if column not in columns:
                connection.execute(f"ALTER TABLE listings ADD COLUMN {column} REAL")


def add_listing(
    food_name: str,
    portions: int,
    pickup_location: str,
    expires_at: datetime,
    notes: str,
    coordinates: tuple[float, float] | None = None,
) -> int:
    if not food_name.strip() or not pickup_location.strip():
        raise ValueError("Food name and pickup location are required.")
    if portions < 1:
        raise ValueError("Portions must be greater than zero.")
    if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= utc_now():
        raise ValueError("Pickup deadline must be in the future.")
    if coordinates and not (-90 <= coordinates[0] <= 90 and -180 <= coordinates[1] <= 180):
        raise ValueError("Pickup coordinates are outside the map.")

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO listings
                (food_name, portions, pickup_location, latitude, longitude, expires_at, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                food_name.strip(),
                portions,
                pickup_location.strip(),
                coordinates[0] if coordinates else None,
                coordinates[1] if coordinates else None,
                expires_at.astimezone(timezone.utc).isoformat(),
                notes.strip(),
                utc_now().isoformat(),
            ),
        )
        return int(cursor.lastrowid)


def active_listings() -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(
            """
            SELECT * FROM listings
            WHERE status = 'available' AND expires_at > ?
            ORDER BY expires_at ASC, id DESC
            """,
            (utc_now().isoformat(),),
        ).fetchall()


def claim_listing(listing_id: int) -> bool:
    """Claim once, atomically, and never claim an expired listing."""
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE listings SET status = 'claimed', claimed_at = ?
            WHERE id = ? AND status = 'available' AND expires_at > ?
            """,
            (utc_now().isoformat(), listing_id, utc_now().isoformat()),
        )
        return cursor.rowcount == 1


def impact_totals() -> sqlite3.Row:
    with connect() as connection:
        return connection.execute(
            """
            SELECT COUNT(*) AS claimed_listings,
                   COALESCE(SUM(portions), 0) AS meals_shared
            FROM listings WHERE status = 'claimed'
            """
        ).fetchone()


def recent_claims() -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(
            """
            SELECT food_name, portions, pickup_location, claimed_at
            FROM listings WHERE status = 'claimed'
            ORDER BY claimed_at DESC LIMIT 5
            """
        ).fetchall()


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
        :root { --charcoal: #171717; --ink: #171717; --muted: #686868; --line: #e5e5e5; }
        html, body, [data-testid="stApp"] { font-family: 'DM Sans', sans-serif; }
        [data-testid="stApp"] { background: #ffffff; color: var(--ink); }
        [data-testid="stHeader"] { background: #ffffff; }
        [data-testid="stAppDeployButton"] { display: none; }
        [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--line); }
        [data-testid="stSidebar"] > div:first-child { padding-top: 2rem; }
        .block-container { max-width: 1120px; padding: 2.1rem 2.7rem 4rem; }
        h1, h2, h3 { font-family: 'Manrope', sans-serif; color: var(--ink); letter-spacing: -.035em; }
        h2 { font-size: 1.18rem !important; font-weight: 750 !important; }
        h3 { font-size: 1.02rem !important; font-weight: 700 !important; }
        [data-testid="stVerticalBlockBorderWrapper"] { border-radius: 16px !important; }
        [data-testid="stVerticalBlockBorderWrapper"] > div { border-color: var(--line) !important; }
        [data-testid="stWidgetLabel"] p { color: #333333 !important; font-weight: 650; font-size: .88rem; }
        [data-baseweb="input"] > div, [data-baseweb="textarea"] > div {
            background: #fff !important; border-color: #dddddd !important; border-radius: 9px !important;
        }
        input, textarea { color: var(--ink) !important; }
        div.stButton > button, div.stFormSubmitButton > button {
            min-height: 2.8rem; border-radius: 9px; font-weight: 700; transition: .16s ease;
        }
        div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] {
            color: #fff; background: var(--charcoal); border-color: var(--charcoal);
        }
        div.stButton > button[kind="primary"]:hover, div.stFormSubmitButton > button[kind="primary"]:hover {
            background: #333333; border-color: #333333;
        }
        [data-testid="stSidebar"] div.stButton > button {
            justify-content: flex-start; width: 100%; min-height: 2.75rem;
            padding: .65rem .85rem; box-shadow: none; text-align: left;
        }
        [data-testid="stSidebar"] div.stButton > button[kind="secondary"] {
            border: 1px solid transparent; background: transparent; color: #555555;
        }
        [data-testid="stSidebar"] div.stButton > button[kind="secondary"]:hover {
            background: #f4f4f4; color: var(--charcoal);
        }
        .brand-row { display: flex; align-items: center; gap: .72rem; }
        .brand-mark { display: block; width: 37px; height: 37px; flex: none; }
        .brand-name { font: 800 1.18rem 'Manrope', sans-serif; letter-spacing: -.04em; color: var(--ink); }
        .brand-caption { color: var(--muted); font-size: .74rem; margin-top: 1px; }
        .nav-label { color: #888888; font-size: .67rem; font-weight: 800;
            letter-spacing: .14em; text-transform: uppercase; margin: 2.25rem 0 .55rem; }
        .side-foot { border-top: 1px solid var(--line); padding-top: 1rem;
            color: #777777; font-size: .73rem; line-height: 1.5; }
        .page-kicker { color: #666666; font-size: .72rem; font-weight: 800;
            letter-spacing: .12em; text-transform: uppercase; margin-bottom: .6rem; }
        .page-title { font: 800 clamp(1.85rem, 3.1vw, 2.55rem)/1.13 'Manrope', sans-serif;
            letter-spacing: -.055em; color: var(--ink); margin: 0 0 .65rem; }
        .page-copy { color: #666666; font-size: .95rem; line-height: 1.55;
            max-width: 610px; margin: 0; }
        .page-header { margin: .45rem 0 1.85rem; }
        .section-heading { font: 750 1.04rem 'Manrope', sans-serif; color: var(--ink);
            letter-spacing: -.025em; margin: .25rem 0 .75rem; }
        .section-copy { color: var(--muted); font-size: .82rem; margin: -.3rem 0 1rem; }
        .fallback-metric { border: 1px solid var(--line); border-radius: 12px;
            background: white; padding: 1rem 1.15rem; min-height: 125px; }
        .fallback-metric-label { color: #666; font-size: .8rem; font-weight: 700; }
        .fallback-metric-value { color: var(--ink); font: 800 1.9rem 'Manrope', sans-serif;
            letter-spacing: -.05em; margin: .35rem 0; }
        .fallback-metric-detail { color: #777; font-size: .76rem; }
        .fallback-badge { display: inline-block; color: #333; background: #f2f2f2;
            border-radius: 999px; padding: .28rem .62rem; font-size: .72rem; font-weight: 700; }
        .quiet-callout { background: #f7f7f7; border: 1px solid #e8e8e8; border-radius: 14px;
            padding: 1.1rem 1.2rem; color: #4d4d4d; font-size: .86rem; line-height: 1.55; }
        .quiet-callout strong { color: #171717; }
        .feed-title { color: var(--ink); font: 750 1.1rem 'Manrope', sans-serif;
            letter-spacing: -.03em; margin-bottom: .45rem; }
        .feed-meta { color: #555555; font-size: .84rem; margin: .32rem 0; }
        .feed-note { color: #777777; font-size: .81rem; margin-top: .6rem; }
        .list-row { border-bottom: 1px solid var(--line); padding: .85rem 0; }
        .list-row:last-child { border-bottom: 0; }
        .list-primary { color: var(--ink); font-weight: 750; font-size: .9rem; }
        .list-secondary { color: #777777; font-size: .76rem; margin-top: .22rem; }
        @media (max-width: 640px) {
            .block-container { padding: 1.3rem 1rem 3rem; }
            .page-header { margin-bottom: 1.4rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(eyebrow: str, title: str, description: str) -> None:
    st.markdown(
        f'<div class="page-header"><div class="page-kicker">{escape(eyebrow)}</div>'
        f'<h1 class="page-title">{escape(title)}</h1>'
        f'<p class="page-copy">{escape(description)}</p></div>',
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: str | int, description: str, key: str) -> None:
    if ui is not None:
        ui.metric_card(label, value, description=description, variant="dashboard", key=key)
        return
    st.markdown(
        f'<div class="fallback-metric"><div class="fallback-metric-label">{escape(label)}</div>'
        f'<div class="fallback-metric-value">{escape(str(value))}</div>'
        f'<div class="fallback-metric-detail">{escape(description)}</div></div>',
        unsafe_allow_html=True,
    )


def status_badge(text: str, key: str) -> None:
    if ui is not None:
        ui.badge(text, variant="secondary", key=key)
    else:
        st.markdown(f'<span class="fallback-badge">{escape(text)}</span>', unsafe_allow_html=True)


def status_alert(title: str, description: str, key: str) -> None:
    if ui is not None:
        ui.alert(title, description, key=key)
    else:
        st.info(f"**{title}** — {description}")


def empty_board_card() -> None:
    title = "Nothing available right now"
    description = "New pickups appear here as soon as cafeteria staff publish them."
    if ui is not None:
        ui.card(title=title, content=description, key="empty_board")
    else:
        with st.container(border=True):
            st.markdown(f'<div class="section-heading">{title}</div>', unsafe_allow_html=True)
            st.caption(description)


def donor_page() -> None:
    page_header(
        "Cafeteria / Share food",
        "Post a surplus pickup",
        "Add a clear pickup window so volunteers and pantry partners can collect food in time.",
    )
    form_area, side_area = st.columns([2.2, 1], gap="large")
    with form_area:
        with st.container(border=True):
            st.markdown('<div class="section-heading">Pickup details</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-copy">Keep it concise. Volunteers see these details on the live board.</div>', unsafe_allow_html=True)
            selected_place = None
            if st_searchbox is not None:
                selected_place = st_searchbox(
                    search_places,
                    key="pickup_place_search",
                    label="Find a place on the map",
                    placeholder="Search a building or street address...",
                    debounce=450,
                    clear_on_submit=False,
                    edit_after_submit="option",
                )
                st.caption("Place search by Photon · © OpenStreetMap contributors. Type at least 3 characters.")
            if selected_place and selected_place != st.session_state.get("last_selected_place"):
                st.session_state["pickup_location"] = selected_place["label"]
                st.session_state["last_selected_place"] = selected_place
            location = st.text_input(
                "Pickup location *",
                key="pickup_location",
                placeholder="e.g. North Dining Hall, room 104",
                max_chars=150,
                help="Choose a map result above, then add a room or entrance; or type a campus location manually.",
            )
            with st.form("new_listing", clear_on_submit=True):
                food_name = st.text_input("Food item name *", placeholder="e.g. Vegetable grain bowls", max_chars=100)
                portions = st.number_input("Portions available *", min_value=1, max_value=10000, value=12, step=1)
                default_deadline = datetime.now().astimezone() + timedelta(hours=2)
                date_col, time_col = st.columns(2)
                with date_col:
                    pickup_date = st.date_input("Pickup date *", value=default_deadline.date(), min_value=datetime.now().date())
                with time_col:
                    pickup_time = st.time_input("Pickup by *", value=default_deadline.time().replace(second=0, microsecond=0))
                notes = st.text_area(
                    "Notes for pickup",
                    placeholder="Ingredients, allergens, packaging, or pickup instructions",
                    max_chars=500,
                    height=86,
                )
                submitted = st.form_submit_button("Publish pickup →", type="primary", use_container_width=True)

            if submitted:
                try:
                    deadline = datetime.combine(pickup_date, pickup_time).astimezone()
                    selected = st.session_state.get("last_selected_place")
                    coordinates = None
                    if selected and location.startswith(selected["label"]):
                        coordinates = (selected["lat"], selected["lon"])
                    add_listing(food_name, int(portions), location, deadline, notes, coordinates)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Pickup published. It is visible on the live board.")
                    if coordinates is None:
                        st.caption("This manually entered location has no map pin. Select a place suggestion to add one.")
    with side_area:
        metric_card("Live pickups", len(active_listings()), "Currently available on campus", "donor_live")
        st.write("")
        st.markdown(
            '<div class="quiet-callout"><strong>Before you publish</strong><br>'
            'Share only food that is safe to eat. Add allergen details in the notes, '
            'and choose a realistic collection deadline.</div>',
            unsafe_allow_html=True,
        )


def map_page() -> None:
    page_header(
        "Explore / Map & routes",
        "Food rescue, mapped",
        "Find the closest available pickup, then plan a walking route to collect or deliver it.",
    )
    if notice := st.session_state.pop("claim_notice", None):
        if notice == "claimed":
            status_alert("Pickup claimed", "Please collect it before the listed deadline.", "map_claim_success")
        else:
            status_alert("Pickup unavailable", "The map has been refreshed. Choose another listing.", "map_claim_unavailable")
    if folium is None or st_folium is None:
        st.info("Install the packages in requirements.txt to enable the interactive map.")
        return

    listings = [row for row in active_listings() if row["latitude"] is not None and row["longitude"] is not None]
    selected_start = st_searchbox(
        search_places,
        key="map_start_search",
        label="Your starting place",
        placeholder="Search an address or campus building...",
        debounce=450,
        edit_after_submit="option",
    ) if st_searchbox is not None else None
    if selected_start and selected_start != st.session_state.get("last_map_start"):
        st.session_state["map_origin"] = (selected_start["lat"], selected_start["lon"])
        st.session_state["last_map_start"] = selected_start
    if streamlit_geolocation is not None:
        with st.expander("Use my device location"):
            st.caption("Tap the location icon and allow browser access. Your position is used for this map session.")
            device_location = streamlit_geolocation()
        if isinstance(device_location, dict) and device_location.get("latitude") is not None and device_location.get("longitude") is not None:
            device_point = (device_location["latitude"], device_location["longitude"])
            if device_point != st.session_state.get("last_device_origin"):
                st.session_state["map_origin"] = device_point
                st.session_state["last_device_origin"] = device_point
    origin = st.session_state.get("map_origin")
    st.caption("Use your device, select a place, or tap an unmarked point on the map to set your start. Only mapped pickups appear here.")

    pickup_points = {
        f"Pickup · {row['food_name']} ({row['id']})": (row["latitude"], row["longitude"])
        for row in listings
    }
    anchors = {"Your starting place": origin} if origin else {}
    anchors.update(pickup_points)
    pantry_col, route_col = st.columns([1, 1], gap="medium")
    with pantry_col:
        if anchors:
            pantry_anchor_name = st.selectbox("Find food banks near", list(anchors), key="pantry_anchor")
            if st.button("Find nearby food banks", use_container_width=True):
                anchor = anchors[pantry_anchor_name]
                with st.spinner("Searching OpenStreetMap..."):
                    st.session_state["map_food_banks"] = nearby_food_banks(*anchor)
                st.session_state["food_banks_anchor"] = anchor
        else:
            st.caption("Set a starting place to search for nearby food banks.")
    food_banks = st.session_state.get("map_food_banks", [])
    destinations = dict(pickup_points)
    destinations.update({f"Food bank · {bank['name']} ({i + 1})": (bank["lat"], bank["lon"])
                         for i, bank in enumerate(food_banks)})
    route_key = None
    with route_col:
        if anchors and destinations:
            route_start = st.selectbox("Route from", list(anchors), key="route_start")
            route_destination = st.selectbox("Route to", list(destinations), key="route_destination")
            start_point, end_point = anchors[route_start], destinations[route_destination]
            route_key = (start_point, end_point)
            if st.button("Show walking route", type="primary", use_container_width=True):
                with st.spinner("Planning route..."):
                    st.session_state["map_route"] = walking_route(start_point, end_point)
                st.session_state["map_route_points"] = route_key
        else:
            st.caption("Add a starting place and a mapped pickup to plan a route.")

    center = origin or (next(iter(pickup_points.values())) if pickup_points else (39.5, -98.35))
    map_view = folium.Map(location=center, zoom_start=14 if origin or pickup_points else 4, tiles="OpenStreetMap", control_scale=True)
    for row in listings:
        folium.Marker(
            [row["latitude"], row["longitude"]],
            tooltip=f"{escape(row['food_name'])} · {row['portions']} portions",
            popup=folium.Popup(escape(row["pickup_location"]), max_width=240),
            icon=folium.Icon(color="black", icon="cutlery", prefix="fa"),
        ).add_to(map_view)
    for bank in food_banks:
        folium.Marker(
            [bank["lat"], bank["lon"]],
            tooltip=f"Food bank · {escape(bank['name'])}",
            icon=folium.Icon(color="blue", icon="home", prefix="fa"),
        ).add_to(map_view)
    if origin:
        folium.CircleMarker(origin, radius=8, color="#171717", fill=True, fill_color="#ffffff",
                            fill_opacity=1, weight=3, tooltip="Your starting place").add_to(map_view)
    route = st.session_state.get("map_route")
    if route and st.session_state.get("map_route_points") == route_key:
        coordinates = route.get("geometry", {}).get("coordinates", [])
        if coordinates:
            folium.PolyLine([(lat, lon) for lon, lat in coordinates], color="#171717", weight=5,
                            opacity=.8, tooltip="Walking route").add_to(map_view)
            map_view.fit_bounds([list(route_key[0]), list(route_key[1])], padding=(32, 32))
    map_result = st_folium(map_view, height=430, use_container_width=True, returned_objects=["last_clicked"], key="rescue_map")
    if map_result and (clicked := map_result.get("last_clicked")):
        clicked_point = (clicked["lat"], clicked["lng"])
        if clicked_point != st.session_state.get("last_map_click"):
            st.session_state["last_map_click"] = clicked_point
            st.session_state["map_origin"] = clicked_point
            st.rerun()

    if route and st.session_state.get("map_route_points") == route_key:
        st.caption(f"Walking route · {route['distance'] / 1609.344:.1f} mi · about {round(route['duration'] / 60)} min. Open directions for turn-by-turn guidance.")
    elif route_key is not None and st.session_state.get("map_route_points") == route_key and route is None:
        st.caption("A mapped walking route is unavailable right now. You can still open directions below.")
    if anchors and destinations:
        st.link_button("Open turn-by-turn directions ↗", directions_url(end_point, start_point))
    st.caption("Map data © OpenStreetMap contributors · Walking routes: FOSSGIS OSRM · Tap an empty map area to change your start.")

    st.markdown('<div class="section-heading">Closest available pickups</div>', unsafe_allow_html=True)
    if not listings:
        st.info("No mapped pickups are available. New posts appear here when a donor selects a place suggestion.")
    else:
        ranked = sorted(listings, key=lambda row: miles_between(origin, (row["latitude"], row["longitude"])) if origin else 0)
        for row in ranked[:6]:
            point = (row["latitude"], row["longitude"])
            distance = f" · {miles_between(origin, point):.1f} mi away (straight line)" if origin else ""
            with st.container(border=True):
                details, action = st.columns([3, 1], vertical_alignment="center")
                with details:
                    st.markdown(f"**{escape(row['food_name'])}** · {row['portions']} portions{distance}")
                    st.caption(row["pickup_location"])
                with action:
                    st.link_button("Directions ↗", directions_url(point, origin), use_container_width=True)
                    if st.button("Claim pickup", key=f"map_claim_{row['id']}", use_container_width=True):
                        st.session_state["claim_notice"] = "claimed" if claim_listing(row["id"]) else "unavailable"
                        st.rerun()

    st.markdown('<div class="section-heading">Nearby food banks</div>', unsafe_allow_html=True)
    st.caption("These are possible donation partners from OpenStreetMap. Contact them first to confirm hours, food safety rules, and current needs.")
    if food_banks:
        bank_origin = st.session_state.get("food_banks_anchor")
        for bank in food_banks[:6]:
            point = (bank["lat"], bank["lon"])
            distance = f" · {miles_between(bank_origin, point):.1f} mi away" if bank_origin else ""
            col_name, col_link = st.columns([3, 1], vertical_alignment="center")
            with col_name:
                st.markdown(f"**{escape(bank['name'])}**{distance}")
            with col_link:
                st.link_button("Directions ↗", directions_url(point, bank_origin), use_container_width=True)
    elif st.session_state.get("food_banks_anchor"):
        st.caption("No mapped food banks were found nearby, or the directory is temporarily unavailable.")


def board_page() -> None:
    page_header(
        "Volunteer / Available food",
        "Find a pickup",
        "Claim available food for a campus volunteer team or pantry partner.",
    )
    if notice := st.session_state.pop("claim_notice", None):
        if notice == "claimed":
            status_alert("Pickup claimed", "Please collect it before the listed deadline.", "claim_success")
        else:
            status_alert("Pickup unavailable", "The board has been refreshed. Choose another listing.", "claim_unavailable")
    live_board()


@st.fragment(run_every="15s")
def live_board() -> None:
    listings = active_listings()
    available_portions = sum(row["portions"] for row in listings)
    st.markdown(
        f'<div class="quiet-callout"><strong>{len(listings)} active pickups</strong>'
        f' &nbsp;·&nbsp; {available_portions} portions available'
        ' &nbsp;·&nbsp; Refreshes every 15 seconds</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    if not listings:
        empty_board_card()
        return

    for listing in listings:
        deadline = datetime.fromisoformat(listing["expires_at"]).astimezone()
        with st.container(border=True):
            details, action = st.columns([3.2, 1], vertical_alignment="center", gap="large")
            with details:
                status_badge("Available for pickup", f"status_{listing['id']}")
                st.markdown(f'<div class="feed-title">{escape(listing["food_name"])}</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="feed-meta"><strong>{listing["portions"]} portions</strong>'
                    f' &nbsp;·&nbsp; {escape(listing["pickup_location"])}</div>'
                    f'<div class="feed-meta">Pickup by {escape(deadline.strftime("%b %d, %I:%M %p"))}</div>',
                    unsafe_allow_html=True,
                )
                if listing["notes"]:
                    st.markdown(f'<div class="feed-note">{escape(listing["notes"])}</div>', unsafe_allow_html=True)
            with action:
                if st.button(
                    "Claim pickup →", key=f"claim_{listing['id']}",
                    type="primary", use_container_width=True,
                ):
                    claimed = claim_listing(listing["id"])
                    st.session_state["claim_notice"] = "claimed" if claimed else "unavailable"
                    st.rerun()
                destination = (
                    (listing["latitude"], listing["longitude"])
                    if listing["latitude"] is not None else listing["pickup_location"]
                )
                st.link_button("Directions ↗", directions_url(destination), use_container_width=True)


def dashboard_page() -> None:
    page_header(
        "Admin / Impact overview",
        "Campus impact",
        "A clear snapshot of claimed food and estimated waste reduction.",
    )
    totals = impact_totals()
    meals = int(totals["meals_shared"])
    pounds = meals * POUNDS_PER_PORTION
    emissions = pounds * KG_CO2E_PER_POUND

    first, second, third = st.columns(3)
    with first:
        metric_card("Food diverted", f"{pounds:,.1f} lb", "Estimated weight", "impact_weight")
    with second:
        metric_card("Meals shared", f"{meals:,}", "Claimed portions", "impact_meals")
    with third:
        metric_card("Emissions avoided", f"{emissions:,.1f} kg", "Estimated CO₂e", "impact_emissions")

    st.write("")
    st.markdown(
        f'<div class="quiet-callout"><strong>{totals["claimed_listings"]:,} pickups claimed</strong>'
        ' &nbsp;·&nbsp; Every claimed portion is counted as one shared meal in this prototype.</div>',
        unsafe_allow_html=True,
    )

    st.write("")
    with st.container(border=True):
        st.markdown('<div class="section-heading">Recent claims</div>', unsafe_allow_html=True)
        claims = recent_claims()
        if not claims:
            st.caption("No pickups claimed yet. Activity will appear here once the first item is reserved.")
        else:
            for claim in claims:
                claimed_at = datetime.fromisoformat(claim["claimed_at"]).astimezone()
                st.markdown(
                    f'<div class="list-row"><div class="list-primary">{escape(claim["food_name"])}'
                    f' · {claim["portions"]} portions</div><div class="list-secondary">'
                    f'{escape(claim["pickup_location"])} &nbsp;·&nbsp; '
                    f'{escape(claimed_at.strftime("%b %d, %I:%M %p"))}</div></div>',
                    unsafe_allow_html=True,
                )

    with st.expander("How these estimates are calculated"):
        st.write(
            "Food weight uses an assumed 0.75 lb per portion. Emissions use 0.25 kg CO₂e "
            "per pound, derived from the U.S. EPA WARM v16 national-average food-waste "
            "landfilling factor (0.50 metric tons CO₂e per short ton). Claims stand in for "
            "completed pickups; actual outcomes may differ."
        )
        st.markdown(
            "[EPA WARM food-waste methodology]"
            "(https://www.epa.gov/system/files/documents/2023-12/warm_organic_materials_v16_dec.pdf)"
        )


def main() -> None:
    st.set_page_config(page_title="CycleBites · Campus food rescue", page_icon=str(LOGO_PATH), layout="wide")
    init_db()
    apply_theme()
    st.session_state.setdefault("role", "Cafeteria staff / Donor")

    with st.sidebar:
        st.markdown(
            f'<div class="brand-row"><img class="brand-mark" src="{LOGO_DATA_URI}" '
            'alt="CycleBites food sharing logo"><div>'
            '<div class="brand-name">CycleBites</div>'
            '<div class="brand-caption">Campus food rescue</div></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="nav-label">Workspace</div>',
            unsafe_allow_html=True,
        )
        for label, button_text in (
            ("Cafeteria staff / Donor", "Share food"),
            ("Volunteer / Pantry", "Available pickups"),
            ("Map & routes", "Map & routes"),
            ("Admin / Teacher", "Impact dashboard"),
        ):
            if st.button(
                button_text,
                key=f"nav_{label}",
                type="primary" if st.session_state["role"] == label else "secondary",
                use_container_width=True,
            ):
                st.session_state["role"] = label
                st.rerun()
        st.write("")
        st.markdown(
            '<div class="side-foot">CycleBites pilot<br>Food shared is food valued.</div>',
            unsafe_allow_html=True,
        )

    role = st.session_state["role"]

    if role == "Cafeteria staff / Donor":
        donor_page()
    elif role == "Volunteer / Pantry":
        board_page()
    elif role == "Map & routes":
        map_page()
    else:
        dashboard_page()


if __name__ == "__main__":
    main()
