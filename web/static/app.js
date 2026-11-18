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
  playheadSec: 0,
  scrubbing: false,
  stillMode: false,
  scrubThrottleTimer: null,
  scrubPendingSec: null,
  lastPostedPlayhead: null,
  selectionStart: null,
  selectionEnd: null,
  selecting: false,
  selectAnchor: null,
  selectMode: null, // "paint" | "start" | "end"
  selectionThrottleTimer: null,
  lastPostedSelection: null,
};

const SCRUB_THROTTLE_MS = 100;
const SELECTION_MIN_SEC = 0.05;

const els = {
  status: document.getElementById("status"),
  undo: document.getElementById("btn-undo"),
  redo: document.getElementById("btn-redo"),
  exportBtn: document.getElementById("btn-export"),
  preview: document.getElementById("preview"),
  stillFrame: document.getElementById("still-frame"),
  previewStage: document.getElementById("preview-stage"),
  previewEmpty: document.getElementById("preview-empty"),
  zoomToast: document.getElementById("zoom-toast"),
  scrubber: document.getElementById("scrubber"),
  scrubberTrack: document.getElementById("scrubber-track"),
  scrubberBlocks: document.getElementById("scrubber-blocks"),
  scrubberTicks: document.getElementById("scrubber-ticks"),
  scrubberPlayhead: document.getElementById("scrubber-playhead"),
  scrubberTimecode: document.getElementById("scrubber-timecode"),
  scrubberSelection: document.getElementById("scrubber-selection"),
  scrubberSelectionRange: document.getElementById("scrubber-selection-range"),
  scrubberClear: document.getElementById("scrubber-clear"),
  scrubberHandleStart: document.getElementById("scrubber-handle-start"),
  scrubberHandleEnd: document.getElementById("scrubber-handle-end"),
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

function formatTimecode(sec) {
  const n = Math.max(0, Number(sec) || 0);
  const m = Math.floor(n / 60);
  const s = n - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

function totalDuration() {
  return Math.max(0, Number(state.summary?.total_duration_sec) || 0);
}

function clampPlayhead(sec) {
  const total = totalDuration();
  if (total <= 0) return 0;
  return Math.min(total, Math.max(0, Number(sec) || 0));
}

function setStillMode(on) {
  state.stillMode = Boolean(on);
  els.previewStage?.classList.toggle("is-scrubbing", state.stillMode);
  if (els.stillFrame) {
    els.stillFrame.hidden = !state.stillMode;
  }
}

function showStillAt(sec) {
  if (!els.stillFrame) return;
  const tSec = clampPlayhead(sec);
  setStillMode(true);
  els.stillFrame.src = `/api/timeline/frame?t=${encodeURIComponent(tSec.toFixed(3))}&_=${Date.now()}`;
}

function updatePlayheadUi(sec) {
  const value = clampPlayhead(sec);
  state.playheadSec = value;
  const total = totalDuration();
  const pct = total > 0 ? (value / total) * 100 : 0;
  if (els.scrubberPlayhead) {
    els.scrubberPlayhead.style.left = `${pct}%`;
  }
  if (els.scrubberTimecode) {
    els.scrubberTimecode.textContent = formatTimecode(value);
  }
  if (els.scrubberTrack) {
    els.scrubberTrack.setAttribute("aria-valuenow", String(Math.round(value * 10) / 10));
    els.scrubberTrack.setAttribute("aria-valuemax", String(total));
  }
}

function postPlayhead(sec) {
  const value = clampPlayhead(sec);
  if (
    state.lastPostedPlayhead != null &&
    Math.abs(state.lastPostedPlayhead - value) < 0.01
  ) {
    return;
  }
  state.lastPostedPlayhead = value;
  fetch(withLang("/api/timeline/playhead"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sec: value }),
  }).catch(() => {});
}

