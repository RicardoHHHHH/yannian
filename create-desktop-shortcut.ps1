$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Run start.cmd once to prepare the Python environment.' }
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopPath '研念工作台.lnk'
$shortcutShell = New-Object -ComObject WScript.Shell
$legacyShortcutPath = Join-Path $desktopPath '研迹工作台.lnk'
if ((Test-Path -LiteralPath $legacyShortcutPath) -and -not (Test-Path -LiteralPath $shortcutPath)) {
    $legacyShortcut = $shortcutShell.CreateShortcut($legacyShortcutPath)
    if ($legacyShortcut.TargetPath -eq $pythonWindowless -and $legacyShortcut.Arguments -eq ('"' + (Join-Path $projectRoot 'launch.pyw') + '"')) {
        Move-Item -LiteralPath $legacyShortcutPath -Destination $shortcutPath
    }
}
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonWindowless
$shortcut.Arguments = '"' + (Join-Path $projectRoot 'launch.pyw') + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = '研念 · 论文阅读、文献检索与研究想法工作台'
$iconPath = Join-Path $projectRoot 'static\yannian-app.ico'
if (-not (Test-Path -LiteralPath $iconPath)) { throw 'The Yannian app icon is missing.' }
$shortcut.IconLocation = $iconPath + ',0'
$shortcut.WindowStyle = 7
$shortcut.Save()
if (-not ('YannianShellNotify' -as [type])) {
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class YannianShellNotify { [DllImport("shell32.dll", CharSet=CharSet.Unicode)] public static extern void SHChangeNotify(uint eventId, uint flags, string item1, IntPtr item2); }'
}
[YannianShellNotify]::SHChangeNotify(0x2000, 0x0005, $shortcutPath, [IntPtr]::Zero)
Write-Output $shortcutPath
