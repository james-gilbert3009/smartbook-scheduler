# SmartBook Demo - AI Appointment Scheduler Agent

## 1. Project title
**SmartBook Demo: an AI-powered appointment scheduling agent** (college prototype; fictional business).

## 2. Abstract
Small service businesses lose time and bookings answering the same scheduling questions by phone or message.
SmartBook Demo is a web application where a customer books, views, reschedules and cancels appointments by chatting
with an AI assistant. The assistant is an *agent*: it chooses and calls backend tools (availability check, create,
reschedule, cancel) and reports the real outcome. A Python scheduling engine with an SQLite database owns all rules
(opening hours, overlaps, verification), so the AI can neither invent free slots nor fabricate confirmations. A
rule-based fallback keeps the system fully usable without an AI API key.

## 3. Problem statement
Manual scheduling is repetitive and error-prone (double bookings, out-of-hours requests, forgotten cancellations).
Plain chatbots that only generate text are unsuitable because they can "hallucinate" availability. The problem: build a
conversational scheduler whose answers are always grounded in the real calendar.

## 4. Project objectives
1. Conversational booking, viewing, rescheduling and cancelling.
2. A reliable engine: hours, durations, overlap prevention, past-time checks, status tracking.
3. AI tool calling with mandatory user confirmation before any change.
4. Persistent SQLite storage, an admin dashboard, a classic booking form.
5. Input validation, parameterised SQL, verification before changes.
6. Automated, deterministic tests that do not need a live AI.

## 5. Scope
**In scope:** one fictional business, three services, Mon-Fri 09:00-17:00, single timezone, English chat.
**Out of scope:** payments, e-mail/SMS reminders, multiple staff or locations, real authentication, calendar sync.

## 6. Technology stack
Python 3.10+ - Streamlit (UI) - SQLite (storage) - Anthropic API with tool use (AI; optional) - `unittest` (tests).
Only `streamlit`, `pandas`, `python-dotenv`, `anthropic`, `tzdata` are needed (`requirements.txt`).

## 7. System architecture
```
 Browser ──> app.py (Streamlit pages)
                │
                ├── AI Booking Assistant ──> ai_agent.py ──┬─ AI model (tool calling)   [AI mode]
                │                                          └─ fallback.py (rules)       [no API key]
                │                                   │ both use the SAME tools
                │                                   v
                │                               tools.py   (tool list, preview/confirm gate, message formatting)
                │                                   │
                └── forms / dashboard ─────────────>│
                                                    v
                                              scheduler.py   (rules + validation + verification)
                                                    │
                                                    v
                                              database.py ──> smartbook.db (SQLite: tables, indexes, triggers)
```
| File | Role |
|---|---|
| `app.py` | Six Streamlit sections |
| `ai_agent.py` | Agent loop, confirmation gate, graceful failure |
| `tools.py` | Tool schemas, read/write split, user-facing messages built from real data |
| `fallback.py` | Rule-based intent routing used without an API key |
| `scheduler.py` | Scheduling engine; all six required functions |
| `database.py` | Schema, transactions, seed data |
| `config.py` | Services, hours, timezone, slot size - edit here |
| `init_db.py` | Optional manual DB initialisation |
| `tests/` | 29 automated tests |

*Structure changes vs. the brief:* added `config.py` (one place to change hours/services), `tools.py` (separates what the AI may call
from the engine) and `fallback.py` (keeps `ai_agent.py` short). Nothing was removed.

**Chatbot vs. AI agent.** A normal chatbot only produces text from what it "remembers". This agent *acts*: it decides that a
question needs `check_availability`, runs it on the real database, reads the structured result, then continues. For changes it
proposes an action, waits for the human's yes, and executes through validated backend code.

