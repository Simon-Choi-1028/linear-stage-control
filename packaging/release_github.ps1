param(
  [string]$Tag = "",
  [string]$Repo = "Simon-Choi-1028/linear-stage-control",
  [switch]$PortableOnly,
  [switch]$IncludePortable,
  [switch]$ForceReplaceAssets,
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
  $match = [regex]::Match(
    $text,
    "(?ms)^##[ \t]+$escapedTag(?:[ \t]+-[^\r\n]*)?[ \t]*\r?\n.*?(?=^##[ \t]+|\z)"
  )
  if ($match.Success) {
    return $match.Value.Trim()
  }
  throw "CHANGELOG.md does not contain a release section for the exact tag '$ReleaseTag'."
}

function Get-RequiredManifestValue {
  param(
    [Parameter(Mandatory = $true)][object]$Object,
    [Parameter(Mandatory = $true)][string]$PropertyName,
    [Parameter(Mandatory = $true)][string]$ManifestName
  )

  $property = $Object.PSObject.Properties[$PropertyName]
  if ($null -eq $property -or $null -eq $property.Value -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
    throw "$ManifestName is missing required property '$PropertyName'."
  }
  return $property.Value
}

function Get-ManifestAssetNames {
  param([Parameter(Mandatory = $true)][string]$Path)

  $manifestItem = Get-Item -LiteralPath $Path
  try {
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestItem.FullName | ConvertFrom-Json
  } catch {
    throw "Could not parse $($manifestItem.Name) as JSON: $($_.Exception.Message)"
  }

  $names = @(
    [string](
      Get-RequiredManifestValue `
        -Object $manifest `
        -PropertyName "asset_name" `
        -ManifestName $manifestItem.Name
    )
  )
  $assetsProperty = $manifest.PSObject.Properties["assets"]
  if ($null -ne $assetsProperty -and $null -ne $assetsProperty.Value) {
    foreach ($entry in @($assetsProperty.Value)) {
      if ($null -eq $entry) {
        throw "$($manifestItem.Name) contains an empty assets entry."
      }
      $names += [string](
        Get-RequiredManifestValue `
          -Object $entry `
          -PropertyName "name" `
          -ManifestName $manifestItem.Name
      )
    }
  }
  return @($names | Sort-Object -Unique)
}

function Assert-UpdateManifestPolicy {
  param([Parameter(Mandatory = $true)][string]$Path)

  $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
  $manifestName = Split-Path -Leaf $Path
  $primaryName = [string](
    Get-RequiredManifestValue -Object $manifest -PropertyName "asset_name" -ManifestName $manifestName
  )
  if ($primaryName -cne "LinearStageControlSetup.exe") {
    throw "$manifestName must use LinearStageControlSetup.exe as its primary asset."
  }

  $channels = Get-RequiredManifestValue -Object $manifest -PropertyName "channels" -ManifestName $manifestName
  if ([string]$channels.default -cne "online" -or [string]$channels.online -cne $primaryName) {
    throw "$manifestName must declare default=online and online=$primaryName."
  }

  $declaredNames = @(Get-ManifestAssetNames -Path $Path)
  $allowedNames = @("LinearStageControlSetup.exe", "LinearStageControlSetup-Offline.exe")
  foreach ($name in $declaredNames) {
    if ($allowedNames -notcontains $name) {
      throw "$manifestName declares unsupported installer asset '$name'."
    }
  }

  $offlineName = [string]$channels.offline
  $offlineDeclared = $declaredNames -contains "LinearStageControlSetup-Offline.exe"
  if ([string]::IsNullOrWhiteSpace($offlineName)) {
    if ($offlineDeclared) {
      throw "$manifestName declares an offline asset entry but channels.offline is empty."
    }
  } elseif ($offlineName -cne "LinearStageControlSetup-Offline.exe" -or -not $offlineDeclared) {
    throw "$manifestName channels.offline and assets[] must both declare LinearStageControlSetup-Offline.exe."
  }
}

