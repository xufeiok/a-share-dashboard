Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "E:\Quant\A股看板系统\A股看板系统"
WshShell.Run "python server.py", 0, False