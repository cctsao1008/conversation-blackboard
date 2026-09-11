[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [ValidateNotNullOrEmpty()]
    [string]$ServiceName = "ConversationBlackboard"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Get-ServiceCommand {
    param([Parameter(Mandatory = $true)][string]$PathName)

    $match = [regex]::Match($PathName, '^\s*(?:"(?<exe>[^"]+)"|(?<exe>\S+))\s+(?<args>.+)$')
    if (-not $match.Success) {
        throw "Could not parse Windows service command line: $PathName"
    }

    $arguments = $match.Groups['args'].Value
    if ($arguments -notmatch '(?i)(?:^|\s)service\s+run(?:\s|$)') {
        throw "Service command line is not a conversation-blackboard service run command: $PathName"
    }

    [pscustomobject]@{
        Executable = $match.Groups['exe'].Value
        Arguments = $arguments
    }
}

function Get-CommandOption {
    param(
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $pattern = '(?i)(?:^|\s)--' + [regex]::Escape($Name) + '\s+(?:"(?<quoted>[^"]+)"|(?<bare>\S+))'
    $match = [regex]::Match($Arguments, $pattern)
    if (-not $match.Success) {
        throw "Required service option --$Name was not found."
    }
    if ($match.Groups['quoted'].Success) {
        return $match.Groups['quoted'].Value
    }
    return $match.Groups['bare'].Value
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Production deployment requires an elevated PowerShell session."
    }
}

function Assert-DatabaseIntegrity {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Database
    )

    $output = Invoke-NativeText -FilePath $Executable -Arguments @("db", "integrity", "--db", $Database)
    $lines = $output -split '\r?\n' | ForEach-Object { $_.Trim() }
    if ($lines -notcontains "ok") {
        throw "Database integrity command did not report ok: $Database"
    }
}

function Assert-Health {
    param([Parameter(Mandatory = $true)][string]$Uri)

    $health = Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5
    if ($health.status -ne "ok") {
        throw "Health endpoint did not report ok: $Uri"
    }
}

function Wait-ServiceState {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet("Running", "Stopped")][string]$State
    )

    $service = Get-Service -Name $Name -ErrorAction Stop
    $service.WaitForStatus($State, [TimeSpan]::FromSeconds(20))
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "deploy-windows.ps1 can only run on Windows."
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

$gitRoot = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "rev-parse", "--show-toplevel")
$resolvedRepoRoot = (Resolve-Path -LiteralPath $repoRoot).Path
$resolvedGitRoot = (Resolve-Path -LiteralPath $gitRoot).Path
if (-not [string]::Equals($resolvedRepoRoot, $resolvedGitRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Script location does not resolve to the Git worktree root."
}

$status = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "status", "--porcelain")
if (-not [string]::IsNullOrWhiteSpace($status)) {
    Write-Host "Working tree changes:"
    Write-Host $status
    throw "Deployment requires a clean Git worktree."
}

$head = Invoke-NativeText -FilePath "git" -Arguments @("-C", $repoRoot, "rev-parse", "HEAD")
$sourceExe = Join-Path $repoRoot "target\release\conversation-blackboard.exe"
$manifestPath = Join-Path $repoRoot "target\release\conversation-blackboard.release.json"
if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
    throw "Release binary not found. Run scripts\verify-release.ps1 first: $sourceExe"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Release verification manifest not found. Run scripts\verify-release.ps1 first."
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne 1) {
    throw "Unsupported release verification manifest schema."
}
if ($manifest.commit -ne $head) {
    throw "Release binary was verified for commit $($manifest.commit), but current HEAD is $head. Run scripts\verify-release.ps1 again."
}
$sourceHash = (Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceFingerprint = $sourceHash.Substring(0, 16).ToUpperInvariant()
if ($manifest.sha256 -ne $sourceHash) {
    throw "Release binary hash no longer matches the verified manifest. Run scripts\verify-release.ps1 again."
}
$null = Invoke-NativeText -FilePath $sourceExe -Arguments @("--help")

$serviceFilterName = $ServiceName.Replace("'", "''")
$serviceConfig = Get-CimInstance Win32_Service -Filter "Name='$serviceFilterName'" -ErrorAction Stop
if ($null -eq $serviceConfig) {
    throw "Windows service not found: $ServiceName"
}
if ($serviceConfig.State -ne "Running") {
    throw "Service must be Running before deployment preflight. Current state: $($serviceConfig.State)"
}

$serviceCommand = Get-ServiceCommand -PathName $serviceConfig.PathName
$productionExe = [System.IO.Path]::GetFullPath($serviceCommand.Executable)
$database = [System.IO.Path]::GetFullPath((Get-CommandOption -Arguments $serviceCommand.Arguments -Name "db"))
$runtimeHost = Get-CommandOption -Arguments $serviceCommand.Arguments -Name "host"
$runtimePortText = Get-CommandOption -Arguments $serviceCommand.Arguments -Name "port"
$runtimePort = 0
if (-not [int]::TryParse($runtimePortText, [ref]$runtimePort) -or $runtimePort -lt 1 -or $runtimePort -gt 65535) {
    throw "Invalid --port in service command line: $runtimePortText"
}

if (-not (Test-Path -LiteralPath $productionExe -PathType Leaf)) {
    throw "Production executable from service configuration does not exist: $productionExe"
}
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "Production database from service configuration does not exist: $database"
}

