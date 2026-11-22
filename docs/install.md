# Installing New Eden Works

Python 3.11 or newer, standard library only — no runtime dependencies and nothing to compile. Linux
and Windows are both supported; [Platform support](#platform-support) says exactly what differs.

On Linux, install it as the Python package it is (below). On Windows you can do the same, or skip
Python entirely with the [standalone executable](#windows-standalone-executable).

## With uv

[uv](https://docs.astral.sh/uv/) is the shortest path, and it needs no pre-existing virtualenv.

Try it with nothing installed:

```bash
git clone https://github.com/kirilan/new-eden-works.git
cd new-eden-works
uv run neworks --version                                  # -> neworks 0.2.0; no install step
uv run neworks system Jita                                # public data, before any login
uv run --with setuptools python -m unittest discover -s tests -t . -q   # the whole suite
```

`--with setuptools` is not decoration: the packaging tier builds a real wheel and sdist to inspect,
so without a build backend its two build-and-install classes skip their tests.

Keep a project environment in sync with the lockfile — this is the install and update path:

```bash
uv sync                            # creates .venv and installs the project (and updates it later)
uv sync --extra release            # …plus the build/twine tooling RELEASE.md uses
uv run neworks chars            # runs against that environment
```

Install it as a tool on your `PATH` for daily use:

```bash
uv tool install .                                                # from this checkout
uv tool install git+https://github.com/kirilan/new-eden-works.git    # …or straight from GitHub
uv tool list                                                         # -> new-eden-works v0.2.0
uv tool uninstall new-eden-works                                     # …and take it off again
```

The console scripts `neworks` and `new-eden-works` land in uv's own tool directory (`~/.local/bin`
on Linux); if the shell cannot find `neworks` afterwards, `uv tool update-shell` puts that directory on `PATH`.

Development means an editable install inside a uv-managed virtualenv:

```bash
uv venv                         # -> .venv (uv found CPython 3.14.4 here)
uv pip install -e .
```

**Read that one before running it.** `uv pip install` installs into the **active** `VIRTUAL_ENV` when
one is set — not necessarily the `.venv` beside you — so an editable install can quietly land in
whichever environment your shell happens to have activated, leaving this checkout uninstalled. Pin the
target explicitly, or work from a shell with nothing active:

```bash
uv pip install -e . --python .venv    # always this directory's .venv, whatever else is active
echo "$VIRTUAL_ENV"                   # empty output = nothing active (PowerShell: echo $env:VIRTUAL_ENV)
```

## Without uv

A stdlib virtualenv needs no extra tooling. POSIX shells:

```bash
git clone https://github.com/kirilan/new-eden-works.git
cd new-eden-works
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/neworks --version
```

PowerShell — the interpreter is `python`, and console scripts are `.exe` files under `Scripts`:

```powershell
git clone https://github.com/kirilan/new-eden-works.git
cd new-eden-works
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\neworks.exe --version
```

## Windows standalone executable

Each release carries `new-eden-works-<version>-windows-x64.zip` on the
[releases page](https://github.com/kirilan/new-eden-works/releases), next to a `.sha256` file with its checksum.
Inside are `neworks.exe`, `LICENSE` and a short `README.txt`. The executable is the same code as the
package, frozen with [PyInstaller](https://pyinstaller.org) together with its own Python and the bundled
SDE snapshot, so nothing else needs installing: every command, flag and output format in this documentation
applies unchanged.

```powershell
# after extracting the zip to %LOCALAPPDATA%\Programs\new-eden-works
$dir = "$env:LOCALAPPDATA\Programs\new-eden-works"
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$dir", "User")
# open a new terminal, then:
neworks --version
```

What to expect:

- **It is a console program.** Run it from PowerShell or Windows Terminal; double-clicking it in
  Explorer only shows a note saying so and waits for Enter.
- **Your data does not live next to it.** Logins, config, caches and `update-data` downloads go to
  `%LOCALAPPDATA%\new-eden-works`, exactly as with the Python package — so upgrading is replacing the
  `.exe`, and switching between the executable and a pip install keeps every login.
- **SmartScreen may warn on first run.** The executable is not code-signed, so Windows can show
  "Windows protected your PC"; *More info → Run anyway*. Compare the zip against its `.sha256` if you
  want to be sure it is the file the release workflow built.
- **Each start unpacks itself** to a temporary folder first, so a command takes a little longer to
  begin than under a Python install — more while an antivirus scans the unpacked files. Irrelevant for
  `--watch` and `dashboard serve`; if you script hundreds of calls, the Python package is the better fit.

There is no Linux build of this kind on purpose: there the package installs with tools every
distribution already has.

## No install at all

The package is plain stdlib Python, so from the checkout you can skip installation entirely and run
the module — the same `main()` the console script calls:

```bash
python -m newedenworks skills --json
```

Everything below assumes `neworks` is on `PATH`; substitute `python -m newedenworks` if it is not.
`new-eden-works` is the same command under its full name (scripts, service units and MCP configs use it).

## Building a release

To build a wheel or source distribution instead of installing from the checkout — and for the
version-bump, artifact-inspection, clean-install and tag procedure — see
[RELEASE.md](../RELEASE.md). Everything shipped here is GPL-3.0-only; the full text is in
[LICENSE](../LICENSE) and travels inside both artifacts.

## Platform support

Linux and Windows are both supported: Python 3.11+, standard library only, no compiled dependency and
no platform-specific package. Commands, flags, output formats and exit codes are identical. Four things
really do differ, and each is handled rather than papered over — with a last note on how much of any of
it has actually been run.

**Locking.** A long-running `--watch` and a manual command can touch the same files, so every
read-modify-write holds an advisory lock — on POSIX with `fcntl.flock` (whole-file, blocking, dropped
by the kernel when the descriptor closes or the holder dies), on Windows with `msvcrt.locking` over
the first byte of that same lock file (`LK_NBLCK` is refused rather than queued, so it is polled every
20 ms, backing off to 250 ms until granted, then released with `LK_UNLCK`; the OS drops it when the
handle closes). The durability guarantees are the same on both: one writer at a time, nothing wedged
by a crashed watcher, and every durable write still a unique temporary plus an atomic rename — a crash
leaves the old file or the new one, never a mix. Two Windows details follow from its own contracts: a
rename fails outright while another process still has the destination open, so `os.replace` is retried
8 times across roughly 1.05 s before the original error propagates; and temporaries are opened
`O_BINARY`, because a text-mode handle would turn every `\n` written to `events.jsonl` into `\r\n`, and
its readers count lines. The lock is held on a zero-length sentinel file that nobody reads or writes —
precisely so the one real difference between the mechanisms (an `msvcrt` byte-range lock denies other
processes access to the locked region, while `flock` stays advisory even for its own file) can never
reach your data.

**File permissions.** On POSIX secret files are created `0600`, and `doctor` checks directories for a
group/other write bit. Windows has no such bits: a new file inherits the ACL of its directory, which
is normally your private user profile — that inheritance is what protects `tokens.json` there, not a
mode. So on Windows `doctor` reports `skip` for exactly the checks it cannot make (`path.config`,
`path.cache`, `path.data`, `path.state`, `config.file`, `permissions.tokens`) and says why, instead of
warning about the mode 666 every Windows `stat()` reports and offering a `chmod` that does not exist. A
skip is never a blocker: skips do not change the exit code, and everything else — including a corrupt
token store — still fails or warns as usual. To look at privacy yourself, use the folder's Security
properties; there is nothing in this tool to configure.

**Desktop notifications.** `--notify` shells out to `notify-send`, which no stock Windows installation
ships: treat it as a Linux feature. Everything else happens everywhere — an alert rings the terminal
bell, is printed, and is recorded in the event history either way. And rather than going quiet after a
flag that promised a ping, the watch says once per run:
`warning: desktop notifications need notify-send; events are still printed and recorded`.

**Watch redraw.** The dashboard clears the screen between frames when stdout is a TTY. A Windows
console ignores the escape sequence unless virtual-terminal processing is switched on, so the watcher
turns it on once per run; a console that refuses gets a plain 72-character rule line instead of escape
codes, with the timestamped header still separating the frames.

**How much of this has been run.** Every Windows branch — the `msvcrt` backend, profile-folder path
resolution, doctor's skips and its cmd-shaped hints, the virtual-terminal fallback — is covered by tests
that *force* the platform (a fake `msvcrt`, an injected platform judgement), and the whole suite also
runs on a real `windows-latest` runner in CI, where the [standalone executable](#windows-standalone-executable)
is built and smoke-tested too. What no runner covers is a desktop: double-clicking the `.exe`,
SmartScreen, roaming profiles, an antivirus holding files open. See
[Development](development.md) for how it is tested.
