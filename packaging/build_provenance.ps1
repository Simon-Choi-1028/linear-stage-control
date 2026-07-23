function Get-ProjectVersion {
  param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot
  )

  $versionLine = Select-String -Path (Join-Path $ProjectRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1
  if ($versionLine -and $versionLine.Matches.Count) {
    return $versionLine.Matches[0].Groups[1].Value
  }
  throw "Could not read project version from pyproject.toml"
}

function Get-SourceFingerprint {
  param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot
  )

  $relativePaths = @(& git -C $ProjectRoot ls-files --cached --others --exclude-standard)
  if ($LASTEXITCODE -ne 0) {
    throw "Could not enumerate source files with git."
  }
  if ($relativePaths.Count -eq 0) {
    throw "No source files were found for build provenance."
  }

  $fingerprintLines = foreach ($relativePath in ($relativePaths | Sort-Object -Unique)) {
    $fullPath = Join-Path $ProjectRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
      continue
    }
    $normalisedPath = $relativePath.Replace("\", "/")
    $fileHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$fileHash`t$normalisedPath"
  }

  $payload = [System.Text.Encoding]::UTF8.GetBytes(($fingerprintLines -join "`n"))
  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([System.BitConverter]::ToString($sha256.ComputeHash($payload))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha256.Dispose()
  }
}
