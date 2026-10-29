#!/usr/bin/env python3
"""Data for the portal's activity page (activity.html): branches, who did what, PRs, pushes.

  activity.py collect  ...  on the runner, inside the checkout: the git graph of the last N
                            commits (all branches and tags) and, with a token, the pull
                            requests, their reviews and comments, and the releases from the
                            GitHub GraphQL API. Writes one raw JSON file.
  activity.py publish  ...  into the checkout of the site branch: appends this run's push to
                            the persistent push log (activity-pushes.json, GitHub doesn't
                            keep who pushed), then writes activity.json from the raw file.

Python standard library only. Privacy: no e-mail address is ever written. The GitHub login
is read from the git author's noreply address or from GitHub; otherwise the author's name is
shown. Free text (subjects, comments, names) is scrubbed of anything that looks like an
address. Failures are never fatal: without a token (fork PRs) or when GitHub doesn't
answer, the page gets the git graph alone.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

NOREPLY = re.compile(r"^(?:(\d+)\+)?([A-Za-z0-9][A-Za-z0-9-]*)@users\.noreply\.github\.com$", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
ZEROS = "0" * 40
PUSH_LOG_MAX = 1000
EVENTS_MAX = 400
FEED_PUSHES = 100       # newest pushes that go to the feed (with their commits)
BRANCHES_MAX = 60       # branches whose creator is worked out
SNIPPET = 200           # characters of a comment kept


def warn(msg):
    print(f"::warning::activity: {msg}", flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scrub(text):
    return EMAIL.sub("[email]", text)


def scrub_tree(obj):
    """Every string in the structure without anything that looks like an e-mail address."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {scrub(k): scrub_tree(v) for k, v in obj.items()}
    return obj