function Assert-PortableManifestPolicy {
  param([Parameter(Mandatory = $true)][string]$Path)

  $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
  $manifestName = Split-Path -Leaf $Path
  $primaryName = [string](
    Get-RequiredManifestValue -Object $manifest -PropertyName "asset_name" -ManifestName $manifestName
  )
  if ($primaryName -cne "LinearStageControl-Portable.zip") {
    throw "$manifestName must use LinearStageControl-Portable.zip as its primary asset."
  }
  if ([string]$manifest.distribution -cne "portable") {
    throw "$manifestName must declare distribution=portable."
  }
  if ([string]$manifest.entrypoint -cne "LinearStageControl\LinearStageControl.exe") {
    throw "$manifestName contains an unexpected portable entrypoint."
  }
}

function Assert-ManifestAssetEntry {
  param(
    [Parameter(Mandatory = $true)][object]$Entry,
    [Parameter(Mandatory = $true)][string]$ManifestName,
    [Parameter(Mandatory = $true)][hashtable]$LocalAssetsByName,
    [Parameter(Mandatory = $true)][hashtable]$HashCache
  )

  $assetName = [string](Get-RequiredManifestValue -Object $Entry -PropertyName "name" -ManifestName $ManifestName)
  $expectedHash = [string](Get-RequiredManifestValue -Object $Entry -PropertyName "sha256" -ManifestName $ManifestName)
  $expectedSizeValue = Get-RequiredManifestValue -Object $Entry -PropertyName "size_bytes" -ManifestName $ManifestName

  if (-not $LocalAssetsByName.ContainsKey($assetName)) {
    throw "$ManifestName references '$assetName', but that local asset is not included in this release."
  }
  if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$') {
    throw "$ManifestName contains an invalid SHA256 value for '$assetName'."
  }

  try {
    $expectedSize = [int64]::Parse(
      [string]$expectedSizeValue,
      [Globalization.CultureInfo]::InvariantCulture
    )
  } catch {
    throw "$ManifestName contains an invalid size_bytes value for '$assetName'."
  }

  $localAsset = $LocalAssetsByName[$assetName]
  if ([int64]$localAsset.Length -ne $expectedSize) {
    throw "$ManifestName size mismatch for '$assetName': expected $expectedSize bytes, found $($localAsset.Length). Rebuild the release assets."
  }

  if (-not $HashCache.ContainsKey($localAsset.FullName)) {
    $HashCache[$localAsset.FullName] = (Get-FileHash -LiteralPath $localAsset.FullName -Algorithm SHA256).Hash
  }
  $actualHash = [string]$HashCache[$localAsset.FullName]
  if (-not $actualHash.Equals($expectedHash, [StringComparison]::OrdinalIgnoreCase)) {
    throw "$ManifestName SHA256 mismatch for '$assetName'. Rebuild the release assets and manifest."
  }
}

