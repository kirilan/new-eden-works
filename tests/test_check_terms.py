"""The private-terms check the repository's CI runs (.github/scripts/check_terms.py)."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "check_terms.py"
_spec = importlib.util.spec_from_file_location("check_terms", SCRIPT)
check_terms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_terms)

TERMS = "[never]\nSecret Corp\n[tip]\n(?<![A-Za-z0-9])XYZ-1(?![A-Za-z0-9])\nordinary\n[ignore]\nordinary\n"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}).stdout


@unittest.skipIf(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, "no git")
class CheckTermsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        git(self.repo, "init", "-q", "-b", "main")
        self.write("README.md", "an ordinary file\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "first")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()

    def write(self, name, text):
        Path(self.repo, name).write_text(text, encoding="utf-8")

    def run_check(self, terms=TERMS, base=None):
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.dict(os.environ, {"PRIVATE_TERMS": terms}), redirect_stdout(out):
                code = check_terms.main([base] if base else [])
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def test_without_the_secret_the_check_is_skipped(self):
        code, out = self.run_check(terms="")
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)

    def test_a_clean_tree_passes_and_an_ignored_term_never_counts(self):
        self.assertEqual(self.run_check()[0], 0)

    def test_a_term_in_a_file_is_found_and_never_printed(self):
        self.write("notes.md", "fine\nbuilt at XYZ-1 today\n")
        git(self.repo, "add", "-A")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("private term at notes.md:2", out)
        self.assertNotIn("XYZ-1", out)

    def test_a_term_in_a_new_commit_message_is_found(self):
        self.write("other.md", "nothing here\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "for secret corp")
        code, out = self.run_check(base=self.base)
        self.assertEqual(code, 1)
        self.assertIn("message:1", out)
        self.assertNotIn("secret corp", out.lower())

    def test_a_term_outside_a_section_is_refused(self):
        code, _ = self.run_check(terms="Secret Corp\n")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
