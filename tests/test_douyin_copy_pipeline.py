import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_templates import VIDEO_COPY_PROMPT_TEMPLATE, render_named_prompt
from webui.pipelines import DouyinCopyPipelineAdapter, pipeline_schemas


class DouyinCopyPipelineTests(unittest.TestCase):
    def test_pipeline_schema_declares_single_video_input(self):
        schemas = {item["id"]: item for item in pipeline_schemas()}
        schema = schemas["douyin_copy"]
        self.assertEqual(schema["providers"], ["chatgpt"])
        self.assertEqual(schema["input"]["max_files"], 1)
        self.assertIn(".mp4", schema["input"]["allowed_suffixes"])

    def test_validate_requires_one_video(self):
        adapter = DouyinCopyPipelineAdapter()
        config = {
            "chatgpt_cdp_url": "http://127.0.0.1:9333",
            "copy_prompt_template": "为 {{brand_name}} 写文案",
        }
        self.assertFalse(adapter.validate(config, [str(Path("demo.mp4"))]))
        self.assertTrue(adapter.validate(config, []))
        self.assertTrue(adapter.validate(config, [str(Path("demo.jpg"))]))

    def test_build_command_passes_copy_fields(self):
        adapter = DouyinCopyPipelineAdapter()
        command = adapter.build_command(
            "D:/video_generation",
            {
                "chatgpt_cdp_url": "http://127.0.0.1:9333",
                "copy_brand_name": "禅缘古艺",
                "copy_business_scope": "喜马拉雅艺术品",
                "copy_context": "父亲早些年收回时的留影",
                "copy_prompt_template": "基于 {{video_context}} 为 {{brand_name}} 写文案",
            },
            ["D:/tmp/input.mp4"],
        )
        joined = "\n".join(command)
        self.assertIn("chatgpt_video_copy_pipeline.py", joined)
        self.assertIn("--video", command)
        self.assertIn("--prompt-template", command)
        self.assertIn("禅缘古艺", command)

    def test_named_prompt_rendering(self):
        prompt = render_named_prompt(
            "{{brand_name}}：{{business_scope}} / {{video_context}}",
            {
                "brand_name": "禅缘古艺",
                "business_scope": "东方工艺老物件",
                "video_context": "藏品留影",
            },
        )
        self.assertEqual(prompt, "禅缘古艺：东方工艺老物件 / 藏品留影")

    def test_default_copy_template_encourages_rich_varied_angles(self):
        self.assertIn("欧洲展陈感版", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("父辈旧藏版", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("老钱图录版", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("履历感四件套", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("装逼感：10/10", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("工艺说明占比：最多20%", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("只输出3篇可直接发布的引流正文", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("每篇正文必须至少融入5个具体细节", VIDEO_COPY_PROMPT_TEMPLATE)

    def test_default_copy_template_forces_visual_period_and_origin_judgment(self):
        self.assertIn("时间、地点、人物、藏品名称、展名", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("如果我没有提供明确履历，按照视频的场景内容进行补充", VIDEO_COPY_PROMPT_TEMPLATE)
        self.assertIn("如果已知线索没有提供真实展名，可以按照视频的内容生成对应中英文展名", VIDEO_COPY_PROMPT_TEMPLATE)

    def test_web_job_endpoint_accepts_single_video_input(self):
        import web_app

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(web_app, "UPLOAD_DIR", Path(tmp_dir)), patch.object(
            web_app, "local_cdp_status", return_value={"online": True, "message": "ok"}
        ), patch.object(web_app.job_manager, "check_runtime", return_value={"ok": True}), patch.object(
            web_app.job_manager,
            "create_job",
            return_value={"id": "job-1", "pipeline_id": "douyin_copy", "status": "queued"},
        ) as create_job:
            response = web_app.app.test_client().post(
                "/api/jobs",
                data={
                    "pipeline_id": "douyin_copy",
                    "config": json.dumps(
                        {
                            "chatgpt_cdp_url": "http://127.0.0.1:9333",
                            "copy_prompt_template": "给 {{brand_name}} 写文案",
                        },
                        ensure_ascii=False,
                    ),
                    "inputs": (io.BytesIO(b"fake video"), "demo.mp4"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["ok"])
        args = create_job.call_args[0]
        self.assertEqual(args[0], "douyin_copy")
        self.assertEqual(args[3], ["demo.mp4"])


if __name__ == "__main__":
    unittest.main()
