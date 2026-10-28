"""
scheduler.py - the scheduling engine (the "brain" that owns the truth).

The AI never decides whether a slot is free. It calls the functions below and
reports what they return. Every public function:
  * validates its inputs,
  * returns {"success": True, "data": ..., "message": ...}
    or      {"success": False, "error": {"code": ..., "message": ...}}
  * never raises for bad user input.

Public (AI-callable) tools:
  list_services, check_availability, create_appointment,
  get_appointments, reschedule_appointment, cancel_appointment
Preview functions (preview_*) run exactly the same validation but change nothing;
they are used to build the "please confirm" summary.
Admin helper: list_all_appointments (used only by the dashboard, never by the AI).
"""
import re
import sqlite3
from datetime import date as date_cls, datetime, timedelta, timezone

import config
import database

# ---------------------------------------------------------------- clock ----
_clock = config.now


def set_clock(fn=None) -> None:
    """Replace the clock (tests use a fixed 'now'). set_clock() restores the real one."""
    global _clock
    _clock = fn or config.now


def now() -> datetime:
    return _clock()


# -------------------------------------------------------------- results ----
def ok(data=None, message: str = "") -> dict:
    return {"success": True, "data": data or {}, "message": message}


def err(code: str, message: str) -> dict:
    return {"success": False, "error": {"code": code, "message": message}}


# ------------------------------------------------------------- helpers -----
FMT = config.DATETIME_FORMAT
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'\-]){1,59}$")  # letters, space . ' -


def human_date(d) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def parse_datetime(value):
    """Accept 'YYYY-MM-DD HH:MM' (or with 'T' / seconds). Returns datetime or None."""
    if isinstance(value, datetime):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if not isinstance(value, str) or len(value) > 32:
        return None
    v = value.strip().replace("T", " ")
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, f).replace(second=0)
        except ValueError:
            pass
    return None


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    if not isinstance(value, str) or len(value) > 16:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def clean_name(name):
    if not isinstance(name, str):
        return None
    n = re.sub(r"\s+", " ", name.strip())
    return n if NAME_RE.match(n) else None


def normalize_contact(contact):
    """Valid e-mail (lower-cased) or phone number (digits, optional +). Else None."""
    if not isinstance(contact, str):
        return None
    c = contact.strip()
    if len(c) > 100:
        return None
    if EMAIL_RE.match(c):
        return c.lower()
    digits = re.sub(r"[\s\-().]", "", c)
    return digits if re.fullmatch(r"\+?\d{7,15}", digits) else None


def _clean_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value < 2**31 else None
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) < 10:
        v = int(value.strip())
        return v if v > 0 else None
    return None


def _hours_for(day):
    """(opens, closes) as datetimes for that date, or None if closed."""
    h = config.OPENING_HOURS.get(day.weekday())
    if not h:
        return None
    o = datetime.combine(day, datetime.strptime(h[0], "%H:%M").time())
    c = datetime.combine(day, datetime.strptime(h[1], "%H:%M").time())
    return o, c


def _find_service(conn, service):
    """Find a service by id, exact name, or unique partial name (case-insensitive)."""
    if isinstance(service, bool):
        return None
    if isinstance(service, int) or (isinstance(service, str) and service.strip().isdigit()):
        return conn.execute("SELECT * FROM services WHERE id = ?", (int(service),)).fetchone()
    if not isinstance(service, str) or not service.strip() or len(service) > 60:
        return None
    q = service.strip().lower()
    rows = conn.execute("SELECT * FROM services").fetchall()
    for r in rows:
        if r["name"].lower() == q:
            return r
    part = [r for r in rows if q in r["name"].lower()]
    return part[0] if len(part) == 1 else None


def _service_dict(r) -> dict:
    return {"id": r["id"], "name": r["name"], "description": r["description"],
            "duration_minutes": r["duration_minutes"], "price": r["price"],
            "currency": config.CURRENCY}


_SELECT = ("SELECT a.*, s.name AS service_name, s.duration_minutes, s.price "
           "FROM appointments a JOIN services s ON s.id = a.service_id")


def _appt_dict(r) -> dict:
    start = parse_datetime(r["start_time"])
    return {
        "id": r["id"], "customer_name": r["customer_name"], "contact": r["contact"],
        "service": r["service_name"], "service_id": r["service_id"],
        "start_time": r["start_time"], "end_time": r["end_time"],
        "date": start.strftime("%Y-%m-%d"), "date_display": human_date(start),
        "weekday": start.strftime("%A"), "time": start.strftime("%H:%M"),
        "duration_minutes": r["duration_minutes"], "price": r["price"],
        "status": r["status"], "created_at": r["created_at"],
    }


