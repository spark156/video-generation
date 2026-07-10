# ChatGPT 网页图片生成流水线

这是第一步 MVP：用 Python + Playwright 驱动可见的 ChatGPT 网页，基于 1 到 3 张输入图片和修改诉求生成候选图，然后把候选图回传给 ChatGPT 判断是否真实自然、是否有明显 AI 味，并把图片分到 `passed`、`failed`、`needs_review`。

## 边界

- 使用你的 Plus 网页账号，不需要 API key。
- 需要你在打开的 Chrome 窗口里手动登录 ChatGPT。
- 不绕过验证码、风控、隐藏接口或平台限制。
- 默认在关键节点等待你按 Enter，避免网页生成还没完成就误下载或误分类；流程跑顺后可以加 `--auto-continue` 自动等待。
- 默认使用专用 Chrome profile：`profiles/chatgpt_chrome`，不会直接复用你的日常 Chrome 用户目录。

## 安装

当前环境已经检测到 `playwright` 和 `Pillow` 可用。如果换机器运行，可执行：

```powershell
python -m pip install -r requirements.txt
```

如果 Playwright 找不到 Chrome，请先安装 Google Chrome。脚本使用 Playwright 的 `channel="chrome"` 启动本机 Chrome。

## 运行

建议第一次先启动普通 Chrome 的 CDP 模式。这个窗口不会显示“Chrome 正受到自动测试软件的控制”，真人认证和登录都在这个普通 Chrome 里手动完成：

```powershell
python .\chatgpt_image_pipeline.py --launch-debug-chrome
```

在打开的窗口里完成真人认证并登录 ChatGPT。登录成功后不要关闭 Chrome，保持窗口打开，再运行正式流程。

```powershell
python .\chatgpt_image_pipeline.py `
  --cdp-url http://127.0.0.1:9222 `
  --images "C:\path\input1.jpg" "C:\path\input2.jpg" `
  --request "把人物换成真实自然的户外傍晚摄影风格，保留服装轮廓和姿态，背景像真实街拍，不要 AI 味"
```

确认上传、生成、下载、审核都稳定后，可以改成自动继续：

```powershell
python .\chatgpt_image_pipeline.py `
  --cdp-url http://127.0.0.1:9222 `
  --images "C:\path\input1.jpg" "C:\path\input2.jpg" `
  --request "把人物换成真实自然的户外傍晚摄影风格，保留服装轮廓和姿态，背景像真实街拍，不要 AI 味" `
  --auto-continue
```

自动模式会等待上传预览出现、等待发送按钮可用、等待候选图达到预期数量、再等待可解析的评估 JSON。候选图会排除你上传的输入图和 user 消息里的图片，避免把参考图误当成生成图。如果上传控件识别失败、生成超时且不足预期数量，仍会停下来让你手动确认。

## 输出结构

每次运行会生成一个目录：

```text
runs/YYYYMMDD_HHMMSS/
  input/
  candidates/
  passed/
  failed/
  needs_review/
  request.json
  generation_prompt.txt
  evaluation_prompt.txt
  detected_images.json
  evaluation_raw.txt
  evaluation.json
  classification.json
