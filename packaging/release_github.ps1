param(
  [string]$Tag = "",
  [string]$Repo = "Simon-Choi-1028/linear-stage-control",
  [switch]$PortableOnly,
  [switch]$IncludePortable,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$ManifestPath = Join-Path $Root "dist\update_manifest.json"
$OnlineSetup = Join-Path $Root "dist\LinearStageControlSetup.exe"
$OfflineSetup = Join-Path $Root "dist\LinearStageControlSetup-Offline.exe"
$PortableZip = Join-Path $Root "dist\LinearStageControl-Portable.zip"
$PortableManifest = Join-Path $Root "dist\portable_manifest.json"
$ChangeLog = Join-Path $Root "CHANGELOG.md"

function Get-ProjectVersion {
  $VersionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($VersionLine -and $VersionLine.Matches.Count) {
    return $VersionLine.Matches[0].Groups[1].Value
  }
  throw "Could not read project version from pyproject.toml"
}

function Assert-Asset {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Release asset missing: $Path. Run packaging\build_release_installers.ps1 first."
  }
}

function Get-ReleaseNotes {
  param([Parameter(Mandatory = $true)][string]$ReleaseTag)

  $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $ChangeLog
  $escapedTag = [regex]::Escape($ReleaseTag)
  $match = [regex]::Match($text, "(?ms)^##\s+$escapedTag\b.*?(?=^##\s+v|\z)")
  if ($match.Success) {
    return $match.Value.Trim()
  }
  return $text.Trim()
}

function Get-GitHubToken {
  if ($env:GITHUB_TOKEN) {
    return $env:GITHUB_TOKEN
  }
  if ($env:GH_TOKEN) {
    return $env:GH_TOKEN
  }

  $credentialInput = "protocol=https`nhost=github.com`n`n"
  try {
    $credential = $credentialInput | git credential fill 2>$null
    foreach ($line in $credential) {
      if ($line -like "password=*") {
        return $line.Substring("password=".Length)
      }
    }
  } catch {
    return $null
  }

  return $null
}

function Invoke-GitHubRest {
  param(
    [Parameter(Mandatory = $true)][string]$Method,
    [Parameter(Mandatory = $true)][string]$Uri,
    [Parameter(Mandatory = $true)][hashtable]$Headers,
    [string]$Body = $null,
    [string]$ContentType = "application/json",
    [string]$InFile = $null
  )

  $parameters = @{
    Method = $Method
    Uri = $Uri
    Headers = $Headers
  }
  if ($Body) {
    $parameters.Body = $Body
    $parameters.ContentType = $ContentType
  }
  if ($InFile) {
    $parameters.InFile = $InFile
    $parameters.ContentType = $ContentType
  }

  return Invoke-RestMethod @parameters
}