## 8. AI agent workflow
1. User message -> model (with tool descriptions and today's date/hours in the system prompt).
2. The model may call **read tools** (`list_services`, `check_availability`, `get_appointments`); the code runs them and returns the real result; the loop repeats (max 6 steps).
3. For **write tools** (`create_appointment`, `reschedule_appointment`, `cancel_appointment`) the code only **validates** (a "preview" with identical rules, nothing saved) and stores the action as *pending*.
4. The *code* (not the model) shows the summary: service, date, time, duration, customer, and asks for confirmation.
5. User says "yes" / clicks **Confirm** -> the engine **re-validates inside a database transaction** and saves; the success text and the appointment ID come from the stored row.
6. "No" or any unrelated message discards the pending action.

Why this is safe: the model cannot save anything, cannot see an `admin` option, and cannot produce a booking confirmation - those
strings are generated by `tools.format_result()` from the database result. Unknown tools are rejected. If the API fails, the agent
falls back to rules for that message instead of crashing.

**Scheduling validation** (`scheduler._validate_slot`) checks, in order: not in the past, not beyond 90 days, not a closed day, inside opening hours
*including the service duration*, aligned to the 30-minute grid, and no overlap with any confirmed appointment (interval test
`existing.start < new.end AND existing.end > new.start`, so a 60-minute booking correctly blocks a 30-minute one starting inside it).

**Conflict prevention** has three layers: (1) the pre-check above; (2) the re-check inside `BEGIN IMMEDIATE` (SQLite's write lock makes
check-then-insert indivisible); (3) database triggers `trg_no_overlap_insert/update` that abort an overlapping confirmed row even if
the Python code were bypassed (tested directly in `test_14`).

## 9. Database design
```
services(id PK, name UNIQUE, description, duration_minutes >0, price >=0)
appointments(id PK, customer_name, contact, service_id FK -> services.id,
             start_time, end_time, status CHECK IN ('confirmed','cancelled'), created_at,  CHECK end_time > start_time)
indexes: appointments(start_time), appointments(contact), appointments(status, start_time)
triggers: no overlapping 'confirmed' rows (insert and update)
```
Times are stored as local business time `YYYY-MM-DD HH:MM` (sortable as text); `created_at` is UTC. Cancellation is a *soft delete*
(`status='cancelled'`), so history is kept and cancelled rows are ignored by availability. Seed data is fictional (`database.init_db()`).

## 10. Main features
AI chat assistant - fallback mode with visible banner - "Agent activity" panel showing every tool call - booking form with live
slots - services page - availability view - My Appointments (view/reschedule/cancel) - admin dashboard (password, filters by
date/status, metrics, reschedule/cancel) - persistent SQLite - about page.

## 11. Installation instructions
```bash
cd smartbook
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```
Edit `.env`: put your key in `ANTHROPIC_API_KEY` (optional) and choose `ADMIN_PASSWORD`. Never share or commit `.env`.

## 12. How to run the application
```bash
streamlit run app.py               # opens http://localhost:8501
python -m unittest discover -v     # run the tests (or: pip install pytest && pytest)
```
The database is created automatically on first start. Admin password: whatever you set in `.env` (default `admin123` if unset - demo only).
The sidebar and the assistant page show whether **AI mode** or **fallback mode** is active.
See `DEMO.md` for a 5-minute presentation script and `DEPLOYMENT.md` for publishing.

## 13. Test cases and results
Run date: 23 Oct 2026, Python 3.12.3, command `python -m unittest discover -v`. Full output: `tests/last_test_run.txt`.
**Result: 29 tests run, 29 passed, 0 failed (0.31 s).** Tests use a temporary database per test and a fixed clock
(Friday 23 Oct 2026 10:00), so they are deterministic and need no internet or API key.

| # | Required scenario | Test | Result |
|---|---|---|---|
| 1 | List services | `test_01_list_services` | PASS |
| 2 | Availability, working day | `test_02_availability_on_working_day` | PASS |
| 3 | Availability, closed day | `test_03_availability_on_closed_day` | PASS |
| 4 | Book an available slot | `test_04_book_available_slot` | PASS |
| 5 | Reject outside business hours | `test_05_reject_outside_business_hours` | PASS |
| 6 | Reject booking in the past | `test_06_reject_past_booking` | PASS |
| 7 | Reject conflicting booking (incl. partial overlap) | `test_07_reject_conflicting_booking` | PASS |
| 8 | Different service duration | `test_08_service_with_different_duration` | PASS |
| 9 | Reschedule successfully | `test_09_reschedule_successfully` | PASS |
| 10 | Reject invalid reschedule | `test_10_reject_invalid_reschedule` | PASS |
| 11 | Cancel | `test_11_cancel_appointment` | PASS |
| 12 | Cancelled slot available again | `test_12_cancelled_slot_becomes_available_again` | PASS |
| 13 | Invalid inputs (incl. SQL-injection-style text) | `test_13_invalid_inputs`, `test_chat_invalid_and_malicious_input_is_safe` | PASS |
| 14 | Missing AI API key | `test_missing_api_key_uses_fallback_gracefully` | PASS |

Extra tests: DB trigger blocks overlaps without Python checks; data persists across connections; contact verification; preview changes nothing;
admin filters; the full 12-step demo scenario through the chat (book -> nothing saved before "yes" -> duplicate rejected -> cancel -> slot free
again); the AI tool loop with a **scripted fake model** (booking not saved until confirmed; invalid slot refused; unknown/admin tool calls ignored;
API outage falls back without crashing).

**What was NOT verified in the build environment (be honest in your presentation):**
- The Streamlit interface was *not* opened in a real browser/Streamlit server (not installed there, no network). Instead all six pages and their button flows were executed against a stub of the Streamlit API, which found no errors - but please run `streamlit run app.py` yourself and click through once.
- No **live** AI call was made (no key/network). The tool-calling loop was tested with a scripted fake client that follows Anthropic's message format. First live run: check `ANTHROPIC_MODEL` is a model your key can use.

## 14. Limitations
- **Security:** the contact (e-mail/phone) is the only verification; anyone who knows it and the ID can change that booking. Real systems need one-time codes or accounts. Appointment IDs alone give no access (same error for "not found" and "wrong contact").
- Admin login is a single shared password in an environment variable; no rate limiting or lockout.
- Chat text is sent to the AI provider in AI mode; the database stores only name and contact.
- SQLite is fine for one server process; not for several app instances (see `DEPLOYMENT.md`).
- Fallback mode understands a limited set of phrasings; the AI mode is more flexible but depends on the model following the system prompt (the backend still enforces every rule).
- One resource/staff member: two appointments can never overlap, even for different services.
- Prompt-injection cannot bypass rules (validation, confirmation and verification live in code), but the model can still be tricked into *wasting* turns or giving odd replies.

## 15. Future improvements
E-mail/SMS reminders; one-time-code verification; multiple staff and per-service calendars; waiting list; PostgreSQL for multi-instance hosting;
streaming replies; multilingual chat; admin editing of services/hours in the UI; calendar (ICS/Google) export.

## 16. Conclusion
SmartBook Demo shows the difference between a chatbot and an agent: language understanding is delegated to an AI model, while truth
(availability, validation, security, persistence) stays in deterministic, tested code. Every change needs explicit confirmation and is re-validated at
save time, the database independently prevents double-booking, and the system keeps working - visibly in fallback mode - without an API key.
