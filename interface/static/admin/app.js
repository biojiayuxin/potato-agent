(() => {
  "use strict";

  const SYSTEM_REFRESH_INTERVAL_MS = 5000;
  const OVERVIEW_REFRESH_INTERVAL_MS = 60 * 1000;
  const state = {
    window: "24h", type: "all", q: "", page: 1, totalPages: 1,
    sort: "default", direction: "desc", overviewController: null,
    overviewRefreshTimer: null, systemRefreshTimer: null, systemRequestInFlight: false
  };
  const elements = Object.fromEntries([
    "login-view", "dashboard-view", "login-form", "login", "password", "login-error",
    "login-submit", "admin-name", "signout", "cpu-value", "memory-used",
    "memory-available", "system-grid", "swap-metric", "swap-value", "load-value", "sample-time",
    "window-control", "type-filter", "user-search", "total-users", "total-input-tokens",
    "total-output-tokens", "total-cache-read-tokens", "total-tokens",
    "table-state", "table-wrap", "users-body", "pagination", "previous-page", "next-page", "page-label"
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
    state.overviewRefreshTimer = window.setInterval(
      () => loadOverview({ silent: true, skipIfBusy: true }),
      OVERVIEW_REFRESH_INTERVAL_MS
    );
    state.systemRefreshTimer = window.setInterval(loadSystem, SYSTEM_REFRESH_INTERVAL_MS);
  }

  function stopDashboardPolling() {
    if (state.overviewRefreshTimer) window.clearInterval(state.overviewRefreshTimer);
    if (state.systemRefreshTimer) window.clearInterval(state.systemRefreshTimer);
    state.overviewRefreshTimer = null;
    state.systemRefreshTimer = null;
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
