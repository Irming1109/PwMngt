// Uploads issue screenshots to the branch "issue-screenshots" on GitHub and prints the Markdown to put in the issue
// (github-issue skill). GitHub has no API for issue attachments, so the images live on their own branch (no history in
// common with main - deploys, pulls of main and the servers never get them); the repo is private, so only people with
// access see them.
//
//   node .claude/skills/github-issue/upload-screenshots.mjs issue-screenshots/27-ukendt-tag.jpg [more files ...]
//
// Runs from the repo's root folder. Works without changing the checked-out branch: the files are added to the branch with
// git plumbing (hash-object / mktree / commit-tree) and pushed. A file name that is already on the branch is refused
// (an issue may show it) - give the new picture another name.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { basename } from "node:path";

const BRANCH = "issue-screenshots";
const REPO_URL = "https://github.com/Irming1109/PwMngt";

const git = (args, input) => execFileSync("git", args, { encoding: "utf8", input, stdio: ["pipe", "pipe", "inherit"] }).trim();

const files = process.argv.slice(2);
if (files.length === 0) {
    console.error("Usage: node .claude/skills/github-issue/upload-screenshots.mjs <image> [<image> ...]");
    process.exit(1);
}
for (const file of files) {
    if (!existsSync(file)) {
        console.error(`Not found: ${file}`);
        process.exit(1);
    }
    if (!/\.(png|jpe?g|gif|webp)$/i.test(file)) {
        console.error(`Not an image: ${file}`);
        process.exit(1);
    }
}

// The branch as it is on GitHub now (it may not exist yet)
const remoteHead = git(["ls-remote", "--heads", "origin", BRANCH]).split(/\s+/)[0] || null;
let entries = [];
if (remoteHead) {
    git(["fetch", "--quiet", "origin", `${BRANCH}:refs/remotes/origin/${BRANCH}`]);
    // "<mode> blob <sha>\t<name>" per file
    entries = git(["ls-tree", remoteHead]).split("\n").filter(Boolean);
}
const existingNames = new Set(entries.map((line) => line.split("\t")[1]));

const added = [];
for (const file of files) {
    const name = basename(file);
    if (existingNames.has(name)) {
        console.error(`Already on the branch: ${name} - give the new picture another name.`);
        process.exit(1);
    }
    const blob = git(["hash-object", "-w", file]);
    entries.push(`100644 blob ${blob}\t${name}`);
    existingNames.add(name);
    added.push(name);
}

const tree = git(["mktree"], entries.join("\n") + "\n");
const commitArgs = ["commit-tree", tree, "-m", `Screenshots: ${added.join(", ")}`];
if (remoteHead) commitArgs.push("-p", remoteHead);
const commit = git(commitArgs);
git(["push", "--quiet", "origin", `${commit}:refs/heads/${BRANCH}`]);

console.log(`Pushed ${added.length} file(s) to ${BRANCH}. Markdown for the issue:\n`);
for (const name of added) {
    console.log(`![${name}](${REPO_URL}/blob/${BRANCH}/${encodeURIComponent(name)}?raw=true)`);
}
