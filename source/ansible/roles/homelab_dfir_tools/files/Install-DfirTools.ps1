<#
    Installs the DFIR toolset into one folder (default C:\Tools).
    Each tool is installed independently: a failed download is reported in the
    summary but never stops the rest, so the lab deploy always completes.
    Safe to re-run; it refreshes everything to the latest release.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$settings = Get-Content -Raw -Path $SettingsPath | ConvertFrom-Json
$dest = $settings.dest
$results = New-Object System.Collections.Generic.List[string]

foreach ($pkg in @($settings.choco_failed)) {
    if ($pkg) { $results.Add("FAILED  Chocolatey package '$pkg' (see the Ansible log)") }
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    try {
        $detail = & $Action
        $results.Add(("OK      {0} {1}" -f $Name, $detail).TrimEnd())
    }
    catch {
        $results.Add("FAILED  $Name - $($_.Exception.Message)")
    }
}

Invoke-Step 'Eric Zimmerman tools' {
    $ErrorActionPreference = 'Continue'
    $target = Join-Path $dest 'ZimmermanTools'
    $script = Join-Path $dest 'Get-ZimmermanTools.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri $settings.zimmerman_script_url -OutFile $script
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -Dest $target -NetVersion 9 | Out-Null
    $count = @(Get-ChildItem -Path $target -Recurse -Filter '*.exe' -ErrorAction SilentlyContinue).Count
    if ($count -eq 0) { throw 'no tools were downloaded' }
    "($count programs in $target)"
}

foreach ($tool in @($settings.github_tools)) {
    Invoke-Step $tool.name {
        $release = Invoke-RestMethod -UseBasicParsing `
            -Uri "https://api.github.com/repos/$($tool.repo)/releases/latest" `
            -Headers @{ 'User-Agent' = 'ludus-homelab' }
        $exclude = if ($tool.exclude) { $tool.exclude } else { '^$' }
        $asset = $null
        foreach ($pattern in @($tool.patterns)) {
            $asset = $release.assets |
                Where-Object { $_.name -match $pattern -and $_.name -notmatch $exclude } |
                Select-Object -First 1
            if ($asset) { break }
        }
        if (-not $asset) {
            $names = ($release.assets | ForEach-Object { $_.name }) -join ', '
            throw "no suitable download in release $($release.tag_name) (assets: $names)"
        }
        $zip = Join-Path ([IO.Path]::GetTempPath()) $asset.name
        Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
        $target = Join-Path $dest $tool.name
        Expand-Archive -Path $zip -DestinationPath $target -Force
        Remove-Item -Path $zip -Force
        "$($release.tag_name) in $target"
    }
}

if (@($settings.pip_packages).Count -gt 0) {
    Invoke-Step ('Python packages: ' + (@($settings.pip_packages) -join ', ')) {
        $ErrorActionPreference = 'Continue'
        $python = Get-ChildItem -Path 'C:\Python3*\python.exe', 'C:\Program Files\Python3*\python.exe' -ErrorAction SilentlyContinue |
            Sort-Object -Property FullName -Descending | Select-Object -First 1
        if (-not $python) { throw 'Python was not found (the python3 Chocolatey package may have failed)' }
        & $python.FullName -m pip install --quiet --disable-pip-version-check @($settings.pip_packages) *> $null
        if ($LASTEXITCODE -ne 0) { throw "pip exited with code $LASTEXITCODE" }
        $scripts = Join-Path (Split-Path $python.FullName) 'Scripts'
        $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        if ($machinePath -notlike "*$scripts*") {
            [Environment]::SetEnvironmentVariable('Path', "$machinePath;$scripts", 'Machine')
        }
        "(run vol.exe from $scripts)"
    }
}

Invoke-Step 'Desktop shortcut' {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut('C:\Users\Public\Desktop\DFIR Tools.lnk')
    $shortcut.TargetPath = $dest
    $shortcut.Save()
    "(Public desktop -> $dest)"
}

$summary = @(
    "DFIR workstation tools - installed $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    ''
    $results
    ''
    'Folders:'
    "  $dest\ZimmermanTools   Eric Zimmerman tools (Timeline Explorer, Registry Explorer, MFTECmd, EvtxECmd, ...)"
    "  $dest\Hayabusa         Windows event log fast-forensics timelines"
    "  $dest\Chainsaw         Sigma-based event log hunting (rules included)"
    "  $dest\CyberChef        Open the .html file in a browser"
    '  Sysinternals, Wireshark, HxD, DB Browser for SQLite, 7-Zip and Notepad++ are in the Start menu'
    '  Volatility 3: run vol.exe in a new terminal'
)
$summary | Set-Content -Path (Join-Path $dest 'README.txt') -Encoding UTF8
$results
exit 0
