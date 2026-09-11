[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    $output = & $FilePath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    if ($exitCode -ne 0) {
        if ($text) {
            Write-Host $text
        }
        throw "$FilePath failed with exit code $exitCode"
    }
    return $text
}

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$cargoToml = Join-Path $repoRoot "Cargo.toml"
if (-not (Test-Path -LiteralPath $cargoToml -PathType Leaf)) {
    throw "Cargo.toml not found at repository root: $repoRoot"
}

$cargoText = Get-Content -LiteralPath $cargoToml -Raw
if ($cargoText -notmatch '(?ms)^\[package\]\s*$.*?^name\s*=\s*"conversation-blackboard"\s*$') {
    throw "Repository is not the conversation-blackboard Cargo package: $repoRoot"
}

$resolvedRepoRoot = (Resolve-Path -LiteralPath $repoRoot).Path
$gitRoot = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "rev-parse", "--show-toplevel")
$resolvedGitRoot = (Resolve-Path -LiteralPath $gitRoot).Path
if (-not [string]::Equals($resolvedRepoRoot, $resolvedGitRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Script location does not resolve to the Git worktree root. repo=$resolvedRepoRoot git=$resolvedGitRoot"
}

$status = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "status", "--porcelain")
if (-not [string]::IsNullOrWhiteSpace($status)) {
    Write-Host "Working tree changes:"
    Write-Host $status
    throw "Release verification requires a clean Git worktree."
}

$head = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "rev-parse", "HEAD")
$branch = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "rev-parse", "--abbrev-ref", "HEAD")

Write-Host "Repository : $repoRoot"
Write-Host "Branch     : $branch"
Write-Host "HEAD       : $head"
Write-Host ""

Push-Location $repoRoot
try {
    Write-Host "[1/4] cargo fmt"
    Invoke-Native -FilePath "cargo" -Arguments @("fmt", "--all", "--", "--check")

    Write-Host "[2/4] cargo clippy"
    Invoke-Native -FilePath "cargo" -Arguments @("clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings")

    Write-Host "[3/4] cargo test"
    Invoke-Native -FilePath "cargo" -Arguments @("test", "--locked", "--all-targets")

    Write-Host "[4/4] cargo build --release"
    Invoke-Native -FilePath "cargo" -Arguments @("build", "--release", "--locked")
}
finally {
    Pop-Location
}

$isWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
$binaryName = if ($isWindows) { "conversation-blackboard.exe" } else { "conversation-blackboard" }
$releaseBinary = Join-Path $repoRoot (Join-Path "target\release" $binaryName)
if (-not (Test-Path -LiteralPath $releaseBinary -PathType Leaf)) {
    throw "Release binary was not produced: $releaseBinary"
}

$null = Invoke-NativeText -FilePath $releaseBinary -Arguments @("--help")
$hash = (Get-FileHash -LiteralPath $releaseBinary -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $releaseBinary).Length

$statusAfterBuild = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "status", "--porcelain")
if (-not [string]::IsNullOrWhiteSpace($statusAfterBuild)) {
    Write-Host "Working tree changes after build:"
    Write-Host $statusAfterBuild
    throw "Build changed the Git worktree."
}

$manifestPath = Join-Path $repoRoot "target\release\conversation-blackboard.release.json"
$manifest = [ordered]@{
    schema = 1
    commit = $head
    branch = $branch
    binary = $binaryName
    sha256 = $hash
    size = $size
    verified_at_utc = [DateTime]::UtcNow.ToString("o")
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "RELEASE VERIFICATION PASSED"
Write-Host "Binary     : $releaseBinary"
Write-Host "Size       : $size bytes"
Write-Host "SHA256     : $hash"
Write-Host "Manifest   : $manifestPath"

[pscustomobject]@{
    Repository = $repoRoot
    Branch = $branch
    Commit = $head
    Binary = $releaseBinary
    Size = $size
    SHA256 = $hash
    Manifest = $manifestPath
}
