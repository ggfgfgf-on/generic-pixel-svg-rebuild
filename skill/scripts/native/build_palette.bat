@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set PATH=C:\Program Files\LLVM\bin;%PATH%
clang++ -O3 -std=c++17 -shared "%~dp0palette_core.cpp" -o "%~dp0palette_core.dll"
