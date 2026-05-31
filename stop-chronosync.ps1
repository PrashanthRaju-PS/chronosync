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
