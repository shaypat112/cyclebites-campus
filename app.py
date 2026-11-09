"""CycleBites: a small campus surplus food exchange."""

from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

import streamlit as st

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
                expires_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available', 'claimed')),
                created_at TEXT NOT NULL,
                claimed_at TEXT
            )
            """
        )


def add_listing(
    food_name: str,
    portions: int,
    pickup_location: str,
    expires_at: datetime,
    notes: str,
) -> int:
    if not food_name.strip() or not pickup_location.strip():
        raise ValueError("Food name and pickup location are required.")
    if portions < 1:
        raise ValueError("Portions must be greater than zero.")
    if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= utc_now():
        raise ValueError("Pickup deadline must be in the future.")

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO listings
                (food_name, portions, pickup_location, expires_at, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                food_name.strip(),
                portions,
                pickup_location.strip(),
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
            with st.form("new_listing", clear_on_submit=True):
                food_name = st.text_input("Food item name *", placeholder="e.g. Vegetable grain bowls", max_chars=100)
                left, right = st.columns(2)
                with left:
                    portions = st.number_input("Portions available *", min_value=1, max_value=10000, value=12, step=1)
                with right:
                    location = st.text_input("Pickup location *", placeholder="e.g. North Dining Hall", max_chars=150)
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
                    add_listing(food_name, int(portions), location, deadline, notes)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Pickup published. It is visible on the live board.")
    with side_area:
        metric_card("Live pickups", len(active_listings()), "Currently available on campus", "donor_live")
        st.write("")
        st.markdown(
            '<div class="quiet-callout"><strong>Before you publish</strong><br>'
            'Share only food that is safe to eat. Add allergen details in the notes, '
            'and choose a realistic collection deadline.</div>',
            unsafe_allow_html=True,
        )


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
    else:
        dashboard_page()


if __name__ == "__main__":
    main()