```

`candidates` 保存下载到的原始候选图。`passed`、`failed`、`needs_review` 是按 ChatGPT 评估结果复制出来的分类结果。进入 `passed` 需要同时满足原始诉求、真实摄影风格、自然无明显滤镜感。

## 常用参数

- `--launch-debug-chrome`：启动普通 Chrome，并开放本机 CDP 端口，用于手动真人认证和登录。
- `--cdp-url`：连接已经打开的普通 Chrome，例如 `http://127.0.0.1:9222`。
- `--login-only`：旧登录准备模式。现在更推荐 `--launch-debug-chrome` + `--cdp-url`。
- `--skip-evaluation`：只生成并下载候选图，不回传评估。
- `--auto-continue`：跳过可自动判断的 Enter 确认，自动等待上传、生成图片、发送按钮和评估 JSON。
- `--expected-candidates`：自动模式下期望等到的候选图数量，默认 4。
- `--generation-timeout`：自动模式下等待图片生成的最长秒数，默认 1200。
- `--generation-refresh-interval`：候选图长时间无进展时自动刷新当前 ChatGPT 会话，默认 90 秒；设为 0 关闭。
- `--max-generation-refreshes`：单次生成最多自动刷新次数，默认 5。
- `--candidate-settle-seconds`：生成已结束但图片少于预期时，确认结果稳定后继续的等待秒数，默认 12。
- `--evaluation-timeout`：自动模式下等待评估 JSON 的最长秒数，默认 300。
- `--upload-timeout`：自动模式下等待上传预览出现的最长秒数，默认 240。
- `--upload-settle-seconds`：检测到上传预览后继续等待页面处理的秒数，默认 8。
- `--send-timeout`：等待发送按钮可用的最长秒数，默认 240。
- `--allow-partial-candidates`：生成超时后允许使用不足 4 张候选图继续；默认不允许。
- `--no-submit`：脚本只填提示词，不自动点发送，适合你想先检查提示词。
- `--keep-open`：脚本结束前暂停，保留 Chrome 窗口给你检查；按 Enter 后关闭。
- `--profile-dir`：指定专用 Chrome 登录态目录。
- `--max-candidates`：最多下载多少张候选图，默认 8。

## 已知不稳定点

ChatGPT 网页 UI 可能变化，上传按钮、发送按钮、生成图 DOM 都可能调整。这个脚本做了多组选择器和人工 fallback，但如果页面大改，需要再针对实际 DOM 修一次。

如果你看到“Chrome 正受到自动测试软件的控制”，说明你运行的是默认 Playwright 浏览器，不是推荐的 CDP 普通 Chrome 流程。请关闭该窗口，先运行 `--launch-debug-chrome`，再在正式命令里加 `--cdp-url http://127.0.0.1:9222`。

## Grok 图生视频

第二步脚本是 `grok_video_pipeline.py`，用于把上一阶段 `passed` 目录里的图片送到 Grok Imagine 生成视频。第一次先启动普通 Chrome 登录 Grok：

```powershell
python .\grok_video_pipeline.py --launch-debug-chrome
```

登录成功后保持窗口打开，再运行：

```powershell
python .\grok_video_pipeline.py `
  --cdp-url http://127.0.0.1:9444 `
  --run-dir "C:\Users\Administrator\Documents\video_generation\runs\YYYYMMDD_HHMMSS" `
  --reuse-current-page `
  --confirm-before-start `
  --no-submit `
  --keep-open
```

第一轮建议保留 `--no-submit`，确认 Grok 页面已正确上传图片并选择 `Video`、`720p`、`10s`、`9:16`。`--no-submit` 是人工生成模式，脚本不会自动点击生成，也不会自动下载视频，避免误抓历史视频。确认稳定后可去掉 `--no-submit`，并加 `--auto-continue`：

```powershell
python .\grok_video_pipeline.py `
  --cdp-url http://127.0.0.1:9444 `
  --run-dir "C:\Users\Administrator\Documents\video_generation\runs\YYYYMMDD_HHMMSS" `
  --reuse-current-page `
  --auto-continue
```

视频会保存到 `video_runs/YYYYMMDD_HHMMSS/videos/`。如果 Grok 页面下载控件或视频地址无法自动识别，脚本会停下来让你手动下载后继续。

如果你手动打开 `https://grok.com/imagine` 时看到的输入框选项和脚本启动时不同，优先使用 `--reuse-current-page`。它会复用你当前已经打开好的 Grok 页面，不会在每张图开始时重新跳转页面。

## ChatGPT 视频生成抖音引流文案

