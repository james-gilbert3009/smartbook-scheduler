"""
tools.py - the bridge between the AI agent and the scheduling engine.

* TOOLS_SCHEMA  : the tool descriptions the AI model receives (what it may call).
* READ_TOOLS    : safe look-ups; run immediately.
* WRITE_TOOLS   : create / reschedule / cancel. These are NEVER run straight from
                  the AI. They are first *previewed* (validated, nothing saved),
                  shown to the user, and only executed after the user confirms.
* The AI can't pass `admin=True`: that option is not part of any tool schema and
  is not forwarded by the functions below.
"""
import scheduler

TOOLS_SCHEMA = [
    {"name": "list_services",
     "description": "List all services with description, duration in minutes and price.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_availability",
     "description": "Get the real free start times for a service on one date. Always use this "
                    "before suggesting times. Never invent times.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "Date as YYYY-MM-DD"},
         "service": {"type": "string", "description": "Service name, e.g. 'Consultation'"}},
         "required": ["date", "service"]}},
    {"name": "get_appointments",
     "description": "List a customer's upcoming appointments. Needs the e-mail or phone they booked with.",
     "input_schema": {"type": "object", "properties": {
         "contact": {"type": "string", "description": "E-mail address or phone number"}},
         "required": ["contact"]}},
    {"name": "create_appointment",
     "description": "Request a new booking. Call only when you have the customer's name, contact, "
                    "service and an exact start time the customer chose. The system validates it and "
                    "asks the customer to confirm; the booking is NOT saved until they confirm.",
     "input_schema": {"type": "object", "properties": {
         "customer_name": {"type": "string"},
         "contact": {"type": "string", "description": "E-mail address or phone number"},
         "service": {"type": "string"},
         "start_time": {"type": "string", "description": "YYYY-MM-DD HH:MM (24-hour, local time)"}},
         "required": ["customer_name", "contact", "service", "start_time"]}},
    {"name": "reschedule_appointment",
     "description": "Request moving an appointment to a new time. Needs the appointment ID and the "
                    "contact used when booking (verification). Not saved until the customer confirms.",
     "input_schema": {"type": "object", "properties": {
         "appointment_id": {"type": "integer"},
         "new_start_time": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
         "verification_details": {"type": "string", "description": "E-mail or phone used when booking"}},
         "required": ["appointment_id", "new_start_time", "verification_details"]}},
    {"name": "cancel_appointment",
     "description": "Request cancelling an appointment. Needs the appointment ID and the contact used "
                    "when booking (verification). Not done until the customer confirms.",
     "input_schema": {"type": "object", "properties": {
         "appointment_id": {"type": "integer"},
         "verification_details": {"type": "string", "description": "E-mail or phone used when booking"}},
         "required": ["appointment_id", "verification_details"]}},
]

READ_TOOLS = {"list_services", "check_availability", "get_appointments"}
WRITE_TOOLS = {"create_appointment", "reschedule_appointment", "cancel_appointment"}


def _args(args) -> dict:
    return args if isinstance(args, dict) else {}


def call_read_tool(name: str, args) -> dict:
    a = _args(args)
    if name == "list_services":
        return scheduler.list_services()
    if name == "check_availability":
        return scheduler.check_availability(a.get("date"), a.get("service"))
    if name == "get_appointments":
        return scheduler.get_appointments(a.get("contact"))
    return scheduler.err("UNKNOWN_TOOL", f"There is no tool called '{name}'.")


def preview_write_tool(name: str, args) -> dict:
    """Validate a write request WITHOUT saving anything."""
    a = _args(args)
    if name == "create_appointment":
        return scheduler.preview_create(a.get("customer_name"), a.get("contact"), a.get("service"), a.get("start_time"))
    if name == "reschedule_appointment":
        return scheduler.preview_reschedule(a.get("appointment_id"), a.get("new_start_time"), a.get("verification_details"))
    if name == "cancel_appointment":
        return scheduler.preview_cancel(a.get("appointment_id"), a.get("verification_details"))
    return scheduler.err("UNKNOWN_TOOL", f"There is no tool called '{name}'.")


def run_write_tool(name: str, args) -> dict:
    """Really perform the change (only called after the user confirmed)."""
    a = _args(args)
    if name == "create_appointment":
        return scheduler.create_appointment(a.get("customer_name"), a.get("contact"), a.get("service"), a.get("start_time"))
    if name == "reschedule_appointment":
        return scheduler.reschedule_appointment(a.get("appointment_id"), a.get("new_start_time"), a.get("verification_details"))
    if name == "cancel_appointment":
        return scheduler.cancel_appointment(a.get("appointment_id"), a.get("verification_details"))
    return scheduler.err("UNKNOWN_TOOL", f"There is no tool called '{name}'.")


