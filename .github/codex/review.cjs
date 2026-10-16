const fs = require('node:fs');
const {decision, ciState, validateReview, smallDocs} = require('./policy.cjs');
const REVIEWER = 'KratosDataGod';
const marker = '<!-- codex-routing-recommendation -->';

async function pullNumber({github, context}) {
  const n = context.payload.pull_request?.number || Number(context.payload.inputs?.pr_number);
  if (!Number.isSafeInteger(n) || n < 1) throw new Error('A valid PR number is required.');
  return n;
}

async function prepare({github, context, core}) {
  const number = await pullNumber({github, context});
  const {data: pr} = await github.rest.pulls.get({...context.repo, pull_number: number});
  const head = context.payload.pull_request?.head.sha || pr.head.sha;
  core.setOutput('number', number);
  core.setOutput('head', head);
  core.setOutput('base', pr.base.sha);
  if (pr.state !== 'open' || pr.draft || pr.head.sha !== head || pr.base.ref !== 'main') {
    core.setOutput('run', 'false');
    return;
  }
  const trusted = ['OWNER', 'MEMBER', 'COLLABORATOR'].includes(pr.author_association);
  core.setOutput('trusted', String(trusted));
  const files = await github.paginate(github.rest.pulls.listFiles,
    {...context.repo, pull_number: number, per_page: 100});
  let complete = pr.base.sha === context.sha && files.length === pr.changed_files && files.length <= 50 &&
    files.every(f => typeof f.patch === 'string' &&
      f.patch.split('\n').filter(line => line.startsWith('+')).length === f.additions &&
      f.patch.split('\n').filter(line => line.startsWith('-')).length === f.deletions);
  const input = {base_sha: pr.base.sha, head_sha: head, files: []};
  let bytes = 0;
  if (complete && trusted && process.env.HAS_API_KEY === 'true') {
    for (const f of files) {
      const entry = {path: f.filename, status: f.status, patch: f.patch};
      // Fetch text as data only. Never check out or execute the PR branch.
      if (f.status !== 'removed') {
        try {
          const [owner, repo] = pr.head.repo.full_name.split('/');
          const {data} = await github.rest.repos.getContent({owner, repo, path: f.filename, ref: head});
          if (!data.content || data.size > 100000 || data.encoding !== 'base64') { complete = false; break; }
          entry.content = Buffer.from(data.content, 'base64').toString('utf8');
        } catch { complete = false; break; }
      }
      bytes += Buffer.byteLength(JSON.stringify(entry));
      if (bytes > 500000) { complete = false; break; }
      input.files.push(entry);
    }
  }
  core.setOutput('complete', String(complete));
  const run = trusted && complete && process.env.HAS_API_KEY === 'true';
  core.setOutput('run', String(run));
  fs.writeFileSync('review-input.json', JSON.stringify(input, null, 2));
  if (!run) core.notice('PR will be routed to manual review without using the API.');
}

function parseReview(raw) {
  try { return JSON.parse(raw); } catch { return null; }
}

