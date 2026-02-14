(function () {
  const html = document.documentElement;
  const themeToggle = document.getElementById("themeToggle");
  const stored = localStorage.getItem("snapurl-theme");
  if (stored === "dark" || stored === "light") {
    html.setAttribute("data-theme", stored);
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      const current = html.getAttribute("data-theme") || "light";
      const next = current === "light" ? "dark" : "light";
      html.setAttribute("data-theme", next);
      localStorage.setItem("snapurl-theme", next);
    });
  }

  // Sync visible options checkboxes into submitted form
  const form = document.querySelector("form.capture-form");
  if (form) {
    form.addEventListener("submit", () => {
      const optionContainer = document.querySelector(".options");
      if (!optionContainer) return;

      // Remove old hidden fields
      form.querySelectorAll("input[data-synced='1']").forEach(el => el.remove());

      const checkboxes = optionContainer.querySelectorAll("input[type='checkbox']");
      checkboxes.forEach((cb) => {
        if (cb.checked) {
          const hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = cb.name;
          hidden.value = "on";
          hidden.setAttribute("data-synced", "1");
          form.appendChild(hidden);
        }
      });
    });
  }
})();


// Poll job statuses for sidebar items and show run time when they complete
(function () {
  const POLL_INTERVAL = 2000; // ms
  const SHOW_RUNTIME_MS = 7000; // show runtime for a few seconds

  function parseISO(s) {
    try {
      return new Date(s);
    } catch (e) {
      return null;
    }
  }

  function formatDurationMs(ms) {
    if (ms < 1000) return `${ms}ms`;
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(2)}s`;
    const m = Math.floor(s / 60);
    const rem = (s % 60).toFixed(0);
    return `${m}m ${rem}s`;
  }

  function updateRowStatus(rowEl, status) {
    const badge = rowEl.querySelector('.history-status');
    if (!badge) return;
    // update classes
    badge.className = `badge badge-${status} history-status`;
    badge.textContent = status;
  }

  function showRuntimeTransient(rowEl, createdAt, updatedAt) {
    const created = parseISO(createdAt);
    const updated = parseISO(updatedAt);
    if (!created || !updated) return;
    const ms = updated - created;
    const formatted = formatDurationMs(ms);

    const badge = rowEl.querySelector('.history-status');
    if (!badge) return;

    const priorText = badge.textContent;
    badge.textContent = `${priorText} (${formatted})`;
    setTimeout(() => {
      badge.textContent = priorText;
    }, SHOW_RUNTIME_MS);
  }

  function pollJob(jobId, rowEl) {
    let stopped = false;
    async function check() {
      try {
        const resp = await fetch(`/api/status/${jobId}`);
        if (!resp.ok) {
          // Keep polling on transient errors
          return;
        }
        const data = await resp.json();
        if (!data.ok) return;
        const payload = data;
        // compatibility: payload may be wrapped or be direct
        const info = payload.ok && payload.job_id ? payload : (payload || {});
        const status = info.status || (info.data && info.data.status) || '';
        const created_at = info.created_at || (info.data && info.data.created_at) || '';
        const updated_at = info.updated_at || (info.data && info.data.updated_at) || '';

        const currentBadge = rowEl.querySelector('.history-status');
        const current = currentBadge ? currentBadge.textContent.trim() : '';
        if (status && status !== current) {
          updateRowStatus(rowEl, status);
          if (status === 'done') {
            showRuntimeTransient(rowEl, created_at, updated_at);
          }
        }

        if (['done', 'error'].includes(status)) {
          stopped = true; // stop polling for this job
        }
      } catch (e) {
        // ignore and retry
      }
    }

    // initial check then interval
    check();
    const iv = setInterval(() => {
      if (stopped) {
        clearInterval(iv);
        return;
      }
      check();
    }, POLL_INTERVAL);
  }

  function initPollers() {
    const rows = document.querySelectorAll('.history-item[data-job-id]');
    rows.forEach((r) => {
      const jobId = r.getAttribute('data-job-id');
      if (jobId) pollJob(jobId, r);
    });
  }

  // Start after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPollers);
  } else {
    initPollers();
  }
})();