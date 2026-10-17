// HTTP client for the local LEExtractor v3.2 service. Runs inside Zotero.
// Flow: session (CSRF) -> create project -> discover -> poll job -> list papers.
(function (root) {
  "use strict";

  function makeClient(base, fetchImpl) {
    const doFetch = fetchImpl || root.fetch;
    let csrf = null;

    async function call(method, path, body, extraHeaders) {
      const headers = Object.assign({ Accept: "application/json" }, extraHeaders || {});
      if (body !== undefined) headers["Content-Type"] = "application/json";
      if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
      const response = await doFetch(base + path, {
        method,
        headers,
        credentials: "include",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await response.text();
      const data = text ? JSON.parse(text) : null;
      if (!response.ok) {
        const error = (data && data.error) || { code: "http_" + response.status, message: "请求失败" };
        const problem = new Error(error.message || error.code);
        problem.code = error.code;
        problem.status = response.status;
        throw problem;
      }
      return data;
    }

    return {
      async session() {
        const data = await call("GET", "/session");
        csrf = data.csrf_token || null;
        return data;
      },
      createProject(name, key) {
        return call("POST", "/projects", { name, entry_mode: "topic" }, { "Idempotency-Key": key });
      },
      discover(pid, body, key) {
        return call("POST", `/projects/${encodeURIComponent(pid)}/discover`, body, { "Idempotency-Key": key });
      },
      job(pid, jobId) {
        return call("GET", `/projects/${encodeURIComponent(pid)}/jobs/${encodeURIComponent(jobId)}`);
      },
      papers(pid, query) {
        return call("GET", `/projects/${encodeURIComponent(pid)}/papers?limit=${query.limit || 20}`);
      },
    };
  }

  const api = { makeClient };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.LEExtractorClient = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
