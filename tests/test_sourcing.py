"""Tests of the supplier status: scripts/supplier_status.py alone, then the real sourcing.py
with canned supplier answers (no network) and report.py on what it wrote."""
import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import supplier_status  # noqa: E402
from supplier_status import Lookups, RateLimited, supplier_status as status_of, used_and_not  # noqa: E402


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://supplier.example", code, "error", {}, io.BytesIO(body))


class StatusTest(unittest.TestCase):
    def test_ok_partial_failed(self):
        self.assertEqual(status_of(40, 0), "ok")
        self.assertEqual(status_of(0, 0), "ok")                      # a key, but no part to ask about
        self.assertEqual(status_of(40, 3, "TimeoutError: timed out"),
                         "partial (3 of 40 lookups failed: TimeoutError: timed out)")
        self.assertEqual(status_of(40, 40, "HTTPError: HTTP Error 401: Unauthorized"),
                         "failed (HTTPError: HTTP Error 401: Unauthorized)")
        self.assertEqual(status_of(1, 1, "boom"), "failed (boom)")

    def test_rate_limited(self):
        self.assertTrue(supplier_status.rate_limited(RateLimited("Maximum calls per minute reached.")))
        self.assertTrue(supplier_status.rate_limited(http_error(429)))
        self.assertFalse(supplier_status.rate_limited(http_error(403)))
        self.assertFalse(supplier_status.rate_limited(TimeoutError()))


class LookupsTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def answer(self, *answers):
        """A lookup that gives these answers in turn; an exception is raised."""
        left = list(answers)

        def fn(key):
            self.calls.append(key)
            a = left.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        return fn

    def test_one_failure_among_many_is_partial(self):
        look = Lookups(wait=0)
        fn = self.answer({"sku": "1"}, ValueError("bad answer"), None, ValueError("another"))
        self.assertEqual([look.run("Mouser", fn, k) for k in "abcd"], [{"sku": "1"}, None, None, None])
        self.assertEqual(look.entry("Mouser"), {"name": "Mouser", "lookups": 4, "failed": 2, "status":
                                                "partial (2 of 4 lookups failed: ValueError: bad answer)"})

    def test_all_failures_is_failed(self):
        look = Lookups(wait=0)
        for k in "abc":
            look.run("Mouser", self.answer(OSError("no route")), k)
        self.assertEqual(look.entry("Mouser")["status"], "failed (OSError: no route)")

    def test_not_found_is_not_a_failure(self):
        look = Lookups(wait=0)
        self.assertIsNone(look.run("LCSC", self.answer(None), "C1"))
        self.assertEqual(look.entry("LCSC"), {"name": "LCSC", "status": "ok", "lookups": 1, "failed": 0})

    def test_suppliers_are_counted_apart(self):
        look = Lookups(wait=0)
        look.run("Mouser", self.answer(OSError("down")), "a")
        look.run("Farnell", self.answer({"sku": "2"}), "a")
        self.assertTrue(look.entry("Mouser")["status"].startswith("failed"))
        self.assertEqual(look.entry("Farnell")["status"], "ok")

    def test_no_key(self):
        self.assertEqual(Lookups().entry("DigiKey", enabled=False),
                         {"name": "DigiKey", "status": "no API key", "lookups": 0, "failed": 0})

    def test_rate_limit_gets_one_retry_after_a_wait(self):
        look = Lookups(wait=7)
        with mock.patch("time.sleep") as sleep:
            got = look.run("Mouser", self.answer(RateLimited("per minute"), {"sku": "1"}), "a")
        self.assertEqual(got, {"sku": "1"})
        self.assertEqual(self.calls, ["a", "a"])
        sleep.assert_called_once_with(7)
        self.assertEqual(look.entry("Mouser"), {"name": "Mouser", "status": "ok", "lookups": 1, "failed": 0})

    def test_http_429_is_retried_too(self):
        look = Lookups(wait=0)
        self.assertEqual(look.run("Farnell", self.answer(http_error(429), {"sku": "1"}), "a"), {"sku": "1"})

    def test_retry_that_fails_counts_once(self):
        look = Lookups(wait=0)
        self.assertIsNone(look.run("Mouser", self.answer(RateLimited("per minute"), RateLimited("still")), "a"))
        self.assertEqual(self.calls, ["a", "a"])                    # one retry, not more
        e = look.entry("Mouser")
        self.assertEqual((e["lookups"], e["failed"]), (1, 1))

    def test_other_errors_are_not_retried(self):
        look = Lookups(wait=0)
        with mock.patch("time.sleep") as sleep:
            self.assertIsNone(look.run("Mouser", self.answer(http_error(401), {"sku": "1"}), "a"))
        self.assertEqual(self.calls, ["a"])
        sleep.assert_not_called()

    def test_retries_per_supplier_are_limited(self):
        look = Lookups(wait=5, retries=2)
        with mock.patch("time.sleep") as sleep:
            for k in "abcde":
                look.run("Mouser", self.answer(RateLimited("spent"), RateLimited("spent")), k)
            look.run("Farnell", self.answer(http_error(429), {"sku": "1"}), "a")   # its own allowance
        self.assertEqual(self.calls, list("aabbcde") + ["a", "a"])
        self.assertEqual(sleep.call_count, 3)
        self.assertTrue(look.entry("Mouser")["status"].startswith("failed (RateLimited: spent"))
        self.assertEqual(look.entry("Farnell")["status"], "ok")


