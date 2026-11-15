' Starts Lead Studio Lite without a console window.
' Stop it again from Task Manager (pythonw.exe) - or use START_LEAD_STUDIO.bat,
' where closing the window stops the app.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Run "pythonw """ & base & "\leadstudio.py""", 0, False
