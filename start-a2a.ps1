$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path (Join-Path (Join-Path $Root ".venv") "Scripts") "python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$BindHost = if ($env:A2A_HOST) { $env:A2A_HOST } else { "127.0.0.1" }
$Port = if ($env:A2A_PORT) { $env:A2A_PORT } else { "8766" }
$ContextId = if ($env:A2A_CONTEXT_ID) { $env:A2A_CONTEXT_ID } else { "claude-worker" }
$BaseUrl = if ($env:A2A_BASE_URL) { $env:A2A_BASE_URL } else { ("http://{0}:{1}" -f $BindHost, $Port) }
$StateDir = if ($env:A2A_STATE_DIR) { $env:A2A_STATE_DIR } else { Join-Path $Root "state" }
$RepositoryUrl = if ($env:A2A_REPOSITORY_URL) { $env:A2A_REPOSITORY_URL } else { "" }

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$LogFile = Join-Path $StateDir "claude-worker.log"
$ErrorLogFile = Join-Path $StateDir "claude-worker.err.log"
$PidFile = Join-Path $StateDir "claude-worker.pid"

$env:A2A_HOST = $BindHost
$env:A2A_PORT = $Port
$env:A2A_BASE_URL = $BaseUrl
$env:A2A_CONTEXT_ID = $ContextId
$env:A2A_STATE_DIR = $StateDir
$env:A2A_REPOSITORY_URL = $RepositoryUrl
$env:A2A_CLAUDE_CWD = $Root
$env:A2A_WORKER_MODE = "background"

$Arguments = @(
    "-u", (Join-Path $Root "a2a_bridge.py"),
    "--host", $BindHost,
    "--port", $Port,
    "--base-url", $BaseUrl,
    "--context-id", $ContextId,
    "--cwd", $Root,
    "--session-file", (Join-Path $StateDir "claude-session.json"),
    "--task-db", (Join-Path $StateDir "tasks.sqlite3")
)

$Process = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $Root -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLogFile -WindowStyle Hidden -PassThru
$Process.Id | Set-Content -Path $PidFile -NoNewline

Write-Output "Claude worker started in background (PID $($Process.Id))."
& $Python (Join-Path $Root "connection_pack.py")
