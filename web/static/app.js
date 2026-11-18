import {
  applyDomTranslations,
  getLang,
  loadStoredLang,
  setLang,
  t,
  transitionLabels,
} from "./i18n.js";

const state = {
  summary: null,
  selectedClip: 1,
  busy: false,
  pollTimer: null,
};

const els = {
  status: document.getElementById("status"),
  undo: document.getElementById("btn-undo"),
  redo: document.getElementById("btn-redo"),
  exportBtn: document.getElementById("btn-export"),
  preview: document.getElementById("preview"),
  previewStage: document.getElementById("preview-stage"),
  previewEmpty: document.getElementById("preview-empty"),
  zoomToast: document.getElementById("zoom-toast"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  libraryMeta: document.getElementById("library-meta"),
  clipStrip: document.getElementById("clip-strip"),
  timelineDuration: document.getElementById("timeline-duration"),
  commandForm: document.getElementById("command-form"),
  commandInput: document.getElementById("command-input"),
  commandReply: document.getElementById("command-reply"),
  textModal: document.getElementById("text-modal"),
  textForm: document.getElementById("text-form"),
  textValue: document.getElementById("text-value"),
  textPosition: document.getElementById("text-position"),
  textFontStyle: document.getElementById("text-font-style"),
  textCancel: document.getElementById("text-cancel"),
  progress: document.getElementById("topbar-progress"),
  progressBar: document.getElementById("topbar-progress-bar"),
};

function setStatus(message, busy = false) {
  state.busy = busy;
  els.status.textContent = message;
  els.status.classList.toggle("is-busy", busy);
  if (!busy) {
    stopProgressPoll();
    setProgressBar(null);
  }
}

function setProgressBar(percent) {
  if (!els.progress || !els.progressBar) return;
  if (percent === null || percent === undefined) {
    els.progress.hidden = true;
    els.progress.classList.remove("is-indeterminate");
    els.progressBar.style.width = "0%";
    return;
  }
  els.progress.hidden = false;
  if (percent < 0) {
    els.progress.classList.add("is-indeterminate");
    els.progressBar.style.width = "35%";
  } else {
    els.progress.classList.remove("is-indeterminate");
    els.progressBar.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  }
}

function statusFromProgress(progress) {
  if (!progress || !progress.active) return t("ready");
  const phase = progress.phase;
  const pct = progress.percent;
  if (phase === "chat") return t("processingCommandQwen");
  if (phase === "export") {
    return pct == null ? t("creatingFinal") : t("creatingFinalPct", { percent: pct });
  }
  if (phase === "preview") {
    return pct == null ? t("updatingPreview") : t("updatingPreviewPct", { percent: pct });
  }
  return progress.detail || t("updating");
}

function applyProgress(progress) {
  if (!progress || !progress.active) {
    setProgressBar(null);
    return;
  }
  setStatus(statusFromProgress(progress), true);
  if (progress.phase === "chat" || progress.percent == null) {
    setProgressBar(-1);
  } else {
    setProgressBar(progress.percent);
  }
}

function stopProgressPoll() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startProgressPoll() {
  stopProgressPoll();
  const tick = async () => {
    try {
      const data = await fetch("/api/status").then((r) => r.json());
      if (data?.progress) applyProgress(data.progress);
    } catch {
      /* pollausvirheet eivät keskeytä päätoimintoa */
    }
  };
  tick();
  state.pollTimer = setInterval(tick, 300);
}

function withLang(path) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("lang", getLang());
  return url.pathname + url.search;
}

async function api(path, options = {}) {
  const res = await fetch(withLang(path), options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.detail || t("errorStatus", { status: res.status }));
  }
  return data;
}

function formatSeconds(sec) {
  const n = Number(sec) || 0;
  if (n < 60) {
    return t("seconds", { n: n.toFixed(1).replace(/\.0$/, "") });
  }
  const m = Math.floor(n / 60);
  const s = (n % 60).toFixed(1).replace(/\.0$/, "");
  return t("minutesSeconds", { m, s });
}

function transitionForClip(afterClip1Based) {
  const list = state.summary?.transitions || [];
  return list.find((item) => item.after_clip === afterClip1Based) || null;
}

