// Pure helpers for the LEExtractor Zotero plugin. No Zotero or DOM calls here,

// so they can be tested with node (see test/core.test.js).

(function (root) {

  "use strict";



  const DEFAULT_BASE = "http://127.0.0.1:8000/api/v3";

  const MAX_ABSTRACT = 1500;



  // Only metadata leaves the user's machine: title, DOI, year and a bounded abstract.

  // Attachments, notes and file paths are never read here.

  function discoverText(items) {

    const parts = [];

    for (const item of items || []) {

      const title = String(item.title || "").trim();

      const doi = String(item.DOI || "").trim();

      if (title) parts.push(title);

      if (doi) parts.push("doi:" + doi);

    }

    return parts.join("; ").slice(0, 3000);

  }



  function selectedMetadata(items) {

    return (items || [])

      .filter(Boolean)

      .map((item) => {

        const get = (field) => (typeof item.getField === "function" ? item.getField(field) : item[field]) || "";

        return {

          key: item.key || "",

          title: String(get("title")),

          DOI: String(get("DOI")),

          year: String(get("date") || "").slice(0, 4),

          abstract: String(get("abstractNote")).slice(0, MAX_ABSTRACT),

        };

      });

  }



  function idempotencyKey(prefix) {

    // 16..128 characters, unique per user action.

    const random = Math.random().toString(36).slice(2, 12);

    return (prefix + "-" + Date.now().toString(36) + "-" + random).padEnd(16, "0").slice(0, 128);

  }



  function isTerminal(state) {

    return ["succeeded", "partial", "failed", "cancelled", "superseded", "interrupted"].includes(state);

  }



  const api = { DEFAULT_BASE, discoverText, selectedMetadata, idempotencyKey, isTerminal };

  if (typeof module !== "undefined" && module.exports) module.exports = api;

  root.LEExtractorCore = api;

  // Zotero 用 loadSubScript(url, scope) 加载本文件：scope 才是脚本的全局对象。
  // 只挂到 globalThis 上时 scope.LEExtractorCore 永远是 undefined，startup 会在
  // core.DEFAULT_BASE 处抛错，插件装了也什么都不做。
  if (root !== globalThis) {
    try {
      root.LEExtractorCore = api;
    } catch (error) {
      /* 顶层 this 不可写时忽略 */
    }
  }
})(typeof this !== "undefined" && this ? this : globalThis);

