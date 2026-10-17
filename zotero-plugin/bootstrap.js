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

async function startup({ id, rootURI }) {
  await Zotero.initializationPromise;
  const scope = {};
  Services.scriptloader.loadSubScript(rootURI + "lib/core.js", scope);
  const core = scope.LEExtractorCore;
  const base = Zotero.Prefs.get("extensions.leextractor.baseUrl", true) || core.DEFAULT_BASE;

  LEExtractorPlugin = { id, core, rootURI, base };

  try {
    LEExtractorPlugin.sectionID = Zotero.ItemPaneManager.registerSection({
      paneID: "leextractor-core",
      pluginID: id,
      header: { l10nID: "leextractor-section-header", icon: "chrome://zotero/skin/16/universal/book.svg" },
      sidenav: { l10nID: "leextractor-section-sidenav", icon: "chrome://zotero/skin/20/universal/book.svg" },
      onRender: ({ body, item }) => renderSection(body, item),
    });
    log("section registered: " + LEExtractorPlugin.sectionID);
  } catch (error) {
    log("registerSection failed: " + (error && error.message));
    throw error;
  }
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

  const copyButton = doc.createElement("button");
  copyButton.textContent = "复制题名与 DOI";
  copyButton.addEventListener("click", async () => {
    const text = LEExtractorPlugin.core.discoverText([
      { title: item.getField("title"), DOI: item.getField("DOI") },
    ]);
    try {
      await Zotero.Utilities.Internal.copyTextToClipboard(text);
      status.textContent = "已复制：" + text.slice(0, 80);
    } catch (error) {
      status.textContent = "复制失败：" + (error.message || error);
    }
  });

  const openButton = doc.createElement("button");
  openButton.textContent = "打开 LEExtractor 检索页";
  openButton.addEventListener("click", () => {
    Zotero.launchURL("http://127.0.0.1:8000/search");
  });

  actions.append(copyButton, openButton);
  body.append(status, actions);

  probe().then((result) => {
    status.textContent = result.running
      ? "本机 LEExtractor 已连接（" + result.detail + "）。在条目上复制题名，再到检索页粘贴即可。"
      : "未发现本机 LEExtractor（" + result.detail + "）。请先在仓库目录运行 python -m litsearch.web --port 8000。";
  });
}
