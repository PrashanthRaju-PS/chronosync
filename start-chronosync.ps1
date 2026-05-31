$ErrorActionPreference = 'Stop'
$root   = $PSScriptRoot
$runDir = Join-Path $root '.run'
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python  -- run: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e .[dev]"
}

function Start-Hidden {
    param([string]$Name, [string]$Exe, [string[]]$ArgList, [string]$Cwd)
    $procArgs = @{
        FilePath               = $Exe
        ArgumentList           = $ArgList
        WorkingDirectory       = $Cwd
        WindowStyle            = 'Hidden'
        RedirectStandardOutput = Join-Path $runDir "$Name.log"
        RedirectStandardError  = Join-Path $runDir "$Name.err.log"
        PassThru               = $true
    }
    $p = Start-Process @procArgs
    $p.Id | Out-File (Join-Path $runDir "$Name.pid") -Encoding ascii
    return $p
}

$daemon = Start-Hidden 'daemon' $python @('-m','chronosync.cli','run')                                                  $root
$api    = Start-Hidden 'api'    $python @('-m','uvicorn','chronosync.api.app:app','--host','127.0.0.1','--port','8088') $root

Write-Output "daemon PID $($daemon.Id)   (cron-driven sync; logs: $runDir\daemon.log)"
Write-Output "api    PID $($api.Id)      -> http://127.0.0.1:8088   (logs: $runDir\api.log)"
Write-Output ""
Write-Output "stop : run stop-chronosync.bat (kills the process trees from the .pid files)"
