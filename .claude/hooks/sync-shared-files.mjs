// SessionStart hook: keeps the shared Claude files (CLAUDE.md, skills, hooks, settings) in step with GitHub,
// without touching the code. Fetches origin/main and copies only SHARED_PATHS that GitHub has changed and
// that have no local changes. The rest of main is only reported (run "git pull --autostash" to get the code).
// Cross-platform (Windows + macOS): plain Node, no shell features.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SHARED_PATHS = ["CLAUDE.md", ".claude/skills", ".claude/hooks", ".claude/settings.json"];
const REMOTE_BRANCH = "origin/main";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function git(...args) {
    return execFileSync("git", args, { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 20000 }).trim();
}

function gitSucceeds(...args) {
    try {
        git(...args);
        return true;
    } catch {
        return false;
    }
}

function lines(text) {
    return text.split("\n").map((line) => line.trim()).filter((line) => line.length > 0);
}

function output(userMessage, claudeContext) {
    process.stdout.write(JSON.stringify({
        systemMessage: userMessage,
        hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: claudeContext },
    }));
}

try {
    git("fetch", "--quiet", "origin", "main");
} catch {
    // Offline or no access: never block the session.
    process.exit(0);
}

const mergeBase = git("merge-base", "HEAD", REMOTE_BRANCH);
// Only what GitHub changed since our common ancestor - never overwrite newer local commits with older remote files.
const changedOnRemote = lines(git("diff", "--name-only", mergeBase, REMOTE_BRANCH, "--", ...SHARED_PATHS));
const changedLocally = new Set(lines(git("diff", "--name-only", mergeBase, "HEAD", "--", ...SHARED_PATHS)));

function blobAt(revision, file) {
    return gitSucceeds("cat-file", "-e", `${revision}:${file}`) ? git("rev-parse", `${revision}:${file}`) : null;
}

function workingTreeBlob(file) {
    return existsSync(resolve(repoRoot, file)) ? git("hash-object", "--", file) : null;
}

// A local copy that equals HEAD or an earlier GitHub version is not an own edit (earlier runs of this hook leave such copies).
function hasOwnEdits(file, localBlob) {
    if (localBlob === blobAt("HEAD", file)) {
        return false;
    }
    const remoteHistory = git("log", "--format=", "--raw", "--no-abbrev", REMOTE_BRANCH, "--", file);
    return localBlob === null || !remoteHistory.includes(localBlob);
}

const updated = [];
const skipped = [];
for (const file of changedOnRemote) {
    const remoteBlob = blobAt(REMOTE_BRANCH, file);
    const localBlob = workingTreeBlob(file);
    if (localBlob === remoteBlob) {
        continue;
    }
    if (changedLocally.has(file) || hasOwnEdits(file, localBlob)) {
        skipped.push(file);
        continue;
    }
    if (remoteBlob !== null) {
        git("checkout", REMOTE_BRANCH, "--", file);
    } else {
        git("rm", "--quiet", "--", file);
    }
    updated.push(file);
}

const behindCount = Number(git("rev-list", "--count", `HEAD..${REMOTE_BRANCH}`));

const userParts = [];
const claudeParts = [];
if (updated.length > 0) {
    userParts.push(`Updated from GitHub: ${updated.join(", ")}`);
    claudeParts.push(`These shared files were just updated from GitHub (${REMOTE_BRANCH}) after this session loaded its instructions: ${updated.join(", ")}. `
        + "Read the updated files now (CLAUDE.md first) before working, and briefly tell the user in Danish what is new.");
}
if (skipped.length > 0) {
    userParts.push(`Not updated (local changes): ${skipped.join(", ")}`);
    claudeParts.push(`These shared files changed on GitHub but were NOT updated because they have local changes: ${skipped.join(", ")}. Tell the user.`);
}
if (behindCount > 0) {
    userParts.push(`${behindCount} new commit(s) on GitHub - run "git pull --autostash" to get the code`);
    claudeParts.push(`The local checkout is ${behindCount} commit(s) behind ${REMOTE_BRANCH}; the code itself was not updated. Mention that "git pull --autostash" gets it (--autostash because files updated by this hook count as uncommitted changes).`);
}

if (userParts.length > 0) {
    output(userParts.join(" | "), claudeParts.join(" "));
}
