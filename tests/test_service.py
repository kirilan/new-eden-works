"""Scheduled jobs: `neworks run JOB` and the `neworks service` loop."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from newedenworks import cmd_dashboard, config, registry, service
from newedenworks.plugin import API_VERSION, Job, Plugin, PluginError


def with_jobs(*jobs, name="demo"):
    return registry.Candidate("entry point (test 1.0)", f"{name}:plugin",
                              lambda: lambda: Plugin(name=name, version="1.0", api=API_VERSION, jobs=tuple(jobs)))


def load(*jobs, name="demo"):
    return registry.load([with_jobs(*jobs, name=name)], enabled=None)


class StateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class PeriodTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(service.seconds("20m"), 1200)
        self.assertEqual(service.seconds("1h"), 3600)
        self.assertEqual(service.seconds("1d"), 86400)
        self.assertEqual(service.seconds("90s"), 90)
        self.assertEqual(service.seconds(""), 0)
        for bad in ("20", "0m", "1w", "m", "-5m"):
            with self.assertRaises(ValueError, msg=bad):
                service.seconds(bad)

    def test_slots_are_utc_multiples(self):
        # 2026-11-21T14:47:10Z
        now = 1795272430.0
        self.assertEqual(service._iso(service.slot_start(now, 1200)), "2026-11-21T14:40:00Z")
        self.assertEqual(service._iso(service.slot_start(now, 3600)), "2026-11-21T14:00:00Z")
        self.assertEqual(service._iso(service.slot_start(now, 86400)), "2026-11-21T00:00:00Z")


class RegistryTests(unittest.TestCase):
    def test_jobs_are_listed_with_their_plugin(self):
        loaded = load(Job("a", every="1h", run=lambda ctx: 0))
        self.assertEqual([(j.name, p) for j, p in loaded.jobs() if p == "demo"], [("a", "demo")])
        self.assertEqual(loaded.job("a")[1], "demo")
        self.assertIsNone(loaded.job("missing"))

    def test_bad_schedule_refuses_the_plugin(self):
        for job in (Job("a", every="hourly", run=print), Job("a", every="", run=print),
                    Job("Bad Name", every="1h", run=print), Job("a", every="1h", jitter="soon", run=print)):
            loaded = load(job)
            self.assertIsNone(loaded.plugin("demo"), job)
            self.assertEqual(len(loaded.refused), 1)

    def test_job_name_clash_refuses_the_second(self):
        loaded = registry.load([with_jobs(Job("a", every="1h", run=print), name="one"),
                                with_jobs(Job("a", every="1d", run=print), name="two")], enabled=None)
        self.assertIsNotNone(loaded.plugin("one"))
        self.assertIsNone(loaded.plugin("two"))
        self.assertIn("job a is already declared by one", loaded.refused[0].reason)

    def test_service_is_a_reserved_name(self):
        loaded = registry.load([with_jobs(name="service")], enabled=None)
        self.assertIsNone(loaded.plugin("service"))

    def test_builtin_jobs(self):
        loaded = registry.load(registry.builtin_candidates(), enabled=None)
        self.assertEqual([(j.name, p, j.every, j.default) for j, p in loaded.jobs()],
                         [("dashboard", "dashboard", "daemon", False), ("alerts", "core", "20m", True)])
        alerts = loaded.job("alerts")[0]
        self.assertEqual((alerts.jitter, alerts.run.argv), ("1m", ("alerts", "run")))
        self.assertEqual(loaded.provider(("service",)), "core")
        self.assertEqual(loaded.provider(("run",)), "core")


class EnabledTests(unittest.TestCase):
    def test_config_overrides_default(self):
        on, off = Job("on", every="1h", run=print), Job("off", every="1h", run=print, default=False)
        self.assertTrue(service.enabled(on, {}))
        self.assertFalse(service.enabled(off, {}))
        cfg = {"service": {"jobs": {"on": False, "off": True}}}
        self.assertFalse(service.enabled(on, cfg))
        self.assertTrue(service.enabled(off, cfg))
        self.assertTrue(service.enabled(on, {"service": {"jobs": {"on": "no"}}}), "not a bool: default")

    def test_problems_report_service_section(self):
        loaded = load(Job("a", every="1h", run=print))
        self.assertEqual(config.problems(loaded, {"service": {"jobs": {"a": True}}}), [])
        found = config.problems(loaded, {"service": {"jobs": {"a": "yes", "zz": True}}})
        self.assertIn(("service", "service.jobs.a must be true or false"), found)
        self.assertIn(("service", "service.jobs names zz, which no loaded plugin declares"), found)
        self.assertEqual(len(config.problems(loaded, {"service": []})), 1)


class RunJobTests(StateDir):
    def run_job(self, loaded, name, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = service.run_job(loaded, name, **kw)
        return code, out.getvalue(), err.getvalue()

    def test_runs_and_records_state(self):
        calls = []
        loaded = load(Job("a", every="20m", run=lambda ctx: calls.append(ctx.plugin) or 3))
        code, _, _ = self.run_job(loaded, "a", now=lambda: 1795272430.0)
        self.assertEqual((code, calls), (3, ["demo"]))
        state = service.load_state("a")
        self.assertEqual(state["slot"], "2026-11-21T14:40:00Z")
        self.assertEqual(state["last_start"], "2026-11-21T14:47:10Z")
        self.assertEqual((state["last_rc"], state["runner"]), (3, "manual"))

    def test_scheduled_run_skips_a_slot_already_run(self):
        calls = []
        loaded = load(Job("a", every="20m", run=lambda ctx: calls.append(1)))
        self.assertEqual(self.run_job(loaded, "a", scheduled=True, now=lambda: 1795272430.0)[0], 0)
        code, out, _ = self.run_job(loaded, "a", scheduled=True, now=lambda: 1795272430.0 + 60)
        self.assertEqual((code, len(calls)), (0, 1))
        self.assertIn("already ran in this slot", out)
        # a manual run always runs; the next slot runs again
        self.run_job(loaded, "a", now=lambda: 1795272430.0 + 61)
        self.run_job(loaded, "a", scheduled=True, now=lambda: 1795272430.0 + 800)
        self.assertEqual(len(calls), 3)

    def test_exception_is_exit_one_and_recorded(self):
        def boom(ctx):
            raise ValueError("nope")
        loaded = load(Job("a", every="1h", run=boom))
        code, _, err = self.run_job(loaded, "a")
        self.assertEqual(code, 1)
        self.assertIn("ValueError: nope", err)
        self.assertEqual(service.load_state("a")["last_rc"], 1)

    def test_system_exit_code_is_kept(self):
        def leave(ctx):
            raise SystemExit(2)
        self.assertEqual(self.run_job(load(Job("a", every="1h", run=leave)), "a")[0], 2)

    def test_unknown_job(self):
        code, _, err = self.run_job(load(Job("a", every="1h", run=print)), "b")
        self.assertEqual(code, 2)
        self.assertIn("no job named b (jobs: a", err)

    def test_command_runs_the_cli(self):
        with mock.patch("newedenworks.cli.main", return_value=4) as main:
            run = service.command("prices", "sync", "--quiet")
            self.assertEqual(run(None), 4)
        main.assert_called_once_with(["prices", "sync", "--quiet"])


class FakeProc:
    def __init__(self, argv, script):
        self.argv = argv
        self.polls = list(script)      # poll() results in turn; the last repeats
        self.terminated = False

    def poll(self):
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class ServeTests(StateDir):
    def serve(self, loaded, *, start, ticks, scripts=None, cfg=None):
        """Run the loop for `ticks` iterations of a fake clock that sleep() advances."""
        clock = {"now": start}
        spawned = []
        left = {"n": ticks}

        def popen(argv):
            proc = FakeProc(argv, (scripts or {}).get(argv[4], [0]))
            spawned.append((clock["now"], argv[4], proc))
            return proc

        def sleep(seconds):
            clock["now"] += seconds
            left["n"] -= 1

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = service.serve(loaded, cfg or {}, clock=lambda: clock["now"], sleep=sleep, popen=popen,
                                 rand=lambda a, b: b, stop=lambda: left["n"] <= 0)
        return code, spawned, out.getvalue()

    def test_periodic_jobs_run_at_start_then_each_slot(self):
        loaded = load(Job("a", every="20m", jitter="1m", run=print))
        start = 1795272430.0              # 14:47:10, never run before: runs at once
        code, spawned, log = self.serve(loaded, start=start, ticks=1000)
        self.assertEqual(code, 0)
        times = [service._iso(t)[11:16] for t, _, _ in spawned]
        self.assertEqual(times[:3], ["14:47", "15:01", "15:21"])
        self.assertEqual(spawned[0][2].argv[-3:], ["run", "a", "--scheduled"])
        self.assertIn("service started: a (20m)", log)

    def test_slot_already_run_waits_for_the_next(self):
        loaded = load(Job("a", every="1h", run=print))
        service._save_state("a", {"slot": "2026-11-21T14:00:00Z"})
        _, spawned, _ = self.serve(loaded, start=1795272430.0, ticks=400)
        self.assertEqual(service._iso(spawned[0][0])[11:16], "15:00")

    def test_daemon_restarts_after_failure_only(self):
        loaded = load(Job("web", every=service.DAEMON, run=print))
        _, spawned, log = self.serve(loaded, start=1795272430.0, ticks=30, scripts={"web": [None, 1]})
        self.assertGreaterEqual(len(spawned), 2)
        self.assertGreaterEqual(spawned[1][0] - spawned[0][0], service.RESTART_SECONDS)
        _, spawned, log = self.serve(loaded, start=1795272430.0, ticks=30, scripts={"web": [None, 0]})
        self.assertEqual(len(spawned), 1)
        self.assertIn("exited cleanly; not restarted", log)

    def test_running_job_is_not_started_twice(self):
        loaded = load(Job("a", every="20m", run=print))
        _, spawned, _ = self.serve(loaded, start=1795272430.0, ticks=600, scripts={"a": [None]})
        self.assertEqual(len(spawned), 1)

    def test_disabled_jobs_are_left_out(self):
        loaded = load(Job("a", every="1h", run=print), Job("b", every="1h", run=print, default=False))
        _, spawned, _ = self.serve(loaded, start=1795272430.0, ticks=3)
        self.assertEqual({name for _, name, _ in spawned}, {"a"})
        code, spawned, _ = self.serve(loaded, start=1795272430.0, ticks=3,
                                      cfg={"service": {"jobs": {"a": False}}})
        self.assertEqual((code, spawned), (1, []))

    def test_stop_terminates_children(self):
        loaded = load(Job("web", every=service.DAEMON, run=print))
        _, spawned, log = self.serve(loaded, start=1795272430.0, ticks=2, scripts={"web": [None]})
        self.assertTrue(spawned[0][2].terminated)
        self.assertIn("service stopped", log)


class StatusTests(StateDir):
    def test_status_data(self):
        loaded = load(Job("a", every="1h", run=service.command("x", "y")), Job("b", every="1h", run=print, default=False))
        service._save_state("a", {"slot": "2026-11-21T14:00:00Z", "last_start": "2026-11-21T14:02:00Z",
                                  "last_end": "2026-11-21T14:02:09Z", "last_rc": 0, "runner": "scheduled"})
        with mock.patch.object(config, "service_jobs", return_value={}):
            data = service.status_data(None, mock.Mock(registry=loaded))
        rows = {r["job"]: r for r in data["jobs"]}
        self.assertEqual(rows["a"]["command"], ["x", "y"])
        self.assertEqual(rows["a"]["last_rc"], 0)
        self.assertFalse(rows["b"]["enabled"])
        self.assertIsNone(rows["b"]["next_due"])
        text = service.status_text(data, None)
        self.assertIn("x y", text)
        json.dumps(data)

    def test_unit_names_this_python(self):
        text = service.unit_text()
        self.assertIn("-m newedenworks service", text)
        self.assertIn("Restart=on-failure", text)


class DashboardJobTests(unittest.TestCase):
    def test_serve_argv(self):
        self.assertEqual(cmd_dashboard.serve_argv(None), ["dashboard", "serve"])
        self.assertEqual(cmd_dashboard.serve_argv({"host": "0.0.0.0", "port": 8765, "refresh_minutes": 60,
                                                   "since": "2026-09-19", "no_sync": False, "no_live": True}),
                         ["dashboard", "serve", "--host", "0.0.0.0", "--port", "8765", "--refresh", "60",
                          "--since", "2026-09-19", "--no-live"])

    def test_wrong_settings_name_the_key(self):
        for bad, key in (({"colour": "red"}, "dashboard.colour"), ({"port": True}, "dashboard.port"),
                         ({"no_sync": "yes"}, "dashboard.no_sync")):
            with self.assertRaisesRegex(RuntimeError, key):
                cmd_dashboard.serve_argv(bad)

    def test_doctor_reports_a_wrong_setting(self):
        loaded = load(Job("a", every="1h", run=print))
        self.assertEqual(config.problems(loaded, {"dashboard": {"port": 8765}}), [])
        self.assertEqual(config.problems(loaded, {"dashboard": {"refresh": 60}})[0][0], "dashboard")

    def test_serve_argv_parses(self):
        from newedenworks import cli
        args = cli.build_parser(registry.load(registry.builtin_candidates(), enabled=None)).parse_args(
            cmd_dashboard.serve_argv({"host": "127.0.0.1", "port": 9000, "refresh_minutes": 5,
                                      "orders_refresh_minutes": 0, "orders_char": "X", "verbose": True}))
        self.assertEqual((args.host, args.port, args.refresh, args.orders_refresh, args.orders_char, args.verbose),
                         ("127.0.0.1", 9000, 5, 0, "X", True))


class ValidateTests(unittest.TestCase):
    def test_daemon_and_periods(self):
        service.validate(Job("web", every="daemon", run=print))
        service.validate(Job("a-b2", every="5m", jitter="30s", run=print))
        with self.assertRaises(PluginError):
            service.validate(Job("a", every="0s", run=print))


if __name__ == "__main__":
    unittest.main()
