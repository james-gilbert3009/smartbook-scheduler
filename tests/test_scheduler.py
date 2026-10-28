"""Automated tests for the scheduling engine (no AI, no internet needed)."""
import sqlite3

import database
import scheduler
from tests.common import CONTACT, MONDAY, SATURDAY, SUNDAY, TUESDAY, DbTestCase


class TestScheduler(DbTestCase):

    # 1 -------------------------------------------------------------------
    def test_01_list_services(self):
        r = scheduler.list_services()
        self.assertTrue(r["success"])
        services = {s["name"]: s for s in r["data"]["services"]}
        self.assertEqual(set(services), {"Consultation", "Standard appointment", "Extended appointment"})
        self.assertEqual(services["Consultation"]["duration_minutes"], 30)
        self.assertEqual(services["Extended appointment"]["duration_minutes"], 60)
        self.assertGreater(services["Consultation"]["price"], 0)

    # 2 -------------------------------------------------------------------
    def test_02_availability_on_working_day(self):
        r = scheduler.check_availability(MONDAY, "Consultation")
        self.assertTrue(r["success"])
        slots = r["data"]["slots"]
        self.assertFalse(r["data"]["closed"])
        self.assertEqual(slots[0], "09:00")
        self.assertEqual(slots[-1], "16:30")          # last 30-min slot ends exactly at 17:00
        self.assertEqual(len(slots), 16)
        self.assertEqual(self.slots(MONDAY, "Extended appointment")[-1], "16:00")  # 60 min must end by 17:00

    # 3 -------------------------------------------------------------------
    def test_03_availability_on_closed_day(self):
        for day in (SATURDAY, SUNDAY):
            r = scheduler.check_availability(day, "Consultation")
            self.assertTrue(r["success"])
            self.assertTrue(r["data"]["closed"])
            self.assertEqual(r["data"]["slots"], [])
        self.assertEqual(self.code(self.book(f"{SATURDAY} 10:00")), "CLOSED")

    # 4 -------------------------------------------------------------------
    def test_04_book_available_slot(self):
        r = self.book(f"{MONDAY} 14:00")
        self.assertTrue(r["success"], r)
        a = r["data"]["appointment"]
        self.assertIsInstance(a["id"], int)
        self.assertEqual((a["start_time"], a["end_time"], a["status"]), (f"{MONDAY} 14:00", f"{MONDAY} 14:30", "confirmed"))
        stored = scheduler.get_appointments(CONTACT)["data"]["appointments"]   # really in the DB
        self.assertEqual([x["id"] for x in stored], [a["id"]])
        self.assertNotIn("14:00", self.slots(MONDAY))

    # 5 -------------------------------------------------------------------
    def test_05_reject_outside_business_hours(self):
        self.assertEqual(self.code(self.book(f"{MONDAY} 08:00")), "OUTSIDE_HOURS")
        self.assertEqual(self.code(self.book(f"{MONDAY} 17:00")), "OUTSIDE_HOURS")
        self.assertEqual(self.code(self.book(f"{MONDAY} 16:30", "Extended appointment")), "OUTSIDE_HOURS")  # would end 17:30
        self.assertEqual(self.code(self.book(f"{MONDAY} 10:15")), "BAD_START_TIME")

    # 6 -------------------------------------------------------------------
    def test_06_reject_past_booking(self):
        self.assertEqual(self.code(self.book("2026-10-22 10:00")), "PAST_TIME")   # yesterday
        self.assertEqual(self.code(self.book("2026-10-23 09:00")), "PAST_TIME")   # earlier today (now = 10:00)
        self.assertEqual(self.code(scheduler.check_availability("2026-10-22", "Consultation")), "PAST_DATE")
        self.assertNotIn("09:00", self.slots("2026-10-23"))                       # today: only future slots
        self.assertIn("10:30", self.slots("2026-10-23"))

    # 7 -------------------------------------------------------------------
    def test_07_reject_conflicting_booking(self):
        self.assertTrue(self.book(f"{MONDAY} 10:00")["success"])
        self.assertEqual(self.code(self.book(f"{MONDAY} 10:00", name="Other Person", contact="other@example.com")), "SLOT_TAKEN")
        # partial overlap: a 60-min appointment at 09:30 runs 09:30-10:30 and hits the 10:00 booking
        self.assertEqual(self.code(self.book(f"{MONDAY} 09:30", "Extended appointment")), "SLOT_TAKEN")
        # back-to-back is fine
        self.assertTrue(self.book(f"{MONDAY} 09:30")["success"])
        self.assertTrue(self.book(f"{MONDAY} 10:30")["success"])

    # 8 -------------------------------------------------------------------
    def test_08_service_with_different_duration(self):
        r = self.book(f"{MONDAY} 11:00", "Extended appointment")
        self.assertTrue(r["success"])
        a = r["data"]["appointment"]
        self.assertEqual((a["duration_minutes"], a["end_time"]), (60, f"{MONDAY} 12:00"))
        ext = self.slots(MONDAY, "Extended appointment")
        self.assertNotIn("10:30", ext)     # 10:30-11:30 overlaps
        self.assertNotIn("11:30", ext)     # 11:30-12:30 overlaps
        self.assertIn("10:00", ext)        # 10:00-11:00 just fits before
        self.assertIn("12:00", ext)
        cons = self.slots(MONDAY, "Consultation")
        self.assertNotIn("11:00", cons)
        self.assertNotIn("11:30", cons)
        self.assertIn("10:30", cons)
        self.assertIn("12:00", cons)

    # 9 -------------------------------------------------------------------
    def test_09_reschedule_successfully(self):
        aid = self.book(f"{MONDAY} 10:00")["data"]["appointment"]["id"]
        r = scheduler.reschedule_appointment(aid, f"{TUESDAY} 14:00", CONTACT)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["data"]["appointment"]["start_time"], f"{TUESDAY} 14:00")
        self.assertIn("10:00", self.slots(MONDAY))        # old slot freed
        self.assertNotIn("14:00", self.slots(TUESDAY))    # new slot taken
        # can move to a time that overlaps its own old slot
        r2 = scheduler.reschedule_appointment(aid, f"{TUESDAY} 14:30", CONTACT)
        self.assertTrue(r2["success"], r2)

    # 10 ------------------------------------------------------------------
    def test_10_reject_invalid_reschedule(self):
        a = self.book(f"{MONDAY} 10:00")["data"]["appointment"]["id"]
        self.book(f"{MONDAY} 11:00", name="Other Person", contact="other@example.com")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, f"{MONDAY} 11:00", CONTACT)), "SLOT_TAKEN")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, f"{SATURDAY} 10:00", CONTACT)), "CLOSED")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, f"{MONDAY} 20:00", CONTACT)), "OUTSIDE_HOURS")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, "2026-10-20 10:00", CONTACT)), "PAST_TIME")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, "not a time", CONTACT)), "INVALID_DATETIME")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, f"{MONDAY} 10:00", CONTACT)), "SAME_TIME")
        self.assertEqual(self.code(scheduler.reschedule_appointment(a, f"{TUESDAY} 10:00", "wrong@example.com")),
                         "NOT_FOUND_OR_NOT_AUTHORIZED")
        self.assertEqual(self.code(scheduler.reschedule_appointment(9999, f"{TUESDAY} 10:00", CONTACT)),
                         "NOT_FOUND_OR_NOT_AUTHORIZED")
        still = scheduler.get_appointments(CONTACT)["data"]["appointments"][0]
        self.assertEqual(still["start_time"], f"{MONDAY} 10:00")   # nothing moved

    # 11 ------------------------------------------------------------------
    def test_11_cancel_appointment(self):
        aid = self.book(f"{MONDAY} 10:00")["data"]["appointment"]["id"]
        self.assertEqual(self.code(scheduler.cancel_appointment(aid, "wrong@example.com")), "NOT_FOUND_OR_NOT_AUTHORIZED")
        r = scheduler.cancel_appointment(aid, CONTACT)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["data"]["appointment"]["status"], "cancelled")
        self.assertEqual(scheduler.get_appointments(CONTACT)["data"]["appointments"], [])
        hist = scheduler.get_appointments(CONTACT, include_cancelled=True)["data"]["appointments"]
        self.assertEqual(hist[0]["status"], "cancelled")                    # row kept for history
        self.assertEqual(self.code(scheduler.cancel_appointment(aid, CONTACT)), "ALREADY_CANCELLED")

    # 12 ------------------------------------------------------------------
    def test_12_cancelled_slot_becomes_available_again(self):
        aid = self.book(f"{MONDAY} 14:00")["data"]["appointment"]["id"]
        self.assertNotIn("14:00", self.slots(MONDAY))
        scheduler.cancel_appointment(aid, CONTACT)
        self.assertIn("14:00", self.slots(MONDAY))
        self.assertTrue(self.book(f"{MONDAY} 14:00", name="Other Person", contact="other@example.com")["success"])

    # 13 ------------------------------------------------------------------
    def test_13_invalid_inputs(self):
        bad = [
            (("", CONTACT, "Consultation", f"{MONDAY} 10:00"), "INVALID_NAME"),
            ((None, CONTACT, "Consultation", f"{MONDAY} 10:00"), "INVALID_NAME"),
            (("Robert'); DROP TABLE appointments;--", CONTACT, "Consultation", f"{MONDAY} 10:00"), "INVALID_NAME"),
            (("<script>alert(1)</script>", CONTACT, "Consultation", f"{MONDAY} 10:00"), "INVALID_NAME"),
            (("Demo Student", "not-a-contact", "Consultation", f"{MONDAY} 10:00"), "INVALID_CONTACT"),
            (("Demo Student", "x' OR '1'='1", "Consultation", f"{MONDAY} 10:00"), "INVALID_CONTACT"),
            (("Demo Student", CONTACT, "Haircut", f"{MONDAY} 10:00"), "UNKNOWN_SERVICE"),
            (("Demo Student", CONTACT, None, f"{MONDAY} 10:00"), "UNKNOWN_SERVICE"),
            (("Demo Student", CONTACT, "Consultation", "tomorrow at 3"), "INVALID_DATETIME"),
            (("Demo Student", CONTACT, "Consultation", None), "INVALID_DATETIME"),
        ]
        for args, expected in bad:
            self.assertEqual(self.code(scheduler.create_appointment(*args)), expected, args)
        self.assertEqual(self.code(scheduler.check_availability("31/12/2026", "Consultation")), "INVALID_DATE")
        self.assertEqual(self.code(scheduler.check_availability(MONDAY, "Nonexistent")), "UNKNOWN_SERVICE")
        self.assertEqual(self.code(scheduler.get_appointments("bad contact")), "INVALID_CONTACT")
        self.assertEqual(self.code(scheduler.cancel_appointment("abc", CONTACT)), "INVALID_ID")
        self.assertEqual(self.code(scheduler.cancel_appointment(-5, CONTACT)), "INVALID_ID")
        self.assertEqual(self.code(scheduler.cancel_appointment(True, CONTACT)), "INVALID_ID")
        # nothing was written and the tables still exist
        self.assertEqual(scheduler.list_all_appointments()["data"]["appointments"], [])

    # ---- extra safety / integrity tests -----------------------------------
    def test_14_database_trigger_blocks_overlap_even_without_python_checks(self):
        self.book(f"{MONDAY} 10:00")
        with database.connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO appointments (customer_name, contact, service_id, start_time, end_time, status, created_at) "
                             "VALUES ('X Y', 'x@example.com', 1, ?, ?, 'confirmed', '2026-10-23 10:00:00')",
                             (f"{MONDAY} 10:15", f"{MONDAY} 10:45"))

    def test_15_data_persists_across_connections(self):
        self.book(f"{MONDAY} 10:00")
        with database.connection() as conn:          # a brand-new connection = like an app restart
            n = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        self.assertEqual(n, 1)

    def test_16_contact_is_the_verification_and_is_normalized(self):
        aid = self.book(f"{MONDAY} 10:00", contact="Demo.Student@Example.com")["data"]["appointment"]["id"]
        self.assertTrue(scheduler.preview_cancel(aid, "demo.student@example.com")["success"])
        self.assertFalse(scheduler.preview_cancel(aid, None)["success"])
        self.assertEqual(scheduler.normalize_contact("+371 2000-0000"), "+37120000000")

    def test_17_preview_changes_nothing(self):
        p = scheduler.preview_create("Demo Student", CONTACT, "Consultation", f"{MONDAY} 10:00")
        self.assertTrue(p["success"])
        self.assertEqual(scheduler.list_all_appointments()["data"]["appointments"], [])

    def test_18_admin_listing_filters(self):
        self.book(f"{MONDAY} 10:00")
        b = self.book(f"{TUESDAY} 10:00")["data"]["appointment"]["id"]
        scheduler.cancel_appointment(b, admin=True)
        L = lambda **k: scheduler.list_all_appointments(**k)["data"]["appointments"]
        self.assertEqual(len(L()), 2)
        self.assertEqual(len(L(status="confirmed")), 1)
        self.assertEqual(len(L(date=TUESDAY)), 1)
        self.assertEqual(self.code(scheduler.list_all_appointments(status="hacked")), "INVALID_STATUS")


if __name__ == "__main__":
    import unittest
    unittest.main()
