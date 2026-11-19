<#
  Parlay Locks - multi-model panel (default: Claude Code + Cursor Agent)
  PAPER only. Runs on Sneed_PC via Task Scheduler at 10:30 and 17:30 ET.

  Default panel (from 2026-11-19): Claude and Cursor. Codex is still wired up
  and can be added back with -Models claude,cursor,codex or panel-config.json
  {"Models":["claude","cursor","codex"]}. A named-error cut from either
  default model vetoes the leg (see consensus.veto).

  Flow: git pull -> find newest candidates file with no data/votes/<run>/_panel.json
        -> one concise prompt per model (in parallel) -> validate strict JSON
        -> write data/votes/<run>/<model>.json + _panel.json + feed lines -> commit + push.

  Usage:
    powershell -ExecutionPolicy Bypass -File C:\parlay-locks\run-panel.ps1            # normal scheduled run (Claude + Cursor)
    ... -Test                     # tiny prompt to each CLI, no repo writes
    ... -Run 2026-11-12-morning   # specific run
    ... -Force                    # re-vote even if voted / past finalize time
    ... -SecondPass               # each model sees the others' votes and may revise (costs ~2x)
    ... -Models claude,cursor,codex  # include Codex (or any subset)
    ... -NoPush                   # write locally only
  Optional overrides: C:\parlay-locks\panel-config.json  e.g. {"ClaudeModel":"sonnet","SecondPass":true,"Models":["claude","cursor","codex"]}
#>
param(
  [string]$Run = "",
  [string[]]$Models = @(),       # empty = Claude + Cursor, or Cfg.Models; pass -Models to override
  [switch]$SecondPass,
  [switch]$Force,
  [switch]$NoPush,
  [switch]$Test,
  [string]$RepoPath = ""        # override repo location (testing)
)

$ErrorActionPreference = "Stop"
$Root   = "C:\parlay-locks"
$Repo   = Join-Path $Root "repo"
if ($RepoPath) { $Repo = $RepoPath }
$Work   = Join-Path $Root "work"
$LogDir = Join-Path $Root "logs"
$SchemaFile = Join-Path $Root "vote-output.schema.json"

$Cfg = @{
  ClaudeModel  = "haiku"   # cheapest Claude; set "sonnet" for deeper reasoning
  ClaudeEffort = "low"
  CodexModel   = ""        # "" = Codex default model (from ~/.codex/config.toml)
  CodexEffort  = "low"
  CursorModel  = ""        # "" = Cursor account default (Auto)
  TimeoutSec   = 420
  SecondPass   = $false
  FinalizeGraceMin = 0     # skip (unless -Force) once the finalize time (11:00 / 18:00) has passed
}
$cfgPath = Join-Path $Root "panel-config.json"
if (Test-Path $cfgPath) {
  $o = Get-Content $cfgPath -Raw | ConvertFrom-Json
  foreach ($p in $o.PSObject.Properties) { $Cfg[$p.Name] = $p.Value }
}
if ($SecondPass) { $Cfg.SecondPass = $true }
function Split-ModelList($raw) {
  $arr = @($raw)
  if ($arr.Count -eq 1 -and [string]$arr[0] -like "*,*") {
    $arr = ([string]$arr[0]).Split(",") | ForEach-Object { $_.Trim() }
  }
  @($arr | ForEach-Object { ([string]$_).ToLower().Trim() } | Where-Object { $_ })
}
if ($Models.Count -eq 0) {
  if ($Cfg.ContainsKey("Models") -and $Cfg.Models) { $Models = Split-ModelList $Cfg.Models }
  else { $Models = @("claude", "cursor") }
} else {
  $Models = Split-ModelList $Models
}
$KnownModels = @("claude", "codex", "cursor")
$bad = @($Models | Where-Object { $KnownModels -notcontains $_ })
if ($bad.Count) { throw "Unknown model(s): $($bad -join ', '). Use claude, cursor, and optionally codex." }
if ($Models.Count -lt 1) { throw "No panel models selected." }