def _conflicts(conn, start, end, exclude_id=None) -> bool:
    """True if any CONFIRMED appointment overlaps [start, end). Cancelled ones are ignored."""
    sql = "SELECT 1 FROM appointments WHERE status = 'confirmed' AND start_time < ? AND end_time > ?"
    params = [end.strftime(FMT), start.strftime(FMT)]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


def _validate_slot(conn, svc, start, exclude_id=None):
    """The single rulebook for 'can an appointment of this service start here?'. Returns an error dict or None."""
    n = now()
    if start <= n:
        return err("PAST_TIME", "That time is in the past. Please choose a future time.")
    if start > n + timedelta(days=config.MAX_BOOKING_DAYS_AHEAD):
        return err("TOO_FAR_AHEAD", f"We only take bookings up to {config.MAX_BOOKING_DAYS_AHEAD} days ahead.")
    hours = _hours_for(start.date())
    if hours is None:
        return err("CLOSED", f"We are closed on {start.strftime('%A')}s.")
    opens, closes = hours
    end = start + timedelta(minutes=svc["duration_minutes"])
    if start < opens or end > closes:
        return err("OUTSIDE_HOURS",
                   f"A {svc['duration_minutes']}-minute {svc['name']} must fit between "
                   f"{opens:%H:%M} and {closes:%H:%M} on {start.strftime('%A')}s.")
    if ((start - opens).total_seconds() / 60) % config.SLOT_INTERVAL_MINUTES != 0:
        return err("BAD_START_TIME",
                   f"Appointments start every {config.SLOT_INTERVAL_MINUTES} minutes (e.g. {opens:%H:%M}, "
                   f"{(opens + timedelta(minutes=config.SLOT_INTERVAL_MINUTES)):%H:%M}).")
    if _conflicts(conn, start, end, exclude_id):
        return err("SLOT_TAKEN", "That time overlaps an existing appointment.")
    return None


def _db_error(ex: sqlite3.Error) -> dict:
    if "OVERLAP" in str(ex):
        return err("SLOT_TAKEN", "That time was just taken by another booking.")
    return err("DB_ERROR", "A database error occurred. Please try again.")


# ===================================================== AI-callable tools ====
def list_services() -> dict:
    with database.connection() as conn:
        rows = conn.execute("SELECT * FROM services ORDER BY id").fetchall()
    return ok({"services": [_service_dict(r) for r in rows]})


def check_availability(date, service) -> dict:
    """Free start times for `service` on `date` ('YYYY-MM-DD')."""
    d = parse_date(date)
    if d is None:
        return err("INVALID_DATE", "Please give the date as YYYY-MM-DD.")
    with database.connection() as conn:
        svc = _find_service(conn, service)
        if svc is None:
            return err("UNKNOWN_SERVICE", "I don't know that service. Use list_services to see the options.")
        today = now().date()
        if d < today:
            return err("PAST_DATE", "That date is in the past.")
        if d > today + timedelta(days=config.MAX_BOOKING_DAYS_AHEAD):
            return err("TOO_FAR_AHEAD", f"We only take bookings up to {config.MAX_BOOKING_DAYS_AHEAD} days ahead.")
        base = {"date": d.strftime("%Y-%m-%d"), "date_display": human_date(d),
                "weekday": d.strftime("%A"), "service": svc["name"],
                "duration_minutes": svc["duration_minutes"]}
        hours = _hours_for(d)
        if hours is None:
            return ok({**base, "closed": True, "slots": []}, f"We are closed on {d.strftime('%A')}s.")
        opens, closes = hours
        slots, t = [], opens
        step = timedelta(minutes=config.SLOT_INTERVAL_MINUTES)
        while t + timedelta(minutes=svc["duration_minutes"]) <= closes:
            if _validate_slot(conn, svc, t) is None:
                slots.append(t.strftime("%H:%M"))
            t += step
    msg = f"{len(slots)} free slot(s)." if slots else "No free slots on that date."
    return ok({**base, "closed": False, "slots": slots}, msg)


