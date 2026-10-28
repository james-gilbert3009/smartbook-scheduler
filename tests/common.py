"""Shared test setup: a fresh temporary database and a FIXED clock for every test."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime

import config
import database
import scheduler

# "Now" in every test: Friday 23 Oct 2026, 10:00.  Monday 26 Oct is the next working day.
FIXED_NOW = datetime(2026, 10, 23, 10, 0)
MONDAY, TUESDAY, SATURDAY, SUNDAY = "2026-10-26", "2026-10-27", "2026-10-24", "2026-10-25"
CONTACT = "demo.student@example.com"


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.old_path = database.DB_PATH
        database.set_db_path(os.path.join(self.tmpdir, "test.db"))
        database.init_db()
        scheduler.set_clock(lambda: FIXED_NOW)

    def tearDown(self):
        scheduler.set_clock()
        database.set_db_path(self.old_path)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # helpers -----------------------------------------------------------
    def book(self, start, service="Consultation", name="Demo Student", contact=CONTACT):
        return scheduler.create_appointment(name, contact, service, start)

    def slots(self, date, service="Consultation"):
        return scheduler.check_availability(date, service)["data"]["slots"]

    def code(self, result):
        self.assertFalse(result["success"], f"expected failure but got {result}")
        return result["error"]["code"]
