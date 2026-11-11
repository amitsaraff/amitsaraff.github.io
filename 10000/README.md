# A year on foot

A buildless static dashboard. Open `index.html` directly, or serve the repository
with `python3 -m http.server 8000` and visit `/10000/`.

- `index.html`: page structure and content.
- `styles.css`: layout, typography, and responsive styles.
- `steps.js`: generated CSV data loaded before the app.
- `app.js`: statistics, charts, calendar, and Forest light/dark theme.

`../steps.csv` is the canonical data source. Run `../update_steps.py` to merge
Garmin records into it and regenerate the CSV block in `steps.js`. The updater
preserves historical records that exist only in the dashboard data. It does not
rewrite the page, application, or styles. Avoid formatting the generated CSV
block: its header and template-literal format are used by the updater.

The scripts use ordered `defer` loading and require no fetch, modules, or build
step, so the dashboard also works from a local file URL.

Updater tests (no Garmin login or network calls):

```sh
uv run --with pytest python -m pytest test_update_steps.py -q
```

Run this command from the repository root.
