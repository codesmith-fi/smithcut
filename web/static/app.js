/** CutSmith Web UI – päämoduuli (esikatselu, klippinauha, ääniraidat ja chat). */

import {
  api,
  setProgressBar,
  setStatus,
  showReply,
  startProgressPoll,
} from "./api.js";
import {
  applyDomTranslations,
  getLang,
  loadStoredLang,
  setLang,
  t,
  transitionLabels,
} from "./i18n.js";
import {
  clearSelectionUiAndServer,
  hideStillAndSyncVideo,
  postPlayhead,
  renderScrubber,
  setSelectedTimelineItem,
  setStillMode,
  updatePlayheadUi,
  wireScrubberEvents,
} from "./scrubber.js";
import {
  SELECTION_MIN_SEC,
  clampPlayhead,
  els,
  formatSeconds,
  formatTimecode,
  isActivelyScrubbing,
  mapTimeToClip,
  resolvePreviewUrl,
  state,
  transitionForClip,
} from "./state.js";

function reconcileTimelineSelection(summary) {
  const sel = state.selectedTimelineItem;
  if (!sel) return;
  if (sel.type === "video") {
    const match = (summary.clips || []).find(
      (c) => (sel.id && c.id === sel.id) || c.index === sel.index
    );
    state.selectedTimelineItem = match
      ? { type: "video", index: match.index, id: match.id || null }
      : null;
  } else if (sel.type === "audio") {
    const match = (summary.audio_clips || []).find(
      (c) => (sel.id && c.id === sel.id) || c.index === sel.index
    );
    state.selectedTimelineItem = match
      ? { type: "audio", index: match.index, id: match.id || null }
      : null;
  } else {
    state.selectedTimelineItem = null;
  }
}

function applyState(summary) {
  state.summary = summary;
  if (!summary.clips?.length) {
    state.selectedClip = 1;
  } else if (state.selectedClip > summary.clips.length) {
    state.selectedClip = summary.clips.length;
  }
  reconcileTimelineSelection(summary);

  els.undo.disabled = !summary.can_undo;
  els.redo.disabled = !summary.can_redo;
  let durationText = t("duration", {
    time: formatSeconds(summary.total_duration_sec),
  });
  const fadeIn = Number(summary.fade_in_sec) || 0;
  const fadeOut = Number(summary.fade_out_sec) || 0;
  if (fadeIn > 0 && fadeOut > 0) {
    durationText += t("timelineFades", { in: formatSeconds(fadeIn), out: formatSeconds(fadeOut) });
  } else if (fadeIn > 0) {
    durationText += t("timelineFadeIn", { in: formatSeconds(fadeIn) });
  } else if (fadeOut > 0) {
    durationText += t("timelineFadeOut", { out: formatSeconds(fadeOut) });
  }
  els.timelineDuration.textContent = durationText;

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
  renderAudioTracks(summary);
  renderClipStrip();
  renderAudioTimeline(summary);
  renderScrubber();
  if (!state._skipPreviewUpdate) {
    updatePreview(resolvePreviewUrl(summary));
  }
  if (state.lastReply && els.commandReply) {
    els.commandReply.hidden = false;
    els.commandReply.textContent = state.lastReply;
  }
}

function updatePreview(url, { forceReload = false } = {}) {
  const resolved = url || resolvePreviewUrl(state.summary);
  const hasPreview = Boolean(resolved);
  els.previewStage.classList.toggle("is-empty", !hasPreview);
  els.preview.controls = true;
  els.preview.preload = "metadata";

  if (!isActivelyScrubbing()) {
    setStillMode(false);
    if (els.stillFrame) {
      els.stillFrame.hidden = true;
      els.stillFrame.removeAttribute("src");
    }
  }

  if (!hasPreview) {
    els.preview.removeAttribute("src");
    els.preview.removeAttribute("poster");
    els.preview.load();
    return;
  }

  const base = String(resolved).split("?")[0];
  const currentBase = (els.preview.getAttribute("src") || "").split("?")[0];
  const resume = state.playheadSec || els.preview.currentTime || 0;
  const wasPlaying = !els.preview.paused && !isActivelyScrubbing();

  if (
    !forceReload &&
    currentBase &&
    currentBase === base &&
    els.preview.readyState >= 1
  ) {
    try {
      els.preview.currentTime = Math.min(resume, els.preview.duration || resume);
    } catch {
      /* ignore */
    }
    return;
  }

  const next = `${base}?t=${Date.now()}`;
  els.preview.removeAttribute("poster");
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
      setStillMode(false);
      if (wasPlaying) {
        els.preview.play().catch(() => {});
      }
    },
    { once: true }
  );
  els.preview.addEventListener(
    "loadeddata",
    () => {
      setStillMode(false);
    },
    { once: true }
  );
}

