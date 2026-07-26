(() => {
  "use strict";

  const TERMINAL_STATUSES = new Set([
    "succeeded", "failed", "timeout", "cancelled", "unknown-interrupted",
  ]);

  const LIST_POLL_MS = 3000;
  const DETAIL_POLL_MS = 2000;
  const HEALTH_POLL_MS = 5000;
  const TAIL_LINES = 200;

  const state = {
    tasks: [],
    selectedId: null,
    activeLog: "stdout",
    lastOutput: null,
  };

  const el = (id) => document.getElementById(id);

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    return { ok: res.ok, status: res.status, body };
  }

  function fmtElapsed(seconds) {
    if (seconds == null) return "—";
    const s = Math.floor(seconds);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  }

  function statusBadge(status) {
    const span = document.createElement("span");
    span.className = `status-badge status-${status}`;
    span.textContent = status;
    return span;
  }

  // --- health ---

  async function pollHealth() {
    const dot = el("health-dot");
    const text = el("health-text");
    try {
      const { ok } = await fetchJson("/api/health");
      dot.className = ok ? "health-dot ok" : "health-dot down";
      text.textContent = ok ? "connected" : "unreachable";
    } catch (_) {
      dot.className = "health-dot down";
      text.textContent = "unreachable";
    }
  }

  // --- task list ---

  function buildListQuery() {
    const params = new URLSearchParams();
    const status = el("filter-status").value;
    const contextId = el("filter-context").value.trim();
    if (status) params.set("status_filter", status);
    if (contextId) params.set("context_id", contextId);
    params.set("limit", "200");
    return params.toString();
  }

  async function pollTasks() {
    const { ok, body } = await fetchJson(`/api/tasks?${buildListQuery()}`);
    if (!ok || !Array.isArray(body)) return;
    state.tasks = body;
    renderTaskList();
  }

  function renderTaskList() {
    const ongoing = state.tasks.filter((t) => !TERMINAL_STATUSES.has(t.status));
    const finished = state.tasks.filter((t) => TERMINAL_STATUSES.has(t.status));

    el("ongoing-count").textContent = String(ongoing.length);
    el("finished-count").textContent = String(finished.length);
    el("ongoing-empty").hidden = ongoing.length > 0;
    el("finished-empty").hidden = finished.length > 0;

    renderTaskGroup(el("ongoing-list"), ongoing);
    renderTaskGroup(el("finished-list"), finished);
  }

  function renderTaskGroup(listEl, tasks) {
    listEl.innerHTML = "";
    for (const task of tasks) {
      const li = document.createElement("li");
      li.className = "task-row" + (task.job_id === state.selectedId ? " selected" : "");
      li.dataset.jobId = task.job_id;

      const top = document.createElement("div");
      top.className = "task-row-top";
      const label = document.createElement("span");
      label.className = "task-row-label";
      label.textContent = task.label || task.job_id;
      top.appendChild(label);
      top.appendChild(statusBadge(task.status));
      li.appendChild(top);

      const meta = document.createElement("div");
      meta.className = "task-row-meta";
      const detailBits = [task.job_id];
      if (task.status === "pending" && task.queue_position != null) {
        detailBits.push(`queue #${task.queue_position}`);
      } else if (task.elapsed_s != null) {
        detailBits.push(fmtElapsed(task.elapsed_s));
      }
      meta.textContent = detailBits.join(" · ");
      li.appendChild(meta);

      li.addEventListener("click", () => selectTask(task.job_id));
      listEl.appendChild(li);
    }
  }

  // --- task detail ---

  function selectTask(jobId) {
    state.selectedId = jobId;
    el("detail-empty").hidden = true;
    el("detail-content").hidden = false;
    renderTaskList(); // refresh "selected" highlight
    refreshDetail();
  }

  async function refreshDetail() {
    if (!state.selectedId) return;
    const jobId = state.selectedId;

    const [detailRes, outputRes] = await Promise.all([
      fetchJson(`/api/tasks/${encodeURIComponent(jobId)}`),
      fetchJson(`/api/tasks/${encodeURIComponent(jobId)}/output?` +
        (el("tail-toggle").checked ? `tail_lines=${TAIL_LINES}` : "")),
    ]);

    if (state.selectedId !== jobId) return; // selection changed mid-flight

    if (!detailRes.ok) {
      el("detail-label").textContent = "Task not found";
      el("detail-job-id").textContent = jobId;
      return;
    }

    renderDetail(detailRes.body, outputRes.ok ? outputRes.body : null);
  }

  function renderDetail(task, output) {
    state.lastOutput = output;

    el("detail-label").textContent = task.label || "(no label)";
    el("detail-job-id").textContent = task.job_id;

    const badgeHost = el("detail-status-badge");
    badgeHost.className = `status-badge status-${task.status}`;
    badgeHost.textContent = task.status;

    const cancelBtn = el("cancel-btn");
    const cancellable = task.status === "pending" || task.status === "running";
    cancelBtn.hidden = !cancellable;
    cancelBtn.disabled = false;
    cancelBtn.onclick = () => cancelTask(task.job_id);

    const meta = el("detail-meta");
    meta.innerHTML = "";
    const rows = [
      ["context_id", task.context_id],
      ["model", task.model || "(default)"],
      ["created_at", task.created_at],
      ["started_at", task.started_at || "—"],
      ["elapsed", fmtElapsed(task.elapsed_s)],
      ["timeout_s", task.timeout_s],
      ["exit_code", task.exit_code ?? "—"],
      ["error_kind", task.error_kind || "—"],
      ["quota_reset_at", task.quota_reset_at || "—"],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = String(v);
      meta.appendChild(dt);
      meta.appendChild(dd);
    }

    renderLogBody(output);
  }

  function renderLogBody(output) {
    const body = el("log-body");
    if (!output) {
      body.textContent = "(no output yet)";
      return;
    }
    const text = output[state.activeLog] || "";
    const truncatedNote = output.truncated ? "\n\n… (truncated, showing tail)" : "";
    body.textContent = (text || "(empty)") + (text ? truncatedNote : "");
  }

  async function cancelTask(jobId) {
    const btn = el("cancel-btn");
    btn.disabled = true;
    await fetchJson(`/api/tasks/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    await Promise.all([pollTasks(), refreshDetail()]);
  }

  // --- wiring ---

  document.querySelectorAll(".log-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".log-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.activeLog = tab.dataset.log;
      renderLogBody(state.lastOutput);
    });
  });

  el("tail-toggle").addEventListener("change", refreshDetail);
  el("refresh-btn").addEventListener("click", () => { pollTasks(); refreshDetail(); });
  el("filter-status").addEventListener("change", pollTasks);
  let filterDebounce;
  el("filter-context").addEventListener("input", () => {
    clearTimeout(filterDebounce);
    filterDebounce = setTimeout(pollTasks, 250);
  });

  pollHealth();
  pollTasks();
  setInterval(pollHealth, HEALTH_POLL_MS);
  setInterval(pollTasks, LIST_POLL_MS);
  setInterval(() => { if (state.selectedId) refreshDetail(); }, DETAIL_POLL_MS);
})();