function applyState(summary) {
  state.summary = summary;
  if (!summary.clips?.length) {
    state.selectedClip = 1;
  } else if (state.selectedClip > summary.clips.length) {
    state.selectedClip = summary.clips.length;
  }

  els.undo.disabled = !summary.can_undo;
  els.redo.disabled = !summary.can_redo;
  els.timelineDuration.textContent = t("duration", {
    time: formatSeconds(summary.total_duration_sec),
  });

  const sourceCount = summary.sources?.length || 0;
  const music = summary.bgm ? t("musicSelected") : "";
  if (sourceCount === 0) {
    els.libraryMeta.textContent = t("noMaterial");
  } else {
    const key = sourceCount === 1 ? "libraryCount" : "libraryCountPlural";
    els.libraryMeta.textContent = t(key, { count: sourceCount }) + music;
  }

  renderClipStrip();
  updatePreview(summary.preview_url);
}

function updatePreview(url) {
  const hasPreview = Boolean(url);
  els.previewStage.classList.toggle("is-empty", !hasPreview);
  if (!hasPreview) {
    els.preview.removeAttribute("src");
    els.preview.load();
    return;
  }
  const next = `${url}?t=${Date.now()}`;
  const resume = els.preview.currentTime || 0;
  const wasPaused = els.preview.paused;
  els.preview.src = next;
  els.preview.load();
  els.preview.addEventListener(
    "loadedmetadata",
    () => {
      try {
        els.preview.currentTime = Math.min(resume, els.preview.duration || resume);
      } catch {
        /* ignore seek errors */
      }
      if (!wasPaused) {
        els.preview.play().catch(() => {});
      }
    },
    { once: true }
  );
}

function renderClipStrip() {
  const clips = state.summary?.clips || [];
  els.clipStrip.innerHTML = "";
  if (!clips.length) {
    const empty = document.createElement("p");
    empty.className = "strip-empty";
    empty.id = "strip-empty";
    empty.textContent = t("stripEmpty");
    els.clipStrip.appendChild(empty);
    return;
  }

  const labels = transitionLabels();

  clips.forEach((clip, idx) => {
    const card = document.createElement("article");
    card.className = "clip-card" + (clip.index === state.selectedClip ? " is-selected" : "");
    card.draggable = true;
    card.dataset.index = String(clip.index);

    const effects = (clip.effects || [])
      .map((effect) => {
        if (effect.type === "zoom") {
          return `<span class="tag zoom">${effect.label || t("effectZoom")}</span>`;
        }
        if (effect.type === "text") {
          return `<span class="tag text">${effect.label || t("effectText")}</span>`;
        }
        return "";
      })
      .join("");

    const trimStart = clip.trim_start ?? clip.in_sec ?? 0;
    const thumbUrl = `/api/clips/${clip.index}/thumbnail?v=${encodeURIComponent(trimStart)}`;
    card.innerHTML = `
      <div class="clip-thumb-wrap">
        <img
          class="clip-thumb"
          src="${thumbUrl}"
          alt=""
          loading="lazy"
          draggable="false"
        />
      </div>
      <h3>${clip.title || t("clipTitle", { n: clip.index })}</h3>
      <div class="duration">${formatSeconds(clip.duration_sec)}</div>
      <div class="effect-tags">${effects || `<span class="tag">${t("noEffects")}</span>`}</div>
      <div class="clip-actions">
        <button type="button" data-act="split">${t("split")}</button>
        <button type="button" class="danger" data-act="remove">${t("remove")}</button>
      </div>
    `;
    const thumb = card.querySelector(".clip-thumb");
    if (thumb) {
      thumb.addEventListener("error", () => {
        thumb.classList.add("is-missing");
        thumb.removeAttribute("src");
      });
    }

    card.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      state.selectedClip = clip.index;
      renderClipStrip();
    });

    card.querySelector('[data-act="remove"]').addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await mutate(() =>
        api("/api/clips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "remove", clip_index: clip.index }),
        })
      );
    });

    card.querySelector('[data-act="split"]').addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const at = Math.max(0.1, (clip.duration_sec || 1) / 2);
      await mutate(() =>
        api("/api/clips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "split",
            clip_index: clip.index,
            split_at_sec: at,
          }),
        })
      );
    });

    card.addEventListener("dragstart", (ev) => {
      card.classList.add("is-dragging");
      ev.dataTransfer.setData("text/plain", String(clip.index));
      ev.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", () => card.classList.remove("is-dragging"));
    card.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
    });
    card.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      const from = Number(ev.dataTransfer.getData("text/plain"));
      const to = clip.index;
      if (!from || from === to) return;
      await mutate(() =>
        api("/api/clips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "move",
            from_index: from,
            to_index: to,
          }),
        })
      );
    });

    els.clipStrip.appendChild(card);

    if (idx < clips.length - 1) {
      const slot = document.createElement("div");
      slot.className = "transition-slot";
      const select = document.createElement("select");
      select.setAttribute(
        "aria-label",
        t("transitionAria", { a: clip.index, b: clip.index + 1 })
      );
      const current = transitionForClip(clip.index);
      Object.entries(labels).forEach(([value, label]) => {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = label;
        if ((value === "none" && !current) || (current && current.type === value)) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });
      select.addEventListener("change", async () => {
        if (select.value === "none") {
          setStatus(t("transitionClearedHint"));
          return;
        }
        await mutate(() =>
          api("/api/effects", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action: "transition",
              after_clip: clip.index,
              transition_type: select.value,
              duration: 0.4,
            }),
          })
        );
      });
      slot.appendChild(select);
      els.clipStrip.appendChild(slot);
    }
  });
}