New-Item -ItemType Directory -Force $Work, $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("run-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
function Log([string]$m) { $line = "[" + (Get-Date -Format "HH:mm:ss") + "] " + $m; Write-Host $line; Add-Content -Path $LogFile -Value $line -Encoding UTF8 }
function NowEt { (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz") }
$Utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-Utf8([string]$path, [string]$text) {
  New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
  [System.IO.File]::WriteAllText($path, $text, $Utf8)
}
function To-Json($obj) { $obj | ConvertTo-Json -Depth 12 }

$ModelLabel = @{ claude = "Claude"; codex = "Codex (ChatGPT)"; cursor = "Cursor" }

# ---------------------------------------------------------------- CLI runners (run as parallel jobs)
$Runner = {
  param([string]$model, [string]$runDir, [string]$promptFile, [string]$schemaFile, [hashtable]$cfg)
  $ErrorActionPreference = "Continue"
  $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
  Set-Location $runDir
  $t0 = Get-Date
  $out = ""; $usage = @{}
  try {
    switch ($model) {
      "claude" {
        $a = @("-p", "--output-format", "json", "--no-session-persistence",
               "--disallowedTools", "Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task")
        if ($cfg.ClaudeModel)  { $a += @("--model", $cfg.ClaudeModel) }
        if ($cfg.ClaudeEffort) { $a += @("--effort", $cfg.ClaudeEffort) }
        $raw = (Get-Content -Raw -Encoding UTF8 $promptFile | & claude @a 2>&1 | Out-String)
        $out = $raw
        try {
          $j = $raw.Substring($raw.IndexOf("{")) | ConvertFrom-Json
          if ($j.is_error) { throw ("claude error: " + $j.result) }
          $out = [string]$j.result
          $usage = @{ cost_usd = $j.total_cost_usd; input_tokens = $j.usage.input_tokens; output_tokens = $j.usage.output_tokens; cache_read = $j.usage.cache_read_input_tokens }
        } catch { if ($_.Exception.Message -like "claude error*") { throw } }
      }
      "codex" {
        $last = Join-Path $runDir "codex-last.txt"
        if (Test-Path $last) { Remove-Item $last -Force }
        $a = @("exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", $runDir,
               "--color", "never", "--output-schema", $schemaFile, "-o", $last)
        if ($cfg.CodexEffort) { $a += @("-c", ("model_reasoning_effort=" + $cfg.CodexEffort)) }
        if ($cfg.CodexModel)  { $a += @("-m", $cfg.CodexModel) }
        $a += "-"
        $raw = (Get-Content -Raw -Encoding UTF8 $promptFile | & codex @a 2>&1 | Out-String)
        if ($raw -match "tokens used\s*\r?\n\s*([\d,]+)") { $usage = @{ tokens = [int]($matches[1] -replace ",", "") } }
        if (Test-Path $last) { $out = Get-Content -Raw -Encoding UTF8 $last } else { $out = $raw }
      }
      "cursor" {
        Copy-Item $promptFile (Join-Path $runDir "panel-prompt.md") -Force
        $a = @("-p", "--mode", "ask", "--trust", "--output-format", "json", "--workspace", $runDir)
        if ($cfg.CursorModel) { $a += @("--model", $cfg.CursorModel) }
        # Prompt kept free of double quotes (PowerShell 5.1 native-arg quoting); the real prompt is in the file.
        $a += "Read the file panel-prompt.md in this workspace and follow its instructions exactly. Reply with only the JSON object it asks for, no prose."
        $raw = (& cursor-agent @a 2>&1 | Out-String)
        $out = $raw
        try {
          $j = $raw.Substring($raw.IndexOf("{")) | ConvertFrom-Json
          if ($j.result) { $out = [string]$j.result }
          if ($j.is_error) { throw ("cursor error: " + $j.result) }
          if ($j.usage) { $usage = $j.usage }
        } catch { if ($_.Exception.Message -like "cursor error*") { throw } }
        if ($raw -match "Authentication required") { throw "cursor-agent is not logged in (run: cursor-agent login)" }
      }
    }
    @{ ok = $true; out = $out; usage = $usage; secs = ((Get-Date) - $t0).TotalSeconds; err = $null }
  } catch {
    @{ ok = $false; out = $out; usage = $usage; secs = ((Get-Date) - $t0).TotalSeconds; err = $_.Exception.Message }
  }
}

function Invoke-Panel([string[]]$models, [string]$runDir, [string]$promptFile) {
  $jobs = @{}
  foreach ($m in $models) {
    $d = Join-Path $runDir $m; New-Item -ItemType Directory -Force $d | Out-Null
    $jobs[$m] = Start-Job -ScriptBlock $Runner -ArgumentList $m, $d, $promptFile, $SchemaFile, $Cfg
  }
  $deadline = (Get-Date).AddSeconds([int]$Cfg.TimeoutSec)
  $res = @{}
  foreach ($m in $models) {
    $left = [Math]::Max(1, [int]($deadline - (Get-Date)).TotalSeconds)
    $j = $jobs[$m]
    if (Wait-Job $j -Timeout $left) {
      $r = Receive-Job $j | Select-Object -Last 1
      if (-not $r) { $r = @{ ok = $false; out = ""; usage = @{}; secs = 0; err = "no output from job" } }
      $res[$m] = $r
    } else {
      Stop-Job $j
      $res[$m] = @{ ok = $false; out = ""; usage = @{}; secs = [int]$Cfg.TimeoutSec; err = "timed out after $($Cfg.TimeoutSec)s" }
    }
    Remove-Job $j -Force
  }
  return $res
}

function Extract-Json([string]$text) {
  if (-not $text) { return $null }
  $t = $text -replace '```json', '' -replace '```', ''
  $s = $t.IndexOf("{"); $e = $t.LastIndexOf("}")
  if ($s -lt 0 -or $e -le $s) { return $null }
  try { return ($t.Substring($s, $e - $s + 1) | ConvertFrom-Json) } catch { return $null }
}

function Normalize-Votes($parsed, $legs) {
  $byId = @{}
  if ($parsed -and $parsed.votes) { foreach ($v in $parsed.votes) { if ($v.leg_id) { $byId[[string]$v.leg_id] = $v } } }
  $outVotes = @(); $missing = 0
  foreach ($leg in $legs) {
    $v = $byId[[string]$leg.id]
    if ($v) {
      $d = ([string]$v.decision).ToLower().Trim()
      if ($d -ne "keep" -and $d -ne "cut") { $d = "abstain" }
      $c = 0; try { $c = [int][Math]::Round([double]$v.confidence) } catch { $c = 0 }
      if ($c -lt 0) { $c = 0 }; if ($c -gt 100) { $c = 100 }
      $r = [string]$v.reasoning; if ($r.Length -gt 2000) { $r = $r.Substring(0, 2000) + "..." }
      $flags = @(); if ($v.risk_flags) { $flags = @($v.risk_flags | ForEach-Object { [string]$_ }) }
      $outVotes += [ordered]@{ leg_id = [string]$leg.id; decision = $d; confidence = $c; reasoning = $r; risk_flags = $flags }
    } else {
      $missing++
      $outVotes += [ordered]@{ leg_id = [string]$leg.id; decision = "abstain"; confidence = 0; reasoning = "No vote returned for this leg."; risk_flags = @() }
    }
  }
  return @{ votes = $outVotes; missing = $missing }
}

function Build-Prompt($cand, [string]$extra) {
  $legs = foreach ($l in $cand.legs) {
    [ordered]@{ id = $l.id; sport = $l.sport; event = $l.event; start_time = $l.start_time; player = $l.player; team = $l.team
      market = $l.market; line = $l.line; pick = $l.pick; platforms = $l.platforms; drafter_est_prob = $l.est_prob
      drafter_reasoning = $l.reasoning; key_stats = $l.key_stats; injury_notes = $l.injury_notes
      price_ts = $l.price_ts; n_books = $l.n_books; price_books = $l.price_books; price_method = $l.price_method; recent_miss = $l.recent_miss }
  }
  $legJson = ConvertTo-Json -InputObject @($legs) -Depth 6 -Compress
  $labels = @($Models | ForEach-Object { if ($ModelLabel.ContainsKey($_)) { $ModelLabel[$_] } else { $_ } })
  if ($labels.Count -le 1) { $panelNames = [string]$labels[0] }
  elseif ($labels.Count -eq 2) { $panelNames = "$($labels[0]) and $($labels[1])" }
  else { $panelNames = (($labels[0..($labels.Count - 2)] -join ", ") + ", and " + $labels[-1]) }
@"
You are one member of a review panel ($panelNames) for a PAPER-ONLY player-prop dry run (PrizePicks/Underdog pick'em, no real money).
The drafter (Parlay Locks) researched today's slate ($($cand.date), $($cand.snapshot) snapshot) and proposes the candidate legs below.
Your job: act as a VETO, not a ranking. Keep every leg by default; vote cut ONLY when you can name a concrete, specific error from the list below (or an equally concrete factual problem). Pick'em break-even for context: ~53.5% for Underdog 2/3-leg Standard, ~54.3% for PrizePicks 5-leg Flex, ~54.9% for 5-leg Power. Your confidence is NOT used to rank legs, so do not cut a leg just because it looks weaker than the others. Cut a leg if:
1. LINE-MISMATCH: the cited sportsbook/prediction probability is NOT for the exact pick'em line and settlement rule (e.g., citing a 60+ yard price for O66.5, or a pushable book Under 9 for a pick'em Under 8.5), or relies on an un-devigged one-sided price.
2. SINGLE-BOOK / CROSS-BOOK-PAIRED: the probability comes from only one book (n_books < 2), or from pairing the best over at one book with the best under at another instead of devigging within each book and taking the median across books.
3. STALE-PRICE: the price timestamp (price_ts) is several hours older than the draft and injury, lineup or role news has come out since (e.g. 9:00 AM prices used for a 5 PM pregame draft after a teammate was ruled out).
4. 3PM-U1.5-ROLE: a 3-pointers-made UNDER 1.5 where the player does not average <=3.0 3PA over his last 10, or a rotation teammate was ruled out in the last 3 games. Judge by attempts and role, not past makes.
5. UNDER-ROLE-EXPANSION: any under where a teammate injury or absence in the last ~3 games is expanding the player's minutes, usage, targets or ice time; also any under priced beyond -180 whose reasoning lacks a volume check (attempts, targets or TOI).
6. RECENT-MISS: the same player + same prop type missed within the last 3 days (see recent_miss or the reasoning) and the line has not moved.
7. BLOWOUT-UNDER: an under relying primarily on a spread-driven blowout benching rather than baseline volume (competitive games or OT blow these up).
8. THIN/RISK: reasoning is thin, ignores recent workload/snap shifts, or carries unresolved injury/minutes/weather risk; name the specific gap.
Every cut MUST name its error: start the reasoning with the tag in capitals (e.g. "STALE-PRICE: ...", "3PM-U1.5-ROLE: ...") and put the same lowercase tag in risk_flags. A cut that names no concrete error will be ignored.
IMPORTANT: You have no web access and today is $($cand.date). Trust the drafter's 2026 player-team rosters and current injuries as fact unless internally contradictory; do NOT vote cut just because your offline memory has a player on an older team.
Do not use tools or browse; rely on the data given plus your own analysis.

Slate notes from drafter: $($cand.slate_notes)
$extra
Candidate legs (JSON):
$legJson

Return ONLY a JSON object, no markdown, exactly this shape:
{"overall_notes":"2-3 sentences on the slate and your overall stance","votes":[{"leg_id":"L1","decision":"keep|cut","confidence":0-100,"reasoning":"one paragraph (2-4 sentences): why, key risk, what would change your mind","risk_flags":["short tags like injury, minutes, blowout, line-moved, small-sample"]}]}
Rules: one vote per leg_id, decision is keep or cut, confidence is an integer 0-100 meaning how sure you are that your decision is right (keep = the leg hits, cut = it misses). Every cut must begin its reasoning with the error tag; per-model keep/cut accuracy is logged and graded after each slate.
"@
}

function Add-Feed([string]$agent, [string]$action, [string]$text, [string]$to = "", [string]$runId = "", [string]$ref = "", $meta = $null) {
  $o = [ordered]@{ ts = (NowEt); agent = $agent; action = $action }
  if ($to) { $o.to = $to }
  if ($runId) { $o.run = $runId }
  $o.text = $text
  if ($ref) { $o.ref = $ref }
  if ($meta) { $o.meta = $meta }
  $line = ($o | ConvertTo-Json -Depth 8 -Compress)
  $f = Join-Path $Repo "data\feed.jsonl"
  [System.IO.File]::AppendAllText($f, $line + "`n", $Utf8)
}

function G {
  # git writes progress/warnings to stderr; never let that trip ErrorActionPreference=Stop
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { $o = & git @args 2>&1 | ForEach-Object { "$_" }; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
  foreach ($l in $o) { if ($l -and $l -notmatch "LF will be replaced|CRLF will be replaced") { Log ("git: " + $l) } }
  $global:LASTEXITCODE = $code
}

function Git-Sync([string]$msg) {
  Push-Location $Repo
  try {
    G add -A data
    G -c user.name="Parlay Panel (Sneed_PC)" -c user.email="57764294+bryce-sneed@users.noreply.github.com" commit -q -m $msg
    if ($NoPush) { Log "NoPush set; committed locally only"; return $true }
    for ($i = 1; $i -le 4; $i++) {
      G pull -q --rebase --autostash origin main
      G push -q origin HEAD:main
      if ($LASTEXITCODE -eq 0) { Log "pushed"; return $true }
      Start-Sleep -Seconds (5 * $i)
    }
    Log "PUSH FAILED after retries"; return $false
  } finally { Pop-Location }
}

# ---------------------------------------------------------------- test mode
if ($Test) {
  $tdir = Join-Path $Work "selftest"; New-Item -ItemType Directory -Force $tdir | Out-Null
  $tp = Join-Path $tdir "prompt.txt"
  Write-Utf8 $tp 'Return ONLY this JSON object: {"overall_notes":"selftest ok","votes":[{"leg_id":"L1","decision":"keep","confidence":50,"reasoning":"selftest","risk_flags":[]}]}'
  $res = Invoke-Panel $Models $tdir $tp
  foreach ($m in $Models) {
    $r = $res[$m]; $p = Extract-Json ([string]$r.out)
    $status = "FAIL"; if ($r.ok -and $p -and $p.votes) { $status = "OK" }
    Log ("{0,-7} {1}  {2:N1}s  {3}" -f $m, $status, $r.secs, $(if ($r.err) { $r.err } else { ([string]$r.out).Trim() -replace "\s+", " " | ForEach-Object { if ($_.Length -gt 160) { $_.Substring(0, 160) } else { $_ } } }))
  }
  exit 0
}

# ---------------------------------------------------------------- normal run
$lock = Join-Path $Root "panel.lock"
if ((Test-Path $lock) -and ((Get-Item $lock).LastWriteTime -gt (Get-Date).AddMinutes(-30))) { Log "another run is in progress (lock); exiting"; exit 0 }
Set-Content $lock (Get-Date) -Encoding ASCII
try {
  Push-Location $Repo
  G config core.autocrlf false; G config core.filemode false
  G pull -q --rebase --autostash origin main
  Pop-Location
  # self-update: the repo copy (pc/) is the source of truth; takes effect next run
  foreach ($pair in @(@("pc\run-panel.ps1", "run-panel.ps1"), @("pc\vote-output.schema.json", "vote-output.schema.json"), @("pc\register-tasks.ps1", "register-tasks.ps1"))) {
    $src = Join-Path $Repo $pair[0]; $dst = Join-Path $Root $pair[1]
    if ((Test-Path $src) -and (-not $RepoPath)) {
      if (-not (Test-Path $dst) -or ((Get-FileHash $src).Hash -ne (Get-FileHash $dst).Hash)) { Copy-Item $src $dst -Force; Log "updated $($pair[1]) from repo" }
    }
  }

  $candDir = Join-Path $Repo "data\candidates"
  $files = Get-ChildItem $candDir -Filter "*.json" | Where-Object { $_.BaseName -match '^\d{4}-\d{2}-\d{2}-(morning|pregame)$' } | Sort-Object BaseName -Descending
  $target = $null
  foreach ($f in $files) {
    if ($Run -and $f.BaseName -ne $Run) { continue }
    $done = Test-Path (Join-Path $Repo ("data\votes\" + $f.BaseName + "\_panel.json"))
    if ($done -and -not $Force) { if ($Run) { Log "$Run already voted (use -Force)"; exit 0 }; continue }
    $target = $f; break
  }
  if (-not $target) { Log "no unvoted candidates file found; nothing to do"; exit 0 }
  $runId = $target.BaseName
  $runDate = [datetime]::ParseExact($runId.Substring(0, 10), "yyyy-MM-dd", $null)
  if ($runDate -lt (Get-Date).Date.AddDays(-1) -and -not $Force) { Log "newest unvoted run $runId is stale; skipping"; exit 0 }
  $finalizeHour = 11; if ($runId -like "*pregame") { $finalizeHour = 18 }
  if ($runDate -eq (Get-Date).Date -and (Get-Date) -gt $runDate.AddHours($finalizeHour).AddMinutes([int]$Cfg.FinalizeGraceMin) -and -not $Force) {
    Log "past finalize time for $runId; skipping (use -Force)"; exit 0
  }

  $cand = Get-Content $target.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
  $legs = @($cand.legs)
  Log "run $runId : $($legs.Count) legs; models: $($Models -join ', ')"
  $started = NowEt
  $runDir = Join-Path $Work $runId; New-Item -ItemType Directory -Force $runDir | Out-Null
  $promptFile = Join-Path $runDir "prompt-r1.md"
  Write-Utf8 $promptFile (Build-Prompt $cand "")
  $relCand = "data/candidates/$runId.json"

  $names = ($Models | ForEach-Object { $ModelLabel[$_] }) -join ", "
  Add-Feed "panel" "pickup" "Picked up $($legs.Count) candidate legs from Parlay Locks for the $($cand.snapshot) slate." "parlay-locks" $runId $relCand
  foreach ($m in $Models) {
    $mn = switch ($m) { "claude" { $Cfg.ClaudeModel } "codex" { if ($Cfg.CodexModel) { $Cfg.CodexModel } else { "default" } } "cursor" { if ($Cfg.CursorModel) { $Cfg.CursorModel } else { "auto" } } }
    Add-Feed "panel" "handoff" "Sent $($legs.Count) legs to $($ModelLabel[$m]) ($mn) for an independent keep/cut vote." $m $runId
  }

  function Save-Round([int]$round, $res, $legsArr) {
    $summary = @{}
    foreach ($m in $res.Keys) {
      $r = $res[$m]; $parsed = Extract-Json ([string]$r.out)
      $norm = Normalize-Votes $parsed $legsArr
      $status = "ok"; $err = $null
      if (-not $r.ok) { $status = "error"; $err = $r.err }
      elseif (-not $parsed) { $status = "error"; $err = "could not parse JSON from model output" }
      elseif ($norm.missing -gt 0) { $status = "partial"; $err = "$($norm.missing) leg(s) missing" }
      $mn = switch ($m) { "claude" { $Cfg.ClaudeModel } "codex" { if ($Cfg.CodexModel) { $Cfg.CodexModel } else { "default" } } "cursor" { if ($Cfg.CursorModel) { $Cfg.CursorModel } else { "auto" } } }
      $doc = [ordered]@{ schema = "votes/v1"; run = $runId; model = $m; model_name = $mn; round = $round; status = $status; error = $err
        started_at = $started; finished_at = (NowEt); duration_s = [Math]::Round($r.secs, 1); usage = $r.usage
        overall_notes = $(if ($parsed) { [string]$parsed.overall_notes } else { "" }); votes = $norm.votes }
      if ($status -eq "error") { $raw = [string]$r.out; if ($raw.Length -gt 1500) { $raw = $raw.Substring(0, 1500) }; $doc.raw_output = $raw }
      $suffix = ""; if ($round -eq 2) { $suffix = "-r2" }
      $rel = "data/votes/$runId/$m$suffix.json"
      Write-Utf8 (Join-Path $Repo ($rel -replace "/", "\")) (To-Json $doc)
      $k = @($norm.votes | Where-Object { $_.decision -eq "keep" }).Count
      $c = @($norm.votes | Where-Object { $_.decision -eq "cut" }).Count
      $ab = @($norm.votes | Where-Object { $_.decision -eq "abstain" }).Count
      $summary[$m] = [ordered]@{ status = $status; keep = $k; cut = $c; abstain = $ab; duration_s = [Math]::Round($r.secs, 1); votes = $norm.votes }
      if ($status -eq "error") {
        Add-Feed $m "error" "$($ModelLabel[$m]) failed: $err" "panel" $runId $rel
        Log "$m ERROR: $err"
      } else {
        $act = "vote"; if ($round -eq 2) { $act = "revise" }
        $notes = [string]$doc.overall_notes; if ($notes.Length -gt 400) { $notes = $notes.Substring(0, 400) + "..." }
        $verb = "Voted"; if ($round -eq 2) { $verb = "After reading the other panelists, revised to" }
        Add-Feed $m $act ("$verb keep $k / cut $c" + $(if ($ab) { " / no vote $ab" } else { "" }) + ". " + $notes) "panel" $runId $rel @{ keep = $k; cut = $c; abstain = $ab; secs = [Math]::Round($r.secs, 1) }
        Log "$m $status keep=$k cut=$c abstain=$ab ($([Math]::Round($r.secs,1))s)"
      }
    }
    return $summary
  }

  $res1 = Invoke-Panel $Models $runDir $promptFile
  $sum = Save-Round 1 $res1 $legs

  if ($Cfg.SecondPass) {
    $okModels = @($Models | Where-Object { $sum[$_].status -ne "error" })
    if ($okModels.Count -ge 2) {
      $table = foreach ($m in $okModels) { foreach ($v in $sum[$m].votes) { [ordered]@{ model = $m; leg_id = $v.leg_id; decision = $v.decision; confidence = $v.confidence; reasoning = $v.reasoning } } }
      $extra = "ROUND 2: here are all panelists' round-1 votes. Reconsider each leg in light of the others' arguments; change your vote only if persuaded, and mention who persuaded you.`nRound-1 votes (JSON): " + (ConvertTo-Json -InputObject @($table) -Depth 5 -Compress) + "`n"
      $p2 = Join-Path $runDir "prompt-r2.md"; Write-Utf8 $p2 (Build-Prompt $cand $extra)
      Add-Feed "panel" "handoff" "Round 2: shared every panelist's round-1 votes with $((($okModels | ForEach-Object { $ModelLabel[$_] }) -join ', ')) for rebuttal/revision." "all" $runId
      $res2 = Invoke-Panel $okModels $runDir $p2
      $sum2 = Save-Round 2 $res2 $legs
      foreach ($m in $sum2.Keys) { if ($sum2[$m].status -ne "error") { $sum[$m] = $sum2[$m] } }
    }
  }

  # consensus
  $cons = foreach ($leg in $legs) {
    $k = 0; $c = 0; $ab = 0; $sc = 0.0; $n = 0; $confs = @()
    foreach ($m in $sum.Keys) {
      $v = $sum[$m].votes | Where-Object { $_.leg_id -eq [string]$leg.id } | Select-Object -First 1
      if (-not $v -or $sum[$m].status -eq "error" -or $v.decision -eq "abstain") { $ab++; continue }
      $n++; $confs += $v.confidence
      if ($v.decision -eq "keep") { $k++; $sc += $v.confidence } else { $c++; $sc -= $v.confidence }
    }
    $avg = 0; if ($confs.Count) { $avg = [Math]::Round(($confs | Measure-Object -Average).Average, 1) }
    $score = 0; if ($n) { $score = [Math]::Round($sc / $n, 1) }
    # Veto: a cut from any voting model drops the leg (2-model default: one cut is enough).
    # Score is still recorded but is not used to rank or override a veto.
    $veto = $c -ge 1
    [ordered]@{ leg_id = [string]$leg.id; keep = $k; cut = $c; abstain = $ab; avg_conf = $avg; score = $score; veto = [bool]$veto }
  }
  $cons = @($cons | Sort-Object { -$_.score })
  $modelsOut = [ordered]@{}
  foreach ($m in $sum.Keys) { $modelsOut[$m] = [ordered]@{ status = $sum[$m].status; keep = $sum[$m].keep; cut = $sum[$m].cut; abstain = $sum[$m].abstain; duration_s = $sum[$m].duration_s } }
  $panel = [ordered]@{ schema = "panel/v1"; run = $runId; host = $env:COMPUTERNAME; started_at = $started; finished_at = (NowEt); second_pass = [bool]$Cfg.SecondPass; models = $modelsOut; consensus = $cons }
  $relPanel = "data/votes/$runId/_panel.json"
  Write-Utf8 (Join-Path $Repo ($relPanel -replace "/", "\")) (To-Json $panel)

  $kept = @($cons | Where-Object { -not $_.veto } | Select-Object -First 5 | ForEach-Object { "$($_.leg_id) ($($_.keep)-$($_.cut), score $($_.score))" }) -join ", "
  $cuts = @($cons | Where-Object { $_.veto } | ForEach-Object { "$($_.leg_id) ($($_.keep)-$($_.cut))" }) -join ", "
  if (-not $kept) { $kept = "none" }; if (-not $cuts) { $cuts = "none" }
  Add-Feed "panel" "consensus" "Panel consensus ready for Parlay Locks (veto from any voting model cuts the leg). Eligible: $kept. Vetoed: $cuts." "parlay-locks" $runId $relPanel
  $ok = Git-Sync "panel: votes for $runId"
  if (-not $ok) { exit 2 }
  Log "done"
} catch {
  Log ("FATAL: " + $_.Exception.Message)
  try { Add-Feed "panel" "error" ("Panel run crashed on " + $env:COMPUTERNAME + ": " + $_.Exception.Message) "parlay-locks" $Run; Git-Sync "panel: error" | Out-Null } catch {}
  exit 1
} finally {
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
