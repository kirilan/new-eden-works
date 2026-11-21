New Eden Works for Windows
==========================

A command line tool for EVE Online: characters and skills, industry jobs and planning, planetary
industry, an accounting ledger and a dashboard. This is the standalone build: neworks.exe carries its own
Python, so nothing else needs to be installed.

Full documentation: https://github.com/kirilan/new-eden-works#readme


1. Put it somewhere permanent
-----------------------------

Extract this zip into a folder that will stay, for example

    %LOCALAPPDATA%\Programs\new-eden-works

To be able to type just `neworks` in any terminal, add that folder to your user PATH once.
In PowerShell:

    $dir = "$env:LOCALAPPDATA\Programs\new-eden-works"
    $path = [Environment]::GetEnvironmentVariable("Path", "User")
    [Environment]::SetEnvironmentVariable("Path", "$path;$dir", "User")

then close and reopen the terminal. Without that step, run it as .\neworks.exe from inside
the folder.


2. Run it from a terminal
-------------------------

neworks is a command line program: double-clicking it only shows a short note. Open
PowerShell or Windows Terminal and try:

    neworks --version
    neworks system Jita                        (public data, no login needed)
    neworks --help


3. Log in a character
---------------------

    neworks update-data                     (game data, ~100 MB, once per game patch)
    neworks login --client-id <your-client-id> --scopes all

Getting a client id (a free EVE developer application) is explained in the README's quick start:
https://github.com/kirilan/new-eden-works#quick-start


Where it keeps its files
------------------------

Logins, settings, caches and the game data live under %LOCALAPPDATA%\new-eden-works, not next to
the .exe. Upgrading means replacing neworks.exe; nothing you set up is lost.


Windows SmartScreen / antivirus
-------------------------------

The executable is not code-signed, so on first run Windows may say "Windows protected your PC".
Choose "More info" and then "Run anyway". The SHA-256 checksum of every release zip is published
next to it on the GitHub release page, and the build itself runs in the open on GitHub Actions
(.github/workflows/release-windows.yml).


License: GPL-3.0-only, see LICENSE.
