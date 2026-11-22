# Moving from eve-skills

New Eden Works was called eve-skills up to 0.1.0. The commands are the same, under the name `neworks`.
Its folders were named `eve-skills`; they keep working, untouched, until you run
[`neworks migrate`](#copying-the-folders-migrate), which copies them to the new names. The
`EVE_SKILLS_*` environment variables are still read; their new names are `NEWORKS_*`.

## Copying the folders: `migrate`

Before New Eden Works the folders were named `eve-skills` (`~/.config/eve-skills`,
`%LOCALAPPDATA%\eve-skills\config`, ...). Each kind keeps resolving to its `eve-skills` folder while
that exists and the `new-eden-works` one does not, so an existing installation works unchanged after
an upgrade. `migrate` moves it over:

```bash
neworks migrate                                   # what would be copied (nothing is written)
systemctl --user stop new-eden-works.service      # and stop any `watch`
neworks migrate --apply                           # copy; from now on the new folders are used
systemctl --user start new-eden-works.service
```

- It copies, never moves: the `eve-skills` folders stay exactly as they were, as a backup nobody reads.
  To go back, stop the service and rename the `new-eden-works` folders away; delete the old ones only once
  you no longer want that.
- Each folder is copied into `<name>.migrating` and renamed into place when complete, and the file count
  is checked, so a command never sees half a folder. A ledger with a write-ahead log is copied through
  SQLite's backup, which carries the log; lock files are left behind.
- `--apply` refuses while a service job holds its lock (the dashboard holds one while it runs).
- Absolute paths you wrote yourself (a git remote, a script, `EVE_SKILLS_OPERATION`) still point at
  the old folders: change them by hand.

## Alert settings: `alerts.json`

Before config.json had the `notify` and `industry` sections ([alerts](automation.md#alerts--industry-job-alerts-in-telegram)),
the same keys sat in one flat `alerts.json` in the config directory. It is still read, unchanged, while
config.json has no `notify` section. `neworks migrate --apply` moves it into the sections, in the new
folder only, refusing if any setting would read differently, and renames the file `alerts.json.migrated`.
