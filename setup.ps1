# SPDX-License-Identifier: GPL-3.0-only
param([switch]$EnableExtraKeys)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3.13 -m venv .venv }
    else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Create the environment with 64-bit Python 3.13 first.' }
}
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$configureArgs=@('configure.py')
if ($EnableExtraKeys) { $configureArgs+='--enable-extra-keys' }
& '.venv/Scripts/python.exe' @configureArgs
if ($LASTEXITCODE -ne 0) { throw 'Pair/select the remote, then run configure.py again.' }
Write-Host 'Configured. Install/configure VB-CABLE and Doubao as described in README.md.'
