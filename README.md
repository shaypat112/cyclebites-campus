# CycleBites

A single-file Streamlit prototype for sharing campus surplus food.

The interface uses [streamlit-shadcn-ui](https://streamlit.io/components?category=all) for impact cards, listing badges, and status cards. Its current release requires Streamlit 1.60 or newer; `requirements.txt` installs a compatible version.

## Run locally

```bash
python3 -m pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit. Use the sidebar to switch between donor, volunteer/pantry, and admin views. SQLite data is stored in `cyclebites.db` beside the app and is excluded from version control.

## Prototype behavior

- Listings have a pickup deadline and disappear from the active board when it passes.
- The board refreshes every 15 seconds. Claiming is atomic, so only one claimant can reserve a listing.
- Admin totals count claimed portions. The weight estimate uses 0.75 lb per portion; the emissions estimate uses the [EPA WARM v16](https://www.epa.gov/system/files/documents/2023-12/warm_organic_materials_v16_dec.pdf) national-average food-waste landfilling factor. Both are estimates, and a claim is treated as a proxy for a completed pickup.
- Role switching is for demonstration and does not authenticate users.

## Commit history

This workspace does not permit creating `.git` inside the project directory. `cyclebites-history.bundle` contains the feature-sized commits and can be cloned into a standard Git repository:

```bash
git clone cyclebites-history.bundle CycleBites-repo
```
