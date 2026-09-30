$keyPath = Join-Path $env:USERPROFILE '.ssh/id_ed25519_ltpi'
& ssh -i $keyPath -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes ltidswell@ltpi @args
exit $LASTEXITCODE
