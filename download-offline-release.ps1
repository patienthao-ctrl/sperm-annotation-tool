param(
    [string]$Version = 'latest',
    [string]$OutputDirectory = '',
    [string]$Repository = 'huangwww11222/sperm-annotation',
    [string]$ApiBase = 'https://api.github.com'
)
# Download every asset of a hospital offline-update Release and verify SHA256SUMS.
# Windows PowerShell 5.1+ (also pwsh on macOS/Linux); public GitHub URLs only, no login.
#   .\download-offline-release.ps1                    # newest vX.Y.Z offline package
#   .\download-offline-release.ps1 -Version v1.3.0
# Same behaviour as download-offline-release.py.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # PS 5.1 progress rendering makes large downloads very slow
if ($PSVersionTable.PSVersion.Major -lt 6) { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 }
$headers = @{ 'User-Agent' = 'sperm-annotation-offline-download'; 'Accept' = 'application/vnd.github+json' }

function Test-OfflinePackage($release) {
    $names = @($release.assets | ForEach-Object { $_.name })
    return ($names -contains 'SHA256SUMS') -and [bool]($names | Where-Object { $_ -like '*-files.tar.gz' })
}
function Get-Sha256([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Save-Asset([string]$Url, [string]$Target) {
    $partial = $Target + '.partial'
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial -Headers @{ 'User-Agent' = $headers['User-Agent']; 'Accept' = 'application/octet-stream' }
    Move-Item -LiteralPath $partial -Destination $Target -Force
}

try {
    $api = $ApiBase.TrimEnd('/')
    if ($Version -eq 'latest') {
        # Invoke-RestMethod returns a JSON array as one object; enumerate it explicitly.
        $releases = foreach ($item in (Invoke-RestMethod -UseBasicParsing -Uri "$api/repos/$Repository/releases?per_page=100" -Headers $headers)) { $item }
        # Other releases (e.g. the SAM3 model bundle) share the repository: pick offline packages only.
        $release = $releases | Where-Object { -not $_.draft -and -not $_.prerelease -and $_.tag_name -match '^v[0-9]+\.[0-9]+\.[0-9]+$' -and (Test-OfflinePackage $_) } | Select-Object -First 1
        if (-not $release) { throw '没有找到已发布的离线更新包（vX.Y.Z）' }
    } else {
        try { $release = Invoke-RestMethod -UseBasicParsing -Uri "$api/repos/$Repository/releases/tags/$Version" -Headers $headers }
        catch { throw "找不到版本 $Version（$($_.Exception.Message)）" }
        if (-not (Test-OfflinePackage $release)) { throw "$Version 不是离线更新包（缺少 SHA256SUMS 或 *-files.tar.gz）" }
    }
    $tag = $release.tag_name
    if (-not $OutputDirectory) { $OutputDirectory = Join-Path (Get-Location) "sperm-annotation-$tag" }
    $OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $assets = @{}
    foreach ($asset in $release.assets) {
        if ($asset.name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw "附件名无效：$($asset.name)" }
        $assets[$asset.name] = $asset.browser_download_url
    }
    Write-Host "版本 $tag，$($assets.Count) 个附件 → $OutputDirectory"
    $sums = Join-Path $OutputDirectory 'SHA256SUMS'
    Save-Asset $assets['SHA256SUMS'] $sums
    $expected = [ordered]@{}
    foreach ($line in [IO.File]::ReadAllLines($sums)) {
        if (-not $line) { continue }
        $digest, $name = $line -split '  ', 2
        if ($digest -cnotmatch '^[0-9a-f]{64}$' -or -not $assets.ContainsKey($name)) { throw "SHA256SUMS 与 Release 附件不符：$name" }
        $expected[$name] = $digest
    }
    $missing = @($assets.Keys | Where-Object { $_ -ne 'SHA256SUMS' -and -not $expected.Contains($_) } | Sort-Object)
    if ($missing.Count) { throw ('SHA256SUMS 未列出附件：' + ($missing -join '、')) }
    foreach ($name in $expected.Keys) {
        $target = Join-Path $OutputDirectory $name
        if ((Test-Path -LiteralPath $target -PathType Leaf) -and (Get-Sha256 $target) -ceq $expected[$name]) { Write-Host "已下载并核对：$name"; continue }
        $ok = $false
        for ($attempt = 1; $attempt -le 3 -and -not $ok; $attempt++) {
            Write-Host "下载 $name（第 $attempt 次）…"
            try { Save-Asset $assets[$name] $target } catch { Write-Warning "下载中断：$($_.Exception.Message)"; continue }
            if ((Get-Sha256 $target) -ceq $expected[$name]) { $ok = $true }
            else { Remove-Item -LiteralPath $target -Force; Write-Warning 'SHA256 不符，重新下载' }
        }
        if (-not $ok) { throw "$name 三次下载都未通过 SHA256 核对，请检查网络后重新运行（已核对的文件会跳过）" }
    }
    Write-Host ''
    Write-Host "全部附件已下载并通过 SHA256 核对：$OutputDirectory"
    Write-Host '把整个文件夹复制到医院服务器的新目录，在该目录执行：bash install-offline.sh /原部署目录'
} catch {
    Write-Host ('未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
