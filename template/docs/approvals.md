# Approvals with plain GitHub features

No extra tooling: issue forms, branch rules, CODEOWNERS and Environments cover part requests, design reviews and release approval.

## 1. Protect `main` (rulesets)

*Settings → Rules → Rulesets → New ruleset → New branch ruleset*

1. Name it `main`, set *Enforcement status* to **Active**.
2. *Target branches → Add target → Include default branch*.
3. Enable **Require a pull request before merging**:
   - *Required approvals*: `1`
   - **Require review from Code Owners** (needs a `CODEOWNERS` file, see 3)
   - **Dismiss stale pull request approvals when new commits are pushed**
4. Enable **Require status checks to pass** → *Add checks* → pick the check of the KiCad workflow (`board`, shown after it has run once on a PR).
5. Enable **Block force pushes**, then *Create*.

(Classic alternative: *Settings → Branches → Add branch protection rule*, same options.)

## 2. Issue forms

The forms in `.github/ISSUE_TEMPLATE/` appear under *Issues → New issue*:

- **Part request**: MPN, manufacturer, LCSC, datasheet, footprint, reason, alternatives.
- **Design review**: what changed, areas to check, deadline, checklist.
- **Fab release**: revision, fab house, quantity, checklist before ordering.

Each form adds a label (`part-request`, `design-review`, `fab-release`). Create them once in *Issues → Labels → New label* (GitHub also creates a label on first use of a form, without colour).

## 3. CODEOWNERS

Copy `.github/CODEOWNERS.example` to `.github/CODEOWNERS`, replace the placeholders and commit it. With the ruleset above, a PR touching `*.kicad_pcb` needs the PCB owner's approval, and each sheet its own owner's. Owners need write access to the repo.

## 4. Link PRs to issues

Write `Closes #12` (or `Fixes #12`, `Resolves #12`) in the PR description. The issue is linked and closed when the PR merges into `main`. For a review request, use `Refs #12` to link without closing.

## 5. Approval before releasing

Releases are published when a tag `vX.Y` is pushed. To require an approval first:

1. *Settings → Environments → New environment*, name it `fab-release`.
2. Tick **Required reviewers** and add up to 6 people or teams, then *Save protection rules*.
3. Optionally, under *Deployment branches and tags*, choose *Selected branches and tags* and add the tag pattern `v*`.
4. In `.github/workflows/kicad.yml`, follow the commented steps of the `release` job: on a tag the job pauses with *Review deployments* until a reviewer approves it in the run's page (*Actions → the run → Review deployments*).

Environments with required reviewers are available in public repos and, in private repos, on paid GitHub plans.

## 6. Suggested flow

1. Open a **Part request** or **Design review** issue.
2. Work on a branch, open a PR with `Closes #n`; CI posts the report.
3. Reviewers and code owners approve; the ruleset lets you merge.
4. Open a **Fab release** issue, tag `vX.Y`, a reviewer approves the `fab-release` environment, CI publishes the release.
