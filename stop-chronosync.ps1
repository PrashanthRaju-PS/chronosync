$runDir = Join-Path $PSScriptRoot '.run'
if (-not (Test-Path $runDir)) {
    Write-Output "no .run directory - nothing to stop"
    exit 0
}

foreach ($name in 'daemon','api') {
    $pidFile = Join-Path $runDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Output ("{0} : no pid file" -f $name)
        continue
    }
    $procId = (Get-Content $pidFile -Raw).Trim()
    if (-not $procId) {
        Write-Output ("{0} : empty pid file" -f $name)
        Remove-Item $pidFile -ErrorAction SilentlyContinue
        continue
    }
    # taskkill /T kills the whole process tree (uvicorn -> worker procs, etc.).
    & taskkill /T /F /PID $procId 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Output ("{0} : stopped (PID tree from {1})" -f $name, $procId)
    } else {
        Write-Output ("{0} : PID {1} already gone" -f $name, $procId)
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

# The recorded PID is the venv python.exe *shim*, which frequently exits right
# after spawning the real interpreter — leaving that child (still bound to 8088)
# re-parented and unreachable via the pidfile PID above. Sweep it up by matching
# the actual chronosync/uvicorn command lines so no orphan survives to block the
# next start. Scoped to our own processes only.
$orphans = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'chronosync|uvicorn' }
foreach ($o in $orphans) {
    & taskkill /T /F /PID $o.ProcessId 2>$null | Out-Null
    Write-Output ("reaped orphan PID {0}" -f $o.ProcessId)
}
if (-not $orphans) {
    Write-Output "no orphaned chronosync processes"
}