async function refreshState() {
  const data = await api("/api/state");
  applyState(data.state);
}

async function mutate(fn, { renderPreview = true } = {}) {
  try {
    setStatus(t("updating"), true);
    const data = await fn();
    if (data.state) applyState(data.state);
    else await refreshState();
    if (renderPreview && (state.summary?.clips?.length || 0) > 0) {
      await runRender("preview");
    } else {
      setStatus(t("ready"));
    }
    return data;
  } catch (err) {
    setStatus(t("ready"));
    showReply(err.message || t("actionFailed"));
    throw err;
  }
}

async function runRender(mode) {
  setStatus(mode === "final" ? t("creatingFinal") : t("updatingPreview"), true);
  setProgressBar(0);
  startProgressPoll();
  try {
    const data = await api("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (data.state) applyState(data.state);
    if (data.url) updatePreview(data.url);
    setStatus(mode === "final" ? t("finalReady") : t("ready"));
    if (mode === "final") {
      showReply(t("finalReadyReply"));
    }
    return data;
  } catch (err) {
    setStatus(t("ready"));
    showReply(err.message || t("renderFailed"));
    throw err;
  }
}

function showReply(text) {
  els.commandReply.hidden = false;
  els.commandReply.textContent = text;
}

async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", "auto");
  form.append("lang", getLang());
  await mutate(() =>
    api("/api/upload", {
      method: "POST",
      body: form,
    })
  );
}

function wireDropTarget(element) {
  ["dragenter", "dragover"].forEach((evt) => {
    element.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      element.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    element.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      element.classList.remove("is-dragover");
    });
  });
  element.addEventListener("drop", async (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) await uploadFile(file);
  });
}

function mapTimeToClip(timeSec) {
  const clips = state.summary?.clips || [];
  let cursor = 0;
  const transitions = state.summary?.transitions || [];
  for (let i = 0; i < clips.length; i++) {
    const clip = clips[i];
    const start = cursor;
    const end = cursor + clip.duration_sec;
    if (timeSec >= start && timeSec <= end + 1e-6) {
      return {
        clipIndex: clip.index,
        localSec: Math.max(0, timeSec - start),
        clip,
      };
    }
    cursor = end;
    const tr = transitions.find((item) => item.after_clip === clip.index);
    if (tr && i < clips.length - 1) {
      cursor -= tr.duration_sec;
    }
  }
  if (clips.length) {
    const last = clips[clips.length - 1];
    return { clipIndex: last.index, localSec: 0, clip: last };
  }
  return null;
}

async function addZoomAtClick(ev) {
  if (!state.summary?.clips?.length || !els.preview.src) return;
  const rect = els.preview.getBoundingClientRect();
  if (ev.clientY > rect.bottom - 48) return;

  const x = (ev.clientX - rect.left) / rect.width;
  const y = (ev.clientY - rect.top) / rect.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;

  const mapped = mapTimeToClip(els.preview.currentTime || 0);
  if (!mapped) return;

  const duration = Math.min(1.5, Math.max(0.4, mapped.clip.duration_sec * 0.35));
  const start = Math.min(
    mapped.localSec,
    Math.max(0, mapped.clip.duration_sec - duration)
  );

  els.zoomToast.hidden = false;
  setTimeout(() => {
    els.zoomToast.hidden = true;
  }, 1200);

  await mutate(() =>
    api("/api/effects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "zoom",
        clip_index: mapped.clipIndex,
        start_sec: start,
        duration,
        target_x: Math.min(1, Math.max(0, x)),
        target_y: Math.min(1, Math.max(0, y)),
        zoom_factor: 1.8,
      }),
    })
  );
}

