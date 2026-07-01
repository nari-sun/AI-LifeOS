[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CodexArgs
)

$ErrorActionPreference = "Stop"

function Get-NpmGlobalPrefix {
    try {
        $output = & npm.cmd prefix -g 2>$null
        $exitCode = $LASTEXITCODE
        $prefix = ($output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
        if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($prefix)) {
            return $null
        }
        return $prefix.Trim()
    }
    catch {
        return $null
    }
}

function Get-CodexExecutable {
    $candidates = @()
    $prefix = Get-NpmGlobalPrefix

    if (-not [string]::IsNullOrWhiteSpace($prefix)) {
        $candidates += Join-Path $prefix "codex.cmd"
        $candidates += Join-Path $prefix "codex.ps1"
        $candidates += Join-Path $prefix "codex"
    }

    foreach ($name in @("codex.cmd", "codex.ps1", "codex")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            $candidates += $command.Source
        }
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }

    return $null
}

function Get-SemVerCore {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    if ($Value -match "(?<version>\d+\.\d+\.\d+)") {
        try {
            return [version]$Matches.version
        }
        catch {
            return $null
        }
    }

    return $null
}

function Get-InstalledCodexVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CodexPath
    )

    try {
        $versionOutput = & $CodexPath --version 2>$null
        $exitCode = $LASTEXITCODE
        $output = ($versionOutput | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
        if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
            return $null
        }
        return Get-SemVerCore -Value $output
    }
    catch {
        return $null
    }
}

function Get-LatestCodexVersion {
    $latestOutput = & npm.cmd view @openai/codex version 2>$null
    $exitCode = $LASTEXITCODE
    $latest = ($latestOutput | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
    if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($latest)) {
        throw "Unable to read @openai/codex version from npm registry."
    }

    $version = Get-SemVerCore -Value $latest
    if (-not $version) {
        throw "Unable to parse @openai/codex version returned by npm: $latest"
    }

    return $version
}

function Install-LatestCodex {
    Write-Host "Installing latest @openai/codex..." -ForegroundColor Cyan
    & npm.cmd install -g @openai/codex@latest
    if ($LASTEXITCODE -ne 0) {
        throw "npm install -g @openai/codex@latest failed."
    }
}

function Ensure-CodexCli {
    $codexPath = Get-CodexExecutable

    try {
        $latestVersion = Get-LatestCodexVersion
    }
    catch {
        Write-Warning "$($_.Exception.Message) Starting installed Codex CLI without update check."
        return $codexPath
    }

    $installedVersion = $null
    if ($codexPath) {
        $installedVersion = Get-InstalledCodexVersion -CodexPath $codexPath
    }

    if (-not $codexPath -or -not $installedVersion -or $installedVersion -lt $latestVersion) {
        if ($installedVersion) {
            Write-Host "Updating @openai/codex from $installedVersion to $latestVersion..." -ForegroundColor Cyan
        }
        else {
            Write-Host "Installing @openai/codex $latestVersion..." -ForegroundColor Cyan
        }

        Install-LatestCodex
        $codexPath = Get-CodexExecutable
    }

    return $codexPath
}

$codexPath = Ensure-CodexCli
if (-not $codexPath) {
    throw "Codex CLI is not installed and could not be located."
}

& $codexPath @CodexArgs
exit $LASTEXITCODE
