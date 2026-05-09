from __future__ import annotations

import csv
import datetime
import os
import shutil
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, text
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, "wind.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

os.makedirs(app.instance_path, exist_ok=True)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Integer, nullable=False, default=0)

    full_name = db.Column(db.String(200), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)

    address_line1 = db.Column(db.String(200), nullable=True)
    address_line2 = db.Column(db.String(200), nullable=True)
    town_city = db.Column(db.String(100), nullable=True)
    county = db.Column(db.String(100), nullable=True)
    eircode = db.Column(db.String(20), nullable=True)

    mprn = db.Column(db.String(20), nullable=True)

    share_phone_public = db.Column(db.Integer, nullable=False, default=0)
    share_address_public = db.Column(db.Integer, nullable=False, default=0)

    turbine_model = db.Column(db.String(200), nullable=True)
    turbine_capacity_kw = db.Column(db.Float, nullable=True)
    turbine_size_notes = db.Column(db.String(200), nullable=True)
    inverter_model = db.Column(db.String(200), nullable=True)
    inverter_model_2 = db.Column(db.String(200), nullable=True)
    install_date = db.Column(db.Date, nullable=True)
    system_notes = db.Column(db.String(500), nullable=True)
    service_notes = db.Column(db.String(1500), nullable=True)

    entries = db.relationship("TurbineEntry", backref="user", lazy=True)

    def share_phone(self) -> bool:
        return bool(self.share_phone_public)

    def share_address(self) -> bool:
        return bool(self.share_address_public)