async function handleQuick(action) {
  const clips = state.summary?.clips || [];
  if (!clips.length) {
    showReply(t("needVideoFirst"));
    return;
  }
  const clipIndex = state.selectedClip;

  if (action === "zoom-center") {
    await mutate(() =>
      api("/api/effects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "zoom",
          clip_index: clipIndex,
          start_sec: 0,
          duration: Math.min(1.5, clips[clipIndex - 1].duration_sec),
          region: "center",
          zoom_factor: 1.8,
        }),
      })
    );
    return;
  }

  if (action === "text") {
    els.textValue.value = "";
    els.textModal.showModal();
    return;
  }

  if (action === "fade") {
    if (clips.length < 2) {
      showReply(t("needTwoClips"));
      return;
    }
    const after = Math.min(clipIndex, clips.length - 1);
    await mutate(() =>
      api("/api/effects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "transition",
          after_clip: after,
          transition_type: "fade",
          duration: 0.4,
        }),
      })
    );
    return;
  }

  if (action === "split") {
    const clip = clips.find((c) => c.index === clipIndex) || clips[0];
    await mutate(() =>
      api("/api/clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "split",
          clip_index: clip.index,
          split_at_sec: Math.max(0.1, clip.duration_sec / 2),
        }),
      })
    );
  }
}

function syncLangButtons() {
  document.querySelectorAll(".lang-btn").forEach((btn) => {
    const active = btn.dataset.lang === getLang();
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function applyLanguage(lang) {
  setLang(lang);
  applyDomTranslations();
  syncLangButtons();
  if (state.summary) {
    // Päivitä dynaamiset UI-merkkijonot heti; serverin description haetaan perään
    applyState(state.summary);
  }
  refreshState()
    .then(() => {
      if (!state.busy) setStatus(t("ready"));
    })
    .catch(() => {});
}

function wireEvents() {
  document.querySelectorAll(".lang-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.lang) applyLanguage(btn.dataset.lang);
    });
  });

  els.undo.addEventListener("click", () =>
    mutate(() => api("/api/undo", { method: "POST" }))
  );
  els.redo.addEventListener("click", () =>
    mutate(() => api("/api/redo", { method: "POST" }))
  );
  els.exportBtn.addEventListener("click", () => runRender("final"));

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files?.[0];
    if (file) await uploadFile(file);
    els.fileInput.value = "";
  });

  wireDropTarget(els.dropzone);
  wireDropTarget(els.previewStage);

  els.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      els.fileInput.click();
    }
  });

  els.preview.addEventListener("click", (e) => {
    addZoomAtClick(e).catch(() => {});
  });

  document.querySelectorAll("[data-quick]").forEach((btn) => {
    btn.addEventListener("click", () => {
      handleQuick(btn.dataset.quick).catch(() => {});
    });
  });

  els.commandForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = els.commandInput.value.trim();
    if (!message) return;
    try {
      setStatus(t("processingCommandQwen"), true);
      setProgressBar(-1);
      startProgressPoll();
      const data = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, lang: getLang() }),
      });
      showReply(data.reply || t("ready"));
      if (data.state) applyState(data.state);
      els.commandInput.value = "";
      if (data.changed && (state.summary?.clips?.length || 0) > 0) {
        await runRender("preview");
      } else {
        setStatus(t("ready"));
      }
    } catch (err) {
      setStatus(t("ready"));
      showReply(err.message);
    }
  });

  els.textCancel.addEventListener("click", () => els.textModal.close());
  els.textForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = els.textValue.value.trim();
    if (!text) return;
    els.textModal.close();
    await mutate(() =>
      api("/api/effects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "text",
          clip_index: state.selectedClip,
          text,
          start_sec: 0,
          duration: 2,
          position: els.textPosition.value,
          font_style: els.textFontStyle?.value || "clean",
        }),
      })
    );
  });
}

setLang(loadStoredLang());
applyDomTranslations();
syncLangButtons();
wireEvents();
refreshState()
  .then(() => setStatus(t("ready")))
  .catch((err) => {
    setStatus(t("ready"));
    showReply(err.message || t("stateLoadFailed"));
  });
