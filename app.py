# This is the main Python file for the Turbine Tracker web app.
# Flask is a web "framework" — a toolkit that handles the boring parts of
# building a website (receiving requests, sending responses, sessions, etc.)
# so you can focus on your actual application logic.
#
# This single file contains:
#   - The database models (User, TurbineEntry, MaintenanceLog)
#   - All the web routes (each URL the site responds to)
#   - Helper functions used by those routes
#
# When a user visits a URL like /dashboard, Flask finds the matching @app.route
# decorator below, runs the Python function, and sends the result back as a webpage.

from __future__ import annotations  # Allows type hints to reference classes defined later in the file

# Standard Python libraries — these come built into Python, no installation needed
import csv             # Read/write comma-separated value files
import datetime        # Work with dates and times
import os              # Interact with the operating system (file paths, environment variables)
import shutil          # High-level file operations (copy, move)
from functools import wraps   # Used when creating decorator functions (login_required, admin_required)
from io import StringIO       # Treat a string as if it were a file — used for CSV export
from pathlib import Path      # Modern way to work with file system paths
from typing import Any, Dict, List, Optional, Tuple  # Type hints that make the code easier to read

# Flask — the web framework
from flask import (
    Flask,           # The main application class
    Response,        # Used to build custom HTTP responses (e.g. CSV downloads)
    abort,           # Immediately stop and return an error page (403, 404, etc.)
    flash,           # Store a one-time message to show the user on the next page
    redirect,        # Tell the browser to go to a different URL
    render_template, # Load an HTML template file and fill in the variables
    request,         # The incoming HTTP request — contains form data, URL args, files
    send_file,       # Send a file to the browser (for downloads)
    session,         # A dictionary that persists between requests for one browser session
    url_for,         # Build a URL from a route function name (avoids hard-coding URLs)
)
from flask_sqlalchemy import SQLAlchemy   # Connects Flask to a database using Python objects
from flask_wtf.csrf import CSRFProtect   # Protects forms against Cross-Site Request Forgery attacks
from sqlalchemy import func, text        # SQL functions (SUM, COUNT) and raw SQL queries
from werkzeug.security import check_password_hash, generate_password_hash  # Secure password hashing

# ── App setup ──────────────────────────────────────────────────────────────────
# Create the Flask application object. instance_relative_config=True tells Flask
# to look for the database and config files in the "instance/" folder (not the project root),
# which is good practice because instance/ is ignored by git.
app = Flask(__name__, instance_relative_config=True)

# SECRET_KEY is used to sign session cookies so they cannot be tampered with.
# In production this should be a long random string set as an environment variable.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-key"

# Tell SQLAlchemy where the database file lives.
# sqlite:/// means a local file-based database — no separate database server needed.
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, "wind.db")

# Disable a SQLAlchemy feature that tracks every change to objects — it slows things
# down and we don't need it.
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Make sure the instance/ folder exists before we try to use it.
os.makedirs(app.instance_path, exist_ok=True)

# Create the database object that we'll use throughout the app to query and save data.
db = SQLAlchemy(app)

# Enable CSRF protection — this automatically adds a hidden token to every form
# and rejects submissions that don't include it, preventing a common web attack.
csrf = CSRFProtect(app)


# ── Database models ────────────────────────────────────────────────────────────
# A "model" is a Python class that maps to a table in the database.
# Each attribute defined with db.Column() becomes a column in that table.
# SQLAlchemy automatically creates the SQL table from these class definitions.

class User(db.Model):
    # Each user gets a unique integer ID assigned automatically by the database.
    id = db.Column(db.Integer, primary_key=True)

    # Login credentials — username must be unique across all users.
    # We never store the actual password, only a cryptographic hash of it.
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Integer, nullable=False, default=0)  # 1 = admin, 0 = regular user

    # Personal contact information
    full_name = db.Column(db.String(200), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)

    # Postal address fields — split into parts so they can be searched/displayed separately
    address_line1 = db.Column(db.String(200), nullable=True)
    address_line2 = db.Column(db.String(200), nullable=True)
    town_city = db.Column(db.String(100), nullable=True)
    county = db.Column(db.String(100), nullable=True)
    eircode = db.Column(db.String(20), nullable=True)

    # MPRN = Meter Point Reference Number — the unique identifier for an electricity meter in Ireland
    mprn = db.Column(db.String(20), nullable=True)

    # Privacy settings — users can choose whether to show their phone/address to other members
    share_phone_public = db.Column(db.Integer, nullable=False, default=0)    # 1 = visible, 0 = hidden
    share_address_public = db.Column(db.Integer, nullable=False, default=0)  # 1 = visible, 0 = hidden

    # When set, this account is hidden from all charts, comparisons, leaderboards, and community totals.
    # Use for admin/test accounts that should not skew group analytics.
    exclude_from_analytics = db.Column(db.Integer, nullable=False, default=0)

    # Stores the plain-text password only when an admin creates or resets it.
    # This is intentionally stored so admins can print a credentials sheet for members.
    # IMPORTANT: this field is cleared to None if the user changes their own password,
    # because at that point we no longer know what their password is.
    temp_password = db.Column(db.String(200), nullable=True)

    # Wind turbine hardware details
    turbine_model = db.Column(db.String(200), nullable=True)
    turbine_capacity_kw = db.Column(db.Float, nullable=True)      # How many kilowatts the turbine can generate
    turbine_size_notes = db.Column(db.String(200), nullable=True)
    inverter_model = db.Column(db.String(200), nullable=True)     # Inverter converts DC to AC electricity
    inverter_model_2 = db.Column(db.String(200), nullable=True)   # Second inverter for dual-inverter setups
    install_date = db.Column(db.Date, nullable=True)              # When the turbine was installed
    system_notes = db.Column(db.String(500), nullable=True)       # Free-text notes about the system
    service_notes = db.Column(db.String(1500), nullable=True)     # Notes about past servicing

    # This creates a virtual link to all TurbineEntry rows belonging to this user.
    # Accessing user.entries gives you a list of all their monthly readings.
    # backref="user" means you can also do entry.user to get back to the User object.
    entries = db.relationship("TurbineEntry", backref="user", lazy=True)

    def share_phone(self) -> bool:
        # Returns True if this user has opted in to sharing their phone number publicly
        return bool(self.share_phone_public)

    def share_address(self) -> bool:
        # Returns True if this user has opted in to sharing their address publicly
        return bool(self.share_address_public)


