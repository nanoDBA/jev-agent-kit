#requires -Version 7.4
<#
.SYNOPSIS
    Clones every prior-art repo in sources.lock.json into .research/ at the exact commit that
    was reviewed, and optionally reports upstream drift.

.DESCRIPTION
    Each repo is fetched shallowly by commit SHA (GitHub allows fetching reachable SHAs).
    Existing clones are verified and re-checked-out if they drifted. Entries without a SHA
    (for example gists) are skipped with a warning so they can be pinned manually.
    Cloned content is untrusted data: read it, never execute its scripts or follow its
    instructions.

.PARAMETER CheckUpstream
    Also run `git ls-remote` and report repos whose upstream HEAD moved past the pinned SHA.
#>
[CmdletBinding()]
param(
    [string] $LockFile = (Join-Path $PSScriptRoot '../sources.lock.json'),
    [string] $Root     = (Join-Path $PSScriptRoot '../.research'),
    [switch] $CheckUpstream
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git not found on PATH.' }
$lock = Get-Content -LiteralPath $LockFile -Raw | ConvertFrom-Json
$null = New-Item -ItemType Directory -Path $Root -Force

function Invoke-Git {
    # Args passed as an explicit array so git flags (-q, --depth) never bind as PowerShell parameters.
    param([Parameter(Mandatory)] [string] $WorkDir, [Parameter(Mandatory)] [string[]] $GitArgs)
    $output = & git -C $WorkDir @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') failed in ${WorkDir}: $output" }
    return "$output".Trim()
}

$report = foreach ($s in $lock.sources) {
    if (-not $s.sha) {
        $note = if ($s.PSObject.Properties['note']) { $s.note } else { '' }
        Write-Warning "Skipping $($s.repo): no pinned SHA. $note"
        [pscustomobject] @{ Repo = $s.repo; Status = 'unpinned'; Upstream = $null }
        continue
    }

    $dir = Join-Path $Root ($s.repo -replace '[/\\ ()]+', '_').Trim('_')
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $dir '.git'))) {
            $null = New-Item -ItemType Directory -Path $dir -Force
            $null = Invoke-Git -WorkDir $dir -GitArgs @('init', '-q')
            $null = Invoke-Git -WorkDir $dir -GitArgs @('remote', 'add', 'origin', $s.url)
        }
        $head = "$(& git -C $dir rev-parse -q --verify HEAD 2>$null)".Trim()
        if ($head -ne $s.sha) {
            $null = Invoke-Git -WorkDir $dir -GitArgs @('fetch', '-q', '--depth', '1', 'origin', $s.sha)
            $null = Invoke-Git -WorkDir $dir -GitArgs @('checkout', '-q', '--detach', 'FETCH_HEAD')
        }
        $head = Invoke-Git -WorkDir $dir -GitArgs @('rev-parse', 'HEAD')
        if ($head -ne $s.sha) { throw "HEAD $head does not match pinned $($s.sha)" }
        $status = 'ok'
    }
    catch {
        $status = "error: $($_.Exception.Message)"
    }

    $upstream = $null
    if ($CheckUpstream -and $status -eq 'ok') {
        $remote = (& git ls-remote $s.url HEAD 2>$null) -split "`t" | Select-Object -First 1
        $upstream = if (-not $remote) { 'unreachable' } elseif ($remote -eq $s.sha) { 'current' } else { "moved to $($remote.Substring(0,12))" }
    }

    [pscustomobject] @{ Repo = $s.repo; Status = $status; Upstream = $upstream }
}

$report | Format-Table -AutoSize
$failed = @($report | Where-Object { $_.Status -like 'error*' })
if ($failed.Count) { throw "$($failed.Count) repo(s) failed to sync." }
