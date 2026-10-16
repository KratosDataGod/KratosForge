const test = require('node:test');
const assert = require('node:assert/strict');
const {smallDocs, validateReview, ciState, decision} = require('./policy.cjs');
const {publish} = require('./review.cjs');
const sha = 'a'.repeat(40);
const docs = [{filename:'docs/user-guide.md',status:'modified',additions:1,deletions:1}];
const report = () => ({reviewed_head_sha:sha,review_complete:true,
  recommendation:'approve',summary:'Editorial clarification.',findings:[]});
const args = () => ({files:docs,review:report(),sha,complete:true,ci:'success',trusted:true});

test('only small editorial documentation is eligible', () => {
  assert.equal(smallDocs(docs),true);
  for (const filename of ['sidecar/fabric_desktop/auth.py','.github/workflows/docs.yml',
    'docs/permissions.md','docs/install.md','AGENTS.md']) {
    assert.equal(smallDocs([{...docs[0],filename}]),false,filename);
  }
  assert.equal(smallDocs([{...docs[0],status:'renamed'}]),false);
  assert.equal(smallDocs([{...docs[0],additions:201}]),false);
});

test('every missing, stale, uncertain, or failed signal routes to a human', () => {
  assert.equal(decision(args()).route,'approve');
  for (const patch of [{complete:false},{ci:'pending'},{ci:'failed'},{trusted:false},
    {review:null},{review:{...report(),reviewed_head_sha:'old'}},
    {review:{...report(),review_complete:false}},
    {review:{...report(),recommendation:'manual_review'}},
    {review:{...report(),findings:[{priority:'P2',path:'docs/user-guide.md',line:1,title:'Broken link',body:'Missing page.'}]}}]) {
    assert.equal(decision({...args(),...patch}).route,'manual_review',JSON.stringify(patch));
  }
  assert.equal(validateReview({...report(),findings:[{}]},sha),false);
});

test('CI requires every expected check and uses the newest rerun', () => {
  const names = ['sidecar (windows-latest)','sidecar (ubuntu-latest)','agent-image','engine','shell'];
  const checks = names.map((name,id)=>({name,id,status:'completed',conclusion:'success',app:{slug:'github-actions'}}));
  assert.equal(ciState(checks,[],false),'success');
  assert.equal(ciState(checks,[],true),'pending');
  assert.equal(ciState(checks.slice(1),[],false),'pending');
  assert.equal(ciState([...checks,{...checks[0],id:100,status:'in_progress'}],[],false),'pending');
  assert.equal(ciState([...checks,{...checks[0],id:100,conclusion:'failure'}],[],false),'failed');
  assert.equal(ciState(checks.map(c=>({...c,app:{slug:'untrusted'}})),[],false),'pending');
});

test('publisher never approves or merges; own PR gets assignment and one comment', async () => {
  Object.assign(process.env,{REVIEW_HEAD:sha,REVIEW_BASE:'base',REVIEW_COMPLETE:'false',CODEX_REVIEW:''});
  const calls=[];
  const pr={state:'open',draft:false,base:{ref:'main',sha:'base'},head:{sha},changed_files:1,
    author_association:'OWNER',user:{login:'KratosDataGod'}};
  const record = name => async payload => {calls.push([name,payload]);return {data:{}};};
  const github={rest:{pulls:{get:async()=>({data:pr}),listFiles:'files'},issues:{
    listComments:'comments',createComment:record('comment'),getLabel:record('label'),
    addLabels:record('addLabels'),removeLabel:record('removeLabel'),addAssignees:record('assign')}},
    paginate:async endpoint=>endpoint==='files'?docs:[]};
  const summary={addHeading(){return this;},addRaw(){return this;},async write(){}};
  await publish({github,context:{repo:{owner:'KratosDataGod',repo:'fabric-desktop'},
    payload:{pull_request:{number:42,head:{sha}}},serverUrl:'https://github.com',runId:1},
    core:{notice(){},warning(){},summary}});
  assert.equal(calls.filter(c=>c[0]==='comment').length,1);
  assert.ok(calls.find(c=>c[0]==='assign'));
  assert.match(calls.find(c=>c[0]==='comment')[1].body,/manual review required/);
});

test('publisher ignores an obsolete reviewed commit', async () => {
  process.env.REVIEW_HEAD=sha;
  let readFiles=false;
  await publish({github:{rest:{pulls:{get:async()=>({data:{state:'open',draft:false,
    base:{ref:'main'},head:{sha:'b'.repeat(40)}}})}},paginate:async()=>{readFiles=true;}},
    context:{repo:{},payload:{pull_request:{number:42}}},core:{notice(){}}});
  assert.equal(readFiles,false);
});