# ------------------------------------------------ text shown to the user ----
# These messages are generated by code from real data, not by the AI model, so a
# confirmation or a booking ID can never be made up.
def format_summary(p: dict) -> str:
    """The 'please confirm' box for a validated pending action."""
    if p["action"] == "create":
        return (f"**Please check your booking:**\n\n"
                f"- **Service:** {p['service']}\n- **Date:** {p['date_display']}\n- **Time:** {p['time']}\n"
                f"- **Duration:** {p['duration_minutes']} minutes\n- **Customer:** {p['customer_name']}\n"
                f"- **Contact:** {p['contact']}\n\n"
                f"Shall I book this? Reply **yes** to confirm or **no** to cancel.")
    if p["action"] == "reschedule":
        return (f"**Please check this change:**\n\n"
                f"- **Appointment:** #{p['appointment_id']} - {p['service']} ({p['customer_name']})\n"
                f"- **Current time:** {p['old_date_display']}, {p['old_time']}\n"
                f"- **New time:** {p['date_display']}, {p['time']} ({p['duration_minutes']} minutes)\n\n"
                f"Shall I move it? Reply **yes** to confirm or **no** to keep it as it is.")
    return (f"**Please check this cancellation:**\n\n"
            f"- **Appointment:** #{p['appointment_id']} - {p['service']} ({p['customer_name']})\n"
            f"- **When:** {p['date_display']}, {p['time']} ({p['duration_minutes']} minutes)\n\n"
            f"Shall I cancel it? Reply **yes** to confirm or **no** to keep it.")


def format_result(tool: str, result: dict) -> str:
    """Report the ACTUAL outcome of a confirmed action."""
    if not result["success"]:
        return f"Sorry, that did not go through: {result['error']['message']}"
    a = result["data"]["appointment"]
    when = f"{a['weekday']}, {a['date_display']} at {a['time']} ({a['duration_minutes']} minutes)"
    if tool == "create_appointment":
        return (f"Your appointment is booked.\n\n- **Appointment ID:** #{a['id']}\n- **Service:** {a['service']}\n"
                f"- **When:** {when}\n- **Customer:** {a['customer_name']}\n- **Status:** {a['status']}\n\n"
                f"Keep your ID and the contact you gave: you need both to change or cancel it.")
    if tool == "reschedule_appointment":
        return f"Appointment #{a['id']} ({a['service']}) is now on {when}."
    return f"Appointment #{a['id']} ({a['service']}, {when}) has been cancelled. The time slot is free again."


def format_appointments(appts: list) -> str:
    if not appts:
        return "I couldn't find any upcoming appointments for that contact."
    lines = [f"- **#{a['id']}** - {a['service']}, {a['weekday']} {a['date_display']} at {a['time']} "
             f"({a['duration_minutes']} min) - {a['status']}" for a in appts]
    return "Here are your upcoming appointments:\n\n" + "\n".join(lines)


def format_services(services: list) -> str:
    lines = [f"- **{s['name']}** - {s['duration_minutes']} min, {s['price']:.2f} {s['currency']}: {s['description']}"
             for s in services]
    return "We offer these services:\n\n" + "\n".join(lines)


def filter_slots(slots: list, daypart: str = None) -> list:
    if daypart == "morning":
        return [s for s in slots if s < "12:00"]
    if daypart == "afternoon":
        return [s for s in slots if s >= "12:00"]
    if daypart == "evening":
        return [s for s in slots if s >= "17:00"]
    return slots


def format_slots(data: dict, daypart: str = None, limit: int = 20) -> str:
    if data.get("closed"):
        return f"We are closed on {data['weekday']}s ({data['date_display']})."
    slots = filter_slots(data["slots"], daypart)
    if not slots:
        return (f"There are no free {daypart + ' ' if daypart else ''}slots for {data['service']} "
                f"on {data['weekday']} {data['date_display']}.")
    shown = ", ".join(slots[:limit]) + (" ..." if len(slots) > limit else "")
    return (f"Free times for **{data['service']}** ({data['duration_minutes']} min) on "
            f"**{data['weekday']} {data['date_display']}**: {shown}")