function Assert-ReleaseManifest {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [Parameter(Mandatory = $true)][string[]]$ReleaseAssets,
    [Parameter(Mandatory = $true)][hashtable]$HashCache
  )

  $manifestItem = Get-Item -LiteralPath $Path
  $manifestName = $manifestItem.Name
  try {
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestItem.FullName | ConvertFrom-Json
  } catch {
    throw "Could not parse $manifestName as JSON: $($_.Exception.Message)"
  }

  $manifestVersion = [string](
    Get-RequiredManifestValue -Object $manifest -PropertyName "version" -ManifestName $manifestName
  )
  if ($manifestVersion -cne $ExpectedVersion) {
    throw "$manifestName version '$manifestVersion' does not match release tag '$ExpectedVersion'. Rebuild the release assets."
  }

  $localAssetsByName = @{}
  foreach ($assetPath in $ReleaseAssets) {
    $asset = Get-Item -LiteralPath $assetPath
    if ($localAssetsByName.ContainsKey($asset.Name)) {
      throw "Duplicate local release asset name: $($asset.Name)"
    }
    $localAssetsByName[$asset.Name] = $asset
  }

  $primaryEntry = [pscustomobject]@{
    name = Get-RequiredManifestValue -Object $manifest -PropertyName "asset_name" -ManifestName $manifestName
    sha256 = Get-RequiredManifestValue -Object $manifest -PropertyName "sha256" -ManifestName $manifestName
    size_bytes = Get-RequiredManifestValue -Object $manifest -PropertyName "size_bytes" -ManifestName $manifestName
  }
  Assert-ManifestAssetEntry `
    -Entry $primaryEntry `
    -ManifestName $manifestName `
    -LocalAssetsByName $localAssetsByName `
    -HashCache $HashCache

  $assetsProperty = $manifest.PSObject.Properties["assets"]
  if ($null -ne $assetsProperty -and $null -ne $assetsProperty.Value) {
    foreach ($entry in @($assetsProperty.Value)) {
      if ($null -eq $entry) {
        throw "$manifestName contains an empty assets entry."
      }
      Assert-ManifestAssetEntry `
        -Entry $entry `
        -ManifestName $manifestName `
        -LocalAssetsByName $localAssetsByName `
        -HashCache $HashCache
    }
  }
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
    [Parameter(Mandatory = $true)][string[]]$ReleaseAssets,
    [Parameter(Mandatory = $true)][string]$ReleaseNotes
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
      body = $ReleaseNotes
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
      body = $ReleaseNotes
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
    $manifestLike = $localAsset.Name -in @("update_manifest.json", "portable_manifest.json")
    if (-not $ForceReplaceAssets -and -not $manifestLike -and [int64]$remoteAsset.size -eq [int64]$localAsset.Length) {
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

$ProjectVersion = Get-ProjectVersion
$ExpectedTag = "v$ProjectVersion"
if (-not $Tag) {
  $Tag = $ExpectedTag
}
if ($Tag -cne $ExpectedTag) {
  throw "Release tag '$Tag' does not match project version '$ProjectVersion'. Expected '$ExpectedTag'."
}

if ($PortableOnly) {
  Assert-Asset $PortableZip
  Assert-Asset $PortableManifest
  Assert-PortableManifestPolicy -Path $PortableManifest
  $Assets = @($PortableZip, $PortableManifest)
} else {
  Assert-Asset $ManifestPath
  Assert-Asset $OnlineSetup
  Assert-UpdateManifestPolicy -Path $ManifestPath
  $Assets = @($OnlineSetup)
  $declaredInstallerAssets = @(Get-ManifestAssetNames -Path $ManifestPath)
  if ($declaredInstallerAssets -contains (Split-Path -Leaf $OfflineSetup)) {
    Assert-Asset $OfflineSetup
    $Assets += $OfflineSetup
  } elseif (Test-Path -LiteralPath $OfflineSetup) {
    Write-Host "Ignoring stale offline installer not declared by update_manifest.json: $OfflineSetup"
  }
  $Assets += $ManifestPath
  if ($IncludePortable) {
    Assert-Asset $PortableZip
    Assert-Asset $PortableManifest
    Assert-PortableManifestPolicy -Path $PortableManifest
    $Assets += @($PortableZip, $PortableManifest)
  }
}

$ReleaseNotes = Get-ReleaseNotes -ReleaseTag $Tag
$HashCache = @{}
if ($PortableOnly) {
  Assert-ReleaseManifest `
    -Path $PortableManifest `
    -ExpectedVersion $Tag `
    -ReleaseAssets $Assets `
    -HashCache $HashCache
} else {
  Assert-ReleaseManifest `
    -Path $ManifestPath `
    -ExpectedVersion $Tag `
    -ReleaseAssets $Assets `
    -HashCache $HashCache
  if ($IncludePortable) {
    Assert-ReleaseManifest `
      -Path $PortableManifest `
      -ExpectedVersion $Tag `
      -ReleaseAssets $Assets `
      -HashCache $HashCache
  }
}

$Gh = Get-Command gh -ErrorAction SilentlyContinue

$GhArgs = @(
  "release", "create", $Tag
  "--repo", $Repo
  "--title", $Tag
  "--notes", $ReleaseNotes
  "--verify-tag"
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
  & $Gh.Source @GhArgs
  if ($LASTEXITCODE -ne 0) {
    throw "GitHub release creation failed with exit code $LASTEXITCODE"
  }
} else {
  Publish-GitHubRestRelease `
    -Repository $Repo `
    -ReleaseTag $Tag `
    -ReleaseAssets $Assets `
    -ReleaseNotes $ReleaseNotes
}