class TurbineEntry(db.Model):
    __table_args__ = (
        db.UniqueConstraint("user_id", "year", "month", name="uq_user_year_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, nullable=False)

    import_start_kwh = db.Column(db.Float, nullable=True)
    import_end_kwh = db.Column(db.Float, nullable=True)
    export_start_kwh = db.Column(db.Float, nullable=True)
    export_end_kwh = db.Column(db.Float, nullable=True)

    inverter_total_kwh = db.Column(db.Float, nullable=True)
    inverter_total_kwh_2 = db.Column(db.Float, nullable=True)
    e_mon_kwh = db.Column(db.Float, nullable=True)
    e_mon_kwh_2 = db.Column(db.Float, nullable=True)
    co2_kg = db.Column(db.Float, nullable=True)
    used_from_wind_kwh = db.Column(db.Float, nullable=True)
    tariff_rate = db.Column(db.Float, nullable=True)
    notes = db.Column(db.String(1000), nullable=True)

    def import_total_kwh(self) -> Optional[float]:
        if self.import_start_kwh is None or self.import_end_kwh is None:
            return None
        return self.import_end_kwh - self.import_start_kwh

    def export_total_kwh(self) -> Optional[float]:
        if self.export_start_kwh is None or self.export_end_kwh is None:
            return None
        return self.export_end_kwh - self.export_start_kwh

    def e_mon_safe(self) -> Optional[float]:
        return None if self.e_mon_kwh is None else float(self.e_mon_kwh)

    def e_mon_2_safe(self) -> Optional[float]:
        return None if self.e_mon_kwh_2 is None else float(self.e_mon_kwh_2)

    def e_mon_total_kwh(self) -> Optional[float]:
        e1, e2 = self.e_mon_safe(), self.e_mon_2_safe()
        if e1 is None and e2 is None:
            return None
        return (e1 or 0.0) + (e2 or 0.0)

    def inverter_total_combined(self) -> Optional[float]:
        v1 = None if self.inverter_total_kwh is None else float(self.inverter_total_kwh)
        v2 = None if self.inverter_total_kwh_2 is None else float(self.inverter_total_kwh_2)
        if v1 is None and v2 is None:
            return None
        return (v1 or 0.0) + (v2 or 0.0)

    def co2_safe(self) -> Optional[float]:
        return None if self.co2_kg is None else float(self.co2_kg)

    def tariff_safe(self) -> float:
        return float(self.tariff_rate) if self.tariff_rate is not None else 0.195

    def used_from_wind_auto_kwh(self) -> Optional[float]:
        e_mon = self.e_mon_total_kwh()
        export_total = self.export_total_kwh()
        if e_mon is None or export_total is None:
            return None
        return max(0.0, e_mon - export_total)

    def used_from_wind_effective_kwh(self) -> Optional[float]:
        if self.used_from_wind_kwh is not None:
            return max(0.0, float(self.used_from_wind_kwh))
        return self.used_from_wind_auto_kwh()

    def house_total_wind_grid_kwh(self) -> Optional[float]:
        e_mon = self.e_mon_total_kwh()
        export_total = self.export_total_kwh()
        import_total = self.import_total_kwh()
        if e_mon is None or export_total is None or import_total is None:
            return None
        return (e_mon - export_total) + import_total

    def value_at_tariff(self) -> Optional[float]:
        e_mon = self.e_mon_total_kwh()
        if e_mon is None:
            return None
        return e_mon * self.tariff_safe()


def init_db() -> None:
    with app.app_context():
        db.create_all()
        ensure_user_schema_sqlite()
        ensure_entry_schema_sqlite()
        ensure_at_least_one_admin()
        ensure_recovery_admin()
        create_daily_backup()


def ensure_user_schema_sqlite() -> None:
    rows = db.session.execute(text("PRAGMA table_info(user)")).fetchall()
    existing = {r[1] for r in rows}
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
            db.session.execute(text(f"ALTER TABLE user ADD COLUMN {col} {sql_type}"))
    db.session.commit()


def ensure_entry_schema_sqlite() -> None:
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
    username = os.environ.get("RECOVERY_ADMIN_USERNAME")
    password = os.environ.get("RECOVERY_ADMIN_PASSWORD")
    if not username or not password:
        return

    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            full_name="Recovery Admin",
            is_admin=1,
        )
        db.session.add(user)
    else:
        user.password_hash = generate_password_hash(password)
        user.is_admin = 1
    db.session.commit()


def ensure_at_least_one_admin() -> None:
    if User.query.filter_by(is_admin=1).first():
        return
    gadge = User.query.filter_by(username="gadge").first()
    if gadge:
        gadge.is_admin = 1
        db.session.commit()
        return
    first_user = User.query.order_by(User.id.asc()).first()
    if first_user:
        first_user.is_admin = 1
        db.session.commit()


def database_path() -> Path:
    return Path(app.instance_path) / "wind.db"


def backups_dir() -> Path:
    path = Path(app.instance_path) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_filename(prefix: str = "wind_backup") -> str:
    now = datetime.datetime.now()
    return f"{prefix}_{now:%Y-%m-%d_%H-%M-%S}.db"


def create_database_backup(prefix: str = "wind_backup") -> Optional[Path]:
    source = database_path()
    if not source.exists():
        return None

    destination = backups_dir() / backup_filename(prefix)

    # sqlite backup API gives a safer live copy than raw file copy.
    connection = db.engine.raw_connection()
    try:
        source_sqlite = connection.connection
        import sqlite3
        backup_conn = sqlite3.connect(destination)
        try:
            source_sqlite.backup(backup_conn)
        finally:
            backup_conn.close()
    finally:
        connection.close()

    return destination


def create_daily_backup() -> Optional[Path]:
    source = database_path()
    if not source.exists():
        return None

    today = datetime.date.today().isoformat()
    existing_today = list(backups_dir().glob(f"daily_backup_{today}_*.db"))
    if existing_today:
        return existing_today[0]

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
    for u in User.query.order_by(User.username.asc()).all():
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
    users_all = User.query.order_by(User.username.asc()).all()
    all_entries = TurbineEntry.query.order_by(TurbineEntry.year.asc(), TurbineEntry.month.asc()).all()
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
            password_hash=generate_password_hash(password),
            is_admin=is_admin_flag,
            full_name=full_name,
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
        target_user.password_hash = generate_password_hash(new_password)
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
            password_hash=generate_password_hash(password),
            is_admin=0,
            full_name=full_name,
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


@app.route("/community")
@login_required
def community():
    all_users = User.query.order_by(User.username.asc()).all()
    all_entries = TurbineEntry.query.all()

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


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
