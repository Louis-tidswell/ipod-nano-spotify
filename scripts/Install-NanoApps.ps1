$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $env:USERPROFILE '.ssh/id_ed25519_ltpi'
if (-not (Test-Path -LiteralPath $keyPath)) {
    throw "Missing SSH key: $keyPath"
}

& ssh -t -i $keyPath -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes `
    ltidswell@ltpi 'cd ~/ipod-nano-spotify && git submodule update --init --recursive && python3 pi/install_nanoapps.py'
if ($LASTEXITCODE -ne 0) { throw 'NanoApps installation failed.' }
