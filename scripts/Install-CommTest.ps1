$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $env:USERPROFILE '.ssh/id_ed25519_ltpi'
if (-not (Test-Path -LiteralPath $keyPath)) {
    throw "Missing SSH key: $keyPath"
}

Write-Host 'The Pi will ask for the ltidswell sudo password once.'
Write-Host 'Password characters will not appear while you type.'
& ssh -t -i $keyPath -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes `
    ltidswell@ltpi 'cd ~/ipod-nano-spotify && chmod +x pi/setup_comm_test.sh pi/comm_test_bridge.py && ./pi/setup_comm_test.sh'
if ($LASTEXITCODE -ne 0) { throw 'Pi Link Test setup failed.' }