function scrubTo(sec, { immediate = false } = {}) {
  const value = clampPlayhead(sec);
  updatePlayheadUi(value);
  state.scrubPendingSec = value;

  const flush = () => {
    state.scrubThrottleTimer = null;
    const pending = state.scrubPendingSec;
    if (pending == null) return;
    showStillAt(pending);
    postPlayhead(pending);
  };

  if (immediate) {
    if (state.scrubThrottleTimer) {
      clearTimeout(state.scrubThrottleTimer);
      state.scrubThrottleTimer = null;
    }
    flush();
    return;
  }

  if (state.scrubThrottleTimer) return;
  state.scrubThrottleTimer = setTimeout(flush, SCRUB_THROTTLE_MS);
}

function normalizeSelection(start, end) {
  if (start == null || end == null) return { start: null, end: null };
  let a = clampPlayhead(start);
  let b = clampPlayhead(end);
  if (a > b) [a, b] = [b, a];
  if (b - a < SELECTION_MIN_SEC) return { start: null, end: null };
  return { start: a, end: b };
}

function updateSelectionUi(start, end) {
  const normalized = normalizeSelection(start, end);
  state.selectionStart = normalized.start;
  state.selectionEnd = normalized.end;
  const total = totalDuration();
  const has =
    normalized.start != null &&
    normalized.end != null &&
    total > 0;

  if (els.scrubberSelectionRange) {
    els.scrubberSelectionRange.hidden = !has;
    if (has) {
      const left = (normalized.start / total) * 100;
      const width = ((normalized.end - normalized.start) / total) * 100;
      els.scrubberSelectionRange.style.left = `${left}%`;
      els.scrubberSelectionRange.style.width = `${Math.max(width, 0.15)}%`;
    }
  }

  if (els.scrubberSelection) {
    els.scrubberSelection.hidden = !has;
    if (has) {
      const dur = (normalized.end - normalized.start).toFixed(1).replace(/\.0$/, "");
      els.scrubberSelection.textContent = t("selectionRange", {
        start: formatTimecode(normalized.start),
        end: formatTimecode(normalized.end),
        duration: dur,
      });
    }
  }

  if (els.scrubberClear) {
    els.scrubberClear.hidden = !has;
  }
}

function postSelection(start, end, { immediate = false } = {}) {
  const flush = () => {
    state.selectionThrottleTimer = null;
    const normalized = normalizeSelection(
      start ?? state.selectionStart,
      end ?? state.selectionEnd
    );
    const key =
      normalized.start == null
        ? "null"
        : `${normalized.start.toFixed(3)}:${normalized.end.toFixed(3)}`;
    if (state.lastPostedSelection === key) return;
    state.lastPostedSelection = key;
    fetch(withLang("/api/timeline/selection"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_sec: normalized.start,
        end_sec: normalized.end,
      }),
    }).catch(() => {});
  };

  if (immediate) {
    if (state.selectionThrottleTimer) {
      clearTimeout(state.selectionThrottleTimer);
      state.selectionThrottleTimer = null;
    }
    flush();
    return;
  }
  if (state.selectionThrottleTimer) return;
  state.selectionThrottleTimer = setTimeout(flush, SCRUB_THROTTLE_MS);
}

function paintSelectionTo(sec, { immediate = false, edgeSec = null } = {}) {
  const tip = clampPlayhead(sec);
  let start;
  let end;
  if (state.selectMode === "start") {
    end = state.selectionEnd ?? tip;
    start = tip;
  } else if (state.selectMode === "end") {
    start = state.selectionStart ?? tip;
    end = tip;
  } else {
    start = state.selectAnchor ?? tip;
    end = tip;
  }
  updateSelectionUi(start, end);
  const previewSec = edgeSec != null ? edgeSec : tip;
  updatePlayheadUi(previewSec);
  const flushEdge = () => {
    showStillAt(previewSec);
    postPlayhead(previewSec);
    postSelection(state.selectionStart, state.selectionEnd, { immediate: true });
  };
  if (immediate) {
    if (state.scrubThrottleTimer) {
      clearTimeout(state.scrubThrottleTimer);
      state.scrubThrottleTimer = null;
    }
    flushEdge();
    return;
  }
  state.scrubPendingSec = previewSec;
  if (state.scrubThrottleTimer) return;
  state.scrubThrottleTimer = setTimeout(() => {
    state.scrubThrottleTimer = null;
    showStillAt(state.scrubPendingSec);
    postPlayhead(state.scrubPendingSec);
    postSelection(state.selectionStart, state.selectionEnd);
  }, SCRUB_THROTTLE_MS);
}

