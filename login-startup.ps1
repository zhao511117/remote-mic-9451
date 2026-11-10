# SPDX-License-Identifier: GPL-3.0-only
param([switch]$Remove)
$ErrorActionPreference='Stop'
$startup=[Environment]::GetFolderPath('Startup')
$link=Join-Path $startup 'RemoteMic9451.lnk'
if ($Remove) {
    if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link }
    Write-Host 'Login startup removed. Use stop.cmd to stop currently running processes.'
    exit
}
$python=Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 first.' }
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut($link)
$shortcut.TargetPath=$python
$shortcut.Arguments='-B "'+(Join-Path $PSScriptRoot 'login_start.pyw')+'"'
$shortcut.WorkingDirectory=$PSScriptRoot
$shortcut.Description='RemoteMic9451 voice receiver and bounded recovery supervisor'
$shortcut.Save()
Write-Host 'Login startup enabled. Keep this source folder and its .venv in place.'
