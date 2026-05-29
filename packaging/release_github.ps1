param(
  [string]$Tag = "",
  [string]$Repo = "Simon-Choi-1028/linear-stage-control",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$ManifestPath = Join-Path $Root "dist\update_manifest.json"
$OnlineSetup = Join-Path $Root "dist\LinearStageControlSetup.exe"
$OfflineSetup = Join-Path $Root "dist\LinearStageControlSetup-Offline.exe"
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
      -Uri "https://api.github.com/repos/$Repository/releases/tags/$ReleaseTag" `
      -Headers $Headers
  } catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
      return $null
    }
    throw
  }
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
      -Uri "https://api.github.com/repos/$Repository/releases" `
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
      -Uri "https://api.github.com/repos/$Repository/releases/$($release.id)" `
      -Headers $headers `
      -Body $body
  }

  $uploadBase = $release.upload_url -replace "\{.*$", ""
  $assetNames = @{}
  foreach ($assetPath in $ReleaseAssets) {
    $asset = Get-Item -LiteralPath $assetPath
    $assetNames[$asset.Name] = $true
  }

  foreach ($asset in @($release.assets)) {
    if ($assetNames.ContainsKey($asset.name)) {
      Invoke-GitHubRest `
        -Method "Delete" `
        -Uri "https://api.github.com/repos/$Repository/releases/assets/$($asset.id)" `
        -Headers $headers | Out-Null
    }
  }

  foreach ($assetPath in $ReleaseAssets) {
    $asset = Get-Item -LiteralPath $assetPath
    $uploadUri = "$uploadBase?name=$([uri]::EscapeDataString($asset.Name))"
    Invoke-GitHubRest `
      -Method "Post" `
      -Uri $uploadUri `
      -Headers $headers `
      -ContentType "application/octet-stream" `
      -InFile $asset.FullName | Out-Null
    Write-Host "Uploaded release asset: $($asset.Name)"
  }
}

if (-not $Tag) {
  $Tag = "v$(Get-ProjectVersion)"
}

Assert-Asset $ManifestPath
Assert-Asset $OnlineSetup
if (Test-Path -LiteralPath $OfflineSetup) {
  $Assets = @($OnlineSetup, $OfflineSetup, $ManifestPath)
} else {
  $Assets = @($OnlineSetup, $ManifestPath)
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
