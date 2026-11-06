#!/usr/bin/env python3
"""Is GitHub Pages serving this repository's design portal?

Prints one word: serving | disabled | unknown.
  serving   Pages is enabled and its source is the portal branch.
  disabled  Pages is not enabled (API answers 404: private repo without a paid plan, or
            simply not turned on) or it serves another branch.
  unknown   The token cannot read the setting (401/403), network failure, anything else:
            the caller keeps the link, as before.
Never fails: the report must not depend on this check.
"""
import json
import os
import sys
import urllib.error
import urllib.request


def classify(status, body, branch):
    if status == 200:
        src = (body or {}).get("source") or {}
        if (body or {}).get("build_type") == "workflow" or (src.get("branch") and src["branch"] != branch):
            return "disabled"  # Pages serves something else, not the portal branch
        return "serving"
    if status == 404:
        return "disabled"
    return "unknown"


def probe(repo, branch, token, api="https://api.github.com"):
    req = urllib.request.Request(f"{api}/repos/{repo}/pages", headers={
        "Accept": "application/vnd.github+json", "User-Agent": "kicad-hub",
        **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return classify(r.status, json.load(r), branch)
    except urllib.error.HTTPError as e:
        return classify(e.code, None, branch)
    except Exception:
        return "unknown"


if __name__ == "__main__":
    print(probe(os.environ.get("REPO", ""), os.environ.get("BRANCH", "kicad-hub-site"),
                os.environ.get("GH_TOKEN", ""), os.environ.get("GITHUB_API_URL", "https://api.github.com")))
    sys.exit(0)
