const EXPECTED_CI = ['sidecar (windows-latest)', 'sidecar (ubuntu-latest)',
  'agent-image', 'engine', 'shell'];

function smallDocs(files) {
  return files.length > 0 && files.length <= 5 &&
    files.reduce((n, f) => n + f.additions + f.deletions, 0) <= 200 &&
    files.every(f => ['added', 'modified'].includes(f.status) &&
      /^(docs\/[^\n]+\.md|README\.md|CONTRIBUTING\.md)$/.test(f.filename) &&
      !/(^|\/)(AGENTS|permissions|azure-setup|agent|install|troubleshooting)\.md$/i.test(f.filename));
}

function validateReview(value, sha) {
  return value && value.reviewed_head_sha === sha &&
    typeof value.review_complete === 'boolean' &&
    ['approve', 'manual_review'].includes(value.recommendation) &&
    typeof value.summary === 'string' && value.summary.length <= 1500 &&
    Array.isArray(value.findings) && value.findings.length <= 20 &&
    value.findings.every(f => ['P0', 'P1', 'P2', 'P3'].includes(f.priority) &&
      typeof f.path === 'string' && Number.isInteger(f.line) && f.line > 0 &&
      typeof f.title === 'string' && f.title.length <= 200 &&
      typeof f.body === 'string' && f.body.length <= 1500);
}

function ciState(checks, statuses, docsChanged) {
  const expected = [...EXPECTED_CI, ...(docsChanged ? ['build'] : [])];
  const trusted = checks.filter(c => c.app?.slug === 'github-actions');
  // API results are newest first. Never let an older success mask a rerun.
  const latest = new Map();
  for (const c of trusted) {
    if (!latest.has(c.name) || c.id > latest.get(c.name).id) latest.set(c.name, c);
  }
  const required = expected.map(name => latest.get(name));
  if (required.some(c => c?.status === 'completed' && c.conclusion !== 'success') ||
      statuses.some(s => ['failure', 'error'].includes(s.state))) return 'failed';
  if (required.some(c => !c || c.status !== 'completed') ||
      statuses.some(s => s.state === 'pending')) return 'pending';
  // Other completed checks may also veto an approval recommendation.
  if ([...latest.values()].some(c => c.status === 'completed' &&
      ['failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure', 'stale'].includes(c.conclusion))) return 'failed';
  return 'success';
}

function decision({files, review, sha, complete, ci, trusted}) {
  if (!trusted) return {route: 'manual_review', reason: 'PR author is outside the trusted collaborator set.'};
  if (!complete) return {route: 'manual_review', reason: 'Review input was incomplete or exceeded the review limits.'};
  if (!validateReview(review, sha) || !review.review_complete)
    return {route: 'manual_review', reason: 'Codex review was missing, invalid, stale, or incomplete.'};
  if (review.findings.length) return {route: 'manual_review', reason: 'Codex reported actionable findings.'};
  if (!smallDocs(files)) return {route: 'manual_review', reason: 'Change is outside the small documentation-only policy.'};
  if (review.recommendation !== 'approve') return {route: 'manual_review', reason: 'Codex requested maintainer judgment.'};
  if (ci !== 'success') return {route: 'manual_review', reason: `CI is ${ci}; approval requires successful validation.`};
  return {route: 'approve', reason: 'Small documentation-only change, complete clean review, and successful CI.'};
}

module.exports = {smallDocs, validateReview, ciState, decision};
