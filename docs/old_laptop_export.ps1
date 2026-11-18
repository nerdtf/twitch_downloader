# =====================================================================================
# Old Windows laptop -> find and export the old twitch project (NO videos)
# -------------------------------------------------------------------------------------
# How to use:
#   1. Open "Windows PowerShell" (Start menu -> type PowerShell). Admin is NOT required.
#   2. Paste ONE BLOCK at a time (blocks are separated by ===== lines) and press Enter.
#   3. At the end you get  Desktop\twitch_export.zip  -> put it on the Mac in
#      ~/dev/twitch_downloader/data/import/  (USB stick, cloud drive, Telegram "Saved", ...).
#
# What we want (small, valuable):        What we do NOT want (huge, useless now):
#   - chatProcessor*.py (keyword list!)    - *.ts / *.mp4 / *.mkv recordings
#   - any other *.py of the project        - venv / site-packages / node_modules
#   - *chat.json, *segments.json (chats)   - config.txt (old Twitch secret; we have new keys)
#   - mp4_processing.db, *.log
#   - streamers.txt, notes, keyword lists
# =====================================================================================


# ===== BLOCK 1: settings (edit paths here if you want) ===============================
$Out      = "$env:USERPROFILE\Desktop\twitch_export"     # where everything is collected
$Drives   = (Get-PSDrive -PSProvider FileSystem).Root     # all drives: C:\, D:\, ...
$SkipDirs = 'Windows','Program Files','Program Files (x86)','ProgramData','$Recycle.Bin',
            'AppData','node_modules','site-packages','.git','venv','myenv','.venv','__pycache__'
# File names that identify the project or are worth keeping:
$Names    = 'chatProcessor*.py','videoWorker*.py','watcher*.py','tg_bot*.py','cron_script*.py',
            '*chat.json','*segments.json','mp4_processing.db','streamers.txt','notes.txt',
            '*keyword*','*lexicon*','processing.log','daemon.log'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
"Drives to search: $Drives"


# ===== BLOCK 2: search all Windows drives (can take 5-20 min on a slow laptop) =======
# Writes the list of matches to found_windows.txt. Errors (access denied) are ignored.
$found = foreach ($d in $Drives) {
  Get-ChildItem -Path $d -Recurse -File -Include $Names -ErrorAction SilentlyContinue |
    Where-Object { $p = $_.FullName; -not ($SkipDirs | Where-Object { $p -like "*\$_\*" }) }
}
$found | Select-Object FullName, Length, LastWriteTime |
  Sort-Object FullName | Format-Table -AutoSize | Out-String -Width 400 |
  Set-Content "$Out\found_windows.txt"
"Found $($found.Count) files. List: $Out\found_windows.txt"
# Project folders = folders that contain one of the project's .py files:
$projectDirs = $found | Where-Object { $_.Extension -eq '.py' } |
  ForEach-Object { $_.DirectoryName } | Sort-Object -Unique
$projectDirs | Set-Content "$Out\project_dirs_windows.txt"
"Project folders:"; $projectDirs


# ===== BLOCK 3: search inside WSL (Linux) disks, if WSL is installed =================
# Lists distros, then searches each one's Linux filesystem (skips /mnt = Windows drives).
$distros = (wsl.exe -l -q 2>$null) -replace "`0", '' | Where-Object { $_.Trim() -ne '' }
"WSL distros: $distros"
foreach ($dist in $distros) {
  $dist = $dist.Trim()
  wsl.exe -d $dist -- sh -c "find / -path /mnt -prune -o -path /proc -prune -o -path /sys -prune -o \( -name 'chatProcessor*.py' -o -name 'videoWorker*.py' -o -name 'watcher.py' -o -name 'tg_bot.py' -o -name '*chat.json' -o -name '*segments.json' -o -name 'mp4_processing.db' -o -name 'streamers.txt' -o -iname '*keyword*' \) -type f -print 2>/dev/null" |
    Set-Content "$Out\found_wsl_$dist.txt"
  "WSL $dist -> $Out\found_wsl_$dist.txt ($((Get-Content "$Out\found_wsl_$dist.txt").Count) files)"
}


# ===== BLOCK 4: copy Windows project folders (without videos / envs) ================
$i = 0
foreach ($dir in $projectDirs) {
  $i++; $dest = "$Out\windows_project_$i"
  "$dir -> $dest" | Add-Content "$Out\copy_map.txt"
  robocopy $dir $dest /S /R:0 /W:0 /NFL /NDL /NJH /NJS `
    /XF *.ts *.mp4 *.mkv *.flv *.mov config.txt `
    /XD .git venv myenv .venv __pycache__ node_modules site-packages | Out-Null
}
# Loose matches outside project folders (e.g. chat json in Downloads):
$loose = "$Out\loose_files"; New-Item -ItemType Directory -Force -Path $loose | Out-Null
$found | Where-Object { $projectDirs -notcontains $_.DirectoryName } | ForEach-Object {
  $safe = ($_.FullName -replace '[:\\]', '_')       # keep origin path in the file name
  Copy-Item $_.FullName "$loose\$safe" -ErrorAction SilentlyContinue
}
"Copied. Map: $Out\copy_map.txt"


# ===== BLOCK 5: copy WSL matches (project folders of .py files + loose files) =======
foreach ($dist in $distros) {
  $dist = $dist.Trim(); $list = "$Out\found_wsl_$dist.txt"
  if (-not (Test-Path $list)) { continue }
  $wslOut = "$Out\wsl_$dist"; New-Item -ItemType Directory -Force -Path $wslOut | Out-Null
  # Copy each matched file via the \\wsl$ share, keeping its Linux path as folder structure.
  Get-Content $list | Where-Object { $_ -ne '' } | ForEach-Object {
    $src  = "\\wsl$\$dist" + ($_ -replace '/', '\')
    $dest = Join-Path $wslOut ($_.TrimStart('/') -replace '/', '\')
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item $src $dest -ErrorAction SilentlyContinue
  }
  # Also every .py next to the project files (the keyword list may live in another script):
  $pyDirs = Get-Content $list | Where-Object { $_ -like '*.py' } | ForEach-Object { ($_ -replace '/[^/]+$', '') } | Sort-Object -Unique
  foreach ($pd in $pyDirs) {
    robocopy ("\\wsl$\$dist" + ($pd -replace '/', '\')) (Join-Path $wslOut ($pd.TrimStart('/') -replace '/', '\')) *.py *.json *.txt *.db *.log /S /R:0 /W:0 /NFL /NDL /NJH /NJS `
      /XF config.txt /XD .git venv myenv .venv __pycache__ site-packages | Out-Null
  }
  "WSL $dist copied to $wslOut"
}


# ===== BLOCK 6: zip it and show the size ===========================================
$zip = "$env:USERPROFILE\Desktop\twitch_export.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$Out\*" -DestinationPath $zip
"ZIP: $zip  ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)"
# If the zip is > 2 GB, chats are probably duplicated many times; send found_*.txt first instead.