function Get-GitHubRelease {
  param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$ReleaseTag,
    [Parameter(Mandatory = $true)][hashtable]$Headers
  )

  try {
    return Invoke-GitHubRest `
      -Method "Get" `
      -Uri ("https://api.github.com/repos/{0}/releases/tags/{1}" -f $Repository, $ReleaseTag) `
      -Headers $Headers
  } catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
      return $null
    }
    throw
  }
}

function Invoke-GitHubAssetUpload {
  param(
    [Parameter(Mandatory = $true)][string]$UploadUri,
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Token,
    [Parameter(Mandatory = $true)][hashtable]$Headers
  )

  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($curl) {
    $curlPath = "@$((Get-Item -LiteralPath $Path).FullName -replace '\\', '/')"
    & $curl.Source `
      --fail `
      --location `
      --retry 5 `
      --retry-delay 10 `
      --retry-all-errors `
      --request POST `
      --header "Authorization: Bearer $Token" `
      --header "Accept: application/vnd.github+json" `
      --header "X-GitHub-Api-Version: 2022-11-28" `
      --header "User-Agent: LinearStageControlReleaseScript" `
      --header "Content-Type: application/octet-stream" `
      --data-binary $curlPath `
      $UploadUri | Out-Null

    if ($LASTEXITCODE -ne 0) {
      throw "GitHub asset upload failed with curl exit code $LASTEXITCODE"
    }
    return
  }

  Invoke-GitHubRest `
    -Method "Post" `
    -Uri $UploadUri `
    -Headers $Headers `
    -ContentType "application/octet-stream" `
    -InFile (Get-Item -LiteralPath $Path).FullName | Out-Null
}

function Publish-GitHubRestRelease {
  param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$ReleaseTag,
    [Parameter(Mandatory = $true)][string[]]$ReleaseAssets
  )

  $token = Get-GitHubToken
  if (-not $token) {
    throw "No GitHub token was found. Set GITHUB_TOKEN/GH_TOKEN or sign in so git credential fill can provide a GitHub token."
  }

  $headers = @{
    Authorization = "Bearer $token"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "LinearStageControlReleaseScript"
  }

  $release = Get-GitHubRelease -Repository $Repository -ReleaseTag $ReleaseTag -Headers $headers
  if (-not $release) {
    $body = @{
      tag_name = $ReleaseTag
      name = $ReleaseTag
      body = Get-ReleaseNotes -ReleaseTag $ReleaseTag
      draft = $false
      prerelease = $false
    } | ConvertTo-Json -Depth 5

    $release = Invoke-GitHubRest `
      -Method "Post" `
      -Uri ("https://api.github.com/repos/{0}/releases" -f $Repository) `
      -Headers $headers `
      -Body $body
  } else {
    $body = @{
      name = $ReleaseTag
      body = Get-ReleaseNotes -ReleaseTag $ReleaseTag
      draft = $false
      prerelease = $false
    } | ConvertTo-Json -Depth 5

    $release = Invoke-GitHubRest `
      -Method "Patch" `
      -Uri ("https://api.github.com/repos/{0}/releases/{1}" -f $Repository, $release.id) `
      -Headers $headers `
      -Body $body
  }

  $uploadBase = [regex]::Replace([string]$release.upload_url, "\{.*$", "")
  $assetNames = @{}
  foreach ($assetPath in $ReleaseAssets) {
    $asset = Get-Item -LiteralPath $assetPath
    $assetNames[$asset.Name] = $true
  }

  $existingAssets = @{}
  foreach ($asset in @($release.assets)) {
    if (-not $asset) {
      continue
    }
    $existingAssets[$asset.name] = $asset
  }

  foreach ($assetPath in $ReleaseAssets) {
    $localAsset = Get-Item -LiteralPath $assetPath
    if (-not $existingAssets.ContainsKey($localAsset.Name)) {
      continue
    }

    $remoteAsset = $existingAssets[$localAsset.Name]
    if ($localAsset.Name -ne "update_manifest.json" -and [int64]$remoteAsset.size -eq [int64]$localAsset.Length) {
      Write-Host "Release asset already exists with matching size, skipping: $($localAsset.Name)"
      continue
    }

    if ($assetNames.ContainsKey($remoteAsset.name)) {
      Invoke-GitHubRest `
        -Method "Delete" `
        -Uri ("https://api.github.com/repos/{0}/releases/assets/{1}" -f $Repository, $remoteAsset.id) `
        -Headers $headers | Out-Null
      $existingAssets.Remove($localAsset.Name)
    }
  }

  foreach ($assetPath in $ReleaseAssets) {
    $asset = Get-Item -LiteralPath $assetPath
    if ($existingAssets.ContainsKey($asset.Name)) {
      continue
    }

    $uploadUri = "{0}?name={1}" -f $uploadBase, [uri]::EscapeDataString($asset.Name)
    Invoke-GitHubAssetUpload -UploadUri $uploadUri -Path $asset.FullName -Token $token -Headers $headers
    Write-Host "Uploaded release asset: $($asset.Name)"
  }
}

if (-not $Tag) {
  $Tag = "v$(Get-ProjectVersion)"
}

if ($PortableOnly) {
  Assert-Asset $PortableZip
  $Assets = @($PortableZip)
  if (Test-Path -LiteralPath $PortableManifest) {
    $Assets += $PortableManifest
  }
} else {
  Assert-Asset $ManifestPath
  Assert-Asset $OnlineSetup
  if (Test-Path -LiteralPath $OfflineSetup) {
    $Assets = @($OnlineSetup, $OfflineSetup, $ManifestPath)
  } else {
    $Assets = @($OnlineSetup, $ManifestPath)
  }
  if ($IncludePortable) {
    Assert-Asset $PortableZip
    $Assets += $PortableZip
    if (Test-Path -LiteralPath $PortableManifest) {
      $Assets += $PortableManifest
    }
  }
}

$Gh = Get-Command gh -ErrorAction SilentlyContinue

$GhArgs = @(
  "release", "create", $Tag
  "--repo", $Repo
  "--title", $Tag
  "--notes-file", $ChangeLog
)
$GhArgs += $Assets

if ($DryRun) {
  Write-Host "Dry run only. Release would be published as $Tag to $Repo with assets:"
  foreach ($asset in $Assets) {
    Write-Host "  $asset"
  }
  exit 0
}

if ($Gh) {
  & gh @GhArgs
  if ($LASTEXITCODE -ne 0) {
    throw "GitHub release creation failed with exit code $LASTEXITCODE"
  }
} else {
  Publish-GitHubRestRelease -Repository $Repo -ReleaseTag $Tag -ReleaseAssets $Assets
}