function renderAudioTracks(summary) {
  if (!els.audioPanel || !els.audioTrackList || !els.audioStatus) return;
  const tracks = summary?.audio_tracks || [];
  const muted = Boolean(summary?.mute_video_audio);
  const activeIndex = summary?.active_audio_track_index ?? null;

  let status = muted ? t("videoAudioMuted") : t("videoAudioOn");
  if (activeIndex != null) {
    status += t("audioAttached", { n: activeIndex });
  }
  els.audioStatus.textContent = status;

  els.audioTrackList.innerHTML = "";
  const showPanel = tracks.length > 0 || muted;
  els.audioPanel.hidden = !showPanel;
  if (!tracks.length) return;

  tracks.forEach((track) => {
    const li = document.createElement("li");
    li.className = "audio-track-item" + (track.active ? " is-active" : "");
    const originKey =
      track.origin === "extracted" ? "audioTrackExtracted" : "audioTrackImported";
    const stateKey = track.active ? "audioTrackActive" : "audioTrackIdle";

    const labelSpan = document.createElement("span");
    labelSpan.className = "track-label";
    labelSpan.textContent = track.label || t("audioTrackLabel", { n: track.index });

    const metaSpan = document.createElement("span");
    metaSpan.className = "track-meta";
    metaSpan.textContent = `${t(originKey)} · ${t(stateKey)} · ${formatSeconds(track.duration_sec)}`;

    li.appendChild(labelSpan);
    li.appendChild(metaSpan);
    els.audioTrackList.appendChild(li);
  });
}

function renderAudioTimeline(summary) {
  if (!els.audioTimeline || !els.audioClipList) return;
  const clips = summary?.audio_clips || [];
  if (!clips.length) {
    els.audioTimeline.hidden = true;
    els.audioClipList.innerHTML = "";
    return;
  }
  els.audioTimeline.hidden = false;
  if (els.audioTimelineMeta) {
    els.audioTimelineMeta.textContent = t("audioTimelineMeta", { count: clips.length });
  }
  els.audioClipList.innerHTML = "";
  clips.forEach((clip) => {
    const start = Math.max(0, Number(clip.timeline_start_sec) || 0);
    const dur = Math.max(0.05, Number(clip.duration_sec) || 0.05);
    const end = Number(clip.timeline_end_sec) || start + dur;
    const vol = typeof clip.volume === "number" ? clip.volume : 1.0;

    const li = document.createElement("li");
    li.className = "audio-clip-row";

    const info = document.createElement("div");
    info.className = "audio-clip-info";

    const badge = document.createElement("span");
    badge.className = "audio-clip-badge";
    badge.textContent = clip.label || t("audioClipLabel", { n: clip.index });

    const meta = document.createElement("span");
    meta.className = "audio-clip-meta";
    const volPart = Math.abs(vol - 1.0) > 0.01 ? ` · ${Math.round(vol * 100)}%` : "";
    meta.textContent = `${formatTimecode(start)} – ${formatTimecode(end)} (${formatSeconds(dur)})${volPart}`;

    info.appendChild(badge);
    info.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "audio-clip-actions";

    const splitBtn = document.createElement("button");
    splitBtn.type = "button";
    splitBtn.textContent = t("split");
    splitBtn.addEventListener("click", async () => {
      const ph = state.playheadSec;
      const at = ph > start + 0.05 && ph < end - 0.05 ? ph : start + dur / 2;
      await mutate(() =>
        api("/api/audio", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "split", timestamp_sec: at }),
        })
      );
    });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "danger";
    removeBtn.textContent = t("remove");
    removeBtn.addEventListener("click", async () => {
      await mutate(() =>
        api("/api/audio", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "remove",
            clip_index: clip.index,
          }),
        })
      );
    });

    actions.appendChild(splitBtn);
    actions.appendChild(removeBtn);

    li.appendChild(info);
    li.appendChild(actions);
    els.audioClipList.appendChild(li);
  });
}