def _check_create(conn, customer_name, contact, service, start_time):
    name = clean_name(customer_name)
    if name is None:
        return err("INVALID_NAME", "Please give a valid name (letters only, 2-60 characters)."), None
    c = normalize_contact(contact)
    if c is None:
        return err("INVALID_CONTACT", "Please give a valid e-mail address or phone number."), None
    start = parse_datetime(start_time)
    if start is None:
        return err("INVALID_DATETIME", "Please give the start time as 'YYYY-MM-DD HH:MM'."), None
    svc = _find_service(conn, service)
    if svc is None:
        return err("UNKNOWN_SERVICE", "I don't know that service. Use list_services to see the options."), None
    e = _validate_slot(conn, svc, start)
    if e:
        return e, None
    return None, {"name": name, "contact": c, "svc": svc, "start": start,
                  "end": start + timedelta(minutes=svc["duration_minutes"])}


def _create_summary(ctx) -> dict:
    s = ctx["start"]
    return {"action": "create", "service": ctx["svc"]["name"], "date": s.strftime("%Y-%m-%d"),
            "date_display": f"{s.strftime('%A')}, {human_date(s)}", "time": s.strftime("%H:%M"),
            "duration_minutes": ctx["svc"]["duration_minutes"], "price": ctx["svc"]["price"],
            "customer_name": ctx["name"], "contact": ctx["contact"]}


def preview_create(customer_name, contact, service, start_time) -> dict:
    """Validate a booking without saving it."""
    with database.connection() as conn:
        e, ctx = _check_create(conn, customer_name, contact, service, start_time)
    return e or ok(_create_summary(ctx), "This booking is possible.")


def create_appointment(customer_name, contact, service, start_time) -> dict:
    """Book a slot. Availability is re-checked inside the write transaction."""
    try:
        with database.transaction() as conn:
            e, ctx = _check_create(conn, customer_name, contact, service, start_time)
            if e:
                return e
            created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO appointments (customer_name, contact, service_id, start_time, end_time, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'confirmed', ?)",
                (ctx["name"], ctx["contact"], ctx["svc"]["id"], ctx["start"].strftime(FMT),
                 ctx["end"].strftime(FMT), created))
            row = conn.execute(_SELECT + " WHERE a.id = ?", (cur.lastrowid,)).fetchone()
            return ok({"appointment": _appt_dict(row)}, "Appointment booked.")
    except sqlite3.Error as ex:
        return _db_error(ex)


def get_appointments(contact, upcoming_only: bool = True, include_cancelled: bool = False) -> dict:
    """A customer's appointments. The contact detail acts as their 'password'."""
    c = normalize_contact(contact)
    if c is None:
        return err("INVALID_CONTACT", "Please give a valid e-mail address or phone number.")
    sql, params = _SELECT + " WHERE a.contact = ?", [c]
    if not include_cancelled:
        sql += " AND a.status = 'confirmed'"
    if upcoming_only:
        sql += " AND a.end_time > ?"
        params.append(now().strftime(FMT))
    with database.connection() as conn:
        rows = conn.execute(sql + " ORDER BY a.start_time", params).fetchall()
    appts = [_appt_dict(r) for r in rows]
    return ok({"appointments": appts}, f"{len(appts)} appointment(s) found.")


# ---- changing an existing appointment (needs verification) ----------------
def _load_for_change(conn, appointment_id, verification_details, admin):
    aid = _clean_id(appointment_id)
    if aid is None:
        return err("INVALID_ID", "The appointment ID must be a positive number."), None
    row = conn.execute(_SELECT + " WHERE a.id = ?", (aid,)).fetchone()
    verified = admin or (row is not None and normalize_contact(verification_details) == row["contact"])
    if row is None or not verified:
        # Same message for "doesn't exist" and "wrong contact" so IDs can't be probed.
        return err("NOT_FOUND_OR_NOT_AUTHORIZED",
                   "No appointment matches that ID and contact detail."), None
    if row["status"] == "cancelled":
        return err("ALREADY_CANCELLED", f"Appointment #{aid} is already cancelled."), None
    if parse_datetime(row["start_time"]) <= now():
        return err("ALREADY_STARTED", "That appointment has already started or passed."), None
    return None, row


