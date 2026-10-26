---
name: pwmngt-deploy
description: Use when Kasper or Claus says "Deploy" (or similar) for PwMngt - runs the pre-release checks, bumps the version if needed, writes release notes with the linked issues, shows ONE summary for approval, then tags and releases (under the deployer's own GitHub name), updates the issues, and (on Kasper's machine) clicks HACS "Update information".
---

# PwMngt deploy

"Deploy" means: a new release of what is on `origin/main` should go out. Wait if the person says more is coming ("vent, der kommer mere"). Find out who you are talking to (Kasper or Claus) if it isn't clear - step 7 depends on it.

## 1. Pre-release checks
- `git pull --autostash`, then run from the repo root:
  ```bash
  python -I .claude/skills/pwmngt-deploy/precheck.py
  ```
  It only reads. It checks: git state (nothing unpushed, not behind), version format `X.Y.ZZ` and newer than the last tag, tag not taken, Python syntax for the whole integration, valid JSON, the same keys in `strings.json` and `translations/en.json` (and no stale keys in `da.json`), and warns if `select.py`/`number.py`/`text.py`/`sensor.py` changed without `www/pwm-cards.js`. It also prints the commits and the issue numbers since the last tag.
- **ERROR** stops the deploy - fix it (or ask) first. The one exception is a missing version bump: see step 2. **WARN** goes into the summary in step 4 for the person to decide on.
- Uncommitted changes in the working tree are never part of a release and are never committed by this skill (they may be the other developer's work in progress) - just mention them.

## 2. Version bump (only if missing)
If the only errors are "not newer than the last tag" / "tag already exists", propose the next patch version (the script suggests it, e.g. `0.4.03` → `0.4.04`; a new feature segment may deserve a minor bump - ask). After the approval in step 4: change `version` in `custom_components/pwmngt/manifest.json`, commit only that file as `Bump version til 0.4.04`, `git pull --autostash`, push, and re-run the precheck.

## 3. Release notes
- Read the real diff: `git log <tag>..HEAD` and `git diff <tag> HEAD -- custom_components`. Never write notes from memory.
- Write `RELEASE_NOTES.md` in the repo root (git-ignored): first line a short title, then `##` sections ("Nyt", "Rettet", "Teknisk") with plain-Danish bullets about what the user of the integration notices. Put the issue number on the bullet it belongs to (`#12`) - GitHub links it. Leave out commits that don't touch `custom_components/` (CLAUDE.md, skills, docs).
- For each issue from the precheck, look it up (`gh issue view <nr> --repo Irming1109/PwMngt --json title,state,assignees`) so the summary shows its title and status.

## 4. One approval
Show in one message: version (and whether it gets bumped), the warnings, the issues (number, title, current status → new status), and the full release notes. Proceed only after a yes. Corrections → update and show again.

## 5. Release
- Run `.\release.ps1` from the repo root with the PowerShell tool. It tags `v<version>` with `RELEASE_NOTES.md` as annotated message (`--cleanup=verbatim`), pushes only the tag, and then creates the GitHub Release itself with `gh release create` (body = `RELEASE_NOTES.md`, marked "Latest"). Done locally so the release shows the deployer as author, not "github-actions". If the script fails after pushing the tag, report it and stop - `.github/workflows/release.yml` can then be run by hand (Actions → Run workflow, with the tag), but ask first.
- Check the result: `gh release view v<version> --repo Irming1109/PwMngt` - it must be "Latest", the author must be the deployer's own login, and the body must be the real notes. Ask before editing a published release.
- Delete `RELEASE_NOTES.md` afterwards, so an old draft is never reused.

## 6. Update the issues
For each issue from step 3 (still open):
- Comment: `Udgivet i [v<version>](https://github.com/Irming1109/PwMngt/releases/tag/v<version>) – klar til test efter opdatering i HACS.`
- If its Status is `in-progress`: set it to `in-test` and assign it to the person whose Home Assistant it will be tested on (ask if unclear) - field and option ids are in the `github-issue` skill.
- Never close an issue here - it is closed when it has been tested in HA.

## 7. HACS (only on Kasper's machine)
- Only when Kasper is the one deploying (his `CLAUDE.local.md` has the HA address and browser). Otherwise skip - Claus refreshes HACS himself.
- In **Claude in Chrome** (the built-in browser is not logged in to HA), open the PwMngt HACS page from `CLAUDE.local.md`, click the ⋮ menu top-right, then **"Update information"**. Nothing else in that menu - never Redownload, Remove or Request for removal.
- Confirm that the "release" badge shows the new version. If HA shows the login page, don't log in - tell Kasper. Close the tab afterwards.

## 8. Report
Release URL and version, that the release (author, "Latest", body) is OK, which issues got a comment / moved to `in-test`, and (Kasper) that HACS shows the update. Installing the update and restarting HA is always the owner's own step.
