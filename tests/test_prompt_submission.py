import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chatgpt_image_pipeline as pipeline


class FakePromptBox(object):
    def __init__(self, persist=True):
        self.value = ""
        self.persist = persist

    def click(self, **_kwargs):
        return None

    def fill(self, text, **_kwargs):
        if self.persist:
            self.value = text

    def press(self, _key):
        return None

    def type(self, text, **_kwargs):
        if self.persist:
            self.value = text

    def evaluate(self, _script, *args):
        if args:
            if self.persist:
                self.value = args[0]
            return None
        return self.value


class FakeButton(object):
    def __init__(self):
        self.clicked = False

    def is_enabled(self, **_kwargs):
        return True

    def click(self, **_kwargs):
        self.clicked = True


class PromptSubmissionTests(unittest.TestCase):
    def test_safe_fill_returns_only_after_text_persists(self):
        box = FakePromptBox(persist=True)
        with patch.object(pipeline, "wait_for_prompt_box", return_value=box), patch.object(
            pipeline.time, "sleep", return_value=None
        ):
            result = pipeline.safe_fill(object(), "生成图片 {{count}}")
        self.assertIs(result, box)
        self.assertEqual(box.value, "生成图片 {{count}}")

    def test_safe_fill_stops_when_composer_drops_text(self):
        box = FakePromptBox(persist=False)
        with patch.object(pipeline, "wait_for_prompt_box", return_value=box), patch.object(
            pipeline.time, "sleep", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "已停止发送"):
                pipeline.safe_fill(object(), "不能丢失的提示词")

    def test_submit_blocks_attachment_only_message(self):
        box = FakePromptBox(persist=False)
        button = FakeButton()
        args = SimpleNamespace(send_timeout=2, auto_continue=True)
        with patch.object(pipeline, "find_first_visible", return_value=button), patch.object(
            pipeline, "wait_for_prompt_box", return_value=box
        ):
            with self.assertRaisesRegex(RuntimeError, "已阻止仅发送图片"):
                pipeline.submit_prompt(object(), box, args, "完整提示词")
        self.assertFalse(button.clicked)


if __name__ == "__main__":
    unittest.main()