function buildClipCard(clip) {
  const card = document.createElement("article");
  card.className = "clip-card" + (clip.index === state.selectedClip ? " is-selected" : "");
  card.draggable = true;
  card.dataset.index = String(clip.index);

  const thumbWrap = document.createElement("div");
  thumbWrap.className = "clip-thumb-wrap";
  const thumb = document.createElement("img");
  thumb.className = "clip-thumb";
  const trimStart = clip.trim_start ?? clip.in_sec ?? 0;
  thumb.src = `/api/clips/${clip.index}/thumbnail?v=${encodeURIComponent(trimStart)}`;
  thumb.alt = "";
  thumb.loading = "lazy";
  thumb.draggable = false;
  thumb.addEventListener("error", () => {
    thumb.classList.add("is-missing");
    thumb.removeAttribute("src");
  });
  thumbWrap.appendChild(thumb);

  const title = document.createElement("h3");
  title.textContent = clip.title || t("clipTitle", { n: clip.index });

  const dur = document.createElement("div");
  dur.className = "duration";
  dur.textContent = formatSeconds(clip.duration_sec);

  const tags = document.createElement("div");
  tags.className = "effect-tags";
  const effectsList = clip.effects || [];
  if (!effectsList.length) {
    const emptyTag = document.createElement("span");
    emptyTag.className = "tag";
    emptyTag.textContent = t("noEffects");
    tags.appendChild(emptyTag);
  } else {
    effectsList.forEach((effect) => {
      if (!["zoom", "text", "blur"].includes(effect.type)) return;
      const tag = document.createElement("span");
      tag.className = `tag ${effect.type}`;
      const fallbackKey =
        effect.type === "zoom"
          ? "effectZoom"
          : effect.type === "text"
            ? "effectText"
            : "effectBlur";
      tag.textContent = effect.label || t(fallbackKey);
      tags.appendChild(tag);
    });
  }

  const actions = document.createElement("div");
  actions.className = "clip-actions";

  const splitBtn = document.createElement("button");
  splitBtn.type = "button";
  splitBtn.dataset.act = "split";
  splitBtn.textContent = t("split");
  splitBtn.addEventListener("click", async (ev) => {
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

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "danger";
  removeBtn.dataset.act = "remove";
  removeBtn.textContent = t("remove");
  removeBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    await mutate(() =>
      api("/api/clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove", clip_index: clip.index }),
      })
    );
  });

  actions.appendChild(splitBtn);
  actions.appendChild(removeBtn);

  card.appendChild(thumbWrap);
  card.appendChild(title);
  card.appendChild(dur);
  card.appendChild(tags);
  card.appendChild(actions);

  card.addEventListener("click", (ev) => {
    if (ev.target.closest("button")) return;
    state.selectedClip = clip.index;
    setSelectedTimelineItem({ type: "video", index: clip.index, id: clip.id || null });
    renderClipStrip();
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

  return card;
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
    els.clipStrip.appendChild(buildClipCard(clip));

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
          await mutate(() =>
            api("/api/effects", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                action: "remove_transition",
                after_clip: clip.index,
              }),
            })
          );
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
    if (data.state) {
      state._skipPreviewUpdate = true;
      try {
        applyState(data.state);
      } finally {
        state._skipPreviewUpdate = false;
      }
    }
    const previewUrl = data.url || data.state?.preview_url || resolvePreviewUrl(data.state);
    if (previewUrl) {
      updatePreview(previewUrl, { forceReload: true });
    }
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
    return;
  }

  if (action === "detach-audio") {
    await mutate(() =>
      api("/api/audio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "detach" }),
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
  if (els.newProject) {
    els.newProject.addEventListener("click", async () => {
      if (!window.confirm(t("newProjectConfirm"))) return;
      try {
        setStatus(t("updating"), true);
        const data = await api("/api/project/reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ delete_media: false }),
        });
        state.selectedClip = 1;
        state.selectedTimelineItem = null;
        state.playheadSec = 0;
        state.selectionStart = null;
        state.selectionEnd = null;
        state.lastReply = null;
        if (els.commandReply) {
          els.commandReply.hidden = true;
          els.commandReply.textContent = "";
        }
        if (data.state) applyState(data.state);
        else await refreshState();
        updatePreview(null, { forceReload: true });
        setStatus(t("projectReset"));
        showReply(t("projectReset"));
      } catch (err) {
        setStatus(t("ready"));
        showReply(err.message || t("actionFailed"));
      }
    });
  }
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
    if (state.stillMode || isActivelyScrubbing()) {
      hideStillAndSyncVideo();
    }
    if (isActivelyScrubbing()) return;
    addZoomAtClick(e).catch(() => {});
  });

  els.preview.addEventListener("play", () => {
    hideStillAndSyncVideo();
  });

  els.preview.addEventListener("pause", () => {
    if (!isActivelyScrubbing()) {
      setStillMode(false);
    }
  });

  els.preview.addEventListener("timeupdate", () => {
    if (isActivelyScrubbing() || els.preview.paused) return;
    updatePlayheadUi(els.preview.currentTime || 0);
  });

  els.preview.addEventListener("seeked", () => {
    if (isActivelyScrubbing()) return;
    if (!els.preview.paused) {
      updatePlayheadUi(els.preview.currentTime || 0);
      postPlayhead(state.playheadSec);
    }
  });