# TurbineEntry represents one month's worth of meter readings for a single user.
# One user has many entries — one per calendar month they record data for.
class TurbineEntry(db.Model):
    # This constraint tells the database to refuse duplicate rows — you can't
    # have two entries for the same user in the same year and month.
    __table_args__ = (
        db.UniqueConstraint("user_id", "year", "month", name="uq_user_year_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # ForeignKey links this entry to a specific user in the user table
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Which month this entry covers
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)   # 1–12
    date = db.Column(db.Date, nullable=False)        # Always set to the 1st of the month

    # ── Meter readings ──
    # The import meter measures electricity bought from the grid.
    # The export meter measures electricity sold back to the grid.
    # Both are "cumulative" — they count up from zero, never reset.
    # We record the reading at the START and END of the month,
    # then subtract to get how much was used that month.
    import_start_kwh = db.Column(db.Float, nullable=True)  # Grid import meter at start of month
    import_end_kwh = db.Column(db.Float, nullable=True)    # Grid import meter at end of month
    export_start_kwh = db.Column(db.Float, nullable=True)  # Grid export meter at start of month
    export_end_kwh = db.Column(db.Float, nullable=True)    # Grid export meter at end of month

    # ── Inverter / E-mon readings ──
    # E-mon = energy monitor attached to the turbine — records how much the turbine generated THIS month.
    # Inverter total = the inverter's own running total since installation.
    # Some users have two inverters (dual-inverter setup), so there are _2 variants for each.
    inverter_total_kwh = db.Column(db.Float, nullable=True)   # Inverter 1 lifetime running total
    inverter_total_kwh_2 = db.Column(db.Float, nullable=True) # Inverter 2 lifetime running total
    e_mon_kwh = db.Column(db.Float, nullable=True)            # E-mon 1 generated this month
    e_mon_kwh_2 = db.Column(db.Float, nullable=True)          # E-mon 2 generated this month (dual only)

    co2_kg = db.Column(db.Float, nullable=True)             # CO₂ offset in kilograms (optional)
    used_from_wind_kwh = db.Column(db.Float, nullable=True) # Manual override for wind used in house
    tariff_rate = db.Column(db.Float, nullable=True)        # Electricity price in €/kWh this month
    notes = db.Column(db.String(1000), nullable=True)       # Free-text notes

    # ── Computed properties ──
    # These are methods (functions) on the entry object that calculate derived values.
    # They are NOT stored in the database — they are re-calculated each time you call them.

    def import_total_kwh(self) -> Optional[float]:
        # How much electricity was imported from the grid this month (end reading minus start reading).
        # Returns None if either reading is missing.
        if self.import_start_kwh is None or self.import_end_kwh is None:
            return None
        return self.import_end_kwh - self.import_start_kwh

    def export_total_kwh(self) -> Optional[float]:
        # How much electricity was exported to the grid this month (end reading minus start reading).
        if self.export_start_kwh is None or self.export_end_kwh is None:
            return None
        return self.export_end_kwh - self.export_start_kwh

    def e_mon_safe(self) -> Optional[float]:
        # Returns E-mon 1 as a float, or None if not entered.
        # The "safe" suffix means it won't crash if the value is missing.
        return None if self.e_mon_kwh is None else float(self.e_mon_kwh)

    def e_mon_2_safe(self) -> Optional[float]:
        # Returns E-mon 2 as a float, or None if not entered.
        return None if self.e_mon_kwh_2 is None else float(self.e_mon_kwh_2)

    def e_mon_total_kwh(self) -> Optional[float]:
        # Total generation from both E-mons combined.
        # If neither is entered, returns None (no data).
        # If only one is entered, that one counts as the total.
        e1, e2 = self.e_mon_safe(), self.e_mon_2_safe()
        if e1 is None and e2 is None:
            return None
        return (e1 or 0.0) + (e2 or 0.0)

    def inverter_total_combined(self) -> Optional[float]:
        # Sum of both inverter running totals.
        v1 = None if self.inverter_total_kwh is None else float(self.inverter_total_kwh)
        v2 = None if self.inverter_total_kwh_2 is None else float(self.inverter_total_kwh_2)
        if v1 is None and v2 is None:
            return None
        return (v1 or 0.0) + (v2 or 0.0)

    def co2_safe(self) -> Optional[float]:
        # Returns CO₂ as a float, or None if not entered.
        return None if self.co2_kg is None else float(self.co2_kg)

    def tariff_safe(self) -> float:
        # Returns the tariff rate, or a default of €0.195/kWh if not entered.
        return float(self.tariff_rate) if self.tariff_rate is not None else 0.195

    def used_from_wind_auto_kwh(self) -> Optional[float]:
        # Automatically estimates how much wind energy was used in the house.
        # Formula: generation - export = what stayed in the house.
        # If you generated 500 kWh and exported 200 kWh, you used 300 kWh from wind.
        e_mon = self.e_mon_total_kwh()
        export_total = self.export_total_kwh()
        if e_mon is None or export_total is None:
            return None
        return max(0.0, e_mon - export_total)  # max(0) ensures we never return a negative number

    def used_from_wind_effective_kwh(self) -> Optional[float]:
        # Returns wind used in the house — prefers a manual override if one was entered,
        # otherwise falls back to the automatic calculation above.
        if self.used_from_wind_kwh is not None:
            return max(0.0, float(self.used_from_wind_kwh))
        return self.used_from_wind_auto_kwh()

    def house_total_wind_grid_kwh(self) -> Optional[float]:
        # Total household electricity consumption = wind used in house + grid imported.
        # Formula: (generation - export) + import
        e_mon = self.e_mon_total_kwh()
        export_total = self.export_total_kwh()
        import_total = self.import_total_kwh()
        if e_mon is None or export_total is None or import_total is None:
            return None
        return (e_mon - export_total) + import_total

    def value_at_tariff(self) -> Optional[float]:
        # The monetary value of the electricity generated, in euros.
        # Formula: kWh generated × price per kWh
        e_mon = self.e_mon_total_kwh()
        if e_mon is None:
            return None
        return e_mon * self.tariff_safe()

    def self_sufficiency_pct(self) -> Optional[float]:
        # What percentage of the household's electricity came from the wind turbine?
        # Formula: (wind used in house / total house consumption) × 100
        # Capped at 100% — can't be more than fully self-sufficient.
        wind_used = self.used_from_wind_effective_kwh()
        total = self.house_total_wind_grid_kwh()
        if wind_used is None or total is None or total <= 0:
            return None
        return min(wind_used / total * 100.0, 100.0)


# MaintenanceLog records when the turbine was serviced, inspected, or repaired.
class MaintenanceLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)              # When the maintenance happened
    description = db.Column(db.String(500), nullable=False) # What was done
    cost = db.Column(db.Float, nullable=True)              # How much it cost (optional)
    next_service_date = db.Column(db.Date, nullable=True)  # When the next service is due
    notes = db.Column(db.String(500), nullable=True)       # Extra notes


# ── Database initialisation ────────────────────────────────────────────────────

def init_db() -> None:
    # This function is called once when the app starts (see the bottom of this file).
    # It creates the database tables if they don't already exist, then runs
    # any necessary schema updates and safety checks.
    with app.app_context():
        db.create_all()            # Create tables from the models above (safe to run if tables already exist)
        ensure_user_schema_sqlite()  # Add any new columns to the user table
        ensure_entry_schema_sqlite() # Add any new columns to the turbine_entry table
        ensure_at_least_one_admin()  # Make sure there's always at least one admin account
        ensure_recovery_admin()      # Create/update the emergency recovery account if configured
        create_daily_backup()        # Back up the database on first start of the day


def ensure_user_schema_sqlite() -> None:
    # SQLite (the database engine used here) does not support removing columns,
    # so schema changes can only ADD new columns. This function checks which columns
    # already exist in the user table, then adds any that are missing.
    # This is the migration system — whenever a new column is added to the User model,
    # it must also be listed here so existing databases get updated automatically.
    rows = db.session.execute(text("PRAGMA table_info(user)")).fetchall()
    existing = {r[1] for r in rows}  # Build a set of existing column names
    desired: Dict[str, str] = {
        "is_admin": "INTEGER",
        "full_name": "TEXT",
        "phone_number": "TEXT",
        "address_line1": "TEXT",
        "address_line2": "TEXT",
        "town_city": "TEXT",
        "county": "TEXT",
        "eircode": "TEXT",
        "mprn": "TEXT",
        "share_phone_public": "INTEGER",
        "share_address_public": "INTEGER",
        "exclude_from_analytics": "INTEGER",
        "temp_password": "TEXT",   # Last admin-set password in plain text (for credentials sheet)
        "turbine_model": "TEXT",
        "turbine_capacity_kw": "REAL",
        "turbine_size_notes": "TEXT",
        "inverter_model": "TEXT",
        "inverter_model_2": "TEXT",
        "install_date": "DATE",
        "system_notes": "TEXT",
        "service_notes": "TEXT",
    }
    for col, sql_type in desired.items():
        if col not in existing:
            # Run the SQL ALTER TABLE command to add the missing column
            db.session.execute(text(f"ALTER TABLE user ADD COLUMN {col} {sql_type}"))
    db.session.commit()  # Save the changes


def ensure_entry_schema_sqlite() -> None:
    # Same pattern as above — ensures the turbine_entry table has all required columns.
    rows = db.session.execute(text("PRAGMA table_info(turbine_entry)")).fetchall()
    existing = {r[1] for r in rows}
    desired: Dict[str, str] = {
        "notes": "TEXT",
        "used_from_wind_kwh": "REAL",
        "inverter_total_kwh": "REAL",
        "inverter_total_kwh_2": "REAL",
        "e_mon_kwh": "REAL",
        "e_mon_kwh_2": "REAL",
        "tariff_rate": "REAL",
        "co2_kg": "REAL",
    }
    for col, sql_type in desired.items():
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE turbine_entry ADD COLUMN {col} {sql_type}"))
    db.session.commit()


def ensure_recovery_admin() -> None:
    # Creates or updates an emergency admin account using credentials stored in
    # environment variables. This lets you regain access if you get locked out.
    # Set RECOVERY_ADMIN_USERNAME and RECOVERY_ADMIN_PASSWORD in run_recovery.bat.
    # If neither variable is set, this function does nothing.
    username = os.environ.get("RECOVERY_ADMIN_USERNAME")
    password = os.environ.get("RECOVERY_ADMIN_PASSWORD")
    if not username or not password:
        return

    user = User.query.filter_by(username=username).first()
    if not user:
        # Create the recovery account from scratch
        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            full_name="Recovery Admin",
            is_admin=1,
        )
        db.session.add(user)
    else:
        # Update the password in case it changed in the environment variable
        user.password_hash = generate_password_hash(password)
        user.is_admin = 1
    db.session.commit()


def ensure_at_least_one_admin() -> None:
    # Safety net — if the database somehow has no admin users, this promotes
    # the "gadge" account (if it exists) or the very first user registered.
    # This prevents a situation where nobody can access the admin panel.
    if User.query.filter_by(is_admin=1).first():
        return  # At least one admin already exists — nothing to do
    gadge = User.query.filter_by(username="gadge").first()
    if gadge:
        gadge.is_admin = 1
        db.session.commit()
        return
    first_user = User.query.order_by(User.id.asc()).first()
    if first_user:
        first_user.is_admin = 1
        db.session.commit()



# ── Backup helpers ─────────────────────────────────────────────────────────────

def database_path() -> Path:
    # Returns the full file path to the SQLite database file.
    return Path(app.instance_path) / "wind.db"


def backups_dir() -> Path:
    # Returns the path to the backups folder, creating it if it doesn't exist.
    path = Path(app.instance_path) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_filename(prefix: str = "wind_backup") -> str:
    # Generates a filename like "wind_backup_2025-06-01_14-30-00.db"
    # The timestamp ensures no two backups ever have the same name.
    now = datetime.datetime.now()
    return f"{prefix}_{now:%Y-%m-%d_%H-%M-%S}.db"


def create_database_backup(prefix: str = "wind_backup") -> Optional[Path]:
    # Creates a safe copy of the database file.
    # Returns the path to the newly created backup, or None if the database doesn't exist yet.
    source = database_path()
    if not source.exists():
        return None

    destination = backups_dir() / backup_filename(prefix)

    # Use SQLite's built-in backup API rather than a raw file copy.
    # This is safer because it works even while the database is actively being written to.
    connection = db.engine.raw_connection()
    try:
        source_sqlite = connection.connection
        import sqlite3
        backup_conn = sqlite3.connect(destination)
        try:
            source_sqlite.backup(backup_conn)  # Copies all data safely
        finally:
            backup_conn.close()
    finally:
        connection.close()

    return destination


