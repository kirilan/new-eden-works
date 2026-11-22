# Releasing New Eden Works

How to go from a working tree to a tagged, installable release. Everything up to the tag is a
command you can read before you run it; the one automated piece is the Windows executable, which a
pushed tag builds into a *draft* GitHub release (step 7).

Two toolchains work and produce the same artifacts: **uv** or a plain virtualenv with `pip`. Pick one per
run — mixing them is how an install lands in the wrong environment. Proven here with uv 0.12.6 on
Linux: `uv venv`, `uv pip install -e .`, `uv run neworks --version`,
`uv run python -m unittest discover -s tests -t . -q`, `uv build`, and `uv tool install .` /
`uv tool list` / `uv tool uninstall new-eden-works`. Not proven here — documented forms written from
`uv --help`: `uv run --with setuptools …`, `uvx twine check …`, and the `git+https://…` tool install in
step 6. On Windows only pip has been run, and only by CI: `ci.yml` installs the package on a
`windows-latest` runner and `release-windows.yml` builds the executable there; uv never has.

Status: **v0.2.0** is the first New Eden Works release (it was called eve-skills up to v0.1.0), with
the Windows executable attached; Linux users install it from the tag or source. The source lives at
<https://github.com/kirilan/new-eden-works> (remote `origin`, branch `main`); no package index account is
configured, so `twine upload` below stays optional.

## Versioning

SemVer (`MAJOR.MINOR.PATCH`) with one source of truth:

```python
newedenworks/__init__.py -> __version__ = "0.2.0"
```

`pyproject.toml` declares `dynamic = ["version"]` and reads that attribute
(`[tool.setuptools.dynamic]`), so the built metadata, `neworks --version` and
`neworks doctor` can never disagree. Bump **only** that line.

The public surface a bump has to respect is what a user or script observes: command names,
flags, the `--json` documents, the CSV headers, exit codes, and the on-disk layout — the `$XDG_*`
tree on POSIX and the `%LOCALAPPDATA%\new-eden-works` tree on Windows. Moving or renaming a
file in either of them is a breaking change for anyone who backs one up. The documents that live
there are `tokens.json`, `config.json` and `sp-history.jsonl` (config), `watch-state.json` and
`events.jsonl` (state), `endpoints.json`, `names.json`, `types.json` and `quotes.json` (cache), and
the three bundled SDE documents under `newedenworks/data/`. The two newest cache documents —
`types.json`, the type/group/category catalogue `inventory` builds, and `quotes.json`, the order-book
figures behind `inventory --value-at` — are `version`-tagged inside, so a user may delete either to
force a refetch; that is not a breaking change. A release moving or renaming one of them is.

| Change | While `0.y.z` (now) | From `1.0.0` |
|---|---|---|
| Bug fix, no surface change; refreshed bundled SDE snapshot | `PATCH` | `PATCH` |
| New command/flag/JSON key, backwards compatible | `MINOR` | `MINOR` |
| Renamed or removed flag, changed JSON key/type, changed CSV header, new required config | `MINOR` + a written "breaking" note | `MAJOR` |

## 1. Pre-flight

With uv, no environment to make first:

```bash
cd new-eden-works                                              # your checkout
git status --short                    # clean tree apart from what you intend to release
uv run --with setuptools python -m unittest discover -s tests -t . -q   # the whole suite: 469 tests
uv run neworks doctor                                   # version, data freshness, config
```

**Read the skip count before you believe `OK`.** `tests/test_packaging.py` builds real wheel and sdist
and installs them into a throwaway venv, and it *skips that tier* when the interpreter running the
suite has no `setuptools` — which is exactly the state of a uv-managed Python. The project's own
dependencies are empty, so this cannot be fixed by installing the project: add the build backend to the
run with `uv run --with setuptools python -m unittest discover -s tests -t . -q`, or install it into the
interpreter as in the venv route below. A green suite that skipped its packaging tier has not checked
that this tree packages.

With a plain virtualenv:

```bash
cd new-eden-works                      # your checkout
.venv/bin/pip install setuptools   # the packaging tier builds real artifacts; it skips without it
git status --short                 # clean tree apart from what you intend to release
.venv/bin/python -m unittest       # full suite, including the packaging tier
.venv/bin/neworks doctor        # offline checks: version, data freshness, config
```

`doctor` reports which SDE build ships inside `newedenworks/data/` (origin *bundled package*).
A fresh install gets exactly that snapshot, so note the build number in the release notes.

## 2. Bump the version

```bash
V=0.2.0
sed -i "s/^__version__ = .*/__version__ = \"$V\"/" newedenworks/__init__.py
.venv/bin/neworks --version          # must print: neworks $V   (or: uv run neworks --version)
git diff                            # one line, nothing else
```