class ReportLabelsTest(unittest.TestCase):
    def test_partial_is_used_with_a_note(self):
        used, other = used_and_not([
            {"name": "LCSC", "status": "ok", "lookups": 40, "failed": 0},
            {"name": "Mouser", "status": "partial (3 of 40 lookups failed: RateLimited: Maximum calls (per minute))",
             "lookups": 40, "failed": 3},
            {"name": "Farnell", "status": "failed (HTTPError: HTTP Error 403: Forbidden)", "lookups": 40, "failed": 40},
            {"name": "DigiKey", "status": "no API key", "lookups": 0, "failed": 0}])
        self.assertEqual(used, ["LCSC", "Mouser (3 of 40 lookups failed)"])
        self.assertEqual(other, ["Farnell (failed)", "DigiKey (no API key)"])


BOM = ("Reference,Value,Footprint,MPN,LCSC,DNP\n"
       "R1,10k,R_0603,AAA,,\n"
       "C1,100nF,C_0603,BBB,,\n"
       "U1,LDO,SOT-23,CCC,,\n")
QUOTA = b'{"Errors":[{"Code":"TooManyRequests","Message":"Maximum calls per minute reached."}],"SearchResults":null}'


def mouser_part(mpn):
    return {"Errors": [], "SearchResults": {"Parts": [{
        "MouserPartNumber": "000-" + mpn, "ManufacturerPartNumber": mpn, "Manufacturer": "Maker",
        "AvailabilityInStock": "500", "Min": "1", "Mult": "1",
        "PriceBreaks": [{"Quantity": 1, "Price": "$0.50", "Currency": "USD"}]}]}}


