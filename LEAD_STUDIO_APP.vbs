' Starts the packaged Lead Studio (the LeadStudioApp folder) without a console window.
'
' It points the app at the data folder this copy already uses, so your leads, offers,
' settings and the new video files stay where they are.
'
' Why a folder build and not a single .exe: this machine runs AVG and ESET, and both
' block PyInstaller's single-file packages when they unpack themselves into %TEMP%
' ("could not create temporary directory", "failed to start embedded python interpreter").
' The folder build needs no unpacking, so it starts normally.

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
exe = fso.BuildPath(fso.BuildPath(base, "LeadStudioApp"), "LeadStudioApp.exe")

If Not fso.FileExists(exe) Then
  MsgBox "LeadStudioApp is missing." & vbCrLf & vbCrLf & _
         "Expected: " & exe & vbCrLf & _
         "Rebuild it with BUILD_EXE.bat, or start from source with START_LEAD_STUDIO.bat.", _
         vbExclamation, "Lead Studio"
  WScript.Quit 1
End If

sh.Environment("PROCESS")("LS_DATA") = fso.BuildPath(base, "data")
sh.Run """" & exe & """", 0, False
