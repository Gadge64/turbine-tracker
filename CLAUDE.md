# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bat
run.bat
```

This creates a `venv` if missing, installs dependencies from `requirements.txt`, and starts the Flask dev server at `http://127.0.0.1:5000`.

To run manually (after activating the venv):

```powershell
venv\Scripts\activate.bat
python app.py
```

Recovery mode (creates a `recovery_admin` / `TempReset123!` admin account for lockout situations):

```bat
run_recovery.bat
```

## Stack

- **Flask 3** + **Flask-SQLAlchemy 3** + **Werkzeug** — single-file backend (`app.py`)
- **SQLite** — database at `instance/wind.db`, created automatically on first run
- **Jinja2 templates** — all in `templates/`, extend `templates/base.html`
- **Chart.js** (CDN) — used in dashboard and compare views for line/pie charts
- No frontend build step; all CSS is inline in `base.html`

## Architecture

Everything lives in `app.py`. There are two SQLAlchemy models:

- `User` — holds login credentials, profile/contact info, turbine hardware details, and privacy flags (`share_phone_public`, `share_address_public`)
- `TurbineEntry` — one row per user per calendar month (unique constraint on `user_id + year + month`); stores raw meter readings (import/export start+end kWh), E-mon kWh, CO₂, tariff rate, and an optional manual `used_from_wind_kwh` override

Key computed properties on `TurbineEntry`:
- `import_total_kwh()` / `export_total_kwh()` — end minus start
- `used_from_wind_effective_kwh()` — uses the manual override if set, otherwise `e_mon - export_total`
- `house_total_wind_grid_kwh()` — `(e_mon - export) + import`
- `value_at_tariff()` — `e_mon * tariff_rate` (defaults to €0.195/kWh)
- `inverter_total_kwh` is auto-calculated from prior month's running total + current e_mon if not entered manually

### Schema migrations

SQLite doesn't support `ALTER TABLE … DROP COLUMN`, so schema changes are additive only. `ensure_user_schema_sqlite()` and `ensure_entry_schema_sqlite()` run at startup and `ALTER TABLE … ADD COLUMN` any missing columns — this is the migration mechanism. New columns must be added there.

### Auth

Session-cookie based. `login_required` and `admin_required` are decorator helpers. The first registered user is automatically made admin. `RECOVERY_ADMIN_USERNAME` / `RECOVERY_ADMIN_PASSWORD` env vars (set in `run_recovery.bat`) upsert an emergency admin account on startup.

### Backups

- A daily backup is created automatically on the first request of each day via `@app.before_request`
- Any logged-in user can download a manual backup via `/backup/download`
- Admins can browse and download all backups at `/admin/backups`
- Backups are written to `instance/backups/` using the SQLite backup API (safe for live databases)

## Routes summary

| Route | Auth | Purpose |
|---|---|---|
| `/` | — | Home / redirect to dashboard |
| `/register`, `/login`, `/logout` | — | Auth |
| `/dashboard` | user | Charts, year tables, pie breakdowns |
| `/add`, `/entry/<id>/edit`, `/entry/<id>/delete` | user | CRUD for monthly entries |
| `/profile`, `/profile/edit` | user | View/edit own profile |
| `/users`, `/users/<id>` | user | List all users; view another user's stats |
| `/compare` | user | Side-by-side multi-user chart comparison |
| `/export/csv` | user | Download own data as CSV |
| `/backup/download` | user | Download a live DB backup |
| `/admin/users`, `/admin/users/<id>/reset-password` | admin | User management |
| `/admin/backups`, `/admin/backups/<file>/download` | admin | Backup management |
