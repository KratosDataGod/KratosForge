// A stand-in for Power BI's embed frame (PBE-05): answers powerbi-client's postMessage protocol, a refused load among it,
// (requests carry headers.id, answers echo it) and raises events the way Power BI does.
(function () {
  const query = new URLSearchParams(location.search);
  const uid = query.get("uid");
  // An embed address with fail=event stands in for a report Power BI can't open (no access, no licence, an expired
  // token). Power BI accepts the load and then raises its error event with the error body, as it does for
  // LoadReportFailed; powerbi-client 2.25 raises no event of its own for a refused load request.
  const failEvent = query.get("fail") === "event";
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
    if (req.url === "/report/load" && failEvent) {
      answer(req, 202, {});
      setTimeout(() => event("error", { message: "LoadReportFailed", detailedMessage: "Fail to initialize - Could not resolve cluster",
        errorCode: "403", level: 6, technicalDetails: { requestId: "req-1129", accessToken: "leaked-token" } }), 10);
    } else if (req.url === "/report/load") {
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
