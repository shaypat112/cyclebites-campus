"""CycleBites: a small campus surplus food exchange."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st


DB_PATH = Path(__file__).with_name("cyclebites.db")
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
        :root { --pine: #174d3b; --mint: #eaf5ee; --ink: #17352a; }
        html, body, [class*="css"], [data-testid="stApp"] {
            font-family: 'DM Sans', sans-serif;
        }
        [data-testid="stApp"] { background: #f8faf8; color: var(--ink); }
        [data-testid="stSidebar"] { background: #f0f6f1; border-right: 1px solid #e0eae2; }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: #53695b; }
        .block-container { max-width: 1080px; padding-top: 2.4rem; padding-bottom: 4rem; }
        h1, h2, h3 { font-family: 'Manrope', sans-serif; letter-spacing: -0.045em; color: var(--ink); }
        h1 { font-weight: 800; }
        div.stButton > button[kind="primary"],
        div.stFormSubmitButton > button[kind="primary"] {
            background: var(--pine); border: 1px solid var(--pine);
            color: white; border-radius: 10px; font-weight: 700;
        }
        div.stButton > button[kind="primary"]:hover,
        div.stFormSubmitButton > button[kind="primary"]:hover {
            background: #0f3f2f; border-color: #0f3f2f;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            border-color: #dfeae2 !important; border-radius: 14px !important;
            background: #fff;
        }
        .eyebrow { color: #39805b; text-transform: uppercase; letter-spacing: .16em;
            font-size: .73rem; font-weight: 800; margin-bottom: .45rem; }
        .subtle { color: #66796d; font-size: 1rem; line-height: 1.6; }
        .brand { font: 800 1.38rem 'Manrope', sans-serif; letter-spacing: -.05em;
            color: #174d3b; margin: .4rem 0 .15rem; }
        .brand-dot { display: inline-block; width: 11px; height: 11px;
            border-radius: 50%; background: #65b781; margin-right: 8px; }
        .sidebar-note { padding: 1rem; border-radius: 12px; background: #e2f0e5;
            color: #345d45; font-size: .82rem; line-height: 1.55; }
        @media (max-width: 640px) {
            .block-container { padding: 1.3rem 1rem 3rem; }
            h1 { font-size: 2rem !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(eyebrow: str, title: str, description: str) -> None:
    st.markdown(f'<div class="eyebrow">{eyebrow}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<p class="subtle">{description}</p>', unsafe_allow_html=True)


def donor_page() -> None:
    page_header(
        "01 / Share surplus",
        "Good food deserves a next stop.",
        "Post safe, ready-to-share food so a campus volunteer or pantry can pick it up.",
    )
    st.write("")
    with st.container(border=True):
        st.subheader("New food listing")
        st.caption("All fields marked * are required. Listings disappear after the pickup deadline.")
        with st.form("new_listing", clear_on_submit=True):
            food_name = st.text_input("Food item name *", placeholder="e.g., Vegetable grain bowls", max_chars=100)
            left, right = st.columns(2)
            with left:
                portions = st.number_input("Quantity / portions *", min_value=1, max_value=10000, value=12, step=1)
            with right:
                location = st.text_input("Pickup location *", placeholder="e.g., North Dining Hall desk", max_chars=150)
            default_deadline = datetime.now().astimezone() + timedelta(hours=2)
            date_col, time_col = st.columns(2)
            with date_col:
                pickup_date = st.date_input("Pickup deadline date *", value=default_deadline.date(), min_value=datetime.now().date())
            with time_col:
                pickup_time = st.time_input("Pickup deadline time *", value=default_deadline.time().replace(second=0, microsecond=0))
            notes = st.text_area(
                "Notes",
                placeholder="Packaging, ingredients, allergens, pickup instructions…",
                max_chars=500,
                height=100,
            )
            submitted = st.form_submit_button("Publish listing", type="primary", use_container_width=True)

        if submitted:
            try:
                deadline = datetime.combine(pickup_date, pickup_time).astimezone()
                add_listing(food_name, int(portions), location, deadline, notes)
            except ValueError as error:
                st.error(str(error))
            else:
                st.success("Listing published. It is now visible on the live board.")


def board_page() -> None:
    page_header(
        "02 / Pick up food",
        "Fresh opportunities, right now.",
        "Browse available campus surplus and claim a pickup before its deadline.",
    )
    if notice := st.session_state.pop("claim_notice", None):
        if notice == "claimed":
            st.success("Food claimed. Please collect it before the listed deadline.")
        else:
            st.warning("That listing is no longer available. The board has been refreshed.")
    st.caption("The board refreshes automatically every 15 seconds.")
    live_board()


@st.fragment(run_every="15s")
def live_board() -> None:
    listings = active_listings()
    count_col, portions_col = st.columns(2)
    with count_col:
        st.metric("Available pickups", len(listings))
    with portions_col:
        st.metric("Portions ready", sum(row["portions"] for row in listings))
    st.divider()

    if not listings:
        with st.container(border=True):
            st.subheader("All caught up")
            st.write("There are no active pickups right now. Check back soon for new food.")
        return

    for listing in listings:
        deadline = datetime.fromisoformat(listing["expires_at"]).astimezone()
        with st.container(border=True):
            details, action = st.columns([3, 1], vertical_alignment="center")
            with details:
                st.subheader(listing["food_name"])
                st.write(f"**{listing['portions']} portions**  ·  📍 {listing['pickup_location']}")
                st.caption(f"Pickup by {deadline.strftime('%b %-d, %Y at %-I:%M %p')}")
                if listing["notes"]:
                    st.write(listing["notes"])
            with action:
                if st.button(
                    "Claim food", key=f"claim_{listing['id']}",
                    type="primary", use_container_width=True,
                ):
                    claimed = claim_listing(listing["id"])
                    st.session_state["claim_notice"] = "claimed" if claimed else "unavailable"
                    st.rerun()


def dashboard_page() -> None:
    page_header(
        "03 / Community impact",
        "A lighter footprint, together.",
        "A simple view of the food claimed through CycleBites and its estimated impact.",
    )
    totals = impact_totals()
    meals = int(totals["meals_shared"])
    pounds = meals * POUNDS_PER_PORTION
    emissions = pounds * KG_CO2E_PER_POUND

    first, second, third = st.columns(3)
    with first:
        with st.container(border=True):
            st.metric("Food diverted", f"{pounds:,.1f} lb")
            st.caption("Estimated from claimed portions")
    with second:
        with st.container(border=True):
            st.metric("Meals shared", f"{meals:,}")
            st.caption("One claimed portion = one meal")
    with third:
        with st.container(border=True):
            st.metric("Landfill emissions avoided", f"{emissions:,.1f} kg CO₂e")
            st.caption("Illustrative greenhouse gas estimate")

    st.write("")
    with st.container(border=True):
        st.subheader("Why this matters")
        st.write(
            "When edible food reaches people instead of a landfill, the campus reduces "
            "potential methane emissions and makes better use of the resources behind each meal."
        )
        st.caption(f"{totals['claimed_listings']:,} successful claims recorded so far.")

    st.write("")
    st.subheader("Recent pickups")
    claims = recent_claims()
    if not claims:
        st.caption("Claims will appear here as soon as the first pickup is reserved.")
    else:
        for claim in claims:
            claimed_at = datetime.fromisoformat(claim["claimed_at"]).astimezone()
            with st.container(border=True):
                label, when = st.columns([3, 1], vertical_alignment="center")
                with label:
                    st.write(f"**{claim['food_name']}** · {claim['portions']} portions")
                    st.caption(claim["pickup_location"])
                with when:
                    st.caption(claimed_at.strftime("%b %d · %I:%M %p"))

    st.write("")
    st.caption(
        "Method: 0.75 lb per portion is an app planning assumption. The emissions estimate "
        "uses 0.25 kg CO₂e per pound, derived from the U.S. EPA WARM v16 national-average "
        "food-waste landfilling factor (0.50 metric tons CO₂e per short ton). "
        "Claims are used as a proxy for completed pickups; actual outcomes may differ."
    )
    st.markdown(
        "[Read the EPA WARM food waste methodology]"
        "(https://www.epa.gov/system/files/documents/2023-12/warm_organic_materials_v16_dec.pdf)"
    )


def main() -> None:
    st.set_page_config(page_title="CycleBites · Campus food rescue", page_icon="🌿", layout="wide")
    init_db()
    apply_theme()

    with st.sidebar:
        st.markdown('<div class="brand"><span class="brand-dot"></span>CycleBites</div>', unsafe_allow_html=True)
        st.caption("Campus food rescue, made simple.")
        st.write("")
        role = st.radio(
            "Workspace",
            ["Cafeteria staff / Donor", "Volunteer / Pantry", "Admin / Teacher"],
            key="role",
        )
        st.write("")
        st.markdown(
            '<div class="sidebar-note">Small pickups make a real difference. '
            'Post early and collect before the listed deadline.</div>',
            unsafe_allow_html=True,
        )

    if role == "Cafeteria staff / Donor":
        donor_page()
    elif role == "Volunteer / Pantry":
        board_page()
    else:
        dashboard_page()


if __name__ == "__main__":
    main()
