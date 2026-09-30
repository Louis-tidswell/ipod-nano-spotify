$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $env:USERPROFILE '.ssh/id_ed25519_ltpi'
if (-not (Test-Path -LiteralPath "$keyPath.pub")) {
    throw "Missing public key: $keyPath.pub"
}

# Run in an interactive PowerShell terminal; SSH prompts for the Pi password.
$publicKey = (Get-Content -Raw -LiteralPath "$keyPath.pub").Trim()
if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+ [A-Za-z0-9@ ._-]+$') {
    throw 'Unexpected public key format.'
}
$remoteCommand = "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && (grep -qxF '$publicKey' ~/.ssh/authorized_keys || printf '%s\n' '$publicKey' >> ~/.ssh/authorized_keys)"
& ssh -o StrictHostKeyChecking=yes -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive ltidswell@ltpi $remoteCommand
if ($LASTEXITCODE -ne 0) { throw 'Public key installation failed.' }
& ssh -i $keyPath -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes ltidswell@ltpi 'hostname; uname -m; cat /etc/os-release'
if ($LASTEXITCODE -ne 0) { throw 'Key authentication verification failed.' }