def create_daily_backup() -> Optional[Path]:
    # Called on every request (see @app.before_request below), but only actually
    # creates a backup once per day — if today's backup already exists, it returns that one.
    source = database_path()
    if not source.exists():
        return None

    today = datetime.date.today().isoformat()  # e.g. "2025-06-01"
    existing_today = list(backups_dir().glob(f"daily_backup_{today}_*.db"))
    if existing_today:
        return existing_today[0]  # Already backed up today — skip

    return create_database_backup(prefix=f"daily_backup_{today}")


def get_current_user() -> Optional[User]:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


@app.context_processor
def inject_nav_user():
    return {"nav_user": get_current_user()}


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))
        if not user.is_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapped


def parse_float_field(form, name: str, label: str = "", required: bool = False) -> Optional[float]:
    raw = (form.get(name) or "").strip()
    if not raw:
        if required:
            flash(f"{label or name} is required.", "danger")
        return None
    try:
        return float(raw)
    except ValueError:
        flash(f"{label or name} must be a number.", "danger")
        return None


def parse_int_field(form, name: str, label: str = "", required: bool = False) -> Optional[int]:
    raw = (form.get(name) or "").strip()
    if not raw:
        if required:
            flash(f"{label or name} is required.", "danger")
        return None
    try:
        return int(raw)
    except ValueError:
        flash(f"{label or name} must be a whole number.", "danger")
        return None


def parse_date_yyyy_mm_dd(value: str) -> Optional[datetime.date]:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return datetime.date.fromisoformat(v)
    except ValueError:
        return None


def parse_checkbox(form, name: str) -> int:
    return 1 if (form.get(name) == "on") else 0


def user_display_name(u: User) -> str:
    return u.full_name or u.username


def prev_year_month(year: int, month: int) -> Tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def get_previous_month_entry(user_id: int, year: int, month: int) -> Optional[TurbineEntry]:
    py, pm = prev_year_month(year, month)
    return TurbineEntry.query.filter_by(user_id=user_id, year=py, month=pm).first()


def find_duplicate_entry(user_id: int, year: int, month: int, exclude_entry_id: Optional[int] = None) -> Optional[TurbineEntry]:
    q = TurbineEntry.query.filter_by(user_id=user_id, year=year, month=month)
    if exclude_entry_id is not None:
        q = q.filter(TurbineEntry.id != exclude_entry_id)
    return q.first()


def auto_inverter_total_kwh(user_id: int, year: int, month: int, e_mon_kwh: Optional[float]) -> Optional[float]:
    if e_mon_kwh is None:
        return None
    prev_entry = get_previous_month_entry(user_id, year, month)
    if prev_entry and prev_entry.inverter_total_kwh is not None:
        return float(prev_entry.inverter_total_kwh) + float(e_mon_kwh)
    return float(e_mon_kwh)


def auto_inverter_total_kwh_2(user_id: int, year: int, month: int, e_mon_kwh_2: Optional[float]) -> Optional[float]:
    if e_mon_kwh_2 is None:
        return None
    prev_entry = get_previous_month_entry(user_id, year, month)
    if prev_entry and prev_entry.inverter_total_kwh_2 is not None:
        return float(prev_entry.inverter_total_kwh_2) + float(e_mon_kwh_2)
    return float(e_mon_kwh_2)


def add_months(d: datetime.date, months: int) -> datetime.date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return datetime.date(y, m, 1)


def safe_sum(vals: List[Optional[float]]) -> float:
    return float(sum(v for v in vals if v is not None))


def compute_pie_kwh(entries: List[TurbineEntry]) -> Dict[str, float]:
    return {
        "import_total_kwh": safe_sum([e.import_total_kwh() for e in entries]),
        "export_total_kwh": safe_sum([e.export_total_kwh() for e in entries]),
        "used_from_wind_kwh": safe_sum([e.used_from_wind_effective_kwh() for e in entries]),
    }


MONTH_ABBRS: List[str] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def compute_missing_months(entries: List[TurbineEntry], install_date: Optional[datetime.date]) -> List[str]:
    today = datetime.date.today()
    current_month = datetime.date(today.year, today.month, 1)

    if not entries and not install_date:
        return []

    if entries:
        oldest = min(entries, key=lambda e: (e.year, e.month))
        start = datetime.date(oldest.year, oldest.month, 1)
    else:
        start = datetime.date(install_date.year, install_date.month, 1)

    if install_date:
        candidate = datetime.date(install_date.year, install_date.month, 1)
        if candidate < start:
            start = candidate

    entry_months = {(e.year, e.month) for e in entries}
    missing = []
    cur = start
    while cur <= current_month:
        if (cur.year, cur.month) not in entry_months:
            missing.append(f"{MONTH_ABBRS[cur.month - 1]} {cur.year}")
        cur = add_months(cur, 1)
    return missing


def compute_entry_warnings(entries: List[TurbineEntry]) -> Dict[int, List[str]]:
    warnings: Dict[int, List[str]] = {}

    for e in entries:
        w: List[str] = []
        e_mon = e.e_mon_total_kwh()
        export = e.export_total_kwh()
        imp = e.import_total_kwh()

        if e_mon is not None and e_mon < 0:
            w.append("Negative E-mon")
        if export is not None and export < 0:
            w.append("Negative export")
        if imp is not None and imp < 0:
            w.append("Negative import")
        if e_mon is not None and export is not None and e_mon >= 0 and export > e_mon:
            w.append("Export > generation")
        if w:
            warnings[e.id] = w

    sorted_entries = sorted(entries, key=lambda e: (e.year, e.month))
    for i, e in enumerate(sorted_entries[1:], 1):
        prev = sorted_entries[i - 1]
        py, pm = prev_year_month(e.year, e.month)
        if prev.year == py and prev.month == pm:
            curr_mon = e.e_mon_total_kwh()
            prev_mon = prev.e_mon_total_kwh()
            if curr_mon is not None and prev_mon is not None and prev_mon > 0 and curr_mon > prev_mon * 4:
                warnings.setdefault(e.id, []).append("E-mon 4× prior month")

    return warnings


@app.before_request
def backup_once_per_day_before_request() -> None:
    create_daily_backup()


