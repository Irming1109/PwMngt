<#
.SYNOPSIS
    Publishes a GitHub Release for PwMngt without touching github.com in a browser.

.DESCRIPTION
    1. Reads the version from custom_components/pwmngt/manifest.json.
    2. Creates an annotated git tag "vX.Y.Z" whose message is the content of
       RELEASE_NOTES.md (written by Claude for this release).
    3. Pushes ONLY that tag to origin.
    4. Creates the GitHub Release itself with the GitHub CLI (gh), marked
       "Latest", with RELEASE_NOTES.md as its body. Done locally rather
       than by GitHub Actions so the release is published under the name
       of whoever deploys -- the Actions token shows "github-actions"
       instead (Claus, 2026-10-26). .github/workflows/release.yml is kept
       only as a manual fallback (Actions -> Run workflow).

    Run this AFTER you've committed & pushed your own code changes as usual,
    and AFTER Claude has written this release's RELEASE_NOTES.md in the repo
    root.

.NOTES
    Requires the GitHub CLI, logged in with your own account (gh auth status).
#>

$ErrorActionPreference = "Stop"

$manifestPath = "custom_components/pwmngt/manifest.json"
if (-not (Test-Path $manifestPath)) {
    Write-Error "Could not find $manifestPath - run this script from the repo root."
    exit 1
}

if (-not (Test-Path "RELEASE_NOTES.md")) {
    Write-Error "RELEASE_NOTES.md is missing in the repo root - ask Claude to write this release's notes first."
    exit 1
}

$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$version = $manifest.version
$tag = "v$version"

# gh is not always on PATH on Windows -- fall back to its default install path.
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) {
    Write-Error "GitHub CLI (gh) not found - install it and log in with 'gh auth login'."
    exit 1
}

$existingTag = git tag -l $tag
if ($existingTag) {
    Write-Error "Tag $tag already exists locally. Bump the version in manifest.json first."
    exit 1
}

Write-Host "Creating annotated tag $tag from manifest.json version $version ..."
# --cleanup=verbatim: without it git strips every line starting with "#"
# as a comment, so Markdown headings ("## ...") silently vanished from
# the release body (v0.3.14).
git tag -a $tag -F RELEASE_NOTES.md --cleanup=verbatim

Write-Host "Pushing tag $tag to origin ..."
git push origin $tag
if ($LASTEXITCODE -ne 0) {
    Write-Error "Pushing tag $tag failed."
    exit 1
}

Write-Host "Creating GitHub Release $tag ..."
& $gh release create $tag --repo Irming1109/PwMngt --title $tag --notes-file RELEASE_NOTES.md --verify-tag --latest
if ($LASTEXITCODE -ne 0) {
    Write-Error "Creating the release failed - the tag is pushed; retry with gh, or run the release workflow manually on github.com."
    exit 1
}

Write-Host ""
Write-Host "Done: https://github.com/Irming1109/PwMngt/releases/tag/$tag"
