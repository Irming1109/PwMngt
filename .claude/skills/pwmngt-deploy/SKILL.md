---
name: pwmngt-deploy
description: Use when Kasper or Claus says "Deploy" (or similar) for PwMngt - writes RELEASE_NOTES.md, runs release.ps1 to tag and push the release, checks the GitHub Release, and (on Kasper's HA) clicks HACS "Update information" so the update shows up.
---

# PwMngt deploy

"Deploy" means: the code (with the version bump in `custom_components/pwmngt/manifest.json`) is committed and pushed, and a new release should go out. Wait if the person says more is coming ("vent, der kommer mere").

## 1. Check what changed
- `git pull --autostash`, then `git status` - everything for the release must be committed and pushed (`git log origin/main..HEAD` empty). If not, ask before committing/pushing anything.
- Find the last tag (`git tag --sort=-creatordate | head -1`) and read the real diff: `git log <tag>..HEAD` and `git diff <tag> HEAD`. Never write notes from memory.

## 2. Confirm the version
- Read `version` from `manifest.json`. It must be newer than the last tag and zero-padded (`0.4.03`). If it equals the last tag or looks wrong, ask - bumping is part of the code change, not of the deploy.

## 3. Write `RELEASE_NOTES.md` (repo root, git-ignored)
- First line: a short title. Then `##` sections (e.g. "Nyt", "Rettet", "Teknisk") with bullets describing the actual changes in plain words - Danish, like the commit messages.

## 4. Release
- Run `.\release.ps1` in PowerShell from the repo root (the person saying "Deploy" is the go-ahead). It tags `v<version>` with `RELEASE_NOTES.md` as annotated message (`--cleanup=verbatim`) and pushes only the tag.
- GitHub Actions (`.github/workflows/release.yml`) creates the release as "Latest" within ~20 s. Check with `gh release view v<version> --repo Irming1109/PwMngt` (or `gh run list --repo Irming1109/PwMngt --limit 3`) that it exists and that the body is the real notes, not a short commit message. If the body is wrong: check that the workflow's tag re-fetch step is intact, and ask before editing the published release.

## 5. HACS (only Kasper's HA)
- In **Claude in Chrome** (Kasper's logged-in Chrome - the built-in browser is not logged in), open `https://homeassistant.karacore.dk/hacs/repository/948891396`, click the ⋮ menu top-right, then **"Update information"**. Nothing else in that menu - never Redownload, Remove or Request for removal.
- Confirm with a screenshot that the "release" badge shows the new version. If HA shows the login page, don't log in - tell Kasper.
- For Claus: skip this step unless his `CLAUDE.local.md` says how to reach his HA; he refreshes HACS himself.

## 6. Report
Release URL, version, that the body is correct, and that HACS shows the update. Installing the update and restarting HA is always the owner's own step.