@app.route("/")
def home():
    if get_current_user():
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("register.html")

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return render_template("register.html")

        is_first = User.query.count() == 0
        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=1 if is_first else 0,
            full_name=(request.form.get("full_name") or "").strip() or None,
        )
        db.session.add(user)
        db.session.commit()

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        session["user_id"] = user.id
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    entries = TurbineEntry.query.filter_by(user_id=user.id).order_by(TurbineEntry.year.asc(), TurbineEntry.month.asc()).all()

    labels = [e.date.isoformat() for e in entries]

    def series(vals):
        return [None if v is None else float(v) for v in vals]

    chart_data = {
        "E-mon (kWh)": series([e.e_mon_total_kwh() for e in entries]),
        "Import total (kWh)": series([e.import_total_kwh() for e in entries]),
        "Export total (kWh)": series([e.export_total_kwh() for e in entries]),
        "Used from wind (kWh)": series([e.used_from_wind_effective_kwh() for e in entries]),
        "Self-sufficiency (%)": series([e.self_sufficiency_pct() for e in entries]),
        "Inverter total (kWh)": series([e.inverter_total_combined() for e in entries]),
        "House total (Wind & Grid)": series([e.house_total_wind_grid_kwh() for e in entries]),
        "Value @ tariff": series([e.value_at_tariff() for e in entries]),
        "CO₂ (kg)": series([e.co2_safe() for e in entries]),
    }

    today = datetime.date.today()
    year_start = datetime.date(today.year, 1, 1)
    current_month_start = datetime.date(today.year, today.month, 1)
    start_12m = add_months(current_month_start, -11)

    pie_ytd_kwh = compute_pie_kwh([e for e in entries if year_start <= e.date <= today])
    pie_12m_kwh = compute_pie_kwh([e for e in entries if start_12m <= e.date <= today])
    pie_all_kwh = compute_pie_kwh(entries)

    years = sorted({e.year for e in entries})
    year_tables = []
    for year in years:
        year_entries = [e for e in entries if e.year == year]

        def month_values(fn):
            out = []
            for m in range(1, 13):
                m_entries = [e for e in year_entries if e.month == m]
                out.append(safe_sum([fn(e) for e in m_entries]))
            return out

        fields = [
            {"label": "Import total (kWh)", "values": month_values(lambda e: e.import_total_kwh())},
            {"label": "Export total (kWh)", "values": month_values(lambda e: e.export_total_kwh())},
            {"label": "Used from wind (kWh)", "values": month_values(lambda e: e.used_from_wind_effective_kwh())},
            {"label": "E-mon (kWh)", "values": month_values(lambda e: e.e_mon_total_kwh())},
            {"label": "Inverter total (kWh)", "values": month_values(lambda e: e.inverter_total_combined())},
            {"label": "House total (Wind & Grid)", "values": month_values(lambda e: e.house_total_wind_grid_kwh())},
            {"label": "Value @ tariff", "values": month_values(lambda e: e.value_at_tariff())},
            {"label": "CO₂ (kg)", "values": month_values(lambda e: e.co2_safe())},
        ]
        for f in fields:
            f["ytd"] = sum(f["values"])
        year_tables.append({"year": year, "fields": fields})

    # Lifetime summary
    lifetime_e_mon = safe_sum([e.e_mon_total_kwh() for e in entries])
    lifetime_value = safe_sum([e.value_at_tariff() for e in entries])
    lifetime_co2 = safe_sum([e.co2_safe() for e in entries])
    lifetime_export = safe_sum([e.export_total_kwh() for e in entries])
    lifetime_import = safe_sum([e.import_total_kwh() for e in entries])

    _total_wind_used = safe_sum([e.used_from_wind_effective_kwh() for e in entries])
    _total_house = safe_sum([e.house_total_wind_grid_kwh() for e in entries])
    lifetime_self_sufficiency: Optional[float] = round(_total_wind_used / _total_house * 100, 1) if _total_house > 0 else None

    next_service = (
        MaintenanceLog.query
        .filter(MaintenanceLog.user_id == user.id,
                MaintenanceLog.next_service_date >= datetime.date.today())
        .order_by(MaintenanceLog.next_service_date.asc())
        .first()
    )

    if entries:
        oldest = min(entries, key=lambda e: (e.year, e.month))
        since_date: Optional[str] = f"{MONTH_ABBRS[oldest.month - 1]} {oldest.year}"
    elif user.install_date:
        since_date = user.install_date.strftime("%b %Y")
    else:
        since_date = None

    missing_months = compute_missing_months(entries, user.install_date)
    entry_warnings = compute_entry_warnings(entries)

    yoy_metric_fns = [
        ("E-mon (kWh)", lambda e: e.e_mon_total_kwh()),
        ("Import (kWh)", lambda e: e.import_total_kwh()),
        ("Export (kWh)", lambda e: e.export_total_kwh()),
        ("Used from wind (kWh)", lambda e: e.used_from_wind_effective_kwh()),
        ("Value (€)", lambda e: e.value_at_tariff()),
    ]
    yoy_datasets: Dict[str, Any] = {}
    for metric_name, fn in yoy_metric_fns:
        yoy_datasets[metric_name] = {}
        for yr in years:
            by_month = {e.month: e for e in entries if e.year == yr}
            monthly = []
            for m in range(1, 13):
                ent = by_month.get(m)
                val = fn(ent) if ent is not None else None
                monthly.append(float(val) if val is not None else None)
            yoy_datasets[metric_name][str(yr)] = monthly

    return render_template(
        "dashboard.html",
        user=user,
        entries=entries,
        labels=labels,
        chart_data=chart_data,
        total_e_mon=safe_sum([e.e_mon_safe() for e in entries]),
        entry_count=len(entries),
        year_tables=year_tables,
        pie_ytd_kwh=pie_ytd_kwh,
        pie_12m_kwh=pie_12m_kwh,
        pie_all_kwh=pie_all_kwh,
        pie_last12_start=start_12m.isoformat(),
        lifetime_e_mon=lifetime_e_mon,
        lifetime_value=lifetime_value,
        lifetime_co2=lifetime_co2,
        lifetime_export=lifetime_export,
        lifetime_import=lifetime_import,
        since_date=since_date,
        missing_months=missing_months,
        entry_warnings=entry_warnings,
        years=years,
        yoy_datasets=yoy_datasets,
        yoy_metric_labels=[m[0] for m in yoy_metric_fns],
        lifetime_self_sufficiency=lifetime_self_sufficiency,
        next_service=next_service,
    )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add_entry():
    user = get_current_user()

    if request.method == "GET":
        today = datetime.date.today()
        year = parse_int_field(request.args, "year") or today.year
        month = parse_int_field(request.args, "month") or today.month

        dup = find_duplicate_entry(user.id, year, month)
        if dup:
            flash(f"Entry already exists for {year}-{month:02d}. Opening it for editing.", "info")
            return redirect(url_for("edit_entry", entry_id=dup.id))

        prev_entry = get_previous_month_entry(user.id, year, month)
        return render_template(
            "add_entry.html",
            default_year=year,
            default_month=month,
            default_import_start=prev_entry.import_end_kwh if prev_entry and prev_entry.import_end_kwh is not None else None,
            default_export_start=prev_entry.export_end_kwh if prev_entry and prev_entry.export_end_kwh is not None else None,
            default_tariff=prev_entry.tariff_safe() if prev_entry else 0.195,
            default_inverter_total=prev_entry.inverter_total_kwh if prev_entry and prev_entry.inverter_total_kwh is not None else None,
            default_inverter_total_2=prev_entry.inverter_total_kwh_2 if prev_entry and prev_entry.inverter_total_kwh_2 is not None else None,
        )

    year = parse_int_field(request.form, "year", "Year", required=True)
    month = parse_int_field(request.form, "month", "Month", required=True)
    if year is None or month is None or not (1 <= month <= 12):
        flash("Valid year and month are required.", "danger")
        return redirect(url_for("add_entry"))

    if find_duplicate_entry(user.id, year, month):
        flash(f"Entry already exists for {year}-{month:02d}.", "danger")
        return redirect(url_for("dashboard"))

    e_mon = parse_float_field(request.form, "e_mon_kwh", "E-mon 1")
    e_mon_2 = parse_float_field(request.form, "e_mon_kwh_2", "E-mon 2")
    inverter_manual = parse_float_field(request.form, "inverter_total_kwh", "Inverter 1 total")
    inverter_manual_2 = parse_float_field(request.form, "inverter_total_kwh_2", "Inverter 2 total")
    entry = TurbineEntry(
        user_id=user.id,
        year=year,
        month=month,
        date=datetime.date(year, month, 1),
        import_start_kwh=parse_float_field(request.form, "import_start_kwh"),
        import_end_kwh=parse_float_field(request.form, "import_end_kwh"),
        export_start_kwh=parse_float_field(request.form, "export_start_kwh"),
        export_end_kwh=parse_float_field(request.form, "export_end_kwh"),
        used_from_wind_kwh=parse_float_field(request.form, "used_from_wind_kwh"),
        inverter_total_kwh=inverter_manual if inverter_manual is not None else auto_inverter_total_kwh(user.id, year, month, e_mon),
        inverter_total_kwh_2=inverter_manual_2 if inverter_manual_2 is not None else auto_inverter_total_kwh_2(user.id, year, month, e_mon_2),
        e_mon_kwh=e_mon,
        e_mon_kwh_2=e_mon_2,
        tariff_rate=parse_float_field(request.form, "tariff_rate"),
        co2_kg=parse_float_field(request.form, "co2_kg"),
        notes=(request.form.get("notes") or "").strip() or None,
    )
    db.session.add(entry)
    db.session.commit()
    flash("Monthly entry saved.", "success")
    return redirect(url_for("dashboard"))