def _check_reschedule(conn, appointment_id, new_start_time, verification_details, admin):
    e, row = _load_for_change(conn, appointment_id, verification_details, admin)
    if e:
        return e, None
    new_start = parse_datetime(new_start_time)
    if new_start is None:
        return err("INVALID_DATETIME", "Please give the new start time as 'YYYY-MM-DD HH:MM'."), None
    if new_start.strftime(FMT) == row["start_time"]:
        return err("SAME_TIME", "The appointment is already at that time."), None
    svc = conn.execute("SELECT * FROM services WHERE id = ?", (row["service_id"],)).fetchone()
    e = _validate_slot(conn, svc, new_start, exclude_id=row["id"])  # ignore itself
    if e:
        return e, None
    return None, {"row": row, "new_start": new_start,
                  "new_end": new_start + timedelta(minutes=svc["duration_minutes"])}


def preview_reschedule(appointment_id, new_start_time, verification_details=None, admin=False) -> dict:
    with database.connection() as conn:
        e, ctx = _check_reschedule(conn, appointment_id, new_start_time, verification_details, admin)
    if e:
        return e
    old = _appt_dict(ctx["row"])
    n = ctx["new_start"]
    return ok({"action": "reschedule", "appointment_id": old["id"], "service": old["service"],
               "customer_name": old["customer_name"], "old_date_display": old["date_display"],
               "old_time": old["time"], "date": n.strftime("%Y-%m-%d"),
               "date_display": f"{n.strftime('%A')}, {human_date(n)}", "time": n.strftime("%H:%M"),
               "duration_minutes": old["duration_minutes"]})


def reschedule_appointment(appointment_id, new_start_time, verification_details=None, admin: bool = False) -> dict:
    """
    Move an appointment (same service, new start). `verification_details` must be the
    contact used when booking. `admin=True` is only passed by the admin dashboard code -
    it is NOT exposed to the AI tools.
    """
    try:
        with database.transaction() as conn:
            e, ctx = _check_reschedule(conn, appointment_id, new_start_time, verification_details, admin)
            if e:
                return e
            conn.execute("UPDATE appointments SET start_time = ?, end_time = ? WHERE id = ?",
                         (ctx["new_start"].strftime(FMT), ctx["new_end"].strftime(FMT), ctx["row"]["id"]))
            row = conn.execute(_SELECT + " WHERE a.id = ?", (ctx["row"]["id"],)).fetchone()
            return ok({"appointment": _appt_dict(row)}, "Appointment rescheduled.")
    except sqlite3.Error as ex:
        return _db_error(ex)


def preview_cancel(appointment_id, verification_details=None, admin=False) -> dict:
    with database.connection() as conn:
        e, row = _load_for_change(conn, appointment_id, verification_details, admin)
    if e:
        return e
    a = _appt_dict(row)
    return ok({"action": "cancel", "appointment_id": a["id"], "service": a["service"],
               "customer_name": a["customer_name"], "date_display": f"{a['weekday']}, {a['date_display']}",
               "time": a["time"], "duration_minutes": a["duration_minutes"]})


def cancel_appointment(appointment_id, verification_details=None, admin: bool = False) -> dict:
    """Cancel (soft delete: status becomes 'cancelled', the row is kept for the history)."""
    try:
        with database.transaction() as conn:
            e, row = _load_for_change(conn, appointment_id, verification_details, admin)
            if e:
                return e
            conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (row["id"],))
            updated = conn.execute(_SELECT + " WHERE a.id = ?", (row["id"],)).fetchone()
            return ok({"appointment": _appt_dict(updated)}, "Appointment cancelled.")
    except sqlite3.Error as ex:
        return _db_error(ex)


# ===================================================== admin only ===========
def list_all_appointments(date=None, status=None, upcoming_only: bool = False) -> dict:
    """All appointments for the admin dashboard, optionally filtered."""
    sql, params = _SELECT + " WHERE 1=1", []
    if date:
        d = parse_date(date)
        if d is None:
            return err("INVALID_DATE", "Please give the date as YYYY-MM-DD.")
        sql += " AND a.start_time >= ? AND a.start_time < ?"
        params += [f"{d:%Y-%m-%d} 00:00", f"{d + timedelta(days=1):%Y-%m-%d} 00:00"]
    if status:
        if status not in ("confirmed", "cancelled"):
            return err("INVALID_STATUS", "Status must be 'confirmed' or 'cancelled'.")
        sql += " AND a.status = ?"
        params.append(status)
    if upcoming_only:
        sql += " AND a.end_time > ?"
        params.append(now().strftime(FMT))
    with database.connection() as conn:
        rows = conn.execute(sql + " ORDER BY a.start_time", params).fetchall()
    return ok({"appointments": [_appt_dict(r) for r in rows]})
