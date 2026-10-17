// Zotero 7/8/9 bootstrap entry. Adds one additive section to the item pane;
// no existing panel, menu or preference is changed.
//
// 这一版刻意不碰鉴权接口：Zotero 进程里的 fetch 不带浏览器的会话 cookie，
// 走 /api/v3 的写接口会 403。所以插件只做两件确定能成的事：
//   1. 读取公开的 /api/v3/capabilities 判断本机服务是否在跑；
//   2. 把选中条目的题名/DOI 复制到剪贴板，并在浏览器里打开检索页。
// 需要"一键把条目写进项目"时，请用网页侧的「从本机 Zotero 导入」侧栏。
var LEExtractorPlugin;

function install() {}
function uninstall() {}

function log(message) {
  try {
    Zotero.debug("[LEExtractor] " + message);
  } catch (error) {
    /* 调试输出失败不影响功能 */
  }
}

// 在 prefs.js 里留痕：装没装、启动走到哪一步、注册失败的原因。
// 这是没有调试控制台时唯一能落盘的证据（Zotero.debug 输出抓不到）。
function mark(name, value) {
  try {
    Zotero.Prefs.set("extensions.leextractor." + name, String(value), true);
  } catch (error) {
    /* 落盘失败不影响功能 */
  }
}

async function startup({ id, rootURI }) {
  await Zotero.initializationPromise;
  mark("startup", Date.now());
  const scope = {};
  Services.scriptloader.loadSubScript(rootURI + "lib/core.js", scope);
  const core = scope.LEExtractorCore;
  const base = Zotero.Prefs.get("extensions.leextractor.baseUrl", true) || core.DEFAULT_BASE;
  mark("scripts_loaded", Boolean(core && core.DEFAULT_BASE));

  LEExtractorPlugin = { id, core, rootURI, base };

  try {
    const result = registerSection(id);
    LEExtractorPlugin.sectionID = result.id;
    mark("section_variant", result.variant);
    mark("section_registered", LEExtractorPlugin.sectionID);
    log("section registered: " + LEExtractorPlugin.sectionID);
  } catch (error) {
    mark("error", (error && error.message) || String(error));
    log("registerSection failed: " + (error && error.message));
    throw error;
  }
}

// 标题文案的兜底：优先用 Fluent（l10nID），解析不到就退回纯文本 label。
// 之前的版本只用 l10nID，FTL 没被加载时 registerSection 直接抛错，区块就不出现。
function registerSection(pluginID) {
  const variants = [
    {
      header: { l10nID: "leextractor-section-header", icon: "chrome://zotero/skin/16/universal/book.svg" },
      sidenav: { l10nID: "leextractor-section-sidenav", icon: "chrome://zotero/skin/20/universal/book.svg" },
    },
    {
      header: { label: "LEExtractor", icon: "chrome://zotero/skin/16/universal/book.svg" },
      sidenav: { label: "LEExtractor", icon: "chrome://zotero/skin/20/universal/book.svg" },
    },
    { header: {}, sidenav: {} },
  ];
  let lastError = null;
  for (const [index, variant] of variants.entries()) {
    try {
      const id = Zotero.ItemPaneManager.registerSection({
        paneID: "leextractor-core",
        pluginID,
        onRender: ({ body, item }) => renderSection(body, item),
        ...variant,
      });
      return { id, variant: index };
    } catch (error) {
      lastError = error;
      log("header variant failed: " + (error && error.message));
    }
  }
  throw lastError || new Error("registerSection failed");
}

function shutdown() {
  if (LEExtractorPlugin && LEExtractorPlugin.sectionID) {
    try {
      Zotero.ItemPaneManager.unregisterSection(LEExtractorPlugin.sectionID);
    } catch (error) {
      log("unregisterSection failed: " + (error && error.message));
    }
  }
  LEExtractorPlugin = undefined;
}

/** 公开接口，不需要会话；用来判断本机服务是否在跑。 */
async function probe() {
  const url = LEExtractorPlugin.base.replace(/\/api\/v3\/?$/, "") + "/api/v3/capabilities";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) return { running: false, detail: "HTTP " + response.status };
    const payload = await response.json();
    return { running: true, detail: payload.deployment_mode || "server_basic" };
  } catch (error) {
    return { running: false, detail: "未连接" };
  } finally {
    clearTimeout(timer);
  }
}

function renderSection(body, item) {
  const doc = body.ownerDocument;
  body.replaceChildren();
  if (!item) return;

  const status = doc.createElement("div");
  status.textContent = "正在检查本机 LEExtractor…";
  status.style.cssText = "font-size:12px;margin-bottom:8px;color:#666;";

  const actions = doc.createElement("div");
  actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;";

  // 主操作：把这条文献送进 LEExtractor（不再让你复制题名再去粘贴）。
  // 落点带着条目键，侧栏打开后会自动勾上它，只需确认导入。
  const importButton = doc.createElement("button");
  importButton.id = "leextractor-import";
  importButton.textContent = "导入到 LEExtractor";
  importButton.addEventListener("click", () => {
    const base = LEExtractorPlugin.base.replace(/\/api\/v3\/?$/, "");
    const key = encodeURIComponent(String(item.key || ""));
    if (!key) {
      status.textContent = "这个条目没有可用的 Zotero 键。";
      return;
    }
    Zotero.launchURL(base + "/search?zotero=1&item=" + key);
    status.textContent = "已打开 LEExtractor，条目已预选，确认导入即可。";
  });

  const openButton = doc.createElement("button");
  openButton.textContent = "打开 LEExtractor";
  openButton.addEventListener("click", () => {
    const base = LEExtractorPlugin.base.replace(/\/api\/v3\/?$/, "");
    Zotero.launchURL(base + "/search");
  });

  actions.append(importButton, openButton);
  body.append(status, actions);

  probe().then((result) => {
    status.textContent = result.running
      ? "本机 LEExtractor 已连接（" + result.detail + "）。点「导入到 LEExtractor」把这条文献带过去。"
      : "未发现本机 LEExtractor（" + result.detail + "）。请先在仓库目录运行 python -m litsearch.web --port 8000。";
  });
}
