// Zotero 7 bootstrap entry. Adds one additive section to the item pane; no existing
// panel, menu or preference is changed. Requires the LEExtractor service on 127.0.0.1.
var LEExtractorPlugin;

function install() {}
function uninstall() {}

async function startup({ id, rootURI }) {
  await Zotero.initializationPromise;
  const scope = {};
  Services.scriptloader.loadSubScript(rootURI + "lib/core.js", scope);
  Services.scriptloader.loadSubScript(rootURI + "lib/client.js", scope);
  const core = scope.LEExtractorCore;
  const clientFactory = scope.LEExtractorClient;
  const base = (Zotero.Prefs.get("extensions.leextractor.baseUrl", true)) || core.DEFAULT_BASE;

  LEExtractorPlugin = { id, core, client: clientFactory.makeClient(base, (...args) => fetch(...args)) };

  // Item-pane section: shows a button, then the ranked core list for the selected items.
  LEExtractorPlugin.sectionID = Zotero.ItemPaneManager.registerSection({
    paneID: "leextractor-core",
    pluginID: id,
    header: { l10nID: "leextractor-section-header", icon: "chrome://zotero/skin/16/universal/book.svg" },
    sidenav: { l10nID: "leextractor-section-header", icon: "chrome://zotero/skin/20/universal/book.svg" },
    onRender: ({ body, item }) => renderSection(body, item),
  });
}

function shutdown() {
  if (LEExtractorPlugin && LEExtractorPlugin.sectionID) {
    Zotero.ItemPaneManager.unregisterSection(LEExtractorPlugin.sectionID);
  }
  LEExtractorPlugin = undefined;
}

function renderSection(body, item) {
  const doc = body.ownerDocument;
  body.replaceChildren();
  const button = doc.createElement("button");
  button.textContent = "找核心文献";
  const out = doc.createElement("div");
  out.textContent = "基于所选条目的题名、DOI 查找（仅发送元数据）。";
  button.addEventListener("click", async () => {
    out.textContent = "正在查找…";
    try {
      const items = [item].map((i) => ({ title: i.getField("title"), DOI: i.getField("DOI") }));
      const lines = await findCore(LEExtractorPlugin.core.discoverText(items));
      out.replaceChildren(...lines.map((text) => {
        const row = doc.createElement("div");
        row.textContent = text;
        return row;
      }));
    } catch (problem) {
      out.textContent = "服务不可用：" + (problem.message || problem.code || "未知错误");
    }
  });
  body.append(button, out);
}

async function findCore(text) {
  const { client, core } = LEExtractorPlugin;
  await client.session();
  const project = await client.createProject("Zotero: " + text.slice(0, 60), core.idempotencyKey("proj"));
  const accepted = await client.discover(project.project_id || project.id, {
    expected_revision: project.revision ?? 0,
    research_text: text,
    mode: "basic",
  }, core.idempotencyKey("disc"));
  const pid = project.project_id || project.id;
  let job = accepted;
  for (let i = 0; i < 60 && !core.isTerminal(job.state); i++) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    job = await client.job(pid, accepted.job_id);
  }
  const listing = await client.papers(pid, { limit: 20 });
  return (listing.items || []).map((row, index) => `${index + 1}. ${row.title} (${row.year || "—"})`);
}
