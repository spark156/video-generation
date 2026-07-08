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


class MissingLocator(object):
    @property
    def first(self):
        return self

    def count(self):
        return 0


class CandidatePage(object):
    url = "https://chatgpt.com/c/test-conversation"

    def __init__(self, image_infos=None):
        self.image_infos = image_infos or []
        self.reload_count = 0

    def evaluate(self, _script):
        return self.image_infos

    def locator(self, _selector):
        return MissingLocator()

    def reload(self, **_kwargs):
        self.reload_count += 1


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

    def test_refresh_reloads_same_conversation(self):
        page = CandidatePage()
        with patch.object(pipeline, "wait_for_prompt_box", return_value=FakePromptBox()), patch.object(
            pipeline.time, "sleep", return_value=None
        ):
            self.assertTrue(pipeline.refresh_chatgpt_conversation(page))
        self.assertEqual(page.reload_count, 1)

    def test_finished_generation_accepts_stable_partial_results(self):
        page = CandidatePage(
            [{
                "src": "https://example.test/generated.png",
                "role": "assistant",
                "width": 1024,
                "height": 1024,
                "clientWidth": 512,
                "clientHeight": 512,
                "alt": "generated image",
            }]
        )
        args = SimpleNamespace(
            generation_timeout=10,
            min_size=512,
            expected_candidates=4,
            candidate_settle_seconds=0,
            generation_refresh_interval=0,
            max_generation_refreshes=0,
            poll_interval=0,
            allow_partial_candidates=False,
        )
        results = pipeline.wait_for_candidate_images(page, set(), args)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