function clearSelectionUiAndServer() {
  updateSelectionUi(null, null);
  postSelection(null, null, { immediate: true });
}

function secFromPointerEvent(e) {
  if (!els.scrubberTrack) return 0;
  const rect = els.scrubberTrack.getBoundingClientRect();
  const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width || 1);
  const ratio = rect.width > 0 ? x / rect.width : 0;
  return clampPlayhead(ratio * totalDuration());
}

function renderScrubber() {
  if (!els.scrubber || !els.scrubberBlocks || !els.scrubberTicks) return;
  const clips = state.summary?.clips || [];
  const total = totalDuration();
  els.scrubber.hidden = clips.length === 0 || total <= 0;
  els.scrubberBlocks.innerHTML = "";
  els.scrubberTicks.innerHTML = "";
  if (!clips.length || total <= 0) {
    updatePlayheadUi(0);
    updateSelectionUi(null, null);
    return;
  }

  clips.forEach((clip) => {
    const start = Number(clip.timeline_start_sec ?? 0);
    const end = Number(clip.timeline_end_sec ?? start + (clip.duration_sec || 0));
    const left = (start / total) * 100;
    const width = Math.max(0.4, ((end - start) / total) * 100);
    const block = document.createElement("div");
    block.className = "scrubber-block";
    block.style.left = `${left}%`;
    block.style.width = `${width}%`;
    const label = document.createElement("span");
    label.textContent = clip.title || t("clipTitle", { n: clip.index });
    block.appendChild(label);
    els.scrubberBlocks.appendChild(block);
  });

  const step = total <= 10 ? 1 : total <= 30 ? 5 : total <= 120 ? 10 : 30;
  for (let mark = 0; mark <= total + 1e-9; mark += step) {
    const tick = document.createElement("div");
    tick.className = "scrubber-tick";
    tick.style.left = `${(mark / total) * 100}%`;
    tick.dataset.label = formatTimecode(mark);
    els.scrubberTicks.appendChild(tick);
  }

  updatePlayheadUi(state.summary?.playhead_sec ?? state.playheadSec);
  updateSelectionUi(
    state.summary?.selection_start_sec ?? state.selectionStart,
    state.summary?.selection_end_sec ?? state.selectionEnd
  );
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

  if (typeof summary.playhead_sec === "number") {
    state.playheadSec = clampPlayhead(summary.playhead_sec);
  }
  if (!state.selecting) {
    state.selectionStart =
      summary.selection_start_sec == null ? null : Number(summary.selection_start_sec);
    state.selectionEnd =
      summary.selection_end_sec == null ? null : Number(summary.selection_end_sec);
  }
  renderClipStrip();
  renderScrubber();
  updatePreview(summary.preview_url);
}

