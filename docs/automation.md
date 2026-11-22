# Watch, alerts and the service

The commands that keep running: a live terminal view (`watch`), the alerts it recorded (`events`),
Telegram alerts for industry jobs (`alerts`), and one service that runs every scheduled job (`service`, `run`).

## `watch` — live terminal dashboard

```bash
neworks skills --watch          # refresh every 5 minutes (training + your market orders)
neworks skills --watch 1        # every minute (minimum 1)
neworks skills --watch 10 --notify
neworks skills --watch --no-orders   # training only: leave the order books alone
neworks skills --watch --no-colonies   # leave the planets alone too
neworks skills --watch --ledger # also keep the accounting ledger synced (hourly)
neworks watch                   # the same loop as skills --watch, every 5 minutes (--every MIN)
```

The loop polls the plugins' **watch sources**: `skills` (training, from the characters plugin), `orders`
(the core's own order books), `colonies` (planetary) and `ledger` (with `--ledger`). `skills --watch` and
`watch` poll every source the flags leave on. A disabled plugin's source is simply not polled, and `plugins` lists each plugin's
sources.

By default watch draws a compact status table for all stored characters — clone state, queue
length with finished-item count, what is training now and its time left (or `blocked (no
schedule)`), total SP, and fetch freshness. It redraws on the timer (clearing the screen only
when stdout is a TTY — on a Windows console only after virtual-terminal processing has been switched
on, with a plain rule line as the fallback for one that refuses it; see
[Platform support](install.md#platform-support)). `--full` keeps the full `skills` view instead. Ctrl-C stops it
and exits 130.

A character whose fetch fails keeps its last-known row marked `<age> stale`, with the error
printed under the table — healthy data is never dropped because one sibling went quiet; a
character never fetched shows `no data yet`. A cycle where no character data could be fetched at
all prints `warning: no character data this cycle - retrying next poll` and keeps the previous
training state, so a transient ESI outage cannot fake a "finished" event.

Alerts fire **exactly once**: every transition a watcher witnesses — training finished, queue
emptied, an order filled, expired or cancelled — is claimed against persisted state under a lock,
so each event is announced once across polls, restarts and concurrent watchers — never repeated
per poll. Announcements ring the terminal bell; `--notify` additionally calls `notify-send` when that
binary exists, which in practice means Linux — Windows ships nothing by that name, so the watch prints
one explanation per run rather than going quiet, and the bell, the printed line and the recorded event
are unchanged. A missing, hanging or failing notify-send never kills an overnight watch. The history
lives in the state directory (`$XDG_STATE_HOME/new-eden-works`, `%LOCALAPPDATA%\new-eden-works\state` on
Windows) and survives restarts — read it with
[events](#events--recorded-watch-history). There is no mail/push/webhook delivery.

`skills --watch` polls the order books too — the run you already leave open overnight is the one that
should notice a fill — and `--no-orders` opts out. Order state lives beside the training state, keyed
by **owner** (`char:<character_id>`, or `corp:<corporation_id>` for a corporation book), because the
same corporation can be watched through any colleague's token. Two rules follow from that:

- **The first poll of an owner announces nothing.** ESI's ~90-day order history is ingested silently,
  so starting a watch does not shout months of old closures as if they had just happened. They are
  still recorded, and `events` marks them `[history]`.
- **A fill, an expiry or a cancellation happens once, ever.** Ids already settled are remembered, so a
  history row surfacing in a later poll — or a second watcher on the same character — adds neither a
  row nor noise. An order that leaves the live book without a matching history row waits two days for
  ESI's backlog to catch up, then is recorded as `order_closed`; if it reappears meanwhile it was a
  cache flicker and nothing is said. A cycle whose history fetch failed freezes conclusions the same
  way: the open book is refreshed and a disappearance still starts its wait, but nothing is declared
  closed on a cycle that could not check.

`skills --watch` also polls colonies for every character holding the `planets` consent, and `--no-colonies`
opts out of that the same way. An extraction that ends while the watch is open announces
`Ice colony in Ditalren - extraction of Heavy Water finished (15/cycle, 3 heads)` once, as
`extractor_expired`. Two things make that trustworthy without any in-game marker: `expiry_time` is an
absolute instant, so "it just ended" is a witnessed crossing of the clock rather than a data change, and
reprogramming an extractor forces ESI to recalculate — a pin moved back into the future returns to being
watched quietly and its next expiry is a separate event with its own id. The first sight of a colony never
announces, even for pins that ran out weeks ago. Requests stay inside ESI's stated freshness: both routes
publish `x-client-cache-ttl: 600`, so at the default interval a character with four colonies costs at most
five conditional GETs per ten minutes.

## `events` — recorded watch history

```bash
neworks events                      # most recent 50 events, newest first
neworks events --char Somecharacter --limit 200
neworks events --kind order_filled  # repeatable: one kind per flag
neworks events --owner "corp:98356123"      # one owner's order events, by exact key…
neworks events --owner ledger               # …or by part of its name, case-insensitive
neworks events --kind extractor_expired   # extractions that ended while a watch was open
neworks events --json
neworks events --csv > events.csv
```

A read-only view of the alert history the watchers record: it never fetches and never writes. Kinds
are `training_finished`, `queue_empty`, `order_filled`, `order_expired`, `order_cancelled`,
`order_closed` and `extractor_expired`; `--kind` filters to any subset and composes with the other filters.

`--char` accepts a stored name or id; a bare numeric id also finds events for characters already
logged out. Order events belong to an *owner*, not necessarily to a character — a corporation
announcement deliberately has no `character_id`, because it was read through one colleague's token
but is nobody's personal order — so `--char` cannot reach those rows and `--owner` exists for them:
exact owner key (`char:90000001`, `corp:98000001`) or a case-insensitive fragment of the owner name.
Training events have no owner, so an `--owner` filter never returns one.

Unreadable history lines are skipped with a
`warning: skipped N unreadable event history line(s)` on stderr. When nothing has been recorded,
the text output says so honestly instead of printing an empty table, `--json` prints `[]` and
`--csv` prints just the header row — one header serving both kinds, with empty cells wherever a
training row has no order fields (and vice versa):

```text
id,ts,time_utc,kind,character_id,character_name,skill_id,skill_name,finished_level,finish_date,order_id,owner_key,owner_name,type_id,type_name,is_buy,price,volume_total,volume_remain,filled,region_id,location_id,issued,expires,wallet_division,issued_by,backfill,ts_estimated
```

`backfill=1` means the event was read out of ESI's history rather than witnessed between two polls;
`ts_estimated=1` means ESI could not say when the order closed, so the timestamp is the best bound it
has (never later than the order's own expiry). The text view prints those two as `[history]` and
`[time estimated]` on the affected lines.

## `alerts` — industry job alerts in Telegram

```bash
neworks alerts setup --token-file token.txt   # token from @BotFather; message the bot once first
neworks alerts test "hello"                   # one line to the chat
neworks alerts run --dry-run                  # print what would be sent; send nothing, save nothing
neworks alerts run                            # what the service's alerts job runs every 20 minutes
```

`run` asks each plugin's **alert source** for its news: industry's `jobs` reads every character's
industry jobs, and other plugins can add sources of their own. The core
owns the channel, the `notify` settings, quiet hours and the one reminder clock the sources share
(`notify.json` in the state root; each source keeps its own state beside it). One Telegram message then
lists what is new since the last run:

- **Jobs ending**, once per industry job, when it has at most `job_notice_minutes` (default 20) left or
  has already finished. Until it is delivered, the job is listed again in the reminder, at most every
  `remind_hours` and never in quiet hours. A delivered job leaves the character's job list and is
  dropped without a message; a paused job is not news. Jobs come from each character's own job list,
  which includes the jobs it installed for its corporation.
- **Window summary**, once per finish window, on the first run inside it: the jobs ready to deliver per
  character, the free manufacturing and science slots per character, and the jobs still ending inside
  the window. It is sent only when something is ready or a slot is free, and the jobs it lists count as
  announced. The windows are `industry.jobs.windows` in config.json, else the operation file's
  `planning.finish_windows`; with neither there are no summaries. Slots are read only on that run.
- **Batching:** jobs finish one by one over an hour or two, so a job due for its "jobs ending" line is
  held back while another job of the same site (the operation file's sites; everyone is one site
  without one) ends within `industry.jobs.batch_minutes` (default 45), unless the oldest held job has waited
  `industry.jobs.max_wait_minutes` (default 90). Then the whole site goes out in one message. In quiet hours no
  job news is sent at all; the next window summary covers the night. A failed jobs read skips the
  summary and the batching for that run (the per-job lines work as before).

The message is laid out for a phone: a title with the counts, then short lines. Jobs are counted per
facility, character and activity, with the time left; the products are not listed.

```
EVE: 7 jobs ending

⚙ Y-EXMPL
   Builder One: 4 manufacturing · ready
   Builder Two: 2 manufacturing, 1 copying · in 5–12 min
```

Settings live in config.json, the shared ones in the core's `notify` section and each source's in its
plugin's section; `alerts setup` writes a `notify` section with the defaults. For example:

```json
{
  "notify": {"exclude_locations": ["Z-EXCL"], "exclude_characters": [], "remind_hours": 4,
             "quiet_hours": "00:00-08:00", "timezone": "Europe/London"},
  "industry": {"job_alerts": true, "job_notice_minutes": 20,
               "jobs": {"enabled": true, "windows": null, "batch_minutes": 45, "max_wait_minutes": 90}}
}
```

- `notify`: `exclude_characters`, `exclude_locations` (case-insensitive parts of a station name, e.g.
  `"Z-EXCL"`, for sources that report places), `remind_hours` (default 4; 0 = no reminders), `quiet_hours` (default
  `"00:00-08:00"`; messages then arrive silently, and job news waits for the morning) and `timezone`
  (default `UTC`; an IANA name needs a time zone database, which Windows only has with `pip install
  tzdata`, otherwise use a fixed offset such as `+02:00`, which ignores daylight saving).
- `industry`: `job_alerts` (default `true`), `job_notice_minutes` (default 20; the job runs every 20
  minutes, so a shorter notice can miss straight to "done") and `jobs` (default `{"enabled": true,
  "windows": null, "batch_minutes": 45, "max_wait_minutes": 90}`: the window summary and batching;
  `enabled: false` turns both off).

`doctor` reports a value it cannot use, naming its section.

The bot token and chat id are in `telegram.json` in the config directory, owner-only. The token is read
from a file or stdin, never from the command line, and no error message contains it. State
(`job-alerts.json` and `notify.json`, state directory) is saved only after Telegram accepted the message,
so a failed send is retried by the next run. The [service](#service-and-run--every-scheduled-job-in-one-process)'s
`alerts` job runs `alerts run` every 20 minutes.

## `service` and `run` — every scheduled job in one process

```bash
neworks service status         # each job: plugin, schedule, on/off, last run and exit code, next slot
neworks service                # run every enabled job on its schedule until stopped (the systemd unit)
neworks service unit           # print a systemd user unit for this checkout and Python
neworks run alerts             # run one job now, under its lock
```

Plugins declare their scheduled work as jobs, and `service` runs all of them from one process instead of a
systemd timer each: `alerts` (core; `alerts run` every 20 minutes, up to a minute late) and `dashboard`
(`dashboard serve`, kept running and restarted 30 s after a crash), plus whatever other plugins add. A job is due once per slot
of its period, counted in UTC (a 20-minute job at :00, :20 and :40, an hourly one on the hour); when the
service starts after a slot the job missed, it runs at once. Each run is a subprocess of its own,
`python -m newedenworks run JOB --scheduled`, so a failing or hanging job never stops the others, and the
service logs one line when each starts and finishes (`journalctl --user -u new-eden-works.service`).

A run takes the job's lock, so two runs of one job never overlap, and records its start, end and exit code
in the state root (`service/JOB.json`). With `--scheduled` it skips a slot in which the job already
started, so the service can run next to another scheduler (a cron line or timer calling `run JOB
--scheduled`): whichever gets to a slot first runs it, and the other logs "already ran in this slot".

`dashboard` is off by default (it opens a port). To turn it on, give config.json the server's settings and
the job:

```json
"service": {"jobs": {"dashboard": true}},
"dashboard": {"host": "0.0.0.0", "port": 8765, "refresh_minutes": 60, "since": "2026-09-19"}
```

The `dashboard` keys are `dashboard serve`'s options: `host`, `port`, `refresh_minutes`,
`orders_refresh_minutes`, `since`, `orders_char`, and `no_sync`, `no_live` and `verbose` as true or false,
plus `pages` (see `dashboard`).
`service.jobs` turns any job on or off the same way (`"alerts": false`); `doctor` reports a name no plugin
declares. The unit is `contrib/systemd/new-eden-works.service`, or `service unit` prints one filled in:

```bash
neworks service unit > ~/.config/systemd/user/new-eden-works.service
loginctl enable-linger "$USER"     # start at boot without a login session (once)
systemctl --user daemon-reload && systemctl --user enable --now new-eden-works.service
journalctl --user -u new-eden-works.service -f     # one line per job start and finish
systemctl --user restart new-eden-works.service    # after an upgrade
```
