$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $env:USERPROFILE '.ssh/id_ed25519_ltpi'
if (-not (Test-Path -LiteralPath $keyPath)) {
    throw "Missing SSH key: $keyPath"
}

& ssh -t -i $keyPath -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes `
    ltidswell@ltpi 'cd ~/ipod-nano-spotify && chmod +x pi/setup_spotify.sh && ./pi/setup_spotify.sh'
if ($LASTEXITCODE -ne 0) { throw 'Spotify player setup failed.' }
