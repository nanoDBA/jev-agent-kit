#requires -Version 7.4
<#
.SYNOPSIS
    Downloads English auto-captions for YouTube videos with yt-dlp and writes a de-duplicated
    plain-text transcript next to each .vtt file.

.DESCRIPTION
    Requires yt-dlp on PATH (winget install yt-dlp.yt-dlp, or pip install yt-dlp).
    Captions are fetched without downloading video. YouTube auto-captions repeat each line as
    it scrolls; the text output collapses those repeats.

.EXAMPLE
    ./Get-VideoTranscripts.ps1
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string[]] $Url = @('https://youtu.be/D-Z5HnLW_ho', 'https://youtu.be/L2K__oshGds'),

    [string] $OutDir = (Join-Path $PSScriptRoot '../docs/research/raw/transcripts')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ytdlp = Get-Command yt-dlp -ErrorAction SilentlyContinue
if (-not $ytdlp) { throw 'yt-dlp not found on PATH. Install with: winget install yt-dlp.yt-dlp' }

$null = New-Item -ItemType Directory -Path $OutDir -Force
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path

$ytArgs = @(
    '--skip-download', '--write-auto-subs', '--write-subs',
    '--sub-langs', 'en.*,en', '--sub-format', 'vtt',
    '--no-overwrites', '--no-progress',
    '-o', (Join-Path $OutDir '%(id)s.%(ext)s')
) + $Url

& $ytdlp.Source @ytArgs
if ($LASTEXITCODE -ne 0) { throw "yt-dlp exited with code $LASTEXITCODE" }

function ConvertFrom-Vtt {
    param([Parameter(Mandatory)] [string] $Path)
    $out  = [System.Collections.Generic.List[string]]::new()
    $last = $null
    foreach ($raw in [System.IO.File]::ReadLines($Path)) {
        $line = $raw.Trim()
        if (-not $line) { continue }
        if ($line -eq 'WEBVTT' -or $line -match '^(Kind|Language|NOTE|STYLE)\b') { continue }
        if ($line -match '-->') { continue }
        if ($line -match '^\d+$') { continue }
        $line = [System.Net.WebUtility]::HtmlDecode(($line -replace '<[^>]+>', '')).Trim()
        if (-not $line -or $line -eq $last) { continue }
        $out.Add($line)
        $last = $line
    }
    return $out
}

$vtts = @(Get-ChildItem -LiteralPath $OutDir -Filter '*.vtt' -File)
if ($vtts.Count -eq 0) { throw "No .vtt files in $OutDir. The videos may have no captions yet." }

foreach ($v in $vtts) {
    $txt = [System.IO.Path]::ChangeExtension($v.FullName, '.txt')
    $lines = ConvertFrom-Vtt -Path $v.FullName
    Set-Content -LiteralPath $txt -Value $lines -Encoding utf8NoBOM
    Write-Host ("{0}: {1} lines" -f (Split-Path $txt -Leaf), $lines.Count)
}