function updatePreview(url) {
  const hasPreview = Boolean(url);
  els.previewStage.classList.toggle("is-empty", !hasPreview);
  if (!hasPreview) {
    els.preview.removeAttribute("src");
    els.preview.load();
    setStillMode(false);
    return;
  }
  const next = `${url}?t=${Date.now()}`;
  const resume = state.playheadSec || els.preview.currentTime || 0;
  const wasPaused = els.preview.paused && !state.scrubbing;
  const keepStill = state.stillMode || state.scrubbing;
  els.preview.src = next;
  els.preview.load();
  els.preview.addEventListener(
    "loadedmetadata",
    () => {
      try {
        els.preview.currentTime = Math.min(
          resume,
          els.preview.duration || resume
        );
      } catch {
        /* ignore seek errors */
      }
      if (!wasPaused && !keepStill) {
        setStillMode(false);
        els.preview.play().catch(() => {});
      } else if (keepStill) {
        showStillAt(state.playheadSec);
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

  // Suhteelliset 0..1 -koordinaatit: toimivat preview- ja full-resoluutiossa samoin
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
    if (state.stillMode) return;
    addZoomAtClick(e).catch(() => {});
  });

  els.preview.addEventListener("play", () => {
    setStillMode(false);
    try {
      els.preview.currentTime = clampPlayhead(state.playheadSec);
    } catch {
      /* ignore */
    }
  });

  els.preview.addEventListener("timeupdate", () => {
    if (state.scrubbing || state.stillMode || els.preview.paused) return;
    updatePlayheadUi(els.preview.currentTime || 0);
  });

  els.preview.addEventListener("seeked", () => {
    if (state.scrubbing) return;
    if (!els.preview.paused) {
      updatePlayheadUi(els.preview.currentTime || 0);
      postPlayhead(state.playheadSec);
    }
  });

  if (els.scrubberClear) {
    els.scrubberClear.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      clearSelectionUiAndServer();
    });
  }

  if (els.scrubberTrack) {
    const beginSelect = (e, mode) => {
      if (!totalDuration()) return;
      e.preventDefault();
      e.stopPropagation();
      state.selecting = true;
      state.scrubbing = false;
      state.selectMode = mode;
      const tip = secFromPointerEvent(e);
      if (mode === "paint") {
        state.selectAnchor = tip;
        updateSelectionUi(tip, tip);
      }
      els.scrubberTrack.setPointerCapture?.(e.pointerId);
      paintSelectionTo(tip, { immediate: true, edgeSec: tip });
    };

    const onPointerDown = (e) => {
      if (!totalDuration()) return;
      if (e.target === els.scrubberHandleStart) {
        beginSelect(e, "start");
        return;
      }
      if (e.target === els.scrubberHandleEnd) {
        beginSelect(e, "end");
        return;
      }
      if (e.shiftKey) {
        beginSelect(e, "paint");
        return;
      }
      e.preventDefault();
      state.scrubbing = true;
      state.selecting = false;
      state.selectMode = null;
      els.scrubberTrack.setPointerCapture?.(e.pointerId);
      scrubTo(secFromPointerEvent(e), { immediate: true });
    };
    const onPointerMove = (e) => {
      if (state.selecting) {
        paintSelectionTo(secFromPointerEvent(e));
        return;
      }
      if (!state.scrubbing) return;
      scrubTo(secFromPointerEvent(e));
    };
    const onPointerUp = (e) => {
      if (state.selecting) {
        state.selecting = false;
        paintSelectionTo(secFromPointerEvent(e), { immediate: true });
        state.selectMode = null;
        state.selectAnchor = null;
        return;
      }
      if (!state.scrubbing) return;
      state.scrubbing = false;
      scrubTo(secFromPointerEvent(e), { immediate: true });
    };
    els.scrubberTrack.addEventListener("pointerdown", onPointerDown);
    els.scrubberTrack.addEventListener("pointermove", onPointerMove);
    els.scrubberTrack.addEventListener("pointerup", onPointerUp);
    els.scrubberTrack.addEventListener("pointercancel", onPointerUp);
    els.scrubberTrack.addEventListener("keydown", (e) => {
      const total = totalDuration();
      if (!total) return;
      if (e.key === "Escape") {
        e.preventDefault();
        clearSelectionUiAndServer();
        return;
      }
      const step = e.shiftKey ? 1 : 0.1;
      let next = state.playheadSec;
      if (e.key === "ArrowLeft") next -= step;
      else if (e.key === "ArrowRight") next += step;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = total;
      else return;
      e.preventDefault();
      scrubTo(next, { immediate: true });
    });
  }

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
