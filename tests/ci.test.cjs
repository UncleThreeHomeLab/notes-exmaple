const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const workflow = fs.readFileSync('.github/workflows/ci.yaml', 'utf8');
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
function script(name) {
  const section = workflow.split(`- name: ${name}\n`)[1];
  assert.ok(section, name);
  const lines = section.split(/(?:script|run): \|\n/)[1].split('\n');
  const indent = lines.find(line => line.trim()).match(/^ */)[0].length;
  const body = [];
  for (const line of lines) {
    if (line && !line.startsWith(' '.repeat(indent))) break;
    body.push(line.slice(indent));
  }
  return body.join('\n');
}
const key = crypto.generateKeyPairSync('rsa', {modulusLength:2048}).privateKey.export({type:'pkcs8',format:'pem'});
const config = {application:'notes',visibility:'public',project:'cloudlab-public',registry:'registry.example.invalid',
  source_repository:'example/notes',source_repository_id:'123',release_repository:'example/gitops',release_repository_id:456,
  workflow_identity:'https://github.com/example/notes/.github/workflows/ci.yaml@refs/heads/main'};
const environment = {GITHUB_REPOSITORY:'example/notes',GITHUB_REPOSITORY_OWNER:'example',GITHUB_REPOSITORY_ID:'123',
  RUNNER_ENVIRONMENT:'self-hosted',GITHUB_REF:'refs/heads/main',GITHUB_EVENT_NAME:'push',GITHUB_SHA:'b'.repeat(40),
  GITHUB_RUN_ID:'123',GITHUB_RUN_ATTEMPT:'1',RUNNER_TEMP:'/tmp'};
