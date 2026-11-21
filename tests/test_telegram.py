"""The Telegram channel the core's alerts use: message splitting, sending, errors without the token,
and finding the chat. No network."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from newedenworks import telegram


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False



class TelegramTests(unittest.TestCase):
    TOKEN = "123:SECRET"

    def opener(self, doc=None, error=None):
        calls = []

        def open_(req, timeout=None):
            calls.append((req.full_url, json.loads(req.data)))
            if error:
                raise error
            return _Response(json.dumps(doc if doc is not None else {"ok": True, "result": {}}).encode())
        return open_, calls

    def test_long_text_is_split_on_lines(self):
        text = "\n".join(f"line {i:04d} " + "x" * 90 for i in range(100))
        parts = telegram.chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= telegram.MAX_MESSAGE for p in parts))
        self.assertEqual(text, "\n".join(parts))

    def test_send_posts_each_part_with_the_silent_flag(self):
        opener, calls = self.opener()
        self.assertEqual(1, telegram.send(self.TOKEN, 42, "hello", silent=True, opener=opener))
        url, body = calls[0]
        self.assertTrue(url.endswith("/bot123:SECRET/sendMessage"))
        self.assertEqual((42, "hello", True), (body["chat_id"], body["text"], body["disable_notification"]))

    def test_errors_carry_telegrams_reason_never_the_token(self):
        refused = urllib.error.HTTPError("https://api.telegram.org/bot123:SECRET/sendMessage", 400, "Bad",
                                         {}, io.BytesIO(b'{"ok": false, "description": "chat not found"}'))
        opener, _ = self.opener(error=refused)
        with self.assertRaises(telegram.TelegramError) as ctx:
            telegram.send(self.TOKEN, 42, "x", opener=opener)
        self.assertIn("chat not found", str(ctx.exception))
        self.assertNotIn("SECRET", str(ctx.exception))
        opener, _ = self.opener(error=urllib.error.URLError("no route"))
        with self.assertRaises(telegram.TelegramError) as ctx:
            telegram.bot_name(self.TOKEN, opener=opener)
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_the_chat_comes_from_the_message_sent_to_the_bot(self):
        updates = {"ok": True, "result": [
            {"message": {"chat": {"id": 7, "type": "private", "username": "old"}}},
            {"message": {"chat": {"id": 9, "type": "private", "first_name": "Kiri"}}},
            {"message": {"chat": {"id": 7, "type": "private", "username": "old"}}}]}
        opener, _ = self.opener(updates)
        self.assertEqual([7, 9], [c["id"] for c in telegram.private_chats(self.TOKEN, opener=opener)])