function hasActiveRangeSelection() {
  return (
    state.selectionStart != null &&
    state.selectionEnd != null &&
    state.selectionEnd - state.selectionStart >= SELECTION_MIN_SEC
  );
}

function isEditableFocus(target) {
  if (!target) return false;
  const tag = (target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (target.isContentEditable) return true;
  if (target.closest?.("[contenteditable='true'], dialog[open]")) return true;
  return false;
}

async function deleteSelectedRange() {
  if (!hasActiveRangeSelection() || state.busy) return;
  const startSec = state.selectionStart;
  const endSec = state.selectionEnd;
  setSelectedTimelineItem(null);
  await mutate(() =>
    api("/api/timeline/cut-range", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_sec: startSec,
        end_sec: endSec,
        include_audio: true,
      }),
    })
  );
}

async function deleteSelectedTimelineItem() {
  const item = state.selectedTimelineItem;
  if (!item || state.busy) return;
  setSelectedTimelineItem(null);
  if (item.type === "audio") {
    await mutate(() =>
      api("/api/audio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "remove",
          clip_index: item.index,
        }),
      })
    );
    return;
  }
  if (item.type === "video") {
    await mutate(() =>
      api("/api/clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "remove",
          clip_index: item.index,
        }),
      })
    );
  }
}

  wireScrubberEvents({
    onSelectChange: () => renderClipStrip(),
    onDeleteSelection: () => {
      deleteSelectedRange().catch(() => {});
    },
  });

  document.addEventListener("keydown", (e) => {
    if (isEditableFocus(e.target)) return;
    if (e.key === "Escape") {
      if (state.selectedTimelineItem || hasActiveRangeSelection()) {
        e.preventDefault();
        setSelectedTimelineItem(null);
        clearSelectionUiAndServer();
        renderClipStrip();
      }
      return;
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      if (state.busy) return;
      if (hasActiveRangeSelection()) {
        e.preventDefault();
        deleteSelectedRange().catch(() => {});
      } else if (state.selectedTimelineItem) {
        e.preventDefault();
        deleteSelectedTimelineItem().catch(() => {});
      }
    }
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
