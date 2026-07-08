#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Shared editable prompt templates for the image pipeline."""

GENERATION_PROMPT_TEMPLATE = """你将基于我上传的 1 到 3 张参考图片生成新的图片。

目标：{{request}}

请生成 {{count}} 张候选图，优先真实摄影风格，而不是 AI 概念图。

质量要求：
- 真实自然，像真实相机拍摄或专业商业摄影修片后的照片。
- 保留参考图片中对任务重要的主体、材质、构图关系或身份特征。
- 光线、透视、阴影、纹理、边缘和背景都要合理。
- 不要塑料感皮肤、过度磨皮、过饱和 HDR、夸张电影感、虚假景深、奇怪手指、扭曲文字、水印或伪 logo。
- 如果需求没有明确要求文字，不要在图里生成文字。

输出：直接生成图片，不需要先写长解释。"""


EVALUATION_PROMPT_TEMPLATE = """请作为严格的商业摄影修片总监，评估我刚上传的 {{count}} 张候选图。

原始修改诉求：
{{request}}

请逐张判断：
1. 是否明确满足原始修改诉求，包括主体、动作/姿态、场景、风格、保留/修改要求、禁用项。
2. 是否是自然真实的摄影风格。
3. 是否无明显滤镜感，调色是否自然，像真实摄影后期而不是套了夸张滤镜。
4. 是否存在明显 AI 味：塑料感、过度锐化、过饱和、HDR 过重、奇怪手指/五官/文字、边缘融化、背景逻辑错误、透视/阴影不一致、材质不真实等。

只输出严格 JSON，不要 Markdown，不要解释性段落。格式如下：
{
  "items": [
    {
      "index": 1,
      "pass": true,
      "request_satisfied": true,
      "filter_natural": true,
      "realism_score": 8.5,
      "request_match_score": 8.0,
      "request_check": "一句话说明这张图如何满足或不满足原始诉求",
      "filter_check": "一句话说明是否存在滤镜感、过度调色或不自然色彩",
      "brief_reason": "一句话说明最终判定",
      "issues": ["问题1", "问题2"]
    }
  ]
}

判定规则：
- request_satisfied=true 只给明确满足原始修改诉求的图片；有关键要求缺失、偏题、保留项丢失或禁用项出现，就必须为 false。
- filter_natural=true 只给没有明显滤镜感、调色自然、色彩不过度偏移、不过曝、不灰雾、不梦幻柔焦、不赛博/电影 LUT、不夸张胶片颗粒、不假 HDR 的图片。
- 如果有明显滤镜、过度调色、异常色偏、磨皮柔焦、网红滤镜感、HDR 太重、阴影和高光被压得不真实，filter_natural=false 且 pass=false。
- pass=true 必须同时满足 request_satisfied=true、filter_natural=true、真实摄影风格稳定、无明显 AI 味。
- 只要有明显 AI 味、明显滤镜感、主体严重变形、背景/光影不合理或不符合诉求，就 pass=false。
- 如果图片真实但不满足原始诉求，request_satisfied=false 且 pass=false。
- index 必须按我上传图片的顺序从 1 到 {{count}}。"""


def render_prompt(template, request, count):
    return str(template).replace("{{request}}", str(request)).replace("{{count}}", str(count))
