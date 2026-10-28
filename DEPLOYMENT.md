# Optional: publishing the app online

Not required for the assignment. **No deployment was performed, so there is no public URL.** Follow these steps yourself with your own accounts.

## Temporary vs. persistent storage (important)
Many free hosts give the app a *temporary* disk: files vanish when the app restarts, redeploys or sleeps. SQLite stores everything in one file,
so on such hosts **your appointments would disappear**. Also, SQLite is not safe if several app instances each have their own copy of the file.

| Situation | Use |
|---|---|
| Demo for a few minutes, losing data is OK | Any host (e.g. Streamlit Community Cloud) - tell viewers data is temporary |
| One app instance + a **persistent disk/volume** | SQLite: mount the disk, set `DB_PATH=/path/on/disk/smartbook.db` |
| Several instances, or host has no persistent disk | Managed **PostgreSQL** (see below) |

Check your chosen provider's current documentation and pricing for "persistent disk/volume" - free tiers often do not include one.

## Steps (generic Python host)
1. Put the project in a GitHub repository. **Check `.env` and `*.db` are not committed** (`.gitignore` already lists them).
2. Create a web service from the repo. Build command: `pip install -r requirements.txt`. Start command:
   `streamlit run app.py --server.port $PORT --server.address 0.0.0.0` (some hosts, like Streamlit Community Cloud, only need `app.py` as the entry file).
3. Set **environment variables in the host's dashboard** (never in the code): `ANTHROPIC_API_KEY`, `ADMIN_PASSWORD`, `BUSINESS_TIMEZONE`, and `DB_PATH` if you attach a disk.
4. Deploy and open the URL the host gives you.

## Migrating to PostgreSQL (if needed)
1. Create a managed PostgreSQL database; copy its connection string into an environment variable `DATABASE_URL`.
2. Replace `sqlite3` in `database.py` with a driver such as `psycopg` and change `?` placeholders to `%s`. The rest of the app only talks to `database.connection()/transaction()`.
3. Replace the SQLite triggers with a PostgreSQL exclusion constraint (the standard way to forbid overlapping ranges):
   ```sql
   CREATE EXTENSION IF NOT EXISTS btree_gist;
   ALTER TABLE appointments ADD CONSTRAINT no_overlap
     EXCLUDE USING gist (tsrange(start_time, end_time) WITH &&) WHERE (status = 'confirmed');
   ```
   (store `start_time`/`end_time` as `TIMESTAMP` instead of text). Use `SELECT ... FOR UPDATE` or the constraint itself to handle races.
4. Run the tests against a test database.

## Post-deployment checklist
- [ ] Home page loads; sidebar shows AI mode (or intentionally fallback).
- [ ] Assistant answers "What services do you offer?"
- [ ] Book -> Confirm -> appears in Admin Dashboard.
- [ ] Same slot again is rejected.
- [ ] Cancel works; slot reappears in Availability.
- [ ] Restart the app: the appointment is still there (proves persistence) - or you know it isn't (temporary storage).
- [ ] Admin password is not the default; the API key is only in the host's settings; no `.env` in the repo.
- [ ] Set a spending limit on your AI provider account.