`chatgpt_video_copy_pipeline.py` 用于把成品视频上传到 ChatGPT，并生成「禅缘古艺」直播间的抖音引流文案。默认方向是：先讲视频拍摄来源或父亲早些年收回藏品时的留影，再讲收回经过和缘分，最后自然引流到直播间。

```powershell
python .\chatgpt_video_copy_pipeline.py `
  --cdp-url http://127.0.0.1:9333 `
  --video "C:\path\demo.mp4" `
  --video-context "父亲早些年在外地从一位老朋友处辗转收回这尊藏品，当时留下了这段视频。" `
  --auto-continue
```

常用参数：

- `--video`：输入视频，支持 MP4、WebM、MOV、M4V 等常见格式。
- `--video-context`：视频拍摄来源、藏品故事、收回经过等背景线索；如果不填，模板会要求 ChatGPT 不要硬编具体地点和年份。
- `--brand-name`：直播间名称，默认 `禅缘古艺`。
- `--business-scope`：主营方向，默认 `喜马拉雅艺术品，东方工艺的老物件`。
- `--prompt-template`：自定义文案模板，支持 `{{brand_name}}`、`{{business_scope}}`、`{{video_context}}`。
- `--response-timeout`：等待 ChatGPT 输出文案的最长秒数，默认 600。

输出位于 `copy_runs/YYYYMMDD_HHMMSS/`：

```text
copy_runs/YYYYMMDD_HHMMSS/
  input/
  request.json
  copy_prompt.txt
  copy_response.txt
  copy_result.json
```

## 全流程自动化

确保 ChatGPT 和 Grok 的两个 Chrome 窗口均已启动、登录并保持打开后，可以用 `full_video_pipeline.py` 一次完成生图、审核、筛选、图生视频和下载：

```powershell
python .\full_video_pipeline.py `
  --chatgpt-cdp-url http://127.0.0.1:9222 `
  --grok-cdp-url http://127.0.0.1:9444 `
  --images "C:\path\input.jpg" `
  --request "生成真实自然、无滤镜感的竖版摄影图片" `
  --video-prompt "以图片作为第一帧，镜头缓慢向前推进，人物动作自然"
```

如果你的 ChatGPT Chrome 使用的是 `9333`，把 `--chatgpt-cdp-url` 改成 `http://127.0.0.1:9333`。

总入口默认启用两个阶段的自动继续。第一阶段没有合格图片时不会调用 Grok；成功时视频位于：

```text
runs/YYYYMMDD_HHMMSS/
  passed/
  full_pipeline.json
  grok/YYYYMMDD_HHMMSS/videos/
```

`--video-prompt` 可以省略，此时脚本会根据第一阶段的图片修改诉求自动构建默认视频提示词。

## 网页操作台

运行：

```cmd
start_web_ui.cmd
```

或：

```cmd
python web_app.py --host 127.0.0.1 --port 7860
```

浏览器访问 `http://127.0.0.1:7860`。网页支持：

- 上传 1 到 3 张参考图片并配置两阶段提示词。
- 单独上传成品视频，让 ChatGPT 生成「禅缘古艺」抖音引流文案，并在页面查看结果。
- 检查或启动 ChatGPT、Grok 调试 Chrome。
- 串行任务队列、实时日志、阶段进度和停止任务。
- 历史任务恢复、合格图片预览、视频播放与下载。

请从你自己的普通 CMD 双击或运行 `start_web_ui.cmd`，不要从受限的后台执行环境启动服务。点击“启动全流程”时，网页会先检查 Playwright，并自动启动缺失的 ChatGPT/Grok 调试 Chrome；若浏览器刚启动，完成两个网站登录后再次点击启动。

网页任务状态保存在 `web_data/jobs/`，上传文件保存在 `web_data/uploads/`。新增流水线时，在 `webui/pipelines.py` 中实现并注册新的 `PipelineAdapter`；阶段进度会根据适配器的 `stages` 自动渲染。
