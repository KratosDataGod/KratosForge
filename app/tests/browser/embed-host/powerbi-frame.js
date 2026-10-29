// A stand-in for Power BI's embed frame (PBE-05): answers powerbi-client's postMessage protocol
// (requests carry headers.id, answers echo it) and raises events the way Power BI does.
(function () {
  const uid = new URLSearchParams(location.search).get("uid");
  window.received = [];
  const answer = (req, statusCode, body) =>
    parent.postMessage({ statusCode, headers: { id: req.headers && req.headers.id }, body }, "*");
  const event = (name, value) =>
    parent.postMessage({ method: "POST", url: `/reports/${uid}/events/${name}`, headers: { id: "ev" + Math.random() },
      body: value }, "*");
  addEventListener("message", (e) => {
    const req = e.data;
    if (!req || typeof req !== "object") return;
    window.received.push(req);
    // Something other than the SDK's own requests (a forged host command) is noted and ignored.
    if (!req.method || !req.url) return;
    if (req.url === "/report/load") {
      answer(req, 202, {});
      setTimeout(() => event("loaded", { accessToken: "leaked-token", nested: { Authorization: "Bearer x" }, kept: 1 }), 10);
      setTimeout(() => event("rendered", {}), 20);
    } else if (req.url === "/report/pages") {
      answer(req, 200, [{ name: "ReportSection1", displayName: "Overview", isActive: true, visibility: 0 }]);
    } else if (req.url === "/report/token") {
      answer(req, 200, {});
    } else {
      answer(req, 200, {});
    }
  });
  // A frame trying to drive the host itself: the host must ignore it (it isn't the host's parent).
  setTimeout(() => parent.postMessage({ kfEmbed: { seq: 99, command: "reset" } }, "*"), 30);
})();
