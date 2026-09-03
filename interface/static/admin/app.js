(() => {
  "use strict";

  const SYSTEM_REFRESH_INTERVAL_MS = 5000;
  const OVERVIEW_REFRESH_INTERVAL_MS = 60 * 1000;
  const CLEANUP_REFRESH_INTERVAL_MS = 60 * 1000;
  const state = {
    window: "24h", type: "all", q: "", page: 1, totalPages: 1,
    sort: "default", direction: "desc", overviewController: null,
    overviewRefreshTimer: null, systemRefreshTimer: null, cleanupRefreshTimer: null,
    systemRequestInFlight: false, cleanupRequestInFlight: false
  };
  const elements = Object.fromEntries([
    "login-view", "dashboard-view", "login-form", "login", "password", "login-error",
    "login-submit", "admin-name", "signout", "cpu-value", "memory-used",
    "memory-available", "system-grid", "swap-metric", "swap-value", "load-value", "sample-time",
    "window-control", "type-filter", "user-search", "total-users", "total-input-tokens",
    "total-output-tokens", "total-cache-read-tokens", "total-tokens",
    "table-state", "table-wrap", "users-body", "pagination", "previous-page", "next-page", "page-label",
    "cleanup-policy", "cleanup-run", "cleanup-state", "cleanup-data", "cleanup-candidates",
    "cleanup-candidates-bytes", "cleanup-staged", "cleanup-staged-bytes", "cleanup-due",
    "cleanup-due-bytes", "cleanup-purged", "cleanup-purged-bytes", "cleanup-history",
    "cleanup-history-bytes", "cleanup-users"
  ].map((id) => [id, document.getElementById(id)]));

  const numberFormatter = new Intl.NumberFormat("zh-CN");
  const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  });

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", "\"": "&quot;"
    })[character]);
  }

  function formatNumber(value) {
    if (value == null || value === "") return "--";
    return Number.isFinite(Number(value)) ? numberFormatter.format(Number(value)) : "--";
  }

  function formatBytes(value) {
    if (value == null || value === "") return "--";
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "--";
    const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
    let size = bytes;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    const digits = index === 0 || size >= 100 ? 0 : size >= 10 ? 1 : 2;
    return `${size.toFixed(digits)} ${units[index]}`;
  }

  function formatDate(value) {
    if (!value) return "--";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "--" : dateFormatter.format(date);
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    if (response.status === 401 && !url.endsWith("/auth/signin")) {
      showLogin();
      throw new Error("unauthorized");
    }
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.detail || "请求失败");
      error.status = response.status;
      error.retryAfter = response.headers.get("Retry-After");
      throw error;
    }
    return payload;
  }

  function showLogin() {
    stopDashboardPolling();
    elements["dashboard-view"].hidden = true;
    elements["login-view"].hidden = false;
    elements.password.value = "";
  }

  function showDashboard(user) {
    stopDashboardPolling();
    elements["login-view"].hidden = true;
    elements["dashboard-view"].hidden = false;
    elements["admin-name"].textContent = user.name || user.username || user.email;
    loadOverview();
    loadSystem();
    loadCleanup();
    state.overviewRefreshTimer = window.setInterval(
      () => loadOverview({ silent: true, skipIfBusy: true }),
      OVERVIEW_REFRESH_INTERVAL_MS
    );
    state.systemRefreshTimer = window.setInterval(loadSystem, SYSTEM_REFRESH_INTERVAL_MS);
    state.cleanupRefreshTimer = window.setInterval(loadCleanup, CLEANUP_REFRESH_INTERVAL_MS);
  }

  function stopDashboardPolling() {
    if (state.overviewRefreshTimer) window.clearInterval(state.overviewRefreshTimer);
    if (state.systemRefreshTimer) window.clearInterval(state.systemRefreshTimer);
    if (state.cleanupRefreshTimer) window.clearInterval(state.cleanupRefreshTimer);
    state.overviewRefreshTimer = null;
    state.systemRefreshTimer = null;
    state.cleanupRefreshTimer = null;
    if (state.overviewController) state.overviewController.abort();
    state.overviewController = null;
  }

  async function bootstrap() {
    try {
      const session = await requestJson("/admin/api/auth/session");
      if (session.authenticated) showDashboard(session.user);
      else showLogin();
    } catch (_) { showLogin(); }
  }

  elements["login-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    elements["login-error"].hidden = true;
    elements["login-submit"].disabled = true;
    try {
      const payload = await requestJson("/admin/api/auth/signin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: elements.login.value.trim(), password: elements.password.value })
      });
      showDashboard(payload.user);
    } catch (error) {
      elements["login-error"].textContent = error.status === 429
        ? `尝试次数过多，请在 ${error.retryAfter || "稍后"} 秒后重试。`
        : "账号或密码无效。";
      elements["login-error"].hidden = false;
    } finally {
      elements["login-submit"].disabled = false;
    }
  });

  elements.signout.addEventListener("click", async () => {
    elements.signout.disabled = true;
    try { await requestJson("/admin/api/auth/signout", { method: "POST" }); }
    catch (_) { /* Cookie is cleared on the normal path; otherwise return to login locally. */ }
    finally { elements.signout.disabled = false; showLogin(); }
  });

  function renderSystem(data) {
    elements["cpu-value"].textContent = data.cpu_percent == null ? "--" : `${Number(data.cpu_percent).toFixed(1)}%`;
    elements["memory-used"].textContent = formatBytes(data.memory?.used_bytes);
    elements["memory-available"].textContent = formatBytes(data.memory?.available_bytes);
    const hasSwap = data.swap_bytes != null;
    elements["swap-metric"].hidden = !hasSwap;
    elements["system-grid"].classList.toggle("with-swap", hasSwap);
    elements["swap-value"].textContent = formatBytes(data.swap_bytes);
    const load = data.load || {};
    elements["load-value"].textContent = [load.one, load.five, load.fifteen]
      .map((value) => value == null ? "--" : Number(value).toFixed(2)).join(" / ");
    elements["sample-time"].textContent = data.sampled_at ? formatDate(data.sampled_at) : "";
  }

  async function loadSystem() {
    if (elements["dashboard-view"].hidden || state.systemRequestInFlight) return;
    state.systemRequestInFlight = true;
    try { renderSystem(await requestJson("/admin/api/system")); }
    catch (error) {
      if (error.message !== "unauthorized") renderSystem({ status: "unavailable", memory: {}, load: {} });
    }
    finally { state.systemRequestInFlight = false; }
  }

  const cleanupReasonLabels = {
    none: "--", runtime_active: "Agent 正在运行", background_jobs: "后台任务运行中",
    login_session: "登录或 SSH 会话活跃", uid_processes: "用户进程运行中",
    activity_check_failed: "活动检查失败", lock_busy: "用户入口占用中",
    invalid_identity: "用户身份预检失败", unsafe_filesystem: "文件系统预检失败",
    atime_unsupported: "atime 语义不安全", limit_reached: "达到扫描上限",
    mapping_changed: "映射已变化", new_origin_preview: "新身份需先完成预览",
    authorization_required: "缺少 enforce 授权", policy_changed: "策略已变化",
    journal_error: "清理日志异常", other: "其他安全跳过原因"
  };

  const cleanupStatusLabels = {
    scanned: "已扫描", skipped: "已跳过", partial: "部分完成", error: "错误"
  };

  function cleanupCountAndBytes(files, bytes) {
    return `${formatNumber(files)} 个 · ${formatBytes(bytes)}`;
  }

  function renderCleanup(data) {
    if (data.status === "unavailable" || !data.totals) {
      elements["cleanup-state"].textContent = "文件清理状态暂时不可用";
      elements["cleanup-state"].hidden = false;
      elements["cleanup-data"].hidden = true;
      elements["cleanup-policy"].textContent = "每日 05:00 · Asia/Shanghai";
      elements["cleanup-run"].textContent = "";
      return;
    }
    const policy = data.policy || {};
    const mode = policy.mode === "enforce" ? "执行" : "预览";
    elements["cleanup-policy"].textContent = `${mode}模式 · 未活动 ${policy.inactive_days} 天 · 历史保留 ${policy.history_days} 天 · 每日 05:00`;
    const run = data.latest_run || {};
    const runStatus = data.status === "stale" ? "心跳已过期" : ({ running: "运行中", ok: "完成", partial: "部分完成", failed: "失败" }[run.status] || "--");
    elements["cleanup-run"].textContent = `${runStatus}${run.started_at ? ` · ${formatDate(run.started_at)}` : ""}`;
    const totals = data.totals;
    [
      ["candidates", "candidate_files", "candidate_bytes"],
      ["staged", "staged_files", "staged_bytes"],
      ["due", "due_files", "due_bytes"],
      ["purged", "purged_files", "purged_bytes"],
      ["history", "history_files", "history_bytes"]
    ].forEach(([id, countKey, bytesKey]) => {
      elements[`cleanup-${id}`].textContent = formatNumber(totals[countKey]);
      elements[`cleanup-${id}-bytes`].textContent = formatBytes(totals[bytesKey]);
    });
    elements["cleanup-users"].innerHTML = (data.users || []).map((user) => `<div class="cleanup-table-row" role="row">
      <span role="cell" data-label="映射用户"><strong>${escapeHtml(user.mapping_username)}</strong></span>
      <span role="cell" data-label="状态">${escapeHtml(cleanupStatusLabels[user.status] || "错误")}</span>
      <span role="cell" data-label="原因">${escapeHtml(cleanupReasonLabels[user.reason] || cleanupReasonLabels.other)}</span>
      <span role="cell" data-label="候选">${cleanupCountAndBytes(user.candidate_files, user.candidate_bytes)}</span>
      <span role="cell" data-label="已转历史">${cleanupCountAndBytes(user.staged_files, user.staged_bytes)}</span>
      <span role="cell" data-label="到期">${cleanupCountAndBytes(user.due_files, user.due_bytes)}</span>
      <span role="cell" data-label="永久删除">${cleanupCountAndBytes(user.purged_files, user.purged_bytes)}</span>
    </div>`).join("") || '<div class="cleanup-empty">本轮没有映射用户</div>';
    elements["cleanup-state"].hidden = true;
    elements["cleanup-data"].hidden = false;
  }

  async function loadCleanup() {
    if (elements["dashboard-view"].hidden || state.cleanupRequestInFlight) return;
    state.cleanupRequestInFlight = true;
    try { renderCleanup(await requestJson("/admin/api/file-cleanup")); }
    catch (error) {
      if (error.message !== "unauthorized") renderCleanup({ status: "unavailable" });
    }
    finally { state.cleanupRequestInFlight = false; }
  }

  function usageValue(usage, field) {
    if (!usage || usage.status !== "available") return '<span class="unavailable">不可用</span>';
    return formatNumber(usage[field]);
  }

  function storageHtml(row) {
    if (row.lifecycle === "retired") return "不适用";
    if (!row.storage) return '<span class="user-secondary">尚无采样</span>';
    if (row.storage.status !== "ok") {
      return `<span class="unavailable">采样失败</span><div class="user-secondary">${escapeHtml(row.storage.error_code || "unavailable")}</div>`;
    }
    return `<strong>${formatBytes(row.storage.allocated_bytes)}</strong>`;
  }

  function renderRows(users) {
    elements["users-body"].innerHTML = users.map((row) => {
      const retired = row.lifecycle === "retired";
      const activityState = row.runtime_active ? "active" : "sleeping";
      const activityLabel = row.runtime_active ? "运行中" : "休眠";
      const identity = retired
        ? escapeHtml(row.mapping_username)
        : `${escapeHtml(row.name)}<div class="user-secondary">${escapeHtml(row.email)} · ${escapeHtml(row.mapping_username)}</div>`;
      return `<tr>
        <td data-label="用户"><div class="user-primary">${identity}</div></td>
        <td class="activity-cell" data-label="活跃"><span class="activity-dot ${activityState}" role="img" aria-label="${activityLabel}" title="${activityLabel}"></span></td>
        <td data-label="注册时间">${formatDate(row.created_at)}</td>
        <td data-label="输入 Token">${usageValue(row.usage, "input_tokens")}</td>
        <td data-label="输出 Token">${usageValue(row.usage, "output_tokens")}</td>
        <td data-label="缓存读">${usageValue(row.usage, "cache_read_tokens")}</td>
        <td data-label="Token 合计">${usageValue(row.usage, "total_tokens")}</td>
        <td data-label="请求">${row.usage?.status === "available" ? formatNumber(row.usage.request_count) : '<span class="unavailable">--</span>'}</td>
        <td data-label="目录空间">${storageHtml(row)}</td>
      </tr>`;
    }).join("");
  }

  async function loadOverview({ silent = false, skipIfBusy = false } = {}) {
    if (elements["dashboard-view"].hidden) return;
    if (skipIfBusy && state.overviewController) return;
    if (state.overviewController) state.overviewController.abort();
    const controller = new AbortController();
    state.overviewController = controller;
    if (!silent) {
      elements["table-state"].hidden = false;
      elements["table-state"].textContent = "正在加载用户数据";
      elements["table-wrap"].hidden = true;
      elements.pagination.hidden = true;
    }
    const params = new URLSearchParams({
      window: state.window, type: state.type, q: state.q, page: String(state.page),
      sort: state.sort, direction: state.direction
    });
    try {
      const data = await requestJson(`/admin/api/overview?${params}`, { signal: controller.signal });
      if (state.overviewController !== controller) return;
      const usageAvailable = data.data_sources?.usage?.status === "available";
      elements["total-users"].textContent = formatNumber(data.totals.user_count);
      elements["total-input-tokens"].textContent = usageAvailable ? formatNumber(data.totals.usage.input_tokens) : "不可用";
      elements["total-output-tokens"].textContent = usageAvailable ? formatNumber(data.totals.usage.output_tokens) : "不可用";
      elements["total-cache-read-tokens"].textContent = usageAvailable ? formatNumber(data.totals.usage.cache_read_tokens) : "不可用";
      elements["total-tokens"].textContent = usageAvailable ? formatNumber(data.totals.usage.total_tokens) : "不可用";
      state.page = data.pagination.page;
      state.totalPages = data.pagination.total_pages;
      if (!data.users.length) {
        elements["table-state"].textContent = "当前筛选条件下没有用户";
        elements["table-state"].hidden = false;
        elements["table-wrap"].hidden = true;
        elements.pagination.hidden = true;
        return;
      }
      renderRows(data.users);
      elements["table-state"].hidden = true;
      elements["table-wrap"].hidden = false;
      elements.pagination.hidden = false;
      elements["page-label"].textContent = `${state.page} / ${state.totalPages}`;
      elements["previous-page"].disabled = state.page <= 1;
      elements["next-page"].disabled = state.page >= state.totalPages;
    } catch (error) {
      if (error.name === "AbortError" || error.message === "unauthorized") return;
      if (!silent) elements["table-state"].textContent = "用户数据暂时不可用";
    } finally {
      if (state.overviewController === controller) state.overviewController = null;
    }
  }

  elements["window-control"].addEventListener("click", (event) => {
    const button = event.target.closest("button[data-window]");
    if (!button || button.dataset.window === state.window) return;
    state.window = button.dataset.window;
    state.page = 1;
    elements["window-control"].querySelectorAll("button").forEach((candidate) => {
      const active = candidate === button;
      candidate.classList.toggle("active", active);
      candidate.setAttribute("aria-pressed", String(active));
    });
    loadOverview();
  });

  elements["type-filter"].addEventListener("change", () => {
    state.type = elements["type-filter"].value;
    state.page = 1;
    loadOverview();
  });

  function updateSortHeadings() {
    elements["table-wrap"].querySelectorAll(".sort-heading[data-sort]").forEach((heading) => {
      const active = heading.dataset.sort === state.sort;
      const direction = active ? state.direction : null;
      const nextDirection = direction === "desc" ? "升序" : "降序";
      const label = heading.textContent.trim();
      heading.classList.toggle("active", active);
      heading.classList.toggle("ascending", direction === "asc");
      heading.classList.toggle("descending", direction === "desc");
      heading.title = `点击按${label}${nextDirection}排列`;
      heading.setAttribute("aria-label", active
        ? `${label}，当前${direction === "asc" ? "升序" : "降序"}，点击切换为${nextDirection}`
        : `${label}，点击按降序排列`);
      heading.closest("th").setAttribute(
        "aria-sort",
        direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"
      );
    });
  }

  elements["table-wrap"].addEventListener("click", (event) => {
    const heading = event.target.closest(".sort-heading[data-sort]");
    if (!heading) return;
    state.direction = state.sort === heading.dataset.sort && state.direction === "desc" ? "asc" : "desc";
    state.sort = heading.dataset.sort;
    state.page = 1;
    updateSortHeadings();
    loadOverview();
  });

  let searchTimer = null;
  elements["user-search"].addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = elements["user-search"].value.trim();
      state.page = 1;
      loadOverview();
    }, 250);
  });

  elements["previous-page"].addEventListener("click", () => {
    if (state.page > 1) { state.page -= 1; loadOverview(); }
  });
  elements["next-page"].addEventListener("click", () => {
    if (state.page < state.totalPages) { state.page += 1; loadOverview(); }
  });

  bootstrap();
})();