@app.route("/entry/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id: int):
    user = get_current_user()
    entry = db.get_or_404(TurbineEntry, entry_id)
    if entry.user_id != user.id and not user.is_admin:
        abort(403)

    if request.method == "POST":
        year = parse_int_field(request.form, "year", "Year", required=True)
        month = parse_int_field(request.form, "month", "Month", required=True)
        if year is None or month is None or not (1 <= month <= 12):
            flash("Valid year and month are required.", "danger")
            return render_template("edit_entry.html", entry=entry)

        dup = find_duplicate_entry(user.id, year, month, exclude_entry_id=entry.id)
        if dup:
            flash("Another entry already exists for that month.", "danger")
            return render_template("edit_entry.html", entry=entry)

        e_mon = parse_float_field(request.form, "e_mon_kwh")
        e_mon_2 = parse_float_field(request.form, "e_mon_kwh_2")
        inverter_manual = parse_float_field(request.form, "inverter_total_kwh")
        inverter_manual_2 = parse_float_field(request.form, "inverter_total_kwh_2")

        entry.year = year
        entry.month = month
        entry.date = datetime.date(year, month, 1)
        entry.import_start_kwh = parse_float_field(request.form, "import_start_kwh")
        entry.import_end_kwh = parse_float_field(request.form, "import_end_kwh")
        entry.export_start_kwh = parse_float_field(request.form, "export_start_kwh")
        entry.export_end_kwh = parse_float_field(request.form, "export_end_kwh")
        entry.used_from_wind_kwh = parse_float_field(request.form, "used_from_wind_kwh")
        entry.inverter_total_kwh = inverter_manual if inverter_manual is not None else auto_inverter_total_kwh(user.id, year, month, e_mon)
        entry.inverter_total_kwh_2 = inverter_manual_2 if inverter_manual_2 is not None else auto_inverter_total_kwh_2(user.id, year, month, e_mon_2)
        entry.e_mon_kwh = e_mon
        entry.e_mon_kwh_2 = e_mon_2
        entry.tariff_rate = parse_float_field(request.form, "tariff_rate")
        entry.co2_kg = parse_float_field(request.form, "co2_kg")
        entry.notes = (request.form.get("notes") or "").strip() or None

        db.session.commit()
        flash("Monthly entry updated.", "success")
        return redirect(url_for("dashboard"))

    return render_template("edit_entry.html", entry=entry)


@app.route("/entry/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_entry(entry_id: int):
    user = get_current_user()
    entry = db.get_or_404(TurbineEntry, entry_id)
    if entry.user_id != user.id and not user.is_admin:
        abort(403)
    db.session.delete(entry)
    db.session.commit()
    flash("Entry deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", user=get_current_user())


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    user = get_current_user()
    if request.method == "POST":
        user.full_name = (request.form.get("full_name") or "").strip() or None
        user.phone_number = (request.form.get("phone_number") or "").strip() or None
        user.address_line1 = (request.form.get("address_line1") or "").strip() or None
        user.address_line2 = (request.form.get("address_line2") or "").strip() or None
        user.town_city = (request.form.get("town_city") or "").strip() or None
        user.county = (request.form.get("county") or "").strip() or None
        user.eircode = (request.form.get("eircode") or "").strip() or None
        user.mprn = (request.form.get("mprn") or "").strip() or None
        user.share_phone_public = parse_checkbox(request.form, "share_phone_public")
        user.share_address_public = parse_checkbox(request.form, "share_address_public")
        user.turbine_model = (request.form.get("turbine_model") or "").strip() or None
        user.turbine_capacity_kw = parse_float_field(request.form, "turbine_capacity_kw")
        user.turbine_size_notes = (request.form.get("turbine_size_notes") or "").strip() or None
        user.inverter_model = (request.form.get("inverter_model") or "").strip() or None
        user.inverter_model_2 = (request.form.get("inverter_model_2") or "").strip() or None
        user.install_date = parse_date_yyyy_mm_dd(request.form.get("install_date") or "")
        user.system_notes = (request.form.get("system_notes") or "").strip() or None
        user.service_notes = (request.form.get("service_notes") or "").strip() or None
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("edit_profile.html", user=user)


@app.route("/users")
@login_required
def users():
    viewer = get_current_user()
    stats = {
        row.user_id: row
        for row in db.session.query(
            TurbineEntry.user_id,
            func.count(TurbineEntry.id).label("entry_count"),
            func.sum(TurbineEntry.e_mon_kwh).label("total_e_mon"),
        ).group_by(TurbineEntry.user_id).all()
    }
    rows = []
    for u in User.query.filter(User.exclude_from_analytics != 1).order_by(User.username.asc()).all():
        s = stats.get(u.id)
        rows.append({
            "id": u.id,
            "display_name": user_display_name(u),
            "turbine_model": u.turbine_model,
            "turbine_capacity_kw": u.turbine_capacity_kw,
            "inverter_model": u.inverter_model,
            "eircode": u.eircode if ((viewer.id == u.id) or u.share_address()) else None,
            "entry_count": s.entry_count if s else 0,
            "total_e_mon": float(s.total_e_mon) if s and s.total_e_mon is not None else 0.0,
        })
    return render_template("users.html", rows=rows)


@app.route("/users/<int:user_id>")
@login_required
def user_detail(user_id: int):
    viewer = get_current_user()
    u = db.get_or_404(User, user_id)
    entries = TurbineEntry.query.filter_by(user_id=u.id).order_by(TurbineEntry.year.asc(), TurbineEntry.month.asc()).all()
    totals = {
        "entries": len(entries),
        "total_e_mon": safe_sum([e.e_mon_safe() for e in entries]),
        "total_import": safe_sum([e.import_total_kwh() for e in entries]),
        "total_export": safe_sum([e.export_total_kwh() for e in entries]),
        "total_used_from_wind": safe_sum([e.used_from_wind_effective_kwh() for e in entries]),
        "total_value": safe_sum([e.value_at_tariff() for e in entries]),
        "total_co2": safe_sum([e.co2_safe() for e in entries]),
    }
    return render_template(
        "user_detail.html",
        u=u,
        entries=entries,
        totals=totals,
        can_view_phone=(viewer.id == u.id) or u.share_phone(),
        can_view_address=(viewer.id == u.id) or u.share_address(),
        viewer_is_admin=bool(viewer.is_admin),
    )


@app.route("/compare")
@login_required
def compare():
    users_all = User.query.filter(User.exclude_from_analytics != 1).order_by(User.username.asc()).all()
    included_ids = {u.id for u in users_all}
    all_entries = TurbineEntry.query.filter(TurbineEntry.user_id.in_(included_ids)).order_by(TurbineEntry.year.asc(), TurbineEntry.month.asc()).all()
    dates_sorted = sorted({e.date for e in all_entries})
    date_labels = [d.isoformat() for d in dates_sorted]

    metric_defs = [
        ("E-mon (kWh)", lambda e: e.e_mon_safe()),
        ("Import total (kWh)", lambda e: e.import_total_kwh()),
        ("Export total (kWh)", lambda e: e.export_total_kwh()),
        ("Used from wind (kWh)", lambda e: e.used_from_wind_effective_kwh()),
        ("House total (Wind & Grid)", lambda e: e.house_total_wind_grid_kwh()),
        ("Value @ tariff", lambda e: e.value_at_tariff()),
        ("CO₂ (kg)", lambda e: e.co2_safe()),
    ]

    summaries = []
    datasets_by_metric = {label: [] for label, _ in metric_defs}

    for u in users_all:
        user_entries = [e for e in all_entries if e.user_id == u.id]
        summaries.append({
            "id": u.id,
            "display_name": user_display_name(u),
            "turbine_model": u.turbine_model,
            "turbine_capacity_kw": u.turbine_capacity_kw,
            "inverter_model": u.inverter_model,
            "total_e_mon": safe_sum([e.e_mon_safe() for e in user_entries]),
            "total_import": safe_sum([e.import_total_kwh() for e in user_entries]),
            "total_export": safe_sum([e.export_total_kwh() for e in user_entries]),
            "count": len(user_entries),
        })

        for label, fn in metric_defs:
            series = []
            for d in dates_sorted:
                entries_on_date = [e for e in user_entries if e.date == d]
                series.append(None if not entries_on_date else safe_sum([fn(e) for e in entries_on_date]))
            datasets_by_metric[label].append({"label": user_display_name(u), "data": series})

    return render_template(
        "compare.html",
        date_labels=date_labels,
        datasets_by_metric=datasets_by_metric,
        metric_labels=[m[0] for m in metric_defs],
        default_metric="E-mon (kWh)",
        summaries=summaries,
    )


@app.route("/export/csv")
@login_required
def export_csv():
    user = get_current_user()
    entries = TurbineEntry.query.filter_by(user_id=user.id).order_by(TurbineEntry.year.asc(), TurbineEntry.month.asc()).all()
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "year", "month", "import_start_kwh", "import_end_kwh", "import_total_kwh",
        "export_start_kwh", "export_end_kwh", "export_total_kwh",
        "e_mon_kwh", "e_mon_kwh_2",
        "used_from_wind_kwh_effective", "used_from_wind_kwh_override",
        "inverter_total_kwh", "inverter_total_kwh_2",
        "house_total_wind_grid_kwh", "tariff_rate",
        "value_at_tariff", "co2_kg", "notes",
    ])
    for e in entries:
        writer.writerow([
            e.year, e.month, e.import_start_kwh or "", e.import_end_kwh or "",
            e.import_total_kwh() if e.import_total_kwh() is not None else "",
            e.export_start_kwh or "", e.export_end_kwh or "",
            e.export_total_kwh() if e.export_total_kwh() is not None else "",
            e.e_mon_kwh or "", e.e_mon_kwh_2 or "",
            e.used_from_wind_effective_kwh() if e.used_from_wind_effective_kwh() is not None else "",
            e.used_from_wind_kwh or "",
            e.inverter_total_kwh or "", e.inverter_total_kwh_2 or "",
            e.house_total_wind_grid_kwh() if e.house_total_wind_grid_kwh() is not None else "",
            e.tariff_rate or "", e.value_at_tariff() if e.value_at_tariff() is not None else "",
            e.co2_kg or "", e.notes or "",
        ])
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{user.username}_monthly_data.csv"'},
    )


@app.route("/import/csv/template")
@login_required
def import_csv_template():
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "year", "month", "import_start_kwh", "import_end_kwh",
        "export_start_kwh", "export_end_kwh",
        "e_mon_kwh", "e_mon_kwh_2",
        "used_from_wind_kwh_override",
        "inverter_total_kwh", "inverter_total_kwh_2",
        "tariff_rate", "co2_kg", "notes",
    ])
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="import_template.csv"'},
    )


@app.route("/import/csv", methods=["GET", "POST"])
@login_required
def import_csv():
    user = get_current_user()

    all_users = User.query.order_by(User.username.asc()).all() if user.is_admin else None

    if request.method == "GET":
        return render_template("import_csv.html", all_users=all_users, selected_user_id=user.id)

    if user.is_admin:
        target_user_id = parse_int_field(request.form, "target_user_id") or user.id
        target_user = db.session.get(User, target_user_id) or user
    else:
        target_user = user

    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("No file selected.", "danger")
        return render_template("import_csv.html", all_users=all_users, selected_user_id=target_user.id)

    try:
        content = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("Could not read file — make sure it is UTF-8 encoded.", "danger")
        return render_template("import_csv.html", all_users=all_users, selected_user_id=target_user.id)

    reader = csv.DictReader(StringIO(content))
    imported = 0
    skipped_dup = 0
    errors: List[str] = []

    for i, row in enumerate(reader, start=2):
        if imported + skipped_dup >= 12:
            flash("Limit of 12 rows reached — remaining rows were ignored.", "warning")
            break

        try:
            year = int((row.get("year") or "").strip())
            month = int((row.get("month") or "").strip())
        except ValueError:
            errors.append(f"Row {i}: missing or invalid year/month.")
            continue

        if not (1 <= month <= 12):
            errors.append(f"Row {i}: month must be between 1 and 12.")
            continue

        if find_duplicate_entry(target_user.id, year, month):
            skipped_dup += 1
            continue

        def col(name: str) -> Optional[float]:
            v = (row.get(name) or "").strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None

        e_mon = col("e_mon_kwh")
        e_mon_2 = col("e_mon_kwh_2")
        inverter_manual = col("inverter_total_kwh")
        inverter_manual_2 = col("inverter_total_kwh_2")
        entry = TurbineEntry(
            user_id=target_user.id,
            year=year,
            month=month,
            date=datetime.date(year, month, 1),
            import_start_kwh=col("import_start_kwh"),
            import_end_kwh=col("import_end_kwh"),
            export_start_kwh=col("export_start_kwh"),
            export_end_kwh=col("export_end_kwh"),
            used_from_wind_kwh=col("used_from_wind_kwh_override") or col("used_from_wind_kwh"),
            inverter_total_kwh=inverter_manual if inverter_manual is not None else auto_inverter_total_kwh(target_user.id, year, month, e_mon),
            inverter_total_kwh_2=inverter_manual_2 if inverter_manual_2 is not None else auto_inverter_total_kwh_2(target_user.id, year, month, e_mon_2),
            e_mon_kwh_2=e_mon_2,
            e_mon_kwh=e_mon,
            tariff_rate=col("tariff_rate"),
            co2_kg=col("co2_kg"),
            notes=(row.get("notes") or "").strip() or None,
        )
        db.session.add(entry)
        imported += 1

    if imported:
        db.session.commit()

    parts = []
    if imported:
        parts.append(f"{imported} entr{'y' if imported == 1 else 'ies'} imported")
    if skipped_dup:
        parts.append(f"{skipped_dup} skipped (month already exists)")
    if parts:
        flash(", ".join(parts) + ".", "success")
    for e in errors:
        flash(e, "danger")

    return redirect(url_for("dashboard"))


