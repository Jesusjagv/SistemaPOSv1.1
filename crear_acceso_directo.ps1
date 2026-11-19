$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut([Environment]::GetFolderPath("Desktop") + "\Sistema POS.lnk")
$Shortcut.TargetPath = "C:\Users\Hp\Documents\Proyectos\Sistema POS v1.1\.venv\Scripts\python.exe"
$Shortcut.Arguments = "C:\Users\Hp\Documents\Proyectos\Sistema POS v1.1\desktop.py"
$Shortcut.WorkingDirectory = "C:\Users\Hp\Documents\Proyectos\Sistema POS v1.1"
$Shortcut.Description = "Sistema POS v1.0 — Escritorio"
$Shortcut.WindowStyle = 1
$Shortcut.Save()
Write-Host "Acceso directo creado en el Escritorio: Sistema POS.lnk"