class SourcingRunTest(unittest.TestCase):
    """sourcing.py from start to end: only Mouser has a key, and its answers are canned."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        with open(self.path("bom.csv"), "w", encoding="utf-8") as f:
            f.write(BOM)
        self.asked = []

    def path(self, name):
        return os.path.join(self.dir, name)

    def run_sourcing(self, answers):
        """answers: MPN -> list of answers in turn (a dict, or an exception to raise)."""
        def urlopen(req, timeout=None):
            if "api.mouser.com" not in req.full_url:
                raise AssertionError("unexpected request: " + req.full_url)
            mpn = json.loads(req.data)["SearchByPartRequest"]["mouserPartNumber"]
            self.asked.append(mpn)
            a = answers[mpn].pop(0)
            if isinstance(a, Exception):
                raise a
            return io.BytesIO(json.dumps(a).encode())

        env = {k: v for k, v in os.environ.items()
               if k not in ("FARNELL_API_KEY", "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")}
        env["MOUSER_API_KEY"] = "test-key"
        argv = ["sourcing.py", "--bom", self.path("bom.csv"), "--boards", "1", "--currency", "USD",
                "--json", self.path("sourcing.json"), "--csv", self.path("sourcing.csv")]
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep"), \
                mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(os.path.join(SCRIPTS, "sourcing.py"), run_name="__main__")
        with open(self.path("sourcing.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data, {s["name"]: s for s in data["suppliers"]}

    def report(self):
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "report.py"), "--name", "board",
                        "--bom", self.path("bom.csv"), "--sourcing", self.path("sourcing.json"),
                        "--out", self.path("report.md"), "--out-json", self.path("report.json")], check=True)
        with open(self.path("report.md"), encoding="utf-8") as f:
            return f.read()

    def test_one_failed_lookup_keeps_the_supplier(self):
        data, sup = self.run_sourcing({"AAA": [mouser_part("AAA")], "BBB": [mouser_part("BBB")],
                                       "CCC": [TimeoutError("timed out")]})
        self.assertEqual(sup["Mouser"], {"name": "Mouser", "lookups": 3, "failed": 1, "status":
                                         "partial (1 of 3 lookups failed: TimeoutError: timed out)"})
        self.assertEqual(sup["LCSC"]["status"], "ok")
        self.assertEqual(sup["Farnell"]["status"], "no API key")
        self.assertEqual(data["builds"][0]["by_supplier"], {"1": {"Mouser": 2}})
        report = self.report()
        self.assertIn("from LCSC, JLCPCB, Mouser (1 of 3 lookups failed); "
                      "not used: Farnell (no API key), DigiKey (no API key). **2 of 3 fitted parts priced**", report)
        self.assertIn("(for 1 board: Mouser 2)", report)

    def test_every_lookup_failed_is_not_used(self):
        data, sup = self.run_sourcing({m: [http_error(401)] for m in ("AAA", "BBB", "CCC")})
        self.assertEqual(sup["Mouser"]["status"], "failed (HTTPError: HTTP Error 401: error)")
        self.assertEqual((sup["Mouser"]["lookups"], sup["Mouser"]["failed"]), (3, 3))
        self.assertEqual(data["builds"][0]["priced"], 0)
        with open(self.path("sourcing.json"), "w", encoding="utf-8") as f:   # a part priced elsewhere, for the full section
            data["builds"][0].update(priced=1, totals={"1": 0.5}, by_supplier={"1": {"LCSC": 1}})
            json.dump(data, f)
        self.assertIn("from LCSC, JLCPCB; not used: Mouser (failed), Farnell (no API key), DigiKey (no API key).",
                      self.report())

    def test_spent_quota_is_retried(self):
        # Mouser's quota answer: as an error status with the reason in the body, and as a normal answer
        data, sup = self.run_sourcing({"AAA": [http_error(403, QUOTA), mouser_part("AAA")],
                                       "BBB": [json.loads(QUOTA), mouser_part("BBB")],
                                       "CCC": [mouser_part("CCC")]})
        self.assertEqual(sup["Mouser"], {"name": "Mouser", "status": "ok", "lookups": 3, "failed": 0})
        self.assertEqual(sorted(self.asked), ["AAA", "AAA", "BBB", "BBB", "CCC"])
        self.assertEqual(data["builds"][0]["priced"], 3)

    def test_quota_of_the_day_is_not_retried(self):
        day = {"Errors": [{"Code": "TooManyRequests", "Message": "Maximum calls per day reached."}]}
        data, sup = self.run_sourcing({m: [day] for m in ("AAA", "BBB", "CCC")})
        self.assertEqual(sup["Mouser"]["status"], "failed (RuntimeError: Maximum calls per day reached.)")
        self.assertEqual(sorted(self.asked), ["AAA", "BBB", "CCC"])


if __name__ == "__main__":
    unittest.main()