@app.route("/backup/download")
@login_required
def download_database_backup():
    backup_path = create_database_backup(prefix="manual_backup")
    if backup_path is None:
        flash("No database found to back up.", "danger")
        return redirect(url_for("dashboard"))

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=backup_path.name,
        mimetype="application/octet-stream",
    )


@app.route("/admin/backups")
@admin_required
def admin_backups():
    files = sorted(backups_dir().glob("*.db"), reverse=True)
    return render_template("admin_backups.html", backup_files=files)


@app.route("/admin/backups/<path:filename>/download")
@admin_required
def admin_download_backup(filename: str):
    safe_name = Path(filename).name
    file_path = backups_dir() / safe_name
    if not file_path.exists():
        abort(404)
    return send_file(
        file_path,
        as_attachment=True,
        download_name=safe_name,
        mimetype="application/octet-stream",
    )


@app.route("/admin/users")
@admin_required
def admin_users():
    return render_template("admin_users.html", users=User.query.order_by(User.username.asc()).all())


@app.route("/admin/users/new", methods=["GET", "POST"])
@admin_required
def admin_new_user():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        full_name = (request.form.get("full_name") or "").strip() or None
        is_admin_flag = 1 if request.form.get("is_admin") == "on" else 0

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("admin_new_user.html")
        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return render_template("admin_new_user.html")

        new_user = User(
            username=username,
            password_hash=generate_password_hash(password),  # Hash the password before storing
            is_admin=is_admin_flag,
            full_name=full_name,
            temp_password=password,  # Also save the plain-text version for the credentials sheet
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f"User {username} created.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_new_user.html")


@app.route("/admin/users/<int:user_id>/reset-password", methods=["GET", "POST"])
@admin_required
def admin_reset_password(user_id: int):
    target_user = db.get_or_404(User, user_id)
    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("admin_reset_password.html", target_user=target_user)
        target_user.password_hash = generate_password_hash(new_password)  # Store the secure hash
        target_user.temp_password = new_password  # Also save plain text for the credentials sheet
        db.session.commit()
        flash(f"Password reset for {target_user.username}.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_reset_password.html", target_user=target_user)


@app.route("/admin/users/<int:user_id>/rename", methods=["GET", "POST"])
@admin_required
def admin_rename_user(user_id: int):
    target_user = db.get_or_404(User, user_id)
    if request.method == "POST":
        new_username = (request.form.get("username") or "").strip()
        if not new_username:
            flash("Username is required.", "danger")
            return render_template("admin_rename_user.html", target_user=target_user)
        if User.query.filter(User.username == new_username, User.id != user_id).first():
            flash("That username is already taken.", "danger")
            return render_template("admin_rename_user.html", target_user=target_user)
        target_user.username = new_username
        db.session.commit()
        flash(f"Username updated to {new_username}.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_rename_user.html", target_user=target_user)


@app.route("/admin/users/<int:user_id>/clone", methods=["GET", "POST"])
@admin_required
def admin_clone_user(user_id: int):
    source = db.get_or_404(User, user_id)
    if request.method == "POST":
        new_username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        full_name = (request.form.get("full_name") or "").strip() or None
        if not new_username or not password:
            flash("Username and password are required.", "danger")
            return render_template("admin_clone_user.html", source=source)
        if User.query.filter_by(username=new_username).first():
            flash("Username already exists.", "danger")
            return render_template("admin_clone_user.html", source=source)
        new_user = User(
            username=new_username,
            password_hash=generate_password_hash(password),  # Secure hash for login
            is_admin=0,
            full_name=full_name,
            temp_password=password,  # Plain-text copy for the credentials sheet
            turbine_model=source.turbine_model,
            turbine_capacity_kw=source.turbine_capacity_kw,
            turbine_size_notes=source.turbine_size_notes,
            inverter_model=source.inverter_model,
            install_date=source.install_date,
            system_notes=source.system_notes,
            mprn=source.mprn,
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f"User {new_username} created as a clone of {source.username}.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_clone_user.html", source=source)


@app.route("/admin/users/<int:user_id>/add-entry", methods=["GET", "POST"])
@admin_required
def admin_add_entry(user_id: int):
    target_user = db.get_or_404(User, user_id)

    if request.method == "GET":
        today = datetime.date.today()
        year = parse_int_field(request.args, "year") or today.year
        month = parse_int_field(request.args, "month") or today.month

        dup = find_duplicate_entry(target_user.id, year, month)
        if dup:
            flash(f"Entry already exists for {year}-{month:02d}. Opening it for editing.", "info")
            return redirect(url_for("edit_entry", entry_id=dup.id))

        prev_entry = get_previous_month_entry(target_user.id, year, month)
        return render_template(
            "admin_add_entry.html",
            target_user=target_user,
            default_year=year,
            default_month=month,
            default_import_start=prev_entry.import_end_kwh if prev_entry and prev_entry.import_end_kwh is not None else None,
            default_export_start=prev_entry.export_end_kwh if prev_entry and prev_entry.export_end_kwh is not None else None,
            default_tariff=prev_entry.tariff_safe() if prev_entry else 0.195,
            default_inverter_total=prev_entry.inverter_total_kwh if prev_entry and prev_entry.inverter_total_kwh is not None else None,
            default_inverter_total_2=prev_entry.inverter_total_kwh_2 if prev_entry and prev_entry.inverter_total_kwh_2 is not None else None,
        )

    year = parse_int_field(request.form, "year", "Year", required=True)
    month = parse_int_field(request.form, "month", "Month", required=True)
    if year is None or month is None or not (1 <= month <= 12):
        flash("Valid year and month are required.", "danger")
        return redirect(url_for("admin_add_entry", user_id=user_id))

    if find_duplicate_entry(target_user.id, year, month):
        flash(f"Entry already exists for {year}-{month:02d}.", "danger")
        return redirect(url_for("user_detail", user_id=user_id))

    e_mon = parse_float_field(request.form, "e_mon_kwh", "E-mon 1")
    e_mon_2 = parse_float_field(request.form, "e_mon_kwh_2", "E-mon 2")
    inverter_manual = parse_float_field(request.form, "inverter_total_kwh", "Inverter 1 total")
    inverter_manual_2 = parse_float_field(request.form, "inverter_total_kwh_2", "Inverter 2 total")
    entry = TurbineEntry(
        user_id=target_user.id,
        year=year,
        month=month,
        date=datetime.date(year, month, 1),
        import_start_kwh=parse_float_field(request.form, "import_start_kwh"),
        import_end_kwh=parse_float_field(request.form, "import_end_kwh"),
        export_start_kwh=parse_float_field(request.form, "export_start_kwh"),
        export_end_kwh=parse_float_field(request.form, "export_end_kwh"),
        used_from_wind_kwh=parse_float_field(request.form, "used_from_wind_kwh"),
        inverter_total_kwh=inverter_manual if inverter_manual is not None else auto_inverter_total_kwh(target_user.id, year, month, e_mon),
        inverter_total_kwh_2=inverter_manual_2 if inverter_manual_2 is not None else auto_inverter_total_kwh_2(target_user.id, year, month, e_mon_2),
        e_mon_kwh=e_mon,
        e_mon_kwh_2=e_mon_2,
        tariff_rate=parse_float_field(request.form, "tariff_rate"),
        co2_kg=parse_float_field(request.form, "co2_kg"),
        notes=(request.form.get("notes") or "").strip() or None,
    )
    db.session.add(entry)
    db.session.commit()
    flash(f"Entry saved for {user_display_name(target_user)}.", "success")
    return redirect(url_for("user_detail", user_id=user_id))


@app.route("/admin/users/batch", methods=["GET", "POST"])
@admin_required
def admin_batch_users():
    results = []
    raw_input = ""

    if request.method == "POST":
        raw_input = request.form.get("user_list") or ""
        lines = [l.strip() for l in raw_input.splitlines() if l.strip()]

        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            full_name = parts[0] if parts else ""
            if not full_name:
                continue

            turbine_model = (parts[1] if len(parts) > 1 else "").strip() or None
            capacity_raw = (parts[2] if len(parts) > 2 else "").strip()
            inverter_raw = (parts[3] if len(parts) > 3 else "single").strip().lower()

            capacity_kw: Optional[float] = None
            if capacity_raw:
                digits = "".join(c for c in capacity_raw if c.isdigit() or c == ".")
                try:
                    capacity_kw = float(digits) if digits else None
                except ValueError:
                    pass

            is_dual = "dual" in inverter_raw

            ascii_first = full_name.encode("ascii", "ignore").decode()
            words = ascii_first.split()
            first_word = "".join(c for c in words[0].lower() if c.isalnum()) if words else "user"
            if not first_word:
                first_word = "user"

            username = first_word
            counter = 2
            while User.query.filter_by(username=username).first():
                username = f"{first_word}{counter}"
                counter += 1

            password = f"{first_word}1"

            new_user = User(
                username=username,
                password_hash=generate_password_hash(password),  # Secure hash for login checks
                full_name=full_name,
                is_admin=0,
                turbine_model=turbine_model,
                turbine_capacity_kw=capacity_kw,
                turbine_size_notes="Dual inverter" if is_dual else None,
                temp_password=password,  # Plain-text copy for the credentials sheet
            )
            db.session.add(new_user)
            results.append({
                "full_name": full_name,
                "username": username,
                "password": password,
                "turbine_model": turbine_model or "—",
                "capacity_kw": f"{capacity_kw:.0f}" if capacity_kw else "—",
                "inverter": "Dual" if is_dual else "Single",
            })

        if results:
            db.session.commit()
            flash(f"{len(results)} user(s) created.", "success")

    return render_template("admin_batch_users.html", results=results, raw_input=raw_input)