`sed` has no honest PowerShell twin, so on Windows open the file and change that one line yourself —
the check afterwards is the same (`uv run neworks --version`, or `.venv\Scripts\neworks.exe
--version`).

## 3. Build

With uv, one command and no install step — uv provisions its own isolated build environment, so the
release extra is not needed to build:

```bash
rm -rf dist
uv build                            # -> dist/new_eden_works-$V-py3-none-any.whl + .tar.gz
```

With pip, build tooling is an optional extra — the package itself has no runtime dependencies:

```bash
.venv/bin/pip install -e ".[release]"   # release tooling only; the package itself needs nothing
rm -rf dist build *.egg-info
.venv/bin/python -m build               # -> the same two artifacts
```

No `build`, or no isolated build available? The PEP 517 hooks work with an already-installed
setuptools and need no network. Run them as **two separate interpreters** — calling both from one
process makes setuptools leave the sdist in a stray `bdist_wheel/` directory instead of `dist/`.
A Python 3.14 virtualenv has no setuptools of its own, so use whichever interpreter has it:

```bash
python3 -c 'import setuptools.build_meta as b; b.build_wheel("dist")' > /dev/null
python3 -c 'import setuptools.build_meta as b; b.build_sdist("dist")' > /dev/null
```

Artifacts land in `dist/`, which `.gitignore` excludes — never commit them.

## 4. Inspect the artifacts

```
python3 -m zipfile -l "dist/new_eden_works-$V-py3-none-any.whl"  # every newedenworks/*.py + data/*.json
unzip -p "dist/new_eden_works-$V-py3-none-any.whl" '*/METADATA'  # Version, License-Expression: GPL-3.0-only
unzip -p "dist/new_eden_works-$V-py3-none-any.whl" '*/entry_points.txt'   # neworks, new-eden-works = newedenworks.cli:main
tar tzf "dist/new_eden_works-$V.tar.gz"                          # sources + README.md + LICENSE + data/
twine check "dist/new_eden_works-$V"*                             # metadata renders (optional)
```

Expect in both artifacts: every `newedenworks/*.py` module (the packaging tests derive the list from
the source tree, so compare against `ls newedenworks/*.py`), the three SDE
documents under `newedenworks/data/`, `README.md` and the verbatim `LICENSE`. In the wheel METADATA
expect `License-Expression: GPL-3.0-only`, and no `Requires-Dist` line **without** an
`extra == "release"` marker — an unmarked one means a runtime dependency crept in (the optional
release tooling may appear, marked).

`twine` is optional and needs no install under uv — `uvx twine check "dist/new_eden_works-$V"*` runs it in a
throwaway environment.

## 5. Clean-install smoke test

Install the wheel into a throwaway environment that cannot see this checkout:

```bash
T=$(mktemp -d)
python3 -m venv "$T/venv"
"$T/venv/bin/pip" install --no-index --no-deps "dist/new_eden_works-$V-py3-none-any.whl"
cd "$T"                                        # out of the source tree, so imports cannot leak
HOME="$T/home" XDG_CONFIG_HOME="$T/cfg" XDG_CACHE_HOME="$T/cache" XDG_DATA_HOME="$T/data" \
  "$T/venv/bin/neworks" --version
HOME="$T/home" XDG_CONFIG_HOME="$T/cfg" XDG_CACHE_HOME="$T/cache" XDG_DATA_HOME="$T/data" \
  "$T/venv/bin/python" -m newedenworks --version   # identical output to the console script
HOME="$T/home" XDG_CONFIG_HOME="$T/cfg" XDG_CACHE_HOME="$T/cache" XDG_DATA_HOME="$T/data" \
  "$T/venv/bin/neworks" doctor --json > "$T/doctor.json"   # exit 1 is expected: nobody logged in
python3 - "$T/doctor.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
print("package:", report["versions"]["package"])
for check in report["checks"]:
    if check["name"].startswith("data."):
        print(f'{check["name"]:<20} {check["status"]:<4} {check.get("origin", "")}')
PY
rm -rf "$T"
```

