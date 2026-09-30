# Shared desktop code with Windows-first delivery

SwimTunes uses a shared Python desktop application so its interface, audio conversion, and file-transfer behavior can be developed and exercised on macOS, even though the intended user runs Windows 10/11 x64. A Windows CI runner builds the portable Windows app; macOS tests do not substitute for Windows device and playback verification. This favors testability here over a smaller Windows-only rewrite of the PowerShell prototype.