async function publish({github, context, core}) {
  const number = await pullNumber({github, context});
  const args = {...context.repo, pull_number: number};
  let {data: pr} = await github.rest.pulls.get(args);
  const head = process.env.REVIEW_HEAD || context.payload.pull_request?.head.sha;
  if (pr.state !== 'open' || pr.draft || pr.base.ref !== 'main') return;
  if (!head || pr.head.sha !== head) { core.notice('Discarding stale review.'); return; }
  const files = await github.paginate(github.rest.pulls.listFiles, {...args, per_page: 100});
  const review = parseReview(process.env.CODEX_REVIEW);
  const trusted = ['OWNER', 'MEMBER', 'COLLABORATOR'].includes(pr.author_association);
  const complete = process.env.REVIEW_COMPLETE === 'true' && files.length === pr.changed_files &&
    process.env.REVIEW_BASE === pr.base.sha;
  const candidate = trusted && complete && smallDocs(files) && validateReview(review, head) &&
    review.review_complete && review.recommendation === 'approve' && !review.findings.length;
  let ci = 'not checked';
  if (candidate) {
    // Allow the existing CI workflow to finish before making a recommendation.
    const deadline = Date.now() + 10 * 60 * 1000;
    do {
      ({data: pr} = await github.rest.pulls.get(args));
      if (pr.head.sha !== head || pr.state !== 'open') return;
      if (pr.base.sha !== process.env.REVIEW_BASE) break;
      const ref = pr.merge_commit_sha;
      if (!ref) { ci = 'pending'; }
      else {
        const checks = [];
        const statuses = new Map();
        for (const revision of new Set([ref, head])) {
          checks.push(...await github.paginate(github.rest.checks.listForRef,
            {...context.repo, ref: revision, per_page: 100}));
          const {data: status} = await github.rest.repos.getCombinedStatusForRef({...context.repo, ref: revision});
          for (const s of status.statuses) {
            if (!statuses.has(s.context) || s.id > statuses.get(s.context).id) statuses.set(s.context, s);
          }
        }
        const docsChanged = files.some(f => f.filename.startsWith('docs/'));
        ci = ciState(checks, [...statuses.values()], docsChanged);
      }
      if (ci !== 'pending' || Date.now() >= deadline) break;
      await new Promise(resolve => setTimeout(resolve, 15000));
    } while (true);
  }
  ({data: pr} = await github.rest.pulls.get(args));
  if (pr.head.sha !== head || pr.state !== 'open' || pr.draft) return;
  const result = decision({files, review, sha: head,
    complete: complete && process.env.REVIEW_BASE === pr.base.sha, ci, trusted});
  const valid = validateReview(review, head);
  const label = result.route === 'approve' ? 'codex: recommend-approve' : 'codex: manual-review';
  const other = result.route === 'approve' ? 'codex: manual-review' : 'codex: recommend-approve';
  const text = s => s.replace(/@/g, '@\u200b').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const findings = valid ? review.findings.map(f =>
    `- **${f.priority}: ${text(f.title)}** — ${text(f.path)}:${f.line}\n  ${text(f.body)}`).join('\n') : '';
  const manual = result.route === 'manual_review';
  const body = `${marker}\n### Codex recommendation: ${manual ? 'manual review required' : 'approve'}\n\n` +
    `**Recommendation-only mode: no GitHub approval or merge was performed.**\n\n` +
    `Reviewed commit: \`${head}\`\n\n${result.reason}\n\n` +
    (valid ? `${text(review.summary)}\n\n${findings}\n\n` : 'No complete validated Codex report is available.\n\n') +
    (manual ? `@${REVIEWER}, please review this PR.\n\n` : '') +
    `[Workflow details](${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId})`;
  const comments = await github.paginate(github.rest.issues.listComments,
    {...context.repo, issue_number: number, per_page: 100});
  const previous = comments.find(c => c.user?.login === 'github-actions[bot]' && c.body?.startsWith(marker));
  if (previous) await github.rest.issues.updateComment({...context.repo, comment_id: previous.id, body});
  else await github.rest.issues.createComment({...context.repo, issue_number: number, body});
  // Labels communicate routing, never authorization.
  try {
    await github.rest.issues.getLabel({...context.repo, name: label});
  } catch (error) {
    if (error.status !== 404) throw error;
    try {
      await github.rest.issues.createLabel({...context.repo, name: label,
        color: manual ? 'D93F0B' : '0E8A16', description: 'Advisory Codex review routing; merging remains manual.'});
    } catch (race) { if (race.status !== 422) throw race; }
  }
  await github.rest.issues.addLabels({...context.repo, issue_number: number, labels: [label]});
  try { await github.rest.issues.removeLabel({...context.repo, issue_number: number, name: other}); }
  catch (error) { if (error.status !== 404) throw error; }
  if (manual) {
    if (pr.user.login !== REVIEWER) {
      try { await github.rest.pulls.requestReviewers({...args, reviewers: [REVIEWER]}); }
      catch (error) { core.warning(`Could not request maintainer review (${error.status}); maintainer is mentioned in the comment.`); }
    } else {
      // GitHub does not allow requesting a review from the PR's own author.
      await github.rest.issues.addAssignees({...context.repo, issue_number: number, assignees: [REVIEWER]});
    }
  }
  await core.summary.addHeading(`Codex: ${result.route}`).addRaw(body).write();
}

module.exports = {prepare, publish, parseReview};