The same pass on Windows, where three details of that block do not transfer. The venv's script
directory is `Scripts`, not `bin`; the console entry point is a real `neworks.exe`; and there is no
`HOME=… XDG_*=… command` prefix form — the variables have to be set in the session first, which works
because `$XDG_*` pins are honoured on Windows too (see [The four
roots](docs/data-and-security.md#the-four-roots)), so this run still tests a throwaway tree rather than the real profile:

```powershell
$V = "0.2.0"
$T = Join-Path $env:TEMP "neworks-smoke-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $T | Out-Null
python -m venv "$T\venv"
& "$T\venv\Scripts\pip.exe" install --no-index --no-deps "dist\new_eden_works-$V-py3-none-any.whl"
cd $T                                                   # out of the source tree, so imports cannot leak
$env:HOME = "$T\home"
$env:XDG_CONFIG_HOME = "$T\cfg"
$env:XDG_CACHE_HOME = "$T\cache"
$env:XDG_DATA_HOME = "$T\data"
& "$T\venv\Scripts\neworks.exe" --version
& "$T\venv\Scripts\python.exe" -m newedenworks --version   # identical output to the console script
& "$T\venv\Scripts\neworks.exe" doctor                # exit 1 expected: nobody logged in
Remove-Item -Recurse -Force $T
```

If you capture `doctor --json` to a file on Windows rather than reading it in the terminal, pick the
encoding deliberately: Windows PowerShell 5.1's `>` writes UTF-16LE, `pwsh` 7 and later write UTF-8, and
the snippet above opens the file as plain text. Reading it in the terminal avoids the question. Note too
that on this platform `doctor` legitimately reports six checks **skipped** — the mode-bit ones — and
`versions.platform` names the OS, which is exactly what a release report should show if you run the
smoke test there.

The two `--version` runs must match, and every `data.*` row must be `ok` — with origin *bundled
package* wherever an origin is shown (`skill_catalog` prints a path instead). Either way the data
came from inside the wheel, not from this working copy. A fresh install legitimately exits 1
overall: no application client id is configured and no character is logged in, and `doctor` calls
both problems. What must not appear is a `data.*` row resolved from this checkout, or missing.

## 6. Tag

```bash
git add newedenworks/__init__.py            # plus any files the release actually changed
git commit -m "Release $V"
git tag -a "v$V" -m "New Eden Works $V"
```

The repository has a remote, so tags and commits publish with:

```bash
git push origin main --follow-tags
```

A package index is *not* configured: `twine upload` needs credentials this project has never
had, and it is only worth doing if you want `pip install new-eden-works` to work without the
GitHub URL.

```bash
twine upload "dist/new_eden_works-$V"*        # only if you also publish to an index
```

Then check the tag itself is what a user would get, from a clean shell and without this checkout on the
path:

```bash
uv tool install "new-eden-works @ git+https://github.com/kirilan/new-eden-works.git@v$V"
neworks --version && neworks doctor          # then: uv tool uninstall new-eden-works
```

That is the only route a user has while no index exists, so it is worth one run per release on each
platform you claim — and on Windows it is the only step here that would show whether the console script
really lands somewhere on `PATH` (`uv tool update-shell` if it does not), since nothing else in this
procedure runs the installed command rather than a venv-relative path.

Never hand-edit `dist/` contents and re-tag: rebuild from the tagged commit
(`git archive` the tag into a clean directory, then repeat steps 3–5) so the artifact matches
the source it claims.

## 7. Windows executable

Linux users get the package; Windows users can also get `new-eden-works-$V-windows-x64.zip` — a single
`neworks.exe` built with PyInstaller (its own Python 3.13, the bundled SDE documents, no
dependencies), plus `LICENSE` and `packaging/windows/README.txt`. Nothing about it is built on your
machine: `.github/workflows/release-windows.yml` does it on a `windows-latest` runner.

**On a tag push** (`v*`, from step 6) the workflow runs the suite, refuses a tag that does not equal
`v` + `newedenworks.__version__`, builds `packaging/windows/neworks.spec`, smoke-tests the `.exe` from
outside the checkout with an empty profile (`--version`, every `data.*` row of `doctor` resolving from the
bundle, a bare run failing for want of a login instead of hanging), and attaches the zip and its
`.sha256` to a **draft** release for the tag with generated notes. Read the draft, add the SDE build
number from step 1, and press *Publish* — until then nobody sees it. Re-running the workflow replaces the
assets on the same release.

**Before tagging**, the same build can be tried from any branch without creating a release:

```bash
gh workflow run release-windows.yml --ref <branch>
gh run watch                                      # then: gh run download <run-id>
```

A pull request that changes `packaging/` or the workflow runs it by itself. Either way the zip is
uploaded as a workflow artifact only. Copy it to a Windows machine, extract, and
`.\neworks.exe doctor` in PowerShell is the one check a runner cannot make: a real user profile and a
real console, double-click included.

**Checking the spec on Linux.** The spec builds on Linux too (a Linux binary, not shipped), which is how
to see a change to it work without Windows:

```bash
uv run --no-project --with pyinstaller==6.22.3 \
  pyinstaller --clean --noconfirm --distpath dist --workpath build/pyinstaller packaging/windows/neworks.spec
dist/neworks --version && dist/neworks doctor     # exit 1 expected with no login
```

Things that differ in a frozen build, should you change code near them: `sys.executable` is the `.exe`,
not an interpreter, so the tool re-invokes itself as `[sys.executable, *args]` when `sys.frozen` is set
(`cmd_dashboard._self_command`), never with `-m newedenworks`; and bundled data is listed in the spec by
hand — `tests/test_packaging.py` fails if it drifts from `pyproject.toml`'s `package-data`.