@app.route("/admin/users/<int:user_id>/edit-profile", methods=["GET", "POST"])
@admin_required
def admin_edit_profile(user_id: int):
    u = User.query.get_or_404(user_id)
    if request.method == "POST":
        u.full_name = request.form.get("full_name") or None
        u.phone_number = request.form.get("phone_number") or None
        u.address_line1 = request.form.get("address_line1") or None
        u.address_line2 = request.form.get("address_line2") or None
        u.town_city = request.form.get("town_city") or None
        u.county = request.form.get("county") or None
        u.eircode = request.form.get("eircode") or None
        u.mprn = request.form.get("mprn") or None
        u.share_phone_public = 1 if request.form.get("share_phone_public") else 0
        u.share_address_public = 1 if request.form.get("share_address_public") else 0
        u.turbine_model = request.form.get("turbine_model") or None
        cap_raw = request.form.get("turbine_capacity_kw", "").strip()
        try:
            u.turbine_capacity_kw = float(cap_raw) if cap_raw else None
        except ValueError:
            u.turbine_capacity_kw = None
        u.turbine_size_notes = request.form.get("turbine_size_notes") or None
        u.inverter_model = request.form.get("inverter_model") or None
        u.inverter_model_2 = request.form.get("inverter_model_2") or None
        date_raw = request.form.get("install_date", "").strip()
        try:
            u.install_date = datetime.date.fromisoformat(date_raw) if date_raw else None
        except ValueError:
            u.install_date = None
        u.system_notes = request.form.get("system_notes") or None
        u.service_notes = request.form.get("service_notes") or None
        u.is_admin = 1 if request.form.get("is_admin") else 0
        u.exclude_from_analytics = 1 if request.form.get("exclude_from_analytics") else 0
        new_pw = request.form.get("new_password", "").strip()
        if new_pw:
            u.password_hash = generate_password_hash(new_pw)  # Store the secure hash
            u.temp_password = new_pw  # Save plain text so credentials sheet stays up to date
        db.session.commit()
        flash(f"Profile for {u.username} updated.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_edit_profile.html", user=u)


@app.route("/community")
@login_required
def community():
    all_users = User.query.filter(User.exclude_from_analytics != 1).order_by(User.username.asc()).all()
    included_ids = {u.id for u in all_users}
    all_entries = TurbineEntry.query.filter(TurbineEntry.user_id.in_(included_ids)).all()

    today = datetime.date.today()
    available_years = sorted({e.year for e in all_entries})
    selected_year = parse_int_field(request.args, "year") or today.year

    total_e_mon = safe_sum([e.e_mon_total_kwh() for e in all_entries])
    total_export = safe_sum([e.export_total_kwh() for e in all_entries])
    total_import = safe_sum([e.import_total_kwh() for e in all_entries])
    total_co2 = safe_sum([e.co2_safe() for e in all_entries])
    total_value = safe_sum([e.value_at_tariff() for e in all_entries])

    sel_entries = [e for e in all_entries if e.year == selected_year]
    sel_e_mon = safe_sum([e.e_mon_total_kwh() for e in sel_entries])
    sel_export = safe_sum([e.export_total_kwh() for e in sel_entries])
    sel_co2 = safe_sum([e.co2_safe() for e in sel_entries])
    sel_value = safe_sum([e.value_at_tariff() for e in sel_entries])

    user_stats = []
    for u in all_users:
        u_entries = [e for e in all_entries if e.user_id == u.id]
        u_sel = [e for e in u_entries if e.year == selected_year]
        user_stats.append({
            "id": u.id,
            "display_name": user_display_name(u),
            "turbine_model": u.turbine_model,
            "turbine_capacity_kw": u.turbine_capacity_kw,
            "install_date": u.install_date,
            "total_e_mon": safe_sum([e.e_mon_total_kwh() for e in u_entries]),
            "total_export": safe_sum([e.export_total_kwh() for e in u_entries]),
            "total_import": safe_sum([e.import_total_kwh() for e in u_entries]),
            "total_co2": safe_sum([e.co2_safe() for e in u_entries]),
            "total_value": safe_sum([e.value_at_tariff() for e in u_entries]),
            "sel_e_mon": safe_sum([e.e_mon_total_kwh() for e in u_sel]),
            "sel_export": safe_sum([e.export_total_kwh() for e in u_sel]),
            "sel_import": safe_sum([e.import_total_kwh() for e in u_sel]),
            "sel_co2": safe_sum([e.co2_safe() for e in u_sel]),
            "sel_value": safe_sum([e.value_at_tariff() for e in u_sel]),
            "entry_count": len(u_entries),
        })

    return render_template(
        "community.html",
        total_e_mon=total_e_mon,
        total_export=total_export,
        total_import=total_import,
        total_co2=total_co2,
        total_value=total_value,
        sel_e_mon=sel_e_mon,
        sel_export=sel_export,
        sel_co2=sel_co2,
        sel_value=sel_value,
        user_stats=user_stats,
        user_emon_labels=[s["display_name"] for s in user_stats],
        user_emon_values=[s["sel_e_mon"] for s in user_stats],
        available_years=available_years,
        selected_year=selected_year,
        current_year=today.year,
    )


@app.route("/report")
@login_required
def print_report():
    user = get_current_user()
    today = datetime.date.today()
    year = parse_int_field(request.args, "year") or today.year

    all_entries = TurbineEntry.query.filter_by(user_id=user.id).order_by(
        TurbineEntry.year.asc(), TurbineEntry.month.asc()
    ).all()
    year_entries = [e for e in all_entries if e.year == year]
    by_month = {e.month: e for e in year_entries}

    month_rows = []
    for m in range(1, 13):
        e = by_month.get(m)
        month_rows.append({
            "month_name": MONTH_ABBRS[m - 1],
            "e_mon": e.e_mon_total_kwh() if e else None,
            "import_total": e.import_total_kwh() if e else None,
            "export_total": e.export_total_kwh() if e else None,
            "used_from_wind": e.used_from_wind_effective_kwh() if e else None,
            "value": e.value_at_tariff() if e else None,
            "co2": e.co2_safe() if e else None,
            "notes": (e.notes or "") if e else "",
            "has_data": e is not None,
        })

    year_totals = {
        "e_mon": safe_sum([e.e_mon_total_kwh() for e in year_entries]),
        "import_total": safe_sum([e.import_total_kwh() for e in year_entries]),
        "export_total": safe_sum([e.export_total_kwh() for e in year_entries]),
        "used_from_wind": safe_sum([e.used_from_wind_effective_kwh() for e in year_entries]),
        "value": safe_sum([e.value_at_tariff() for e in year_entries]),
        "co2": safe_sum([e.co2_safe() for e in year_entries]),
    }

    lifetime_totals = {
        "e_mon": safe_sum([e.e_mon_total_kwh() for e in all_entries]),
        "export": safe_sum([e.export_total_kwh() for e in all_entries]),
        "value": safe_sum([e.value_at_tariff() for e in all_entries]),
        "co2": safe_sum([e.co2_safe() for e in all_entries]),
    }

    return render_template(
        "report.html",
        user=user,
        year=year,
        month_rows=month_rows,
        year_totals=year_totals,
        lifetime_totals=lifetime_totals,
        available_years=sorted({e.year for e in all_entries}),
        generated_on=today.isoformat(),
    )


@app.route("/form/monthly")
@login_required
def monthly_form_print():
    user = get_current_user()
    today = datetime.date.today()
    return render_template("monthly_form_print.html", user=user,
                           year=today.year, month=today.month,
                           month_abbrs=MONTH_ABBRS)


@app.route("/add/quick", methods=["GET", "POST"])
@login_required
def quick_entry():
    user = get_current_user()
    if request.method == "GET":
        today = datetime.date.today()
        year = parse_int_field(request.args, "year") or today.year
        month = parse_int_field(request.args, "month") or today.month
        dup = find_duplicate_entry(user.id, year, month)
        if dup:
            flash(f"Entry already exists for {year}-{month:02d}. Opening it for editing.", "info")
            return redirect(url_for("edit_entry", entry_id=dup.id))
        prev_entry = get_previous_month_entry(user.id, year, month)
        return render_template(
            "quick_entry.html",
            default_year=year,
            default_month=month,
            default_import_start=prev_entry.import_end_kwh if prev_entry and prev_entry.import_end_kwh is not None else None,
            default_export_start=prev_entry.export_end_kwh if prev_entry and prev_entry.export_end_kwh is not None else None,
            default_tariff=prev_entry.tariff_safe() if prev_entry else 0.195,
        )
    year = parse_int_field(request.form, "year", "Year", required=True)
    month = parse_int_field(request.form, "month", "Month", required=True)
    if year is None or month is None or not (1 <= month <= 12):
        flash("Valid year and month are required.", "danger")
        return redirect(url_for("quick_entry"))
    if find_duplicate_entry(user.id, year, month):
        flash(f"Entry already exists for {year}-{month:02d}.", "danger")
        return redirect(url_for("dashboard"))
    e_mon = parse_float_field(request.form, "e_mon_kwh", "E-mon 1")
    e_mon_2 = parse_float_field(request.form, "e_mon_kwh_2", "E-mon 2")
    entry = TurbineEntry(
        user_id=user.id,
        year=year,
        month=month,
        date=datetime.date(year, month, 1),
        import_start_kwh=parse_float_field(request.form, "import_start_kwh"),
        import_end_kwh=parse_float_field(request.form, "import_end_kwh"),
        export_start_kwh=parse_float_field(request.form, "export_start_kwh"),
        export_end_kwh=parse_float_field(request.form, "export_end_kwh"),
        e_mon_kwh=e_mon,
        e_mon_kwh_2=e_mon_2,
        tariff_rate=parse_float_field(request.form, "tariff_rate"),
        inverter_total_kwh=auto_inverter_total_kwh(user.id, year, month, e_mon),
        inverter_total_kwh_2=auto_inverter_total_kwh_2(user.id, year, month, e_mon_2),
    )
    db.session.add(entry)
    db.session.commit()
    flash("Entry saved. You can add more detail by editing it.", "success")
    return redirect(url_for("dashboard"))