function fixture(options={}) {
  const calls = [], masks = [], failures = [];
  const request = async (route, args) => {
    calls.push({route,args});
    if (route.startsWith('POST')) {
      if (options.exchangeError) throw Object.assign(new Error('secret response'), {status:403});
      return {data:{token:'synthetic-installation-token'}};
    }
    if (options.revokeError) throw new Error('secret response');
    return {data:undefined};
  };
  const operation = (name, fn) => async args => { calls.push({name,args}); return {data:fn(args)}; };
  let mainReads = 0;
  const rest = {
    repos:{get:operation('repository', () => ({id:options.foreign ? 999 : 456,full_name:'example/gitops',
      private:false,fork:false,archived:false,disabled:false,default_branch:'main'})),
      compareCommits:operation('compare', () => ({total_commits:options.foreignBranch ? 2 : 1,
        commits:[{parents:[{sha:'base'}]}],files:[{filename:'apps/releases/notes.json'}]})),
      getContent:operation('content', () => ({type:'file',content:Buffer.from(desired()).toString('base64')}))},
    git:{getRef:operation('ref', args => {
      if (args.ref === 'heads/main') return {object:{sha:options.mainAdvanced && mainReads++ ? 'new-base' : 'base'}};
      if (options.existing) return {object:{sha:'head'}};
      throw Object.assign(new Error('not found'),{status:404});
    }),getCommit:operation('commit', () => ({tree:{sha:'root-tree'}})),
      getTree:operation('tree', args => ({tree:args.tree_sha === 'root-tree' ? [{path:'apps',mode:'040000',sha:'apps-tree'}] :
        args.tree_sha === 'apps-tree' ? [{path:'releases',mode:options.symlink ? '120000' : '040000',sha:'release-tree'}] : []})),
      createTree:operation('create-tree', () => ({sha:'new-tree'})),
      createCommit:operation('create-commit', () => ({sha:'head'})),createRef:operation('create-ref', () => ({}))},
    pulls:{list:operation('pulls', () => options.existing ? [{state:options.closed ? 'closed' : 'open',head:{sha:'head'}}] : []),
      create:operation('create-pr', () => ({}))}
  };
  const github = {request,constructor:class {constructor(){this.rest=rest;}}};
  const core = {setSecret:v => masks.push(v),setOutput(){},info(){},setFailed:v => failures.push(v)};
  const mockRequire = name => name === 'fs' ? {readFileSync:path => path.endsWith('config.json') ? JSON.stringify(config) :
    path.endsWith('/digest') ? (options.badDigest ? 'latest' : 'sha256:'+'a'.repeat(64)) :
    path.endsWith('private_key') ? key : '123'} : require(name);
  return {calls,masks,failures, run:() => new AsyncFunction('require','process','github','core',script('Propose single-file GitOps release'))(
    mockRequire,{env:environment},github,core)};
}
function desired(){return JSON.stringify({digest:'sha256:'+'a'.repeat(64),source_revision:environment.GITHUB_SHA,workflow_run_id:'123'},null,2)+'\n';}
test('publication rejects foreign repository, fork events and hosted execution', async () => {
  const run = new AsyncFunction('require','process','core',script('Validate publication scope'));
  const outputs = {};
  const mockRequire = () => ({readFileSync:() => JSON.stringify(config)});
  const core = {setSecret(){},setOutput:(k,v) => outputs[k]=v};
  await run(mockRequire,{env:environment},core);
  assert.equal(outputs.project,'cloudlab-public');
  for (const override of [{RUNNER_ENVIRONMENT:'github-hosted'},{GITHUB_REF:'refs/pull/1/merge'},
    {GITHUB_REPOSITORY_ID:'999'},{GITHUB_EVENT_NAME:'pull_request_target'}]) {
    await assert.rejects(run(mockRequire,{env:{...environment,...override}},core),/Publication scope/);
  }
});
test('release scopes token, changes one file, proposes without merging and revokes', async () => {
  const f = fixture(); await f.run();
  const exchange=f.calls.find(c=>c.route?.startsWith('POST'));
  assert.deepEqual(exchange.args.repository_ids,[456]);
  assert.deepEqual(exchange.args.permissions,{contents:'write',pull_requests:'write'});
  const tree=f.calls.find(c=>c.name==='create-tree').args.tree;
  assert.deepEqual(tree,[{path:'apps/releases/notes.json',mode:'100644',type:'blob',content:desired()}]);
  assert.equal(f.calls.filter(c=>c.name==='create-pr').length,1);
  assert.equal(f.calls.at(-1).route,'DELETE /installation/token');
  assert.ok(f.masks.includes('synthetic-installation-token'));
});
for (const [name,option,phase] of [['foreign target',{foreign:true},'repository validation'],
  ['foreign branch',{existing:true,foreignBranch:true},'release branch'],
  ['symlink parent',{symlink:true},'repository validation'],['closed PR',{existing:true,closed:true},'release proposal']]) {
  test(`release refuses ${name} and preserves prior branches`,async()=>{
    const f=fixture(option); await assert.rejects(f.run(),new RegExp(phase));
    assert.ok(!f.calls.some(c=>['create-tree','create-commit','create-ref','create-pr'].includes(c.name)));
    assert.equal(f.calls.at(-1).route,'DELETE /installation/token');
  });
}
test('existing matching release is reused without mutation',async()=>{
  const f=fixture({existing:true}); await f.run();
  assert.ok(!f.calls.some(c=>c.name?.startsWith('create-')));
});
test('main advancement retains branch and refuses PR',async()=>{
  const f=fixture({mainAdvanced:true}); await assert.rejects(f.run(),/release proposal/);
  assert.ok(f.calls.some(c=>c.name==='create-ref'));
  assert.ok(!f.calls.some(c=>c.name==='create-pr'));
});
test('API error category never includes response, token or private key',async()=>{
  const f=fixture({exchangeError:true}); await assert.rejects(f.run(),error=>{
    assert.match(error.message,/token exchange failed \(HTTP 403\)/);
    for(const secret of ['secret response','synthetic-installation-token',key]) assert.ok(!error.message.includes(secret));
    return true;
  });
});
test('invalid digest cannot mint token and failed revocation fails job',async()=>{
  const invalid=fixture({badDigest:true}); await assert.rejects(invalid.run(),/scanned digest/); assert.equal(invalid.calls.length,0);
  const revoke=fixture({revokeError:true}); await revoke.run(); assert.equal(revoke.failures.length,1);
});
test('native workflow commands parse as bash and no Python CI helper remains',()=>{
  assert.ok(!workflow.includes('python'));
  assert.ok(!fs.existsSync('ci/publisher.py')&&!fs.existsSync('ci/release.py'));
  for(const name of ['Test Notes with rootless BuildKit','Build application archive','Scan immutable archive',
    'Push scanned digest','Sign and verify exact workflow identity','Remove ephemeral publication files']) {
    const result=spawnSync('bash',['-n'],{input:script(name),encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
  }
});
test('real scan command rejects findings and never passes caller credentials to scanner',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'notes-ci-'));
  try {
    const state=path.join(root,'publication'),bin=path.join(root,'bin');
    fs.mkdirSync(state);fs.mkdirSync(bin);
    fs.writeFileSync(path.join(state,'digest'),'sha256:'+'a'.repeat(64)+'\n');
    fs.writeFileSync(path.join(bin,'crane'),'#!/bin/bash\nprintf "sha256:'+'a'.repeat(64)+'\\n"\n',{mode:0o755});
    fs.writeFileSync(path.join(bin,'trivy'),`#!/bin/bash
      test -z "\${GH_TOKEN:-}" && test -z "\${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" || exit 7
      while [ "$1" != --output ]; do shift; done
      cp '${root}/report.json' "$2"
    `,{mode:0o755});
    for(const findings of [[],[{VulnerabilityID:'synthetic-critical'}]]) {
      fs.writeFileSync(path.join(root,'report.json'),JSON.stringify({SchemaVersion:2,ArtifactType:'container_image',Results:[{Vulnerabilities:findings}]}));
      const result=spawnSync('bash',['-e','-o','pipefail'],{input:script('Scan immutable archive'),encoding:'utf8',
        env:{...process.env,PATH:bin+':'+process.env.PATH,RUNNER_TEMP:root,GH_TOKEN:'never-pass',ACTIONS_ID_TOKEN_REQUEST_TOKEN:'never-pass'}});
      assert.equal(result.status,findings.length?1:0,result.stderr);
      assert.ok(!result.stdout.includes('never-pass')&&!result.stderr.includes('never-pass'));
    }
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
