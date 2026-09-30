"use strict";
const $ = id => document.getElementById(id);
const embedded = window.__CODEX_USAGE_MCP__ === true;
function stored(kind, name, value) {
  try { const store = window[kind]; if (value === undefined) return store.getItem(name); store.setItem(name, value); } catch (_) { return null; }
}
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("key")) {
  stored("sessionStorage", "codexUsageKey", fragment.get("key"));
  history.replaceState(null, "", location.pathname + location.search);
}
const key = fragment.get("key") || stored("sessionStorage", "codexUsageKey") || "";
let selected = stored("localStorage", "codexUsageThread") || "";
let scope = stored("localStorage", "codexUsageScope") || "local";
let snapshot = null;
let loading = false;
let lastSuccess = null;
$("scope").value = scope;
const full = n => n == null ? "—" : new Intl.NumberFormat("zh-CN").format(n);
const brief = n => n == null ? "—" : n >= 1e9 ? (n / 1e9).toFixed(2) + "B" : n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "K" : String(n);
const percent = n => n == null ? "—" : Number(n).toFixed(n % 1 ? 1 : 0) + "%";
const at = (value, tz) => value ? new Date(value).toLocaleString("zh-CN", {timeZone: tz || "Asia/Shanghai", month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false}) : "—";
function text(id, value) { $(id).textContent = value; }
function quotaElement(bucket, window, stale) {
  const row = document.createElement("div");
  row.className = "quota" + (window.remainingPercent != null && window.remainingPercent < 15 ? " low" : window.remainingPercent != null && window.remainingPercent < 35 ? " medium" : "");
  const head = document.createElement("div"); head.className = "metric-heading";
  const name = document.createElement("span"); name.textContent = (bucket.id !== "codex" ? bucket.name + " · " : "") + window.label;
  const value = document.createElement("strong"); value.textContent = percent(window.remainingPercent);
  head.append(name, value);
  const meter = document.createElement("meter"); meter.min = 0; meter.max = 100; meter.value = window.remainingPercent || 0;
  meter.setAttribute("aria-label", window.label + "剩余额度");
  const note = document.createElement("p");
  if (stale) note.textContent = "上次成功读取的额度，等待重新连接";
  else if (window.resetsAt && window.resetsAt * 1000 <= Date.now()) note.textContent = "已到重置时间，等待官方更新";
  else note.textContent = window.resetsAt ? "重置于 " + at(window.resetsAt * 1000, snapshot.local.timezone) : "未提供重置时间";
  row.append(head, meter, note); return row;
}
function renderAccount() {
  const account = snapshot.account;
  text("plan", account.planType ? account.planType.toUpperCase() : account.authType === "apiKey" ? "API KEY" : "未识别账户");
  const parent = $("quotas"); parent.replaceChildren();
  const extra = $("extra-quota-content"); extra.replaceChildren();
  let extraCount = 0;
  for (const bucket of account.buckets) {
    for (const window of bucket.windows) {
      const node = quotaElement(bucket, window, account.status === "stale");
      if (window.visibleByDefault) parent.append(node);
      else { extra.append(node); extraCount++; }
    }
    if (bucket.credits && (bucket.credits.hasCredits || bucket.credits.unlimited)) {
      const note = document.createElement("p"); note.className = "footnote";
      note.textContent = bucket.credits.unlimited ? "官方 credits：不限额" : "官方 credits 余额：" + (bucket.credits.balance ?? "未提供");
      parent.append(note);
    }
  }
  if (!parent.children.length) {
    const note = document.createElement("p"); note.className = "muted";
    note.textContent = account.status === "loading" ? "正在读取官方额度…" : account.error || "官方没有返回可用额度窗口";
    parent.append(note);
  }
  $("extra-quotas").hidden = !extraCount;
  text("quota-updated", account.updatedAt ? "读取于 " + at(account.updatedAt, snapshot.local.timezone) : "每 60 秒更新");
}
function renderTokens() {
  const official = scope === "official";
  const data = official ? snapshot.account.usage : snapshot.local;
  for (const period of ["today", "week", "month", "all"]) {
    const n = data?.totals?.[period];
    text(period, brief(n));
    $(period).title = full(n) + " tokens";
    $(period).parentNode.title = full(n) + " tokens";
  }
  text("chart-date", snapshot.local.periodDate.slice(5));
  const days = new Map((data?.daily || []).map(item => [item.date, item.tokens]));
  const today = new Date(snapshot.local.periodDate + "T00:00:00Z");
  const dates = Array.from({length:14}, (_, i) => new Date(today.getTime() - (13 - i) * 86400000).toISOString().slice(0,10));
  const maximum = Math.max(1, ...dates.map(day => days.get(day) || 0));
  $("chart").replaceChildren();
  for (const [i, day] of dates.entries()) {
    const n = days.get(day);
    const missing = official && n == null;
    const column = document.createElement("div"); column.className = "bar-column";
    column.title = day + " · " + (missing ? "官方未返回该日期" : full(n || 0) + " tokens");
    column.setAttribute("aria-label", column.title);
    const bar = document.createElement("div"); bar.className = "bar"; bar.style.height = ((n || 0) / maximum * 100) + "%";
    if (missing) bar.style.opacity = "0.25";
    column.append(bar);
    if ([0,6,13].includes(i)) { const label = document.createElement("small"); label.textContent = day.slice(5).replace("-", "/"); column.append(label); }
    $("chart").append(column);
  }
  let note;
  if (official) {
    note = data ? "官方日桶最新日期：" + (data.latestDate || "未返回") + "。今日缺失显示 —；周/月仅合计已返回日桶，可能不完整。日桶时区由服务决定。" : "官方 token 汇总尚未返回。可切换到本机实时统计。";
  } else {
    note = "按 " + snapshot.local.timezone + " 统计；本周从周一开始。含本机归档及子代理，覆盖 " + snapshot.local.fileCount + " 个日志；其他设备与已删除记录不包含在内。";
    if (snapshot.local.readErrors || snapshot.local.skippedLines) note += " 部分日志无法读取或格式不完整，统计可能缺失。";
  }
  text("source-note", note);
}
function renderThreads() {
  const select = $("thread"); select.replaceChildren();
  const automatic = document.createElement("option"); automatic.value = ""; automatic.textContent = "跟随最近有活动的聊天"; select.append(automatic);
  for (const thread of snapshot.local.threads) {
    const option = document.createElement("option"); option.value = thread.id; option.textContent = thread.project + " / " + thread.title; select.append(option);
  }
  if (selected && !snapshot.local.threads.some(thread => thread.id === selected)) {
    const option = document.createElement("option"); option.value = selected; option.textContent = "所选聊天暂无可读取记录"; select.append(option);
  }
  select.value = selected;
  text("selection-mode", selected ? "手动选择" : "最近活动");
  const thread = snapshot.local.selectedThread;
  text("thread-title", thread?.title || "暂无可用聊天用量记录");
  $("thread-title").title = thread?.title || "";
  text("model", thread ? (thread.model || "未知模型") + " · " + thread.id.slice(0,8) : "");
  text("cache", percent(thread?.cacheHitPercent));
  $("cache-meter").value = thread?.cacheHitPercent || 0;
  text("cache-detail", thread ? full(thread.cachedInputTokens) + " / " + full(thread.inputTokens) + " 输入 tokens · 累计命中 " + percent(thread.sessionCacheHitPercent) : "缓存输入 tokens ÷ 输入 tokens");
  text("context", percent(thread?.contextUsedPercent));
  $("context-meter").value = thread?.contextUsedPercent || 0;
  $("context-meter").parentNode.classList.toggle("low", thread?.contextUsedPercent >= 85);
  text("context-detail", thread ? full(thread.contextTokens) + " / " + full(thread.contextWindow) + " tokens" : "最近请求 tokens ÷ 日志记录的上下文上限");
  text("thread-updated", thread ? "最近请求记录：" + at(thread.updatedAt, snapshot.local.timezone) + (selected ? "" : " · 并非桌面端选中状态") : "切换聊天时可在上方手动选择。");
}
function render() {
  renderAccount(); renderTokens(); renderThreads();
  const error = snapshot.account.error;
  $("error").hidden = !error;
  text("error", error || "");
  if (snapshot.account.status === "stale") text("connection", "本机在线 · 官方额度读取失败");
  else text("connection", "本机在线 · " + at(lastSuccess, snapshot.local.timezone) + " 更新");
}
const bridgePending = new Map();
let bridgeId = 0;
let bridgeReady;
function bridgeRequest(method, params) {
  const id = ++bridgeId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { bridgePending.delete(id); reject(new Error("面板宿主未响应，可用本机浏览器面板打开。")); }, 12000);
    bridgePending.set(id, {resolve, reject, timer});
    window.parent.postMessage({jsonrpc:"2.0", id, method, params}, "*");
  });
}
if (embedded) {
  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.data?.jsonrpc !== "2.0") return;
    const message = event.data;
    const pending = bridgePending.get(message.id);
    if (pending) {
      clearTimeout(pending.timer); bridgePending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message || "宿主请求失败"));
      else pending.resolve(message.result);
    }
  });
}
async function readSnapshot(thread) {
  if (!embedded) {
    const response = await fetch("/api/snapshot" + (thread ? "?thread=" + encodeURIComponent(thread) : ""), {headers:{Authorization:"Bearer " + key}, cache:"no-store", signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error(response.status === 401 ? "访问密钥失效，请使用启动时的完整地址重新打开面板。" : "本机服务暂时无法读取统计。");
    return response.json();
  }
  if (!bridgeReady) {
    bridgeReady = bridgeRequest("ui/initialize", {appInfo:{name:"codex-usage-monitor",version:"0.1.0"}, appCapabilities:{}, protocolVersion:"2026-01-26"})
      .then(() => window.parent.postMessage({jsonrpc:"2.0",method:"ui/notifications/initialized"}, "*"))
      .catch(error => { bridgeReady = null; throw error; });
  }
  await bridgeReady;
  const result = await bridgeRequest("tools/call", {name:"get_usage_snapshot", arguments:thread ? {thread_id:thread} : {}});
  if (!result?.structuredContent || result.isError) throw new Error("宿主未返回可用用量数据。");
  return result.structuredContent;
}
async function poll() {
  if (loading) return;
  loading = true; $("refresh").disabled = true;
  const requestedThread = selected;
  try {
    const data = await readSnapshot(requestedThread);
    if (requestedThread === selected) { snapshot = data; lastSuccess = new Date(); render(); }
  } catch (error) {
    $("error").hidden = false;
    text("error", error.message || "连接中断，请检查本机服务是否仍在运行。");
    text("connection", "连接中断" + (lastSuccess ? " · 保留 " + at(lastSuccess) + " 的数据" : ""));
  } finally { loading = false; $("refresh").disabled = false; if (requestedThread !== selected) poll(); }
}
$("thread").addEventListener("change", () => { selected = $("thread").value; stored("localStorage", "codexUsageThread", selected); text("thread-title", "正在读取所选聊天…"); text("cache", "—"); text("context", "—"); poll(); });
$("scope").addEventListener("change", () => { scope = $("scope").value; stored("localStorage", "codexUsageScope", scope); if (snapshot) renderTokens(); });
$("refresh").addEventListener("click", poll);
$("compact").addEventListener("click", () => { document.body.classList.toggle("compact"); $("compact").textContent = document.body.classList.contains("compact") ? "完整视图" : "精简视图"; });
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
poll(); setInterval(() => { if (!document.hidden) poll(); }, 10000);
