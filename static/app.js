(() => {
  "use strict";

  const state = {
    defaults: {},
    pipelines: [],
    jobs: [],
    selectedJobId: null,
    selectedFiles: [],
    previewUrls: [],
    logOffset: 0,
    pollTimer: null,
    artifactSignature: "",
    selectedPipelineId: "full_video",
  };

  const elements = {
    form: document.getElementById("jobForm"),
    imageInput: document.getElementById("imageInput"),
    uploadZone: document.getElementById("uploadZone"),
    imagePreviews: document.getElementById("imagePreviews"),
    imageCounter: document.getElementById("imageCounter"),
    requestInput: document.getElementById("requestInput"),
    videoPromptInput: document.getElementById("videoPromptInput"),
    pipelineOptions: document.getElementById("pipelineOptions"),
    generationPromptTemplateInput: document.getElementById("generationPromptTemplateInput"),
    evaluationPromptTemplateInput: document.getElementById("evaluationPromptTemplateInput"),
    resetPromptsButton: document.getElementById("resetPromptsButton"),
    resolutionInput: document.getElementById("resolutionInput"),
    durationInput: document.getElementById("durationInput"),
    aspectRatioInput: document.getElementById("aspectRatioInput"),
    chatgptCdpInput: document.getElementById("chatgptCdpInput"),
    grokCdpInput: document.getElementById("grokCdpInput"),
    candidateCountInput: document.getElementById("candidateCountInput"),
    partialCandidatesInput: document.getElementById("partialCandidatesInput"),
    reuseGrokInput: document.getElementById("reuseGrokInput"),
    startButton: document.getElementById("startButton"),
    startButtonLabel: document.getElementById("startButtonLabel"),
    newJobButton: document.getElementById("newJobButton"),
    jobList: document.getElementById("jobList"),
    chatgptStatus: document.getElementById("chatgptStatus"),
    grokStatus: document.getElementById("grokStatus"),
    refreshStatusButton: document.getElementById("refreshStatusButton"),
    pageSubtitle: document.getElementById("pageSubtitle"),
    runStatus: document.getElementById("runStatus"),
    runMeta: document.getElementById("runMeta"),
    stageTrack: document.getElementById("stageTrack"),
    logOutput: document.getElementById("logOutput"),
    passedGrid: document.getElementById("passedGrid"),
    videoGrid: document.getElementById("videoGrid"),
    passedCount: document.getElementById("passedCount"),
    videoCount: document.getElementById("videoCount"),
    cancelButton: document.getElementById("cancelButton"),
    toast: document.getElementById("toast"),
  };

  const statusLabels = {
    idle: "待启动",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已停止",
    interrupted: "已中断",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = { ok: false, error: `HTTP ${response.status}` };
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function showToast(message, isError = false) {
    elements.toast.textContent = message;
    elements.toast.classList.toggle("error", isError);
    elements.toast.classList.add("visible");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => elements.toast.classList.remove("visible"), 3200);
  }

  function applyDefaults(defaults) {
    elements.chatgptCdpInput.value = defaults.chatgpt_cdp_url || "http://127.0.0.1:9333";
    elements.grokCdpInput.value = defaults.grok_cdp_url || "http://127.0.0.1:9444";
    elements.resolutionInput.value = defaults.resolution || "720p";
    elements.durationInput.value = defaults.duration || "10s";
    elements.aspectRatioInput.value = defaults.aspect_ratio || "9:16";
    elements.candidateCountInput.value = defaults.expected_candidates || 4;
    elements.partialCandidatesInput.checked = Boolean(defaults.allow_partial_candidates);
    elements.reuseGrokInput.checked = Boolean(defaults.reuse_current_grok_page);
    elements.generationPromptTemplateInput.value = defaults.generation_prompt_template || "";
    elements.evaluationPromptTemplateInput.value = defaults.evaluation_prompt_template || "";
  }

  function selectedPipeline() {
    return state.pipelines.find((item) => item.id === state.selectedPipelineId) || null;
  }

  function setPipelineMode(pipelineId) {
    state.selectedPipelineId = pipelineId;
    const pipeline = selectedPipeline();
    const providers = pipeline?.providers || (pipelineId === "video_only" ? ["grok"] : ["chatgpt", "grok"]);
    document.querySelectorAll("[data-requires]").forEach((element) => {
      element.classList.toggle("hidden", !providers.includes(element.dataset.requires));
    });
    const labels = {
      full_video: "启动全流程",
      image_only: "开始生成图片",
      video_only: "开始生成视频",
    };
    elements.startButtonLabel.textContent = labels[pipelineId] || "启动任务";
    elements.requestInput.required = providers.includes("chatgpt");
    renderStages();
  }

  function setBrowserStatus(element, status) {
    element.classList.remove("online", "offline");
    element.classList.add(status.online ? "online" : "offline");
    element.title = status.message || (status.online ? "已连接" : "未连接");
  }

  async function checkBrowserStatus() {
    elements.refreshStatusButton.disabled = true;
    try {
      const payload = await api("/api/browser-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chatgpt_cdp_url: elements.chatgptCdpInput.value,
          grok_cdp_url: elements.grokCdpInput.value,
        }),
      });
      setBrowserStatus(elements.chatgptStatus, payload.chatgpt);
      setBrowserStatus(elements.grokStatus, payload.grok);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      elements.refreshStatusButton.disabled = false;
    }
  }

  async function launchBrowser(provider) {
    const url = provider === "chatgpt" ? elements.chatgptCdpInput.value : elements.grokCdpInput.value;
    try {
      const payload = await api(`/api/browser-launch/${provider}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      showToast(payload.status.online ? "浏览器已连接。" : "浏览器已启动，请完成登录。")
      window.setTimeout(checkBrowserStatus, 1800);
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function clearPreviewUrls() {
    state.previewUrls.forEach((url) => URL.revokeObjectURL(url));
    state.previewUrls = [];
  }

  function renderSelectedFiles() {
    clearPreviewUrls();
    elements.imagePreviews.innerHTML = "";
    state.selectedFiles.forEach((file, index) => {
      const url = URL.createObjectURL(file);
      state.previewUrls.push(url);
      const wrapper = document.createElement("div");
      wrapper.className = "image-preview";
      const image = document.createElement("img");
      image.src = url;
      image.alt = file.name;
      const remove = document.createElement("button");
      remove.className = "remove-image";
      remove.type = "button";
      remove.title = "移除图片";
      remove.setAttribute("aria-label", `移除 ${file.name}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        state.selectedFiles.splice(index, 1);
        renderSelectedFiles();
      });
      wrapper.append(image, remove);
      elements.imagePreviews.appendChild(wrapper);
    });
    elements.imageCounter.textContent = `${state.selectedFiles.length} / 3`;
  }

  function acceptFiles(fileList) {
    const images = Array.from(fileList).filter((file) => file.type.startsWith("image/"));
    if (!images.length) {
      showToast("请选择 PNG、JPG 或 WebP 图片。", true);
      return;
    }
    state.selectedFiles = images.slice(0, 3);
    if (images.length > 3) showToast("最多保留前 3 张图片。", true);
    renderSelectedFiles();
  }

  function formatTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function renderJobList() {
    if (!state.jobs.length) {
      elements.jobList.innerHTML = '<div class="job-list-empty">暂无任务</div>';
      return;
    }
    elements.jobList.innerHTML = state.jobs.map((job) => {
      const title = job.input_names?.[0] || job.pipeline_name || "视频任务";
      const active = job.id === state.selectedJobId ? " active" : "";
      return `
        <button class="job-list-item${active}" type="button" data-job-id="${escapeHtml(job.id)}">
          <span class="job-list-title">
            <strong>${escapeHtml(title)}</strong>
            <span class="mini-status ${escapeHtml(job.status)}"></span>
          </span>
          <span class="job-list-meta">
            <span>${escapeHtml(statusLabels[job.status] || job.status)}</span>
            <span>${escapeHtml(formatTime(job.created_at))}</span>
          </span>
        </button>`;
    }).join("");
    elements.jobList.querySelectorAll("[data-job-id]").forEach((button) => {
      button.addEventListener("click", () => selectJob(button.dataset.jobId));
    });
  }

  async function loadJobs() {
    try {
      const payload = await api("/api/jobs");
      state.jobs = payload.jobs;
      renderJobList();
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function activePipeline(job = null) {
    const pipelineId = job?.pipeline_id || state.selectedPipelineId;
    return state.pipelines.find((item) => item.id === pipelineId) || {
      stages: [
        { id: "preflight", label: "浏览器检查" },
        { id: "chatgpt", label: "生图与审核" },
        { id: "grok", label: "视频生成" },
        { id: "complete", label: "完成" },
      ],
    };
  }

  function renderStages(job = null) {
    const stages = activePipeline(job).stages;
    const currentId = job?.stage || "preflight";
    const currentIndex = stages.findIndex((stage) => stage.id === currentId);
    const isComplete = job?.status === "completed";
    elements.stageTrack.innerHTML = stages.map((stage, index) => {
      const done = isComplete || index < currentIndex;
      const active = !isComplete && index === currentIndex && ["queued", "running"].includes(job?.status);
      return `
        <div class="stage-item${done ? " done" : ""}${active ? " active" : ""}">
          <div class="stage-marker-row"><span class="stage-marker"></span><span class="stage-line"></span></div>
          <span class="stage-label">${escapeHtml(stage.label)}</span>
        </div>`;
    }).join("");
  }

  function setRunStatus(status) {
    const knownStatuses = Object.keys(statusLabels);
    elements.runStatus.classList.remove(...knownStatuses);
    elements.runStatus.classList.add(status || "idle");
    elements.runStatus.textContent = statusLabels[status] || status || statusLabels.idle;
  }

  function mediaUrl(jobId, kind, index, download = false) {
    return `/api/jobs/${encodeURIComponent(jobId)}/media/${kind}/${index}${download ? "?download=1" : ""}`;
  }

  function renderArtifacts(job) {
    const artifacts = job.artifacts || {};
    const passed = artifacts.passed || [];
    const videos = artifacts.videos || [];
    const signature = JSON.stringify([job.id, passed, videos]);
    elements.passedCount.textContent = passed.length;
    elements.videoCount.textContent = videos.length;
    if (signature === state.artifactSignature) return;
    state.artifactSignature = signature;

    elements.passedGrid.innerHTML = passed.length
      ? passed.map((_path, index) => `
          <article class="media-card">
            <img src="${mediaUrl(job.id, "passed", index)}" alt="合格图片 ${index + 1}" loading="lazy">
            <div class="media-card-footer"><span>候选 ${String(index + 1).padStart(2, "0")}</span><a href="${mediaUrl(job.id, "passed", index, true)}">下载</a></div>
          </article>`).join("")
      : '<div class="empty-result">暂无合格图片</div>';

    elements.videoGrid.innerHTML = videos.length
      ? videos.map((_path, index) => `
          <article class="video-card">
            <video src="${mediaUrl(job.id, "videos", index)}" controls preload="metadata"></video>
            <div class="media-card-footer"><span>视频 ${String(index + 1).padStart(2, "0")}</span><a href="${mediaUrl(job.id, "videos", index, true)}">下载</a></div>
          </article>`).join("")
      : '<div class="empty-result">暂无视频</div>';
  }

  function renderJob(job) {
    setRunStatus(job.status);
    const title = job.input_names?.[0] || job.pipeline_name || "视频任务";
    elements.pageSubtitle.textContent = title;
    elements.runMeta.textContent = `${job.id} · ${formatTime(job.created_at)}`;
    elements.cancelButton.classList.toggle("hidden", !["queued", "running"].includes(job.status));
    renderStages(job);
    renderArtifacts(job);
  }

  function renderIdle() {
    state.selectedJobId = null;
    state.logOffset = 0;
    state.artifactSignature = "";
    elements.pageSubtitle.textContent = "新建流水线任务";
    elements.runMeta.textContent = "选择历史任务或启动新任务";
    elements.logOutput.innerHTML = '<span class="log-placeholder">等待任务日志</span>';
    elements.passedGrid.innerHTML = '<div class="empty-result">暂无合格图片</div>';
    elements.videoGrid.innerHTML = '<div class="empty-result">暂无视频</div>';
    elements.passedCount.textContent = "0";
    elements.videoCount.textContent = "0";
    elements.cancelButton.classList.add("hidden");
    setRunStatus("idle");
    renderStages();
    renderJobList();
  }

  async function selectJob(jobId) {
    state.selectedJobId = jobId;
    state.logOffset = 0;
    state.artifactSignature = "";
    elements.logOutput.textContent = "";
    renderJobList();
    await pollSelectedJob();
  }

  async function pollSelectedJob() {
    const jobId = state.selectedJobId;
    if (!jobId) return;
    try {
      const [jobPayload, logPayload] = await Promise.all([
        api(`/api/jobs/${encodeURIComponent(jobId)}`),
        api(`/api/jobs/${encodeURIComponent(jobId)}/log?offset=${state.logOffset}`),
      ]);
      if (state.selectedJobId !== jobId) return;
      renderJob(jobPayload.job);
      if (logPayload.text) {
        elements.logOutput.textContent += logPayload.text;
        elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
      }
      state.logOffset = logPayload.next_offset;
      const index = state.jobs.findIndex((item) => item.id === jobId);
      if (index >= 0) state.jobs[index] = jobPayload.job;
      else state.jobs.unshift(jobPayload.job);
      renderJobList();
    } catch (error) {
      showToast(error.message, true);
    }
  }

  async function submitJob(event) {
    event.preventDefault();
    if (!state.selectedFiles.length) {
      showToast("请先选择参考图片。", true);
      return;
    }
    const pipeline = selectedPipeline();
    const providers = pipeline?.providers || ["chatgpt", "grok"];
    if (providers.includes("chatgpt") && !elements.requestInput.value.trim()) {
      showToast("请填写图片生成诉求。", true);
      elements.requestInput.focus();
      return;
    }

    const config = {
      request: elements.requestInput.value.trim(),
      video_prompt: elements.videoPromptInput.value.trim(),
      chatgpt_cdp_url: elements.chatgptCdpInput.value.trim(),
      grok_cdp_url: elements.grokCdpInput.value.trim(),
      resolution: elements.resolutionInput.value,
      duration: elements.durationInput.value,
      aspect_ratio: elements.aspectRatioInput.value,
      expected_candidates: Number(elements.candidateCountInput.value || 4),
      max_candidates: Number(elements.candidateCountInput.value || 4),
      allow_partial_candidates: elements.partialCandidatesInput.checked,
      reuse_current_grok_page: elements.reuseGrokInput.checked,
      generation_prompt_template: elements.generationPromptTemplateInput.value.trim(),
      evaluation_prompt_template: elements.evaluationPromptTemplateInput.value.trim(),
    };
    elements.startButton.disabled = true;
    elements.startButtonLabel.textContent = "前置检查中";
    try {
      const preflight = await api("/api/preflight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pipeline_id: state.selectedPipelineId,
          chatgpt_cdp_url: config.chatgpt_cdp_url,
          grok_cdp_url: config.grok_cdp_url,
        }),
      });
      if (providers.includes("chatgpt")) setBrowserStatus(elements.chatgptStatus, preflight.chatgpt);
      if (providers.includes("grok")) setBrowserStatus(elements.grokStatus, preflight.grok);
      if (!preflight.ready) {
        showToast(preflight.message, true);
        return;
      }

      elements.startButtonLabel.textContent = "正在创建";
      const formData = new FormData();
      formData.append("pipeline_id", state.selectedPipelineId);
      formData.append("config", JSON.stringify(config));
      state.selectedFiles.forEach((file) => formData.append("images", file, file.name));
      const payload = await api("/api/jobs", { method: "POST", body: formData });
      state.jobs.unshift(payload.job);
      showToast("任务已进入队列。")
      await selectJob(payload.job.id);
      await checkBrowserStatus();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      elements.startButton.disabled = false;
      setPipelineMode(state.selectedPipelineId);
    }
  }

  async function cancelSelectedJob() {
    if (!state.selectedJobId) return;
    try {
      const payload = await api(`/api/jobs/${encodeURIComponent(state.selectedJobId)}/cancel`, { method: "POST" });
      renderJob(payload.job);
      showToast("任务已停止。")
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function setupTabs() {
    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".tab-button").forEach((item) => item.classList.toggle("active", item === button));
        document.querySelectorAll(".tab-content").forEach((panel) => {
          panel.classList.toggle("active", panel.dataset.panel === button.dataset.tab);
        });
      });
    });
  }

  function setupUpload() {
    elements.imageInput.addEventListener("change", () => acceptFiles(elements.imageInput.files));
    ["dragenter", "dragover"].forEach((eventName) => {
      elements.uploadZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        elements.uploadZone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      elements.uploadZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        elements.uploadZone.classList.remove("dragging");
      });
    });
    elements.uploadZone.addEventListener("drop", (event) => acceptFiles(event.dataTransfer.files));
  }

  async function initialize() {
    setupTabs();
    setupUpload();
    renderIdle();
    elements.form.addEventListener("submit", submitJob);
    elements.pipelineOptions.addEventListener("change", (event) => {
      if (event.target.name === "pipeline") setPipelineMode(event.target.value);
    });
    elements.resetPromptsButton.addEventListener("click", () => {
      elements.generationPromptTemplateInput.value = state.defaults.generation_prompt_template || "";
      elements.evaluationPromptTemplateInput.value = state.defaults.evaluation_prompt_template || "";
      showToast("Prompt 模板已恢复默认。")
    });
    elements.cancelButton.addEventListener("click", cancelSelectedJob);
    elements.refreshStatusButton.addEventListener("click", checkBrowserStatus);
    elements.chatgptStatus.addEventListener("click", () => launchBrowser("chatgpt"));
    elements.grokStatus.addEventListener("click", () => launchBrowser("grok"));
    elements.newJobButton.addEventListener("click", () => {
      renderIdle();
      (state.selectedPipelineId === "video_only" ? elements.videoPromptInput : elements.requestInput).focus();
    });

    try {
      const configPayload = await api("/api/config");
      state.defaults = configPayload.defaults;
      state.pipelines = configPayload.pipelines;
      applyDefaults(state.defaults);
      setPipelineMode(state.selectedPipelineId);
      renderStages();
      await Promise.all([loadJobs(), checkBrowserStatus()]);
    } catch (error) {
      showToast(error.message, true);
    }

    state.pollTimer = window.setInterval(async () => {
      await pollSelectedJob();
      await loadJobs();
    }, 2000);
  }

  initialize();
})();
