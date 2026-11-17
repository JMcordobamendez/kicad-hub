"""How each supplier's lookups went, for sourcing.py and the readers of its JSON.

One request that fails (a timeout, a spent quota) must not hide the offers the supplier's
other requests returned, so lookups and failures are counted per supplier:
  ok                                          every lookup was answered (found or not found)
  partial (3 of 40 lookups failed: <error>)   some failed: the supplier's offers are used
  failed (<error>)                            all failed: the supplier could not be used
  no API key                                  not asked
"""
import threading
import time
import urllib.error


class RateLimited(Exception):
    """A supplier answered that its quota of requests is spent for now."""


def rate_limited(e):
    return isinstance(e, RateLimited) or (isinstance(e, urllib.error.HTTPError) and e.code == 429)


def supplier_status(lookups, failed, error=""):
    """The status text of a supplier with a key, from its counts and its first error."""
    if not failed:
        return "ok"
    if failed >= lookups:
        return f"failed ({error})"
    return f"partial ({failed} of {lookups} lookups failed: {error})"


class Lookups:
    """Runs the lookups, counting those made and those that failed for each supplier.

    A lookup refused for a rate limit is tried once more after `wait` seconds. At most
    `retries` lookups per supplier get that second try, so a quota that stays spent costs
    the step about retries x wait seconds at worst, not one wait per part."""

    def __init__(self, wait=10, retries=3):
        self.wait, self.retries = wait, retries
        self.made, self.failed, self.retried, self.errors = {}, {}, {}, {}
        self.lock = threading.Lock()

    def _retry(self, name):
        with self.lock:
            if self.retried.get(name, 0) >= self.retries:
                return False
            self.retried[name] = self.retried.get(name, 0) + 1
            return True

    def run(self, name, fn, key):
        """fn(key), or None when it fails: network, auth, quota, format change. Noted, never fatal."""
        with self.lock:
            self.made[name] = self.made.get(name, 0) + 1
        try:
            try:
                return fn(key)
            except Exception as e:
                if not (rate_limited(e) and self._retry(name)):
                    raise
            time.sleep(self.wait)
            return fn(key)
        except Exception as e:
            with self.lock:
                self.failed[name] = self.failed.get(name, 0) + 1
                self.errors.setdefault(name, f"{type(e).__name__}: {str(e)[:120]}")
            return None

    def entry(self, name, enabled=True):
        """The supplier's entry in sourcing.json."""
        made, failed = self.made.get(name, 0), self.failed.get(name, 0)
        status = supplier_status(made, failed, self.errors.get(name, "")) if enabled else "no API key"
        return {"name": name, "status": status, "lookups": made, "failed": failed}


def used_and_not(suppliers):
    """Two lists of labels from the entries of sourcing.json: the suppliers whose offers are in
    the totals (with a note when some of their lookups failed) and those not used, with the reason."""
    used, other = [], []
    for s in suppliers:
        status = s["status"]
        if status == "ok":
            used.append(s["name"])
        elif status.startswith("partial"):
            used.append(f"{s['name']} ({s['failed']} of {s['lookups']} lookups failed)")
        else:
            other.append(f"{s['name']} ({status.split(' (')[0]})")
    return used, other