$probeHost = $runtimeHost
if ($probeHost -eq "0.0.0.0" -or $probeHost -eq "::") {
    $probeHost = "127.0.0.1"
}
if ($probeHost.Contains(":")) {
    $probeHost = "[$probeHost]"
}
$healthUri = "http://${probeHost}:$runtimePort/api/health"

if (-not $WhatIfPreference) {
    Assert-Administrator
}

Write-Host "DISCOVERED PRODUCTION CONFIGURATION"
Write-Host "Repository    : $repoRoot"
Write-Host "HEAD          : $head"
Write-Host "Source EXE    : $sourceExe"
Write-Host "SHA256-64     : $sourceFingerprint"
Write-Host "Service       : $ServiceName"
Write-Host "Service state : $($serviceConfig.State)"
Write-Host "Production EXE: $productionExe"
Write-Host "Database      : $database"
Write-Host "Host          : $runtimeHost"
Write-Host "Port          : $runtimePort"
Write-Host "Health        : $healthUri"
Write-Host ""

Write-Host "PRE-FLIGHT"
Write-Host "[1/3] Source release manifest and hash ... PASS"
Assert-Health -Uri $healthUri
Write-Host "[2/3] Current service health .............. PASS"
Assert-DatabaseIntegrity -Executable $productionExe -Database $database
Write-Host "[3/3] Production database integrity ....... PASS"
Write-Host "PRE-FLIGHT PASSED"
Write-Host ""

$targetDescription = "$ServiceName using $productionExe and $database"
if (-not $PSCmdlet.ShouldProcess($targetDescription, "Back up, replace the executable, and restart the service")) {
    Write-Host "No production mutation performed."
    return
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$productionBinDir = Split-Path -Parent $productionExe
$installRoot = Split-Path -Parent $productionBinDir
$backupDirectory = Join-Path $installRoot "backups"
$dbBaseName = [System.IO.Path]::GetFileNameWithoutExtension($database)
$databaseBackup = Join-Path $backupDirectory ("{0}-pre-deploy-{1}.db" -f $dbBaseName, $stamp)
$binaryBackup = "$productionExe.pre-$stamp"

New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

Write-Host "MUTATION"
Write-Host "[1/7] Creating SQLite-aware backup: $databaseBackup"
$null = Invoke-NativeText -FilePath $productionExe -Arguments @("db", "backup", "--db", $database, "--out", $databaseBackup)
Assert-DatabaseIntegrity -Executable $productionExe -Database $databaseBackup
Write-Host "      Backup integrity .................... PASS"

Write-Host "[2/7] Preserving current executable: $binaryBackup"
Copy-Item -LiteralPath $productionExe -Destination $binaryBackup -Force

$binaryReplaced = $false
try {
    Write-Host "[3/7] Stopping $ServiceName"
    Stop-Service -Name $ServiceName -ErrorAction Stop
    Wait-ServiceState -Name $ServiceName -State "Stopped"

    Write-Host "[4/7] Replacing production executable"
    Copy-Item -LiteralPath $sourceExe -Destination $productionExe -Force
    $binaryReplaced = $true

    $productionHash = (Get-FileHash -LiteralPath $productionExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($productionHash -ne $sourceHash) {
        throw "Production executable SHA256 does not match verified source binary."
    }
    Write-Host "[5/7] Production SHA256 .................... PASS"

    Write-Host "[6/7] Starting $ServiceName"
    Start-Service -Name $ServiceName -ErrorAction Stop
    Wait-ServiceState -Name $ServiceName -State "Running"

    Assert-Health -Uri $healthUri
    Write-Host "      Service health ....................... PASS"
    Assert-DatabaseIntegrity -Executable $productionExe -Database $database
    Write-Host "[7/7] Post-deploy database integrity ....... PASS"
}
catch {
    $deploymentError = $_
    Write-Warning "Deployment failed. Attempting executable/service rollback."

    try {
        $current = Get-Service -Name $ServiceName -ErrorAction Stop
        if ($current.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction Stop
            Wait-ServiceState -Name $ServiceName -State "Stopped"
        }

        if ($binaryReplaced) {
            Copy-Item -LiteralPath $binaryBackup -Destination $productionExe -Force
        }

        Start-Service -Name $ServiceName -ErrorAction Stop
        Wait-ServiceState -Name $ServiceName -State "Running"
        Assert-Health -Uri $healthUri
        Write-Warning "Rollback completed; the previous executable is running."
    }
    catch {
        throw "Deployment failed: $($deploymentError.Exception.Message). Rollback also failed: $($_.Exception.Message)"
    }

    throw $deploymentError
}

Write-Host ""
Write-Host "DEPLOYMENT PASSED"
Write-Host "HEAD          : $head"
Write-Host "SHA256-64     : $sourceFingerprint"
Write-Host "Database      : $database"
Write-Host "DB backup     : $databaseBackup"
Write-Host "Binary backup : $binaryBackup"
Write-Host "Health        : $healthUri"
