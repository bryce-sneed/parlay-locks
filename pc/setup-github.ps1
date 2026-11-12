<#
  ONE-TIME setup for Bryce (run in PowerShell on Sneed_PC):
    powershell -ExecutionPolicy Bypass -File C:\parlay-locks\setup-github.ps1
  It will: sign you into GitHub (browser code), create github.com/<you>/parlay-locks (public, so free GitHub Pages works),
  push the panel + dashboard, turn on GitHub Pages, sign in cursor-agent if needed, and run a self-test of all 3 CLIs.
  Safe to re-run.
#>
$ErrorActionPreference = "Continue"
$Root = "C:\parlay-locks"; $Repo = Join-Path $Root "repo"; $Bin = Join-Path $Root "bin"
$gh = Join-Path $Bin "gh.exe"
if (-not (Test-Path $gh)) { $c = Get-Command gh -ErrorAction SilentlyContinue; if ($c) { $gh = $c.Source } else { throw "gh.exe not found in $Bin" } }
$RepoName = "parlay-locks"

Write-Host "`n== 1/6 GitHub sign-in" -ForegroundColor Cyan
& $gh auth status -h github.com 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "A one-time code will appear below. Press Enter to open the browser, paste the code, and approve." -ForegroundColor Yellow
  & $gh auth login -h github.com -p https -w -s repo
  if ($LASTEXITCODE -ne 0) { throw "GitHub sign-in did not complete" }
}
$Owner = (& $gh api user -q .login).Trim()
$Full = "$Owner/$RepoName"
Write-Host "Signed in as $Owner"

Write-Host "`n== 2/6 Repository $Full" -ForegroundColor Cyan
& $gh repo view $Full --json name 2>$null | Out-Null
$exists = ($LASTEXITCODE -eq 0)
if (-not $exists) {
  & $gh repo create $Full --public --description "Parlay Locks paper dry run: multi-model (Grok Bot + Claude + Codex + Cursor) prop research panel and dashboard"
  if ($LASTEXITCODE -ne 0) { throw "repo create failed" }
}
Push-Location $Repo
git config credential.https://github.com.helper ""
git config --add credential.https://github.com.helper ("!'" + ($gh -replace '\\', '/') + "' auth git-credential")
git config user.name "Parlay Panel (Sneed_PC)"
git config user.email "57764294+bryce-sneed@users.noreply.github.com"
$remote = (git remote 2>$null) -contains "origin"
if (-not $remote) { git remote add origin "https://github.com/$Full.git" } else { git remote set-url origin "https://github.com/$Full.git" }
git fetch -q origin 2>$null
$remoteHasMain = (git ls-remote --heads origin main) -ne $null
if ($remoteHasMain) {
  git pull -q --rebase --allow-unrelated-histories origin main -X theirs 2>$null
}
git push -u origin HEAD:main
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "git push failed" }
Pop-Location

Write-Host "`n== 3/6 GitHub Pages" -ForegroundColor Cyan
& $gh api "repos/$Full/pages" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  & $gh api -X POST "repos/$Full/pages" -f "source[branch]=main" -f "source[path]=/" | Out-Null
}
$pagesUrl = "https://$($Owner.ToLower()).github.io/$RepoName/"
Write-Host "Dashboard: $pagesUrl (first deploy takes ~1-2 min)"

Write-Host "`n== 4/6 Cursor Agent sign-in" -ForegroundColor Cyan
$st = (cursor-agent --list-models 2>&1 | Out-String)
if ($st -match "Authentication required") {
  Write-Host "Opening Cursor sign-in in your browser..." -ForegroundColor Yellow
  cursor-agent login
} else { Write-Host "cursor-agent already signed in" }

Write-Host "`n== 5/6 Scheduled task (10:30 + 17:30 daily)" -ForegroundColor Cyan
& (Join-Path $Root "register-tasks.ps1")

Write-Host "`n== 6/6 Self-test of all three CLIs" -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "run-panel.ps1") -Test

Write-Host "`nAll set. Repo: https://github.com/$Full   Dashboard: $pagesUrl" -ForegroundColor Green
Write-Host "Bookmark the dashboard on your iPad (Safari > Share > Add to Home Screen)."
