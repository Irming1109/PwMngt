"""Pre-release checks for PwMngt, run by the pwmngt-deploy skill before tagging.

Usage (from the repo root): python -I .claude/skills/pwmngt-deploy/precheck.py

Prints ERROR / WARN / INFO lines and exits 1 if any ERROR was found.
Nothing is changed - it only reads the working tree and git.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / "custom_components" / "pwmngt"
MANIFEST = COMPONENT / "manifest.json"
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d{2})$")
# Platform files whose entity descriptions are mirrored by hand in www/pwm-cards.js.
CARD_MIRRORED_FILES = ("select.py", "number.py", "text.py", "sensor.py")

errors: list[str] = []
warnings: list[str] = []
info: list[str] = []


def git(*args: str) -> str:
    result = subprocess.run(["git", "-c", "i18n.logOutputEncoding=utf-8", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip() if result.returncode == 0 else ""


def flatten_keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        keys |= flatten_keys(value, path) if isinstance(value, dict) else {path}
    return keys


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")
        return None


def version_tuple(version: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.match(version)
    return tuple(int(part) for part in match.groups()) if match else None


# --- Git state -----------------------------------------------------------------
git("fetch", "--quiet", "--tags", "origin")
if git("status", "--porcelain", "--untracked-files=no"):
    warnings.append("Uncommitted changes in tracked files - they are NOT part of the release.")
unpushed = git("log", "--oneline", "origin/main..HEAD")
if unpushed:
    errors.append(f"Commits not pushed to origin/main:\n    " + unpushed.replace("\n", "\n    "))
behind = git("rev-list", "--count", "HEAD..origin/main")
if behind and behind != "0":
    errors.append(f"Local main is {behind} commit(s) behind origin/main - run git pull --autostash.")

# --- Version -------------------------------------------------------------------
manifest = load_json(MANIFEST) or {}
version = manifest.get("version", "")
last_tag = git("describe", "--tags", "--abbrev=0", "--match", "v*")
last_version = last_tag.removeprefix("v")
current, previous = version_tuple(version), version_tuple(last_version)
info.append(f"version={version} last_tag={last_tag or '-'}")
if current is None:
    errors.append(f"manifest.json version '{version}' is not X.Y.ZZ (patch zero-padded to two digits).")
elif previous and current <= previous:
    major, minor, patch = previous
    errors.append(f"Version {version} is not newer than {last_tag} - bump it (suggested: {major}.{minor}.{patch + 1:02d}).")
if git("tag", "-l", f"v{version}"):
    errors.append(f"Tag v{version} already exists.")

# --- Python and JSON -----------------------------------------------------------
# compile() instead of py_compile: checks the syntax without writing __pycache__ files.
for source in sorted(COMPONENT.rglob("*.py")):
    try:
        compile(source.read_text(encoding="utf-8"), str(source), "exec")
    except SyntaxError as exc:
        errors.append(f"Syntax error: {source.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")

strings = load_json(COMPONENT / "strings.json")
english = load_json(COMPONENT / "translations" / "en.json")
danish = load_json(COMPONENT / "translations" / "da.json")
if strings is not None and english is not None:
    strings_keys, english_keys = flatten_keys(strings), flatten_keys(english)
    for missing in sorted(strings_keys - english_keys):
        errors.append(f"In strings.json but not translations/en.json: {missing}")
    for missing in sorted(english_keys - strings_keys):
        errors.append(f"In translations/en.json but not strings.json: {missing}")
    if danish is not None:
        danish_keys = flatten_keys(danish)
        for stale in sorted(danish_keys - english_keys):
            warnings.append(f"In translations/da.json but not en.json (stale key?): {stale}")
        info.append(f"da.json translates {len(danish_keys & english_keys)} of {len(english_keys)} keys (the rest fall back to English).")

# --- Changes since the last release --------------------------------------------
if last_tag:
    changed = set(git("diff", "--name-only", f"{last_tag}..HEAD").splitlines())
    component_prefix = "custom_components/pwmngt/"
    mirrored_changed = [f for f in CARD_MIRRORED_FILES if component_prefix + f in changed]
    if mirrored_changed and component_prefix + "www/pwm-cards.js" not in changed:
        warnings.append(f"{', '.join(mirrored_changed)} changed but www/pwm-cards.js did not - check whether a card's fields array needs the same change.")
    if not any(name.startswith(component_prefix) for name in changed):
        warnings.append(f"No changes under custom_components/pwmngt since {last_tag} - the release would ship nothing new.")

    # Only the commit convention counts (see the github-issue skill): issue numbers leading the
    # subject ("#12 #15 ...") and "Refs #12" in the body - not any "#N" mentioned in passing.
    issue_set: set[int] = set()
    for subject in git("log", "--format=%s", f"{last_tag}..HEAD").splitlines():
        leading = re.match(r"^((?:#\d+[ ,]+)+)", subject + " ")
        if leading:
            issue_set |= {int(n) for n in re.findall(r"#(\d+)", leading.group(1))}
    for refs in re.findall(r"Refs((?:[ ,]+#\d+)+)", git("log", "--format=%b", f"{last_tag}..HEAD")):
        issue_set |= {int(n) for n in re.findall(r"#(\d+)", refs)}
    issue_numbers = sorted(issue_set)
    subjects = git("log", "--format=%h %s", f"{last_tag}..HEAD")
    info.append(f"commits since {last_tag}:\n    " + (subjects.replace("\n", "\n    ") if subjects else "(none)"))
    info.append("issues=" + (" ".join(f"#{n}" for n in issue_numbers) if issue_numbers else "(none)"))

sys.stdout.reconfigure(encoding="utf-8")
for label, lines in (("ERROR", errors), ("WARN", warnings), ("INFO", info)):
    for line in lines:
        print(f"{label}: {line}")
sys.exit(1 if errors else 0)
