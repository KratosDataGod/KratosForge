// The test's stand-in for the main window: sends commands to the host and keeps what comes back.
window.got = [];
window.violations = [];
document.addEventListener("securitypolicyviolation", (e) => window.violations.push(`${e.violatedDirective} ${e.blockedURI}`));
const host = () => document.getElementById("host");
addEventListener("message", (e) => {
  if (e.source === host().contentWindow && e.data && e.data.kfEmbedHost) window.got.push({ origin: e.origin, ...e.data.kfEmbedHost });
});
let next = 1;
window.command = (body) => {
  const seq = next++;
  host().contentWindow.postMessage({ kfEmbed: { ...body, seq } }, "http://pbiembed.localhost");
  return seq;
};
