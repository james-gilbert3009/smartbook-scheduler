# 5-minute demonstration script

**Before you start:** `streamlit run app.py`. Check the sidebar says AI mode (or fallback - both work). Use a clean database for a clean story
(stop the app, delete `smartbook.db`, start again).

> ⚠️ **Date caveat:** "tomorrow" only works if tomorrow is Mon-Fri. On a Friday/Saturday/Sunday the assistant will (correctly!) say
> "we are closed". Either demo it on purpose (nice proof the AI doesn't invent slots) or say *"Monday afternoon"*. Fallback mode understands
> weekday names, "tomorrow", "2026-10-26", "26 October", "26.10.2026".

| Min | Step | You say / do | What to point out |
|---|---|---|---|
| 0:00 | Intro | Home page | Fictional business, services, opening hours (Mon-Fri 09-17) |
| 0:30 | 1-2 | **AI Booking Assistant** -> "I'd like a consultation on Monday afternoon" | Banner shows AI/fallback mode |
| 1:00 | 3-4 | Open **🔧 Agent activity** under the reply | The agent called `check_availability`; times come from the database, not the AI's imagination |
| 1:30 | 5 | "14:00" -> give name "Demo Student" -> "demo@example.com" | The agent asks only for what is missing |
| 2:00 | 6 | Summary box appears | Service, date, time, duration, customer. **Nothing is saved yet** (optional: show empty dashboard) |
| 2:20 | 7-8 | Click **✅ Confirm** (or type "yes") | Real appointment ID from the database |
| 2:45 | 9 | **Admin Dashboard** (password) | The row is in SQLite - not hard-coded. Show filters |
| 3:15 | 10 | New conversation (*Try asking... -> Start a new conversation*): "book a consultation on Monday at 14:00", name "Other Person", `other@example.com` | Rejected: slot overlaps; alternatives offered. Mention 3 layers of conflict protection |
| 3:50 | 11 | "cancel my appointment, my email is demo@example.com" -> yes | Needs the contact (verification) + confirmation |
| 4:20 | 12 | "What appointments are available on Monday?" | 14:00 is free again |
| 4:40 | Wrap | Terminal: `python -m unittest discover -v` | 29/29 tests, no AI needed for scheduling tests |

## Cheat sheet for questions
- **Chatbot vs. agent?** Chatbot = text only. Agent = chooses tools, runs them, uses results, needs confirmation for actions.
- **Tools?** `list_services`, `check_availability`, `create_appointment`, `get_appointments`, `reschedule_appointment`, `cancel_appointment`.
- **Can the AI double-book / invent a slot?** No: the engine validates (hours, past, overlap) and the DB triggers refuse overlaps.
- **What if the AI is down / no key?** Fallback mode, same tools, same confirmation.
- **Security?** Parameterised SQL, validation, contact verification, confirmation, no arbitrary code execution; limits listed in README section 14.
- **Test results?** 29 run, 29 passed (see README 13) - and say what was *not* automated (browser UI, live AI).
