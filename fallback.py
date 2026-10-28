"""
fallback.py - rule-based assistant used when no AI API key is available.

It is the "small, documented intent-routing layer" described in the assignment:
  1. detect_intent()   -> what does the user want? (book, cancel, ...)
  2. parse_*()         -> pull out service / date / time / contact / name / ID
  3. FallbackEngine    -> asks for whatever is still missing, then calls the SAME
                          backend tools (tools.py -> scheduler.py) as the AI mode.

It is less flexible than a language model (it understands a fixed set of
phrasings) but the scheduling rules, confirmation step and database are identical.
"""
import re
from datetime import date, timedelta

import scheduler
import tools

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
            "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
             r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")


# ------------------------------------------------------------- parsers -----
def _mk_date(day, month, year, today):
    try:
        if year is None:
            d = date(today.year, month, day)
            return d if d >= today else date(today.year + 1, month, day)
        return date(year if year > 99 else 2000 + year, month, day)
    except ValueError:
        return None


def parse_date_text(text: str, today: date):
    t = text.lower()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return _mk_date(int(m[3]), int(m[2]), int(m[1]), today)
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b", t)
    if m:
        return _mk_date(int(m[1]), int(m[2]), int(m[3]), today)
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\b(?:,?\s+(\d{{4}}))?", t)
    if m:
        return _mk_date(int(m[1]), MONTHS.index(m[2][:3]) + 1, int(m[3]) if m[3] else None, today)
    m = re.search(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", t)
    if m:
        return _mk_date(int(m[2]), MONTHS.index(m[1][:3]) + 1, int(m[3]) if m[3] else None, today)
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    if re.search(r"\b(today|tonight)\b", t):
        return today
    m = re.search(r"\b(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", t)
    if m:
        ahead = (WEEKDAYS[m[1]] - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)      # "Monday" = the next Monday
    return None


def _hour_to_time(h: int, minute: int = 0, ampm: str = None):
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif ampm is None and 1 <= h <= 8:      # business hours are 09-17, so "at 2" means 14:00
        h += 12
    return f"{h:02d}:{minute:02d}" if 0 <= h <= 23 and 0 <= minute <= 59 else None


def parse_time_text(text: str):
    """Returns (time 'HH:MM' or None, daypart 'morning'/'afternoon'/'evening' or None)."""
    t = text.lower()
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", t)
    if m:
        hour, ampm = int(m[1]), m[3]
        if not ampm and (hour > 12 or m[1].startswith("0")):
            return f"{hour:02d}:{m[2]}" if hour < 24 and int(m[2]) < 60 else None, None   # clearly 24-hour
        return _hour_to_time(hour, int(m[2]), ampm), None
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t)
    if m:
        return _hour_to_time(int(m[1]), 0, m[2]), None
    m = re.search(r"\bat\s+(\d{1,2})\b(?!\s*[:.]?\d)", t)
    if m:
        return _hour_to_time(int(m[1])), None
    for part in ("morning", "afternoon", "evening"):
        if part in t:
            return None, part
    return None, None


def parse_service(text: str, services: list):
    t = text.lower()
    for s in services:
        if s["name"].lower() in t or s["name"].lower().split()[0] in t:
            return s["name"]
    return None


def parse_contact(text: str):
    no_dates = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)          # an ISO date must not look like a phone number
    m = re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", no_dates)
    if m:
        return scheduler.normalize_contact(m[0].strip(".,;"))
    m = re.search(r"\+?\d[\d\s\-]{6,}\d", no_dates)
    return scheduler.normalize_contact(m[0]) if m else None


def parse_name(text: str):
    m = re.search(r"(?:my name is|name is|call me|name:)\s+([^\W\d_]+(?:[ '\-][^\W\d_]+){0,3})", text, re.I)
    if not m:
        return None
    part = re.split(r"\b(?:and|my|email|e-mail|phone|contact)\b", m[1], flags=re.I)[0].strip()
    return scheduler.clean_name(part)


def parse_id(text: str):
    m = re.search(r"(?:#|\bid\s*:?\s*|\bappointment\s*(?:number|no\.?)?\s*#?\s*)(\d{1,6})\b", text.lower())
    return int(m[1]) if m else None


def detect_intent(t: str):
    if re.match(r"^\s*(hi|hello|hey|help|start over|reset)\b", t) and not re.search(r"\b(book|cancel|move|reschedul)", t):
        return "help"
    if re.search(r"\b(cancel|delete|remove)\b", t):
        return "cancel"
    if re.search(r"\b(reschedul\w*|move|postpone|another time|different time|change (?:my|the) (?:appointment|booking|time))\b", t):
        return "reschedule"
    if re.search(r"\b(my (?:upcoming )?(?:appointments?|bookings?)|upcoming (?:appointments?|bookings?)|view .*appointments?)\b", t):
        return "my"
    if re.search(r"\b(services?|prices?|cost|how much|offer)\b", t):
        return "services"
    if re.search(r"\b(available|availability|free|open slots?|slots?|what times)\b", t):
        return "availability"
    if re.search(r"\b(book|schedule|reserve|appointment|consultation|i need|i want|i'd like|i would like)\b", t):
        return "book"
    return None


# ------------------------------------------------------------- engine ------
HELP = ("I'm the SmartBook assistant. I can show our **services**, check **availability**, "
        "**book** an appointment, show **your appointments**, and **reschedule** or **cancel** one.\n\n"
        "Try: *\"I'd like a consultation on Monday afternoon\"*.")


class FallbackEngine:
    """Keeps a little conversation state (what's been collected so far)."""

    def __init__(self):
        self.profile = {}    # name / contact remembered during this chat
        self.state = {}
        self.log, self.services = [], []

    def reset(self):
        self.profile, self.state, self.log = {}, {}, []

    # --- logging so the UI can show "which tools did the agent call?" ---
    def _log(self, name, args, r):
        self.log.append({"tool": name, "args": dict(args), "success": r["success"],
                         "summary": r["message"] if r["success"] else f"{r['error']['code']}: {r['error']['message']}"})

    def _read(self, name, args):
        r = tools.call_read_tool(name, args)
        self._log(name, args, r)
        return r

    def _preview(self, name, args):
        r = tools.preview_write_tool(name, args)
        self._log(name + " (validate only)", args, r)
        return r

    # --- main entry ---
    def handle(self, text: str) -> dict:
        self.log = []
        reply, pending = self._handle(text)
        return {"text": reply, "pending": pending, "tool_calls": self.log}

    def _handle(self, text):
        t, today = text.lower(), scheduler.now().date()
        services = self.services = self._read("list_services", {})["data"]["services"]
        new_intent = detect_intent(t)
        st = self.state
        awaiting = st.get("awaiting")

        if new_intent and new_intent != st.get("intent"):       # user changed topic
            keep = {}
            if new_intent in ("book", "availability") and st.get("intent") in ("book", "availability"):
                keep = {k: st[k] for k in ("service", "date", "time", "daypart") if k in st}
            self.state = st = {"intent": new_intent, **keep}
            awaiting = None
        st["awaiting"] = None

        # ---- collect details from this message ----
        svc = parse_service(t, services)
        if svc:
            st["service"] = svc
        d = parse_date_text(t, today)
        tm, part = parse_time_text(t)
        if awaiting == "time" and not tm and re.fullmatch(r"\s*\d{1,2}\s*", t):
            tm = _hour_to_time(int(t))
        if d:
            st["date"] = d.strftime("%Y-%m-%d")
            if not tm:
                st.pop("time", None)
        if tm:
            st["time"] = tm
            st.pop("daypart", None)
        elif part:
            st["daypart"] = part
        contact = parse_contact(text)
        if contact:
            self.profile["contact"] = contact
        name = parse_name(text)
        if not name and awaiting == "name" and not new_intent:
            cand = scheduler.clean_name(text)
            name = cand if cand and len(cand.split()) <= 4 else None
        if name:
            self.profile["name"] = name
        aid = parse_id(t)
        if aid:
            st["appointment_id"] = aid

        if not st.get("intent") and (svc or d or tm or part):     # e.g. just "Tuesday morning please"
            st["intent"] = "book" if (svc or tm) else "availability"
        intent = st.get("intent")
        if intent == "help":
            self.state = {}
            return HELP, None
        if intent == "services":
            self.state = {}
            return tools.format_services(services), None
        if intent == "availability":
            return self._availability(st, services)
        if intent == "book":
            return self._book(st)
        if intent == "my":
            return self._my_appointments(st)
        if intent in ("cancel", "reschedule"):
            return self._change(st, intent)
        return HELP, None

    # --- flows ---
    def _ask(self, st, what, question):
        st["awaiting"] = what
        return question, None

    def _slots(self, st, service, ask_time=True):
        """Show real free slots for st['date']. Returns (text, any_slots_shown)."""
        r = self._read("check_availability", {"date": st["date"], "service": service})
        if not r["success"]:
            st.pop("date", None)
            st.pop("time", None)
            return f"{r['error']['message']} Which other date would you like?", False
        data = r["data"]
        text = tools.format_slots(data, st.get("daypart"))
        if data["closed"] or not tools.filter_slots(data["slots"], st.get("daypart")):
            st.pop("date", None)
            st.pop("time", None)
            return f"{text} Would another date work?", False
        return text + ("\n\nWhich time would you like?" if ask_time else ""), True

    def _availability(self, st, services):
        if not st.get("date"):
            return self._ask(st, "date", "Which date would you like me to check?")
        service = st.get("service") or services[0]["name"]
        text, shown = self._slots(st, service, ask_time=False)
        if not shown:
            st["awaiting"] = "date"
            return text, None
        note = "" if st.get("service") else f" (showing {service}; tell me another service for other durations)"
        st["intent"], st["awaiting"] = "book", "time"
        return text + note + "\n\nWant to book one of these? Tell me the time.", None

    def _book(self, st):
        if not st.get("service"):
            names = ", ".join(s["name"] for s in self.services)
            return self._ask(st, "service", f"Which service would you like? We offer: {names}.")
        if not st.get("date"):
            return self._ask(st, "date", "Which date would you like? (e.g. 'Monday' or '2026-10-26')")
        if not st.get("time"):
            text, shown = self._slots(st, st["service"])
            st["awaiting"] = "time" if shown else "date"
            return text, None
        if not self.profile.get("name"):
            return self._ask(st, "name", "Great. What name should I put the booking under?")
        if not self.profile.get("contact"):
            return self._ask(st, "contact", "Thanks. What e-mail address or phone number can we reach you on?")
        args = {"customer_name": self.profile["name"], "contact": self.profile["contact"],
                "service": st["service"], "start_time": f"{st['date']} {st['time']}"}
        pv = self._preview("create_appointment", args)
        if pv["success"]:
            self.state = {}
            return tools.format_summary(pv["data"]), {"tool": "create_appointment", "args": args, "preview": pv["data"]}
        code, msg = pv["error"]["code"], pv["error"]["message"]
        if code == "INVALID_NAME":
            self.profile.pop("name", None)
            return self._ask(st, "name", f"{msg} What name should I use?")
        if code == "INVALID_CONTACT":
            self.profile.pop("contact", None)
            return self._ask(st, "contact", f"{msg} What e-mail or phone should I use?")
        st.pop("time", None)
        if code in ("SLOT_TAKEN", "OUTSIDE_HOURS", "BAD_START_TIME"):
            text, shown = self._slots(st, st["service"])
            st["awaiting"] = "time" if shown else "date"
            return f"{msg}\n\n{text}", None
        st.pop("date", None)
        return self._ask(st, "date", f"{msg} Which date would you like instead?")

    def _my_appointments(self, st):
        contact = self.profile.get("contact")
        if not contact:
            return self._ask(st, "contact", "Please give the e-mail or phone number you booked with.")
        r = self._read("get_appointments", {"contact": contact})
        self.state = {}
        return (tools.format_appointments(r["data"]["appointments"]) if r["success"] else r["error"]["message"]), None

    def _change(self, st, intent):
        contact = self.profile.get("contact")
        if not contact:
            return self._ask(st, "contact", "For security, please give the e-mail or phone number you booked with.")
        r = self._read("get_appointments", {"contact": contact})
        appts = r["data"]["appointments"] if r["success"] else []
        if not appts:
            self.state = {}
            return "I couldn't find any upcoming appointments for that contact. Please check the e-mail/phone you booked with.", None
        aid = st.get("appointment_id") or (appts[0]["id"] if len(appts) == 1 else None)
        appt = next((a for a in appts if a["id"] == aid), None)
        if appt is None:
            st.pop("appointment_id", None)
            lead = f"I can't find appointment #{aid} under that contact.\n\n" if aid else ""
            return self._ask(st, "id", lead + tools.format_appointments(appts) + "\n\nWhich appointment ID do you mean?")
        st["appointment_id"] = aid

        if intent == "cancel":
            args = {"appointment_id": aid, "verification_details": contact}
            pv = self._preview("cancel_appointment", args)
            tool = "cancel_appointment"
        else:
            if not st.get("date"):
                return self._ask(st, "date", f"Which new date would you like for appointment #{aid} ({appt['service']})?")
            if not st.get("time"):
                text, shown = self._slots(st, appt["service"])
                st["awaiting"] = "time" if shown else "date"
                return text, None
            args = {"appointment_id": aid, "new_start_time": f"{st['date']} {st['time']}",
                    "verification_details": contact}
            pv = self._preview("reschedule_appointment", args)
            tool = "reschedule_appointment"
        if pv["success"]:
            self.state = {}
            return tools.format_summary(pv["data"]), {"tool": tool, "args": args, "preview": pv["data"]}
        msg = pv["error"]["message"]
        if intent == "reschedule" and pv["error"]["code"] in ("SLOT_TAKEN", "OUTSIDE_HOURS", "BAD_START_TIME", "SAME_TIME"):
            st.pop("time", None)
            text, shown = self._slots(st, appt["service"])
            st["awaiting"] = "time" if shown else "date"
            return f"{msg}\n\n{text}", None
        self.state = {}
        return msg, None