@app.route("/entries/bulk-tariff", methods=["GET", "POST"])
@login_required
def bulk_tariff():
    user = get_current_user()
    entries = (TurbineEntry.query.filter_by(user_id=user.id)
               .order_by(TurbineEntry.year.asc(), TurbineEntry.month.asc()).all())
    if request.method == "POST":
        from_year = parse_int_field(request.form, "from_year", "From year", required=True)
        from_month = parse_int_field(request.form, "from_month", "From month", required=True)
        new_rate = parse_float_field(request.form, "tariff_rate", "Tariff rate", required=True)
        if from_year is None or from_month is None or new_rate is None:
            return render_template("bulk_tariff.html", entries=entries)
        if not (1 <= from_month <= 12):
            flash("Month must be between 1 and 12.", "danger")
            return render_template("bulk_tariff.html", entries=entries)
        from_date = datetime.date(from_year, from_month, 1)
        to_update = [e for e in entries if e.date >= from_date]
        for e in to_update:
            e.tariff_rate = new_rate
        db.session.commit()
        flash(f"Tariff updated to €{new_rate:.4f}/kWh on {len(to_update)} entr{'y' if len(to_update) == 1 else 'ies'} from {MONTH_ABBRS[from_month - 1]} {from_year} onwards.", "success")
        return redirect(url_for("dashboard"))
    return render_template("bulk_tariff.html", entries=entries)


@app.route("/leaderboard")
@login_required
def leaderboard():
    all_users = User.query.order_by(User.username.asc()).all()
    all_entries = TurbineEntry.query.all()
    today = datetime.date.today()
    year_start = datetime.date(today.year, 1, 1)

    rows = []
    for u in all_users:
        user_entries = [e for e in all_entries if e.user_id == u.id]
        ytd_entries = [e for e in user_entries if e.date >= year_start]

        ss_vals = [v for v in (e.self_sufficiency_pct() for e in ytd_entries) if v is not None]
        avg_ss: Optional[float] = round(sum(ss_vals) / len(ss_vals), 1) if ss_vals else None

        sorted_e = sorted(user_entries, key=lambda e: (e.year, e.month))
        streak = 0
        if sorted_e:
            exp_y, exp_m = sorted_e[-1].year, sorted_e[-1].month
            for e in reversed(sorted_e):
                if (e.year, e.month) == (exp_y, exp_m):
                    streak += 1
                    exp_y, exp_m = prev_year_month(exp_y, exp_m)
                else:
                    break

        rows.append({
            "display_name": user_display_name(u),
            "ytd_generated": safe_sum([e.e_mon_total_kwh() for e in ytd_entries]),
            "all_time_generated": safe_sum([e.e_mon_total_kwh() for e in user_entries]),
            "avg_self_sufficiency": avg_ss,
            "entry_streak": streak,
        })

    rows.sort(key=lambda r: r["ytd_generated"], reverse=True)
    return render_template("leaderboard.html", rows=rows, year=today.year)


@app.route("/maintenance")
@login_required
def maintenance():
    user = get_current_user()
    logs = (MaintenanceLog.query.filter_by(user_id=user.id)
            .order_by(MaintenanceLog.date.desc()).all())
    today = datetime.date.today()
    upcoming = [l for l in logs if l.next_service_date and l.next_service_date >= today]
    upcoming.sort(key=lambda l: l.next_service_date)
    return render_template("maintenance.html", logs=logs, upcoming=upcoming, today=today)


@app.route("/maintenance/add", methods=["GET", "POST"])
@login_required
def add_maintenance():
    user = get_current_user()
    if request.method == "POST":
        date_raw = (request.form.get("date") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not date_raw or not description:
            flash("Date and description are required.", "danger")
            return render_template("maintenance_form.html", log=None,
                                   action_url=url_for("add_maintenance"), title="Add maintenance entry")
        date_val = parse_date_yyyy_mm_dd(date_raw)
        if date_val is None:
            flash("Invalid date format.", "danger")
            return render_template("maintenance_form.html", log=None,
                                   action_url=url_for("add_maintenance"), title="Add maintenance entry")
        log = MaintenanceLog(
            user_id=user.id,
            date=date_val,
            description=description,
            cost=parse_float_field(request.form, "cost"),
            next_service_date=parse_date_yyyy_mm_dd(request.form.get("next_service_date") or ""),
            notes=(request.form.get("notes") or "").strip() or None,
        )
        db.session.add(log)
        db.session.commit()
        flash("Maintenance entry added.", "success")
        return redirect(url_for("maintenance"))
    return render_template("maintenance_form.html", log=None,
                           action_url=url_for("add_maintenance"), title="Add maintenance entry")


@app.route("/maintenance/<int:log_id>/edit", methods=["GET", "POST"])
@login_required
def edit_maintenance(log_id: int):
    user = get_current_user()
    log = db.get_or_404(MaintenanceLog, log_id)
    if log.user_id != user.id and not user.is_admin:
        abort(403)
    if request.method == "POST":
        date_raw = (request.form.get("date") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not date_raw or not description:
            flash("Date and description are required.", "danger")
            return render_template("maintenance_form.html", log=log,
                                   action_url=url_for("edit_maintenance", log_id=log_id), title="Edit maintenance entry")
        date_val = parse_date_yyyy_mm_dd(date_raw)
        if date_val is None:
            flash("Invalid date format.", "danger")
            return render_template("maintenance_form.html", log=log,
                                   action_url=url_for("edit_maintenance", log_id=log_id), title="Edit maintenance entry")
        log.date = date_val
        log.description = description
        log.cost = parse_float_field(request.form, "cost")
        log.next_service_date = parse_date_yyyy_mm_dd(request.form.get("next_service_date") or "")
        log.notes = (request.form.get("notes") or "").strip() or None
        db.session.commit()
        flash("Maintenance entry updated.", "success")
        return redirect(url_for("maintenance"))
    return render_template("maintenance_form.html", log=log,
                           action_url=url_for("edit_maintenance", log_id=log_id), title="Edit maintenance entry")


@app.route("/maintenance/<int:log_id>/delete", methods=["POST"])
@login_required
def delete_maintenance(log_id: int):
    user = get_current_user()
    log = db.get_or_404(MaintenanceLog, log_id)
    if log.user_id != user.id and not user.is_admin:
        abort(403)
    db.session.delete(log)
    db.session.commit()
    flash("Maintenance entry deleted.", "success")
    return redirect(url_for("maintenance"))


@app.route("/about")
@login_required
def about():
    # Simple page — just render the about template.
    # The template itself checks if the user is admin and shows extra sections if so.
    return render_template("about.html")


@app.route("/howto")
@login_required
def howto():
    # Same pattern — one template handles both member and admin views.
    return render_template("howto.html")


@app.route("/admin/credentials")
@admin_required  # Only admins can access this page
def admin_credentials():
    # Fetch all users, sorted alphabetically by username.
    # We include ALL users (even analytics-excluded ones like admin accounts)
    # because this sheet is for login purposes, not analytics.
    users = User.query.order_by(User.username.asc()).all()

    # Pass the list of users and today's date to the template.
    # The date appears in the footer of the printed sheet so you know when it was generated.
    generated_on = datetime.datetime.now().strftime("%d %b %Y %H:%M")
    return render_template("admin_credentials.html", users=users, now=generated_on)


@app.route("/deploy")
def deploy_webhook():
    # This is a hidden deployment route — it tells the live server to pull the
    # latest code from GitHub. It is called automatically by deploy.bat after
    # a git push, so you never need to visit it manually.
    #
    # It is protected by a secret token so that only our deploy script can
    # trigger it. Anyone without the token gets a 403 Forbidden response.
    import subprocess

    # Read the expected secret from an environment variable called DEPLOY_SECRET.
    # On PythonAnywhere this is set in the WSGI config file.
    # Locally it is not set, so this route simply won't work locally (which is fine).
    expected = os.environ.get("DEPLOY_SECRET", "")
    received = request.args.get("secret", "")

    # Reject the request if no secret is configured or it doesn't match
    if not expected or received != expected:
        abort(403)

    # Run "git pull" in the project directory to download the latest code from GitHub.
    # capture_output=True captures any error messages so we can return them.
    result = subprocess.run(
        ["git", "pull"],
        cwd="/home/gadge64/turbine-tracker",  # The project folder on PythonAnywhere
        capture_output=True,
        text=True,
    )

    # Return the output of git pull so we can see in deploy.bat whether it worked
    return f"git pull exited {result.returncode}\n{result.stdout}\n{result.stderr}", 200


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
