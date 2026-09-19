<#
.SYNOPSIS
    Publishes a GitHub Release for PwMngt without touching github.com in a browser.

.DESCRIPTION
    1. Reads the version from custom_components/pwmngt/manifest.json.
    2. Creates an annotated git tag "vX.Y.Z" whose message is the content of
       RELEASE_NOTES.md (written by Claude for this release).
    3. Pushes ONLY that tag to origin.
    4. GitHub Actions (.github/workflows/release.yml) then automatically
       creates the actual GitHub Release (as a pre-release) from the tag's
       message -- no manual steps on github.com.

    Run this AFTER you've committed & pushed your own code changes as usual,
    and AFTER Claude has written this release's RELEASE_NOTES.md in the repo
    root.

.NOTES
    One-time setup (only needed once, not per release):
    On github.com -> Settings -> Actions -> General -> Workflow permissions,
    make sure "Read and write permissions" is selected, otherwise the
    workflow can't create releases.
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

$existingTag = git tag -l $tag
if ($existingTag) {
    Write-Error "Tag $tag already exists locally. Bump the version in manifest.json first."
    exit 1
}

Write-Host "Creating annotated tag $tag from manifest.json version $version ..."
git tag -a $tag -F RELEASE_NOTES.md

Write-Host "Pushing tag $tag to origin ..."
git push origin $tag

Write-Host ""
Write-Host "Done. GitHub Actions is now creating the release automatically:"
Write-Host "  https://github.com/Irming1109/PwMngt/actions"
Write-Host "The finished release should appear within ~10-20 seconds at:"
Write-Host "  https://github.com/Irming1109/PwMngt/releases/tag/$tag"
