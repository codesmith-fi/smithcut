/** API-kutsut, status-palkki ja edistymistilan pollaus. */

import { getLang, t } from "./i18n.js";
import { els, state } from "./state.js";

export function withLang(path) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("lang", getLang());
  return url.pathname + url.search;
}

export async function api(path, options = {}) {
  const res = await fetch(withLang(path), options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.detail || t("errorStatus", { status: res.status }));
  }
  return data;
}

export function setProgressBar(percent) {
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

export function stopProgressPoll() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

export function setStatus(message, busy = false) {
  state.busy = busy;
  els.status.textContent = message;
  els.status.classList.toggle("is-busy", busy);
  if (!busy) {
    stopProgressPoll();
    setProgressBar(null);
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

export function applyProgress(progress) {
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

export function startProgressPoll() {
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

export function showReply(text) {
  state.lastReply = text;
  els.commandReply.hidden = false;
  els.commandReply.textContent = text;
}
