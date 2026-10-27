# make_shortcut.ps1 - puts an "NBA Picks" icon on the Desktop and in the Start menu.
# Run it through make_shortcut.bat (double-click). Safe to run again; it just replaces the shortcuts.
#   - basketball icon (nba_picks.ico)
#   - opens the app with no command window (same as NBA Picks.bat: pulls updates, then opens)
#   - keyboard shortcut Ctrl+Alt+N opens it from anywhere
#   - the Start-menu copy can be pinned: Start > type "NBA Picks" > right-click > Pin to taskbar

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat  = Join-Path $here "NBA Picks.bat"
$icon = Join-Path $here "nba_picks.ico"
$cmd  = Join-Path $env:WINDIR "System32\cmd.exe"

$places = @(
    [Environment]::GetFolderPath("Desktop"),                                   # works with OneDrive desktops too
    [Environment]::GetFolderPath("Programs")                                   # Start menu
)

$shell = New-Object -ComObject WScript.Shell
$i = 0
foreach ($dir in $places) {
    $path = Join-Path $dir "NBA Picks.lnk"
    $s = $shell.CreateShortcut($path)
    $s.TargetPath       = $cmd
    $s.Arguments        = "/c `"`"$bat`"`""
    $s.WorkingDirectory = $here
    $s.IconLocation     = "$icon,0"
    $s.WindowStyle      = 7                    # minimized: the brief update window stays out of the way
    $s.Description      = "NBA Picks - game lines and player props"
    if ($i -eq 0) { $s.Hotkey = "CTRL+ALT+N" } # one hotkey is enough (two would clash)
    $s.Save()
    Write-Host "Made: $path"
    $i++
}
Write-Host ""
Write-Host "Done. Double-click 'NBA Picks' on your Desktop, or press Ctrl+Alt+N."
Write-Host "To pin it: Start > type NBA Picks > right-click > Pin to taskbar."
