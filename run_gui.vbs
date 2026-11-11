' ROKauto - Completely silent GUI launcher (no console window)
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
strDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strDir

' Launch pythonw windowless GUI without creating any console window
WshShell.Run "pythonw -m rok_automation --gui", 0, False