def git(*args, stdin=None, timeout=300):
    """Output of a git command, None when it fails."""
    try:
        r = subprocess.run(["git", *args], input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def login_of_email(email):
    """(login, user id) from a GitHub noreply address, else (None, None). The address itself is dropped."""
    m = NOREPLY.match(email.strip())
    return (m.group(2), m.group(1)) if m else (None, None)


def short_ref(ref):
    return re.sub(r"^refs/(heads|tags|remotes/origin)/", "", ref)


# ---------------------------------------------------------------- git

def deepen():
    """The checkout of a board repo is usually shallow: fetch the whole history and every branch."""
    shallow = git("rev-parse", "--is-shallow-repository")
    if shallow is None or shallow.strip() != "true":
        return
    if git("fetch", "--quiet", "--unshallow", "--tags", "origin") is None:
        warn("could not fetch the full history; the graph shows what is in the checkout")
    if git("fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*") is None:
        warn("could not fetch the other branches")


def list_refs(exclude):
    """name -> {kind, sha} for branches (remote ones win over local) and tags."""
    out = git("for-each-ref", "--format=%(refname)%09%(objectname)%09%(*objectname)",
              "refs/heads", "refs/remotes/origin", "refs/tags") or ""
    branches, tags = {}, {}
    for line in out.splitlines():
        ref, sha, peeled = (line.split("\t") + ["", ""])[:3]
        name = short_ref(ref)
        if name in ("HEAD", "origin/HEAD") or name in exclude:
            continue
        if ref.startswith("refs/tags/"):
            tags[name] = peeled or sha
        elif ref.startswith("refs/remotes/origin/") or name not in branches:
            branches[name] = sha
    return branches, tags


def read_log(shas, limit):
    """Commits newest first, children always before their parents (--topo-order)."""
    fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1e"
    out = git("log", "--topo-order", f"--max-count={limit}", f"--format={fmt}", "--stdin",
              stdin="\n".join(sorted(shas)) + "\n") or ""
    commits = []
    for rec in out.split("\x1e"):
        f = rec.strip("\n").split("\x1f")
        if len(f) < 7:
            continue
        sha, parents, an, ae, ad, cn, subject = f[:7]
        c = {"sha": sha, "parents": parents.split(), "author": an, "date": ad, "subject": subject[:200]}
        if cn and cn != an:
            c["committer"] = cn
        login, uid = login_of_email(ae)
        if login:
            c["login"] = login
            c["_uid"] = uid
        commits.append(c)
    return commits


def author_of(sha, by_sha):
    """(login or None, name) of any commit, in the graph or not."""
    c = by_sha.get(sha)
    if c:
        return c.get("login"), c["author"]
    out = git("log", "-1", "--format=%an%x1f%ae", sha)
    if not out:
        return None, None
    name, _, email = out.strip().partition("\x1f")
    return login_of_email(email)[0], name


def branch_creators(branches, default, prs, by_sha):
    """name -> (who, how): the PR's author, else the author of the first commit only that branch has."""
    result = {}
    by_head = {}
    for p in sorted(prs, key=lambda p: p["number"]):
        if p.get("author") and not p.get("fork"):
            by_head[p["head"]] = p
    tips = {n: s for n, s in branches.items()}
    # newest tips first; the work per branch is one git call
    for name in sorted(tips, key=lambda n: by_sha.get(tips[n], {}).get("date", ""), reverse=True)[:BRANCHES_MAX]:
        if name == default:
            continue
        if name in by_head:
            result[name] = (by_head[name]["author"], "pr")
            continue
        others = sorted({s for n, s in tips.items() if n != name and s != tips[name]})
        for against in (others, [tips[default]] if default in tips and tips[default] != tips[name] else []):
            if not against:
                continue
            out = git("rev-list", tips[name], "--not", *against)
            if out and out.split():
                who = author_of(out.split()[-1], by_sha)  # rev-list is newest first: the oldest is last
                if who[0] or who[1]:
                    result[name] = (who[0] or who[1], "commit")
                    break
    return result


# ---------------------------------------------------------------- GitHub

class GhError(Exception):
    pass


def graphql(query, variables, token, server):
    url = ("https://api.github.com/graphql" if server.rstrip("/") == "https://github.com"
           else server.rstrip("/") + "/api/graphql")
    body = json.dumps({"query": query, "variables": variables}).encode()
    headers = {"Authorization": "bearer " + token, "Content-Type": "application/json", "User-Agent": "kicad-hub"}
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                answer = json.load(r)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            last = e
            time.sleep(2)
            continue
        if answer.get("errors") and not answer.get("data"):
            raise GhError("; ".join(str(e.get("message")) for e in answer["errors"])[:200])
        if answer.get("errors"):
            warn("GitHub answered with errors: " + "; ".join(str(e.get("message")) for e in answer["errors"])[:200])
        return answer["data"]
    raise GhError(f"no answer from GitHub ({last})")


PR_QUERY = """
query($o:String!,$n:String!,$after:String){repository(owner:$o,name:$n){
  defaultBranchRef{name}
  pullRequests(first:25,after:$after,orderBy:{field:UPDATED_AT,direction:DESC}){
    pageInfo{hasNextPage endCursor}
    nodes{number title state isDraft createdAt mergedAt closedAt headRefName baseRefName headRefOid
      headRepository{nameWithOwner}
      author{login avatarUrl} mergedBy{login avatarUrl} mergeCommit{oid}
      reviews(first:30){nodes{state submittedAt bodyText author{login avatarUrl}}}
      comments(first:30){nodes{createdAt bodyText author{login avatarUrl}}}
      commits(first:30){nodes{commit{oid author{user{login avatarUrl}}}}}}}}}
"""
DEFAULT_HISTORY_QUERY = """
query($o:String!,$n:String!,$after:String){repository(owner:$o,name:$n){defaultBranchRef{target{... on Commit{
  history(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{oid author{user{login avatarUrl}}}}}}}}}
"""
HEADS_QUERY = """
query($o:String!,$n:String!){repository(owner:$o,name:$n){
  refs(refPrefix:"refs/heads/",first:30,orderBy:{field:TAG_COMMIT_DATE,direction:DESC}){
    nodes{name target{... on Commit{history(first:20){nodes{oid author{user{login avatarUrl}}}}}}}}}}
"""
RELEASES_QUERY = """
query($o:String!,$n:String!){repository(owner:$o,name:$n){
  releases(first:50,orderBy:{field:CREATED_AT,direction:DESC}){
    nodes{tagName createdAt publishedAt isPrerelease author{login avatarUrl} tagCommit{oid}}}}}
"""


def github_data(repo, server, token, limit, in_graph, errors):
    """Pull requests (with reviews, comments), releases and which login made which commit."""
    owner, name = repo.split("/", 1)
    var = {"o": owner, "n": name}
    users, login_of, prs, events = {}, {}, [], []
    default = None

    def who(actor):
        if actor and actor.get("login"):
            users.setdefault(actor["login"], actor.get("avatarUrl") or "")
            return actor["login"]
        return None

    def snippet(text):
        return scrub(" ".join((text or "").split())[:SNIPPET])

    def commit_users(nodes):
        for n in nodes or []:
            c = n.get("commit", n)
            login = who(((c.get("author") or {}).get("user")))
            if login and c.get("oid"):
                login_of[c["oid"]] = login

    # pull requests, 25 per page, the 100 most recently updated
    after = None
    try:
        for _ in range(4):
            repo_node = graphql(PR_QUERY, {**var, "after": after}, token, server)["repository"]
            default = (repo_node.get("defaultBranchRef") or {}).get("name") or default
            page = repo_node["pullRequests"]
            for p in page["nodes"]:
                author = who(p.get("author"))
                head_repo = (p.get("headRepository") or {}).get("nameWithOwner")
                commit_users((p.get("commits") or {}).get("nodes"))
                oids = [n["commit"]["oid"] for n in (p.get("commits") or {}).get("nodes") or []]
                pr = {"number": p["number"], "title": scrub(p["title"]), "state": p["state"], "draft": p["isDraft"],
                      "author": author, "head": p["headRefName"], "base": p["baseRefName"], "head_sha": p["headRefOid"],
                      "created": p["createdAt"], "merged": p.get("mergedAt"), "closed": p.get("closedAt"),
                      "merged_by": who(p.get("mergedBy")), "merge_sha": (p.get("mergeCommit") or {}).get("oid"),
                      "fork": bool(head_repo and head_repo.lower() != repo.lower()),
                      "commits": [o[:10] for o in oids if o in in_graph][:30]}
                prs.append(pr)
                n = p["number"]
                events.append({"type": "pr_opened", "at": p["createdAt"], "who": author, "pr": n})
                for r in (p.get("reviews") or {}).get("nodes") or []:
                    if r.get("state") == "PENDING" or not r.get("submittedAt"):
                        continue
                    events.append({"type": "review", "at": r["submittedAt"], "who": who(r.get("author")), "pr": n,
                                   "state": r["state"].lower(), "text": snippet(r.get("bodyText"))})
                for c in (p.get("comments") or {}).get("nodes") or []:
                    events.append({"type": "comment", "at": c["createdAt"], "who": who(c.get("author")), "pr": n,
                                   "text": snippet(c.get("bodyText"))})
                if p.get("mergedAt"):
                    events.append({"type": "merged", "at": p["mergedAt"], "who": who(p.get("mergedBy")), "pr": n})
                elif p["state"] == "CLOSED" and p.get("closedAt"):
                    events.append({"type": "closed", "at": p["closedAt"], "who": None, "pr": n})
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
    except (GhError, KeyError, TypeError) as e:
        errors.append(f"pull requests: {e}")

    # releases
    try:
        for r in graphql(RELEASES_QUERY, var, token, server)["repository"]["releases"]["nodes"]:
            events.append({"type": "release", "at": r.get("publishedAt") or r["createdAt"], "who": who(r.get("author")),
                           "tag": r["tagName"], "prerelease": r["isPrerelease"],
                           "rel": [((r.get("tagCommit") or {}).get("oid") or "")[:10]]})
    except (GhError, KeyError, TypeError) as e:
        errors.append(f"releases: {e}")

    # which login made which commit: the default branch's history, then the newest branches
    try:
        after = None
        for _ in range(min(10, limit // 100 + 1)):
            node = graphql(DEFAULT_HISTORY_QUERY, {**var, "after": after}, token, server)["repository"]
            hist = node["defaultBranchRef"]["target"]["history"]
            commit_users(hist["nodes"])
            if not hist["pageInfo"]["hasNextPage"]:
                break
            after = hist["pageInfo"]["endCursor"]
        for ref in graphql(HEADS_QUERY, var, token, server)["repository"]["refs"]["nodes"]:
            commit_users(((ref.get("target") or {}).get("history") or {}).get("nodes"))
    except (GhError, KeyError, TypeError) as e:
        errors.append(f"commit authors: {e}")
    return {"users": users, "login_of": login_of, "prs": prs, "events": events, "default": default}


# ---------------------------------------------------------------- the two commands

def collect(a):
    exclude = {x for x in re.split(r"[,\s]+", a.exclude) if x}
    if a.deepen:
        deepen()
    branches, tags = list_refs(exclude)
    default = a.default_branch if a.default_branch in branches else (
        "main" if "main" in branches else "master" if "master" in branches else None)
    shas = set(branches.values()) | set(tags.values())
    commits = read_log(shas, a.limit) if shas else []
    if not commits:
        warn("no commits read from git")
    by_sha = {c["sha"]: c for c in commits}
    users = {}
    errors = []
    gh = None
    token = os.environ.get("GH_TOKEN", "")
    if token:
        gh = github_data(a.repo, a.server, token, a.limit, by_sha, errors)
        default = default or gh["default"]
        users.update(gh["users"])
        for sha, login in gh["login_of"].items():
            if sha in by_sha and "login" not in by_sha[sha]:
                by_sha[sha]["login"] = login
        for e in errors:
            warn("GitHub data incomplete: " + e)
    else:
        errors.append("no token: git graph only")

    # a name that always belongs to one login also names that login's other commits
    names = {}
    for c in commits:
        if c.get("login"):
            names.setdefault(c["author"], set()).add(c["login"])
    for c in commits:
        if "login" not in c and len(names.get(c["author"], ())) == 1:
            c["login"] = next(iter(names[c["author"]]))
    for c in commits:
        uid = c.pop("_uid", None)
        if c.get("login") and c["login"] not in users:
            users[c["login"]] = f"https://avatars.githubusercontent.com/u/{uid}?v=4&s=40" if uid else ""

    # refs on the commits they point to
    branch_on, tag_on = {}, {}
    for n, s in branches.items():
        branch_on.setdefault(s, []).append(n)
    for n, s in tags.items():
        tag_on.setdefault(s, []).append(n)
    for c in commits:
        if c["sha"] in branch_on:
            c["branches"] = sorted(branch_on[c["sha"]])
        if c["sha"] in tag_on:
            c["tags"] = sorted(tag_on[c["sha"]])

    prs = gh["prs"] if gh else []
    creators = branch_creators({n: s for n, s in branches.items() if s in by_sha}, default, prs, by_sha) if commits else {}
    branch_list = []
    for n, s in sorted(branches.items()):
        if s not in by_sha:
            continue
        b = {"name": n, "tip": s, "default": n == default}
        if n in creators:
            b["creator"], b["how"] = creators[n]
        branch_list.append(b)

    # events whose commits are in the graph
    events = (gh or {}).get("events", [])
    for e in events:
        if e.get("rel"):
            e["rel"] = [r for r in e["rel"] if r]
    for p in prs:
        if p.get("merge_sha"):
            for e in events:
                if e["type"] == "merged" and e["pr"] == p["number"]:
                    e["rel"] = [p["merge_sha"][:10]]
    data = {"default_branch": default, "limit": a.limit, "truncated": len(commits) >= a.limit,
            "github": bool(gh) and not any(e.startswith(("pull requests", "commit authors")) for e in errors),
            "github_note": "; ".join(errors) or None,
            "users": users, "commits": commits, "branches": branch_list, "prs": prs, "events": events}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"activity: {len(commits)} commits, {len(branch_list)} branches, {len(prs)} PRs, {len(events)} events"
          + (f" ({'; '.join(errors)})" if errors else ""))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def publish(a):
    data = load_json(a.data, None)
    if data is None:
        warn("no collected data: activity.json not written")
        return
    # the push log lives in the site branch; this run's push is appended to it
    log_path = os.path.join(a.root, "activity-pushes.json")
    log = load_json(log_path, {})
    pushes = log.get("pushes", []) if isinstance(log, dict) else []
    since = log.get("since") if isinstance(log, dict) else None
    if a.push_actor and a.push_ref and a.push_after:
        if not any(p.get("ref") == a.push_ref and p.get("after") == a.push_after and p.get("before") == a.push_before
                   for p in pushes):
            n = None
            if a.push_before and a.push_before != ZEROS:
                out = git("rev-list", "--count", f"{a.push_before}..{a.push_after}")
                n = int(out) if out and out.strip().isdigit() else None
            pushes.append({"actor": a.push_actor, "ref": a.push_ref, "before": a.push_before, "after": a.push_after,
                           "date": now(), "commits": n})
        since = since or now()
    pushes = pushes[-PUSH_LOG_MAX:]
    if pushes or since:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump({"since": since, "pushes": pushes}, f, separators=(",", ":"))

    in_graph = {c["sha"] for c in data["commits"]}
    users = data["users"]
    events = list(data["events"])
    first_push = {}
    for p in pushes:
        first_push.setdefault(short_ref(p["ref"]), p["actor"])
    for p in pushes[-FEED_PUSHES:]:
        ref = p["ref"]
        e = {"type": "push", "at": p["date"], "who": p["actor"], "ref": short_ref(ref), "commits": p.get("commits"),
             "tag": ref.startswith("refs/tags/")}
        rel = [p["after"][:10]] if p["after"] in in_graph else []
        if p.get("before") and p["before"] != ZEROS and not e["tag"]:
            out = git("rev-list", "--max-count=30", f"{p['before']}..{p['after']}")
            rel = [s[:10] for s in (out or "").split() if s in in_graph] or rel
        e["rel"] = rel
        events.append(e)
        users.setdefault(p["actor"], f"https://avatars.githubusercontent.com/{p['actor']}?s=40")
    # a tag pushed for a release is already in the feed as the release
    released = {e["tag"] for e in events if e["type"] == "release"}
    events = [e for e in events if not (e["type"] == "push" and e.get("tag") and e["ref"] in released)]
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    for i, e in enumerate(events[:EVENTS_MAX]):
        e["id"] = i
    data["events"] = events[:EVENTS_MAX]
    for b in data["branches"]:
        if "creator" not in b and b["name"] in first_push:
            b["creator"], b["how"] = first_push[b["name"]], "push"
    data.update({"version": 1, "generated": now(), "repo": a.repo, "server": a.server, "pushes_since": since})
    text = json.dumps(scrub_tree(data), separators=(",", ":"))
    with open(os.path.join(a.root, "activity.json"), "w", encoding="utf-8") as f:
        f.write(text)
    print(f"activity.json: {len(text) // 1024} KB, {len(pushes)} pushes in the log")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True, help="raw data file")
    c.add_argument("--repo", required=True, help="owner/name")
    c.add_argument("--server", default="https://github.com")
    c.add_argument("--limit", type=int, default=500, help="newest commits to read")
    c.add_argument("--default-branch", default="")
    c.add_argument("--exclude", default="", help="branches left out (the site and image branches)")
    c.add_argument("--deepen", action="store_true", help="fetch the full history and all branches of a shallow checkout")
    p = sub.add_parser("publish")
    p.add_argument("--root", required=True, help="checkout of the site branch")
    p.add_argument("--data", required=True, help="file written by collect")
    p.add_argument("--repo", required=True)
    p.add_argument("--server", default="https://github.com")
    for k in ("actor", "ref", "before", "after"):
        p.add_argument(f"--push-{k}", default="")
    a = ap.parse_args()
    try:
        collect(a) if a.cmd == "collect" else publish(a)
    except Exception as e:  # never fail the build for this
        warn(f"{a.cmd} failed: {e}")
        sys.exit(0)


if __name__ == "__main__":
    main()
