<#
.SYNOPSIS
  Builds the Padthru release files into .\dist:
    Padthru.wgt   unsigned Samsung TV app (Apps2Samsung or Tizen Studio signs it for your TV)
    Padthru.exe         Windows tray app (one file)
    padthru-linux.pyz   Linux app (one file, Python 3.8+)
    install-linux.sh    Linux installer
    SHA256SUMS.txt

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\build.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist'
New-Item -ItemType Directory -Force $dist | Out-Null

Write-Host '==> TV app (Padthru.wgt)'
$wgt = Join-Path $dist 'Padthru.wgt'
if (Test-Path $wgt) { Remove-Item $wgt }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($wgt, 'Create')
foreach ($f in 'config.xml', 'index.html', 'main.js', 'style.css', 'icon.png') {
  [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $root "tv\$f"), $f) | Out-Null
}
$zip.Dispose()

Write-Host '==> PC app (Padthru.exe)'
python -m pip install --quiet -r (Join-Path $root 'pc\requirements.txt') pyinstaller
$pc = Join-Path $root 'pc'
$work = Join-Path $root 'build'
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Padthru `
  --icon (Join-Path $pc 'assets\padthru.ico') `
  --paths (Join-Path $pc 'vendor') `
  --hidden-import pystray._win32 --exclude-module vpad_linux `
  --add-data "$(Join-Path $pc 'assets\padthru.png');assets" `
  --add-data "$(Join-Path $pc 'vendor\vgamepad\win\vigem\client\x64\ViGEmClient.dll');vgamepad\win\vigem\client\x64" `
  --distpath $dist --workpath $work --specpath $work `
  (Join-Path $pc 'padthru.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

Write-Host '==> Linux app (padthru-linux.pyz, install-linux.sh)'
python (Join-Path $root 'tools\build_pyz.py') --out (Join-Path $dist 'padthru-linux.pyz')
if ($LASTEXITCODE -ne 0) { throw 'build_pyz.py failed' }
Copy-Item (Join-Path $root 'linux\install.sh') (Join-Path $dist 'install-linux.sh') -Force

Write-Host '==> Checksums'
# Unix line endings, so `sha256sum -c` and the Linux installer can read it
$sums = Get-ChildItem $dist -File -Include *.wgt, *.exe, *.pyz, *.sh -Recurse | ForEach-Object {
  '{0}  {1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
}
[IO.File]::WriteAllText((Join-Path $dist 'SHA256SUMS.txt'), (($sums -join "`n") + "`n"))
Get-Content (Join-Path $dist 'SHA256SUMS.txt')
Write-Host "Done: $dist"
