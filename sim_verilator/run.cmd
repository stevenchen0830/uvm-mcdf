@echo off
rem Run the Verilator flow from Windows through WSL. In PowerShell type .\run.cmd, in cmd.exe run.cmd:
rem     .\run.cmd                    compile + run case0
rem     .\run.cmd regress            run case0 case1 case2
rem     .\run.cmd TESTNAME=case2 DUMP=1
rem     .\run.cmd COV=1 regress cov
rem It uses the default WSL distribution (see "wsl -l -v").
wsl.exe --cd "%~dp0." bash ./run.sh %*
