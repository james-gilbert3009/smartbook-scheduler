"""
app.py - the Streamlit web interface.   Run with:  streamlit run app.py

Sections: Home | AI Booking Assistant | Book an Appointment | My Appointments |
          Admin Dashboard | About the Project

The UI contains no scheduling rules. Every page calls scheduler.py (directly or
through the AI agent), so the SQLite database is always the single source of truth.
"""
import hmac
import json
import os
from datetime import timedelta

import pandas as pd
import streamlit as st

import config
import database
import scheduler
import tools
from ai_agent import SchedulerAgent

st.set_page_config(page_title=config.BUSINESS_NAME, page_icon="📅", layout="wide")


@st.cache_resource
def _setup() -> bool:
    database.init_db()          # creates tables + fictional services on first run
    return True


_setup()

st.markdown("""
<style>
.hero {padding: 1.6rem 1.8rem; border-radius: 14px; margin-bottom: 1rem;
       background: linear-gradient(120deg, #0f766e 0%, #134e4a 100%); color: #fff;}
.hero h1 {margin: 0; font-size: 2.1rem; color: #fff;}
.hero p {margin: .3rem 0 0 0; font-size: 1.05rem; opacity: .92;}
</style>
""", unsafe_allow_html=True)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ------------------------------------------------------------ helpers -------
def flash(kind: str, text: str) -> None:
    """Show a message after the next page refresh."""
    st.session_state["flash"] = (kind, text)


def show_flash() -> None:
    f = st.session_state.pop("flash", None)
    if f:
        {"success": st.success, "error": st.error}.get(f[0], st.info)(f[1])


def appts_df(appts: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "ID": a["id"], "Customer": a["customer_name"], "Service": a["service"],
        "Date": a["date"], "Day": a["weekday"], "Time": f"{a['time']} - {a['end_time'][-5:]}",
        "Duration (min)": a["duration_minutes"], "Status": a["status"]} for a in appts])


def default_date():
    return scheduler.now().date() + timedelta(days=1)


def free_slots(date_value, service):
    r = scheduler.check_availability(date_value.isoformat(), service)
    return r, (r["data"]["slots"] if r["success"] else [])


def hours_table() -> pd.DataFrame:
    rows = []
    for i, name in enumerate(DAYS):
        h = config.OPENING_HOURS.get(i)
        rows.append({"Day": name, "Hours": f"{h[0]} - {h[1]}" if h else "Closed"})
    return pd.DataFrame(rows)


def appointment_actions(a: dict, verification=None, admin: bool = False) -> None:
    """Reschedule / cancel controls for one appointment (used by 'My Appointments' and the admin dashboard)."""
    k = f"{'adm' if admin else 'usr'}_{a['id']}"
    left, right = st.columns(2)
    with left:
        st.markdown("**Reschedule**")
        d = st.date_input("New date", value=max(default_date(), scheduler.now().date()),
                          min_value=scheduler.now().date(), key=f"{k}_date")
        r, slots = free_slots(d, a["service_id"])
        if slots:
            t = st.selectbox("New time", slots, key=f"{k}_time")
        else:
            t = None
            st.info(r["message"] if r["success"] else r["error"]["message"])
        sure = st.checkbox("I confirm this change", key=f"{k}_rc")
        if st.button("Reschedule", key=f"{k}_rb", disabled=not (t and sure)):
            res = scheduler.reschedule_appointment(a["id"], f"{d.isoformat()} {t}", verification, admin=admin)
            flash("success" if res["success"] else "error",
                  f"Appointment #{a['id']} moved to {d.isoformat()} {t}." if res["success"] else res["error"]["message"])
            st.rerun()
    with right:
        st.markdown("**Cancel**")
        st.caption("The time slot becomes available again for other customers.")
        sure = st.checkbox("Yes, cancel this appointment", key=f"{k}_cc")
        if st.button("Cancel appointment", key=f"{k}_cb", disabled=not sure):
            res = scheduler.cancel_appointment(a["id"], verification, admin=admin)
            flash("success" if res["success"] else "error",
                  f"Appointment #{a['id']} cancelled." if res["success"] else res["error"]["message"])
            st.rerun()


# -------------------------------------------------------------- pages -------
def page_home():
    st.markdown(f"""<div class="hero"><h1>📅 {config.BUSINESS_NAME}</h1>
    <p>{config.BUSINESS_TAGLINE} - book, change or cancel appointments by chatting with our AI assistant.</p></div>""",
                unsafe_allow_html=True)
    upcoming = scheduler.list_all_appointments(status="confirmed", upcoming_only=True)["data"]["appointments"]
    svc = scheduler.list_services()["data"]["services"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Services offered", len(svc))
    c2.metric("Upcoming appointments", len(upcoming))
    c3.metric("Local time now", scheduler.now().strftime("%d %b, %H:%M"))
    left, right = st.columns([3, 2])
    with left:
        st.subheader("What would you like to do?")
        st.markdown("- 💬 **AI Booking Assistant** - just tell it what you need\n"
                    "- 🗓️ **Book an Appointment** - classic form with live availability\n"
                    "- 📋 **My Appointments** - view, reschedule or cancel\n"
                    "- 🛠️ **Admin Dashboard** - see every booking (password protected)")
        st.subheader("Our services")
        st.dataframe(pd.DataFrame([{"Service": s["name"], "Duration (min)": s["duration_minutes"],
                                    f"Price ({s['currency']})": s["price"]} for s in svc]), hide_index=True)
    with right:
        st.subheader("Opening hours")
        st.dataframe(hours_table(), hide_index=True)
        st.caption(f"Times are in {config.TIMEZONE_NAME}. Demo business - no real appointments are made.")


EXAMPLES = ["What services do you offer?", "What appointments are available on Monday?",
            "I'd like a consultation on Monday afternoon", "Show my upcoming appointments"]


def page_assistant():
    st.title("💬 AI Booking Assistant")
    if "agent" not in st.session_state:
        st.session_state.agent = SchedulerAgent()
        st.session_state.messages = [{"role": "assistant", "tool_calls": [], "text":
                                      "Hi! I can check availability and book, reschedule or cancel appointments. How can I help?"}]
    agent, messages = st.session_state.agent, st.session_state.messages

    if agent.mode == "ai":
        st.success(f"🤖 {agent.mode_label}")
    else:
        st.warning(f"⚠️ {agent.mode_label} Add ANTHROPIC_API_KEY to enable the AI model; scheduling works either way.")

    with st.expander("Try asking..."):
        for i, ex in enumerate(EXAMPLES):
            if st.button(ex, key=f"ex_{i}"):
                st.session_state["queued"] = ex
                st.rerun()
        if st.button("🔄 Start a new conversation"):
            for k in ("agent", "messages"):
                st.session_state.pop(k, None)
            st.rerun()

    typed = st.chat_input("Type your message...")
    prompt = typed or st.session_state.pop("queued", None)
    if prompt:
        messages.append({"role": "user", "text": prompt, "tool_calls": []})
        with st.spinner("Thinking..."):
            reply = agent.respond(prompt)
        messages.append({"role": "assistant", "text": reply["text"], "tool_calls": reply["tool_calls"]})

    for m in messages:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            if m["tool_calls"]:
                with st.expander("🔧 Agent activity - backend tools used"):
                    for c in m["tool_calls"]:
                        st.markdown(f"{'✅' if c['success'] else '⛔'} `{c['tool']}` - {c['summary']}")
                        st.code(json.dumps(c["args"], ensure_ascii=False), language="json")

    if agent.pending:
        st.info("This action is waiting for your confirmation. Nothing has been saved yet.")
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("✅ Confirm", key="confirm_pending"):
            messages.append({"role": "user", "text": "✅ Confirm", "tool_calls": []})
            r = agent.confirm()
            messages.append({"role": "assistant", "text": r["text"], "tool_calls": r["tool_calls"]})
            st.rerun()
        if c2.button("✖ Cancel", key="decline_pending"):
            messages.append({"role": "user", "text": "✖ Cancel", "tool_calls": []})
            r = agent.decline()
            messages.append({"role": "assistant", "text": r["text"], "tool_calls": []})
            st.rerun()


def page_book():
    st.title("🗓️ Book an Appointment")
    t_services, t_avail, t_book = st.tabs(["Services", "Check availability", "Book"])
    svc = scheduler.list_services()["data"]["services"]
    names = [s["name"] for s in svc]

    with t_services:
        st.dataframe(pd.DataFrame([{"Service": s["name"], "Description": s["description"],
                                    "Duration (min)": s["duration_minutes"],
                                    f"Price ({s['currency']})": s["price"]} for s in svc]), hide_index=True)

    with t_avail:
        c1, c2 = st.columns(2)
        service = c1.selectbox("Service", names, key="av_service")
        d = c2.date_input("Date", value=default_date(), min_value=scheduler.now().date(),
                          max_value=scheduler.now().date() + timedelta(days=config.MAX_BOOKING_DAYS_AHEAD), key="av_date")
        r, slots = free_slots(d, service)
        if not r["success"]:
            st.error(r["error"]["message"])
        elif r["data"]["closed"]:
            st.warning(r["message"])
        elif slots:
            st.success(f"{len(slots)} free start times on {r['data']['weekday']} {r['data']['date_display']}")
            st.write("  ".join(f"`{s}`" for s in slots))
        else:
            st.warning("Fully booked on that date.")

    with t_book:
        c1, c2 = st.columns(2)
        service = c1.selectbox("Service", names, key="bk_service")
        d = c2.date_input("Date", value=default_date(), min_value=scheduler.now().date(),
                          max_value=scheduler.now().date() + timedelta(days=config.MAX_BOOKING_DAYS_AHEAD), key="bk_date")
        r, slots = free_slots(d, service)
        time_choice = st.selectbox("Time", slots, key="bk_time") if slots else None
        if not slots:
            st.info(r["message"] if r["success"] else r["error"]["message"])
        c3, c4 = st.columns(2)
        name = c3.text_input("Your name", key="bk_name")
        contact = c4.text_input("E-mail or phone", key="bk_contact", help="You will need this to change or cancel the booking.")
        if st.button("Review booking", disabled=time_choice is None):
            args = {"customer_name": name, "contact": contact, "service": service,
                    "start_time": f"{d.isoformat()} {time_choice}"}
            pv = tools.preview_write_tool("create_appointment", args)
            if pv["success"]:
                st.session_state["review"] = {"args": args, "preview": pv["data"]}
            else:
                st.session_state.pop("review", None)
                st.error(pv["error"]["message"])
        rev = st.session_state.get("review")
        if rev:
            p = rev["preview"]
            st.markdown("#### Please confirm your booking")
            st.table(pd.DataFrame({"Detail": ["Service", "Date", "Time", "Duration", "Customer", "Contact"],
                                   "Value": [p["service"], p["date_display"], p["time"], f"{p['duration_minutes']} minutes",
                                             p["customer_name"], p["contact"]]}).set_index("Detail"))
            b1, b2, _ = st.columns([1, 1, 4])
            if b1.button("✅ Confirm booking"):
                res = tools.run_write_tool("create_appointment", rev["args"])   # re-validated on the server
                st.session_state.pop("review", None)
                if res["success"]:
                    a = res["data"]["appointment"]
                    flash("success", f"Booked! Appointment ID #{a['id']} - {a['service']} on {a['date_display']} at {a['time']}.")
                else:
                    flash("error", res["error"]["message"] + " Please choose another time.")
                st.rerun()
            if b2.button("Edit"):
                st.session_state.pop("review", None)
                st.rerun()


def page_my_appointments():
    st.title("📋 My Appointments")
    st.caption("Enter the e-mail or phone number you booked with. It acts as your password, so only you can see or change your bookings.")
    c1, c2 = st.columns([3, 1])
    contact = c1.text_input("E-mail or phone", value=st.session_state.get("my_contact", ""), key="my_contact_input")
    if c2.button("Show my appointments"):
        st.session_state["my_contact"] = contact
        st.rerun()
    verified = st.session_state.get("my_contact")
    if not verified:
        return
    r = scheduler.get_appointments(verified)
    if not r["success"]:
        st.error(r["error"]["message"])
        return
    appts = r["data"]["appointments"]
    if not appts:
        st.info("No upcoming appointments found for that contact.")
        return
    st.dataframe(appts_df(appts), hide_index=True)
    for a in appts:
        with st.expander(f"#{a['id']} - {a['service']} - {a['weekday']} {a['date_display']} at {a['time']}"):
            appointment_actions(a, verification=verified)


def page_admin():
    st.title("🛠️ Admin Dashboard")
    if not st.session_state.get("is_admin"):
        entered = st.text_input("Admin password", type="password")
        if st.button("Log in"):
            if hmac.compare_digest(entered.encode(), os.getenv("ADMIN_PASSWORD", "admin123").encode()):
                st.session_state["is_admin"] = True
                st.rerun()
            st.error("Wrong password.")
        st.caption("Demo password is set by ADMIN_PASSWORD in .env (default shown in the README).")
        return
    if st.button("Log out"):
        st.session_state["is_admin"] = False
        st.rerun()

    everything = scheduler.list_all_appointments()["data"]["appointments"]
    today = scheduler.now().date().isoformat()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("All appointments", len(everything))
    m2.metric("Confirmed", sum(a["status"] == "confirmed" for a in everything))
    m3.metric("Cancelled", sum(a["status"] == "cancelled" for a in everything))
    m4.metric("Today", sum(a["date"] == today and a["status"] == "confirmed" for a in everything))

    f1, f2, f3 = st.columns(3)
    use_date = f1.checkbox("Filter by date")
    day = f1.date_input("Date", value=scheduler.now().date(), key="adm_filter_date", disabled=not use_date)
    status = f2.selectbox("Status", ["All", "confirmed", "cancelled"])
    upcoming = f3.checkbox("Upcoming only", value=True)
    res = scheduler.list_all_appointments(date=day.isoformat() if use_date else None,
                                          status=None if status == "All" else status, upcoming_only=upcoming)
    rows = res["data"]["appointments"] if res["success"] else []
    if rows:
        st.dataframe(appts_df(rows), hide_index=True)
    else:
        st.info("No appointments match these filters.")

    st.subheader("Manage an appointment")
    manageable = [a for a in rows if a["status"] == "confirmed" and a["start_time"] > scheduler.now().strftime(config.DATETIME_FORMAT)]
    if not manageable:
        st.caption("No upcoming confirmed appointments in the current list.")
        return
    choice = st.selectbox("Choose an appointment", manageable,
                          format_func=lambda a: f"#{a['id']} - {a['customer_name']} - {a['service']} - {a['date']} {a['time']}")
    appointment_actions(choice, admin=True)


def page_about():
    st.title("ℹ️ About the Project")
    st.markdown(f"""
**{config.BUSINESS_NAME}** is a college prototype of an *AI appointment scheduler agent* for small service
businesses (salons, tutors, consultants, repair shops). It is a demonstration - no real business data is used.

### Chatbot vs. AI agent
A normal chatbot only produces text. This **agent** decides which *tool* to call (check availability, create,
reschedule, cancel), runs it on the real database, reads the result and continues the conversation.

### How the pieces fit
| Layer | File | Responsibility |
|---|---|---|
| Interface | `app.py` | Streamlit pages |
| Agent | `ai_agent.py`, `tools.py` | Chooses tools, enforces user confirmation |
| Fallback | `fallback.py` | Rule-based assistant when no API key is available |
| Engine | `scheduler.py` | Rules: hours, overlaps, validation, verification |
| Storage | `database.py` | SQLite tables, indexes, no-overlap triggers |

### Agent tools
`list_services` - `check_availability` - `create_appointment` - `get_appointments` - `reschedule_appointment` - `cancel_appointment`

### Safety design
- The AI never invents slots: availability always comes from the database.
- Create/reschedule/cancel run **only after the user confirms**, and availability is re-checked at save time.
- Changing or cancelling needs the appointment ID **and** the contact used when booking.
- All SQL is parameterised; customer messages are treated as untrusted data.

### Known limitations
Contact-based verification is not full authentication (no one-time codes); SQLite suits a single-server demo;
fallback mode understands a limited set of phrasings. See the README for details.
""")


PAGES = {"🏠 Home": page_home, "💬 AI Booking Assistant": page_assistant, "🗓️ Book an Appointment": page_book,
         "📋 My Appointments": page_my_appointments, "🛠️ Admin Dashboard": page_admin, "ℹ️ About the Project": page_about}


def main():
    st.sidebar.markdown(f"## 📅 {config.BUSINESS_NAME}")
    st.sidebar.caption(config.BUSINESS_TAGLINE)
    choice = st.sidebar.radio("Navigate", list(PAGES), label_visibility="collapsed")
    agent = st.session_state.get("agent")
    st.sidebar.divider()
    if agent is not None:
        st.sidebar.caption(f"Assistant: {'🤖 AI mode' if agent.mode == 'ai' else '⚙️ fallback mode'}")
    else:
        has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
        st.sidebar.caption("Assistant: " + ("🤖 AI mode (key found)" if has_key else "⚙️ fallback mode (no API key)"))
    show_flash()
    PAGES[choice]()


main()
