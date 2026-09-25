#requires -Version 7.0
<#
.SYNOPSIS
    Pulls X posts (full long-form text) plus the same-author thread into JSON for offline digestion.

.DESCRIPTION
    Uses X API v2: tweet lookup for the given IDs, then recent search on
    conversation_id + author to reconstruct the thread. Long posts are read from
    note_tweet, because `text` is truncated for posts over 280 chars.

    Recent search only covers roughly the last 7 days. Threads older than that come
    back empty; the script records that as thread_complete = $false instead of hiding it.

.PARAMETER BearerToken
    Optional. Resolution order: this parameter, then the SecretManagement secret
    'XBearerToken', then $env:X_BEARER_TOKEN. Never paste the token into a chat.

.EXAMPLE
    ./Get-XThread.ps1 -OutFile ./x-jev-posts.json
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string[]] $TweetId = @('2102020016539324501', '2103157617715491278', '2102382523879678023'),

    [string] $OutFile = (Join-Path $PSScriptRoot '../docs/research/raw/x-jev-posts.json'),

    [string] $BearerToken,

    [ValidateRange(1, 3600)]
    [int] $MaxRateLimitWaitSec = 900
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- token
if ([string]::IsNullOrWhiteSpace($BearerToken) -and (Get-Command Get-Secret -ErrorAction SilentlyContinue)) {
    try { $BearerToken = Get-Secret -Name XBearerToken -AsPlainText -ErrorAction Stop } catch { $BearerToken = $null }
}
if ([string]::IsNullOrWhiteSpace($BearerToken)) { $BearerToken = $env:X_BEARER_TOKEN }
if ([string]::IsNullOrWhiteSpace($BearerToken)) {
    throw 'No X bearer token. Set SecretManagement secret XBearerToken or $env:X_BEARER_TOKEN.'
}

$base    = 'https://api.x.com/2'
$fields  = 'tweet.fields=created_at,conversation_id,author_id,note_tweet,entities,referenced_tweets' +
           '&expansions=author_id&user.fields=username'
$headers = @{ Authorization = "Bearer $BearerToken" }

# ---------------------------------------------------------------- helpers
function Get-Prop {
    # StrictMode-safe property read: returns $null instead of throwing on a missing member.
    param([object] $Object, [string] $Name)
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Invoke-X {
    param([Parameter(Mandatory)] [string] $Uri)

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $sc = 0; $rh = $null
        $resp = Invoke-RestMethod -Uri $Uri -Headers $headers -TimeoutSec 30 `
            -SkipHttpErrorCheck -StatusCodeVariable sc -ResponseHeadersVariable rh

        if ($sc -eq 200) { return $resp }

        if ($sc -eq 429) {
            $wait = 60
            $key = @($rh.Keys) | Where-Object { $_ -ieq 'x-rate-limit-reset' } | Select-Object -First 1
            if ($key) {
                $reset = [long] (@($rh[$key])[0])
                $wait  = [math]::Max(1, $reset - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 1)
            }
            if ($wait -gt $MaxRateLimitWaitSec) {
                throw "429 on $Uri : reset in ${wait}s exceeds cap of ${MaxRateLimitWaitSec}s."
            }
            Write-Warning "429 rate limited; sleeping ${wait}s (attempt $attempt of 3)"
            Start-Sleep -Seconds $wait
            continue
        }

        # 401/403 = auth or access tier; anything else = real error. Surface the body, never swallow it.
        throw "X API HTTP $sc for $Uri : $($resp | ConvertTo-Json -Depth 10 -Compress)"
    }
    throw "Gave up after 3 rate-limited attempts: $Uri"
}

function ConvertTo-Post {
    param([object] $Tweet, [hashtable] $Users)
    $note = Get-Prop $Tweet 'note_tweet'
    $ents = Get-Prop $Tweet 'entities'
    $urls = @(Get-Prop $ents 'urls' | Where-Object { $_ } | ForEach-Object { Get-Prop $_ 'expanded_url' })
    [ordered] @{
        id              = $Tweet.id
        created_at      = Get-Prop $Tweet 'created_at'
        author          = $Users[[string] (Get-Prop $Tweet 'author_id')]
        conversation_id = Get-Prop $Tweet 'conversation_id'
        text            = if ($note) { $note.text } else { $Tweet.text }   # full text for long posts
        long_form       = [bool] $note
        urls            = $urls
    }
}

# ---------------------------------------------------------------- lookup
$result = [ordered] @{
    fetched_utc = [DateTime]::UtcNow.ToString('o')
    requested   = $TweetId
    posts       = [System.Collections.Generic.List[object]]::new()
    threads     = [ordered] @{}
    errors      = [System.Collections.Generic.List[object]]::new()
}

$lookup = Invoke-X "$base/tweets?ids=$($TweetId -join ',')&$fields"

# A 200 can still carry per-ID errors (deleted, protected, suspended). Keep them.
foreach ($e in @(Get-Prop $lookup 'errors')) { if ($e) { $result.errors.Add($e) } }

$users = @{}
foreach ($u in @(Get-Prop (Get-Prop $lookup 'includes') 'users')) { if ($u) { $users[[string] $u.id] = $u.username } }

$roots = @(Get-Prop $lookup 'data' | Where-Object { $_ })
foreach ($t in $roots) { $result.posts.Add((ConvertTo-Post $t $users)) }

# ---------------------------------------------------------------- threads
$cutoff = [DateTime]::UtcNow.AddDays(-6.5)   # margin inside the 7-day recent-search window
$seen = @{}
foreach ($post in $result.posts) {
    $cid = [string] $post.conversation_id
    if (-not $cid -or $seen.ContainsKey($cid) -or -not $post.author) { continue }
    $seen[$cid] = $true

    $entry = [ordered] @{
        conversation_id = $cid
        root_in_lookup  = [bool] ($post.id -eq $cid)
        thread_complete = ([DateTime] $post.created_at).ToUniversalTime() -gt $cutoff
        replies         = [System.Collections.Generic.List[object]]::new()
    }

    $query = [uri]::EscapeDataString("conversation_id:$cid from:$($post.author)")
    $next  = $null
    try {
        do {
            $page = "$base/tweets/search/recent?query=$query&max_results=100&$fields"
            if ($next) { $page += "&next_token=$next" }
            $resp = Invoke-X $page
            foreach ($u in @(Get-Prop (Get-Prop $resp 'includes') 'users')) { if ($u) { $users[[string] $u.id] = $u.username } }
            foreach ($t in @(Get-Prop $resp 'data' | Where-Object { $_ })) { $entry.replies.Add((ConvertTo-Post $t $users)) }
            $next = Get-Prop (Get-Prop $resp 'meta') 'next_token'
        } while ($next)
    }
    catch {
        # Most likely your access tier lacks search. Record it; the root posts are still useful.
        $entry.thread_complete = $false
        $result.errors.Add([ordered] @{ conversation_id = $cid; error = $_.Exception.Message })
    }

    $sorted = @($entry.replies | Sort-Object { [DateTime] $_.created_at })
    $entry.replies = $sorted
    $result.threads[$cid] = $entry
}

# ---------------------------------------------------------------- output
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutFile -Encoding utf8NoBOM
Write-Host ("Wrote {0}: {1} posts, {2} threads, {3} errors" -f `
    $OutFile, $result.posts.Count, $result.threads.Count, $result.errors.Count)
