import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const load = name => import(pathToFileURL(path.join(root, 'src/extensions', name)).href);
const {sendPlainText, matchReceipt, readPlainIdentity} = await load('line-plain-send.mjs');
const {LineUi} = await load('line-ui.mjs');
const {createLineExtensions, ACTIVE_TOOL_DESCRIPTORS, EXTENSION_VERSION} = await load('line-extensions.mjs');
const chatName='合成測試♋️', message='合成文字\n第二行';
const chatRef='chat:0123456789abcdef01234567', ownSenderRef='sender:0123456789abcdef01234567';
const day=()=>new Date(Date.now()+8*3600000).toISOString().slice(0,10);
const baseline=()=>({chatRef,ownSenderRef,chatIdentity:{kind:'group'},messages:[],
  freshness:{clockOrderValid:true,snapshotCapturedAt:new Date().toISOString()}});
const receipt=()=>({sourceRef:'message:new',sourceMessageId:'123',sourceTimestamp:Date.now(),
  senderRef:ownSenderRef,text:message,contentType:0,sourceStatus:2});
const args={chatName,message,chatType:'group',autoSend:true,idempotencyKey:'synthetic-intent'};

function environment({draft='', pressError=false, saveError=false, old=null,
  switchAtSnapshot=Infinity, emitReceipt=true, setError=null,
  identityError=null, backendError=null, identityChangeAtRead=Infinity,
  senderChangeAtRead=Infinity}={}) {
  let reads=0, presses=0, writes=0, snapshots=0, journalWrites=0, journal=old, readScopes=[];
  const window={app_name:'LINE.exe',title:chatName,pid:42,window_id:99,is_on_screen:true,minimized:false};
  const api={tools:new Set(['list_windows','get_window_state','set_value','press_key']),async call(name, input){
    if(name==='list_windows')return {windows:[{...window,
      title:snapshots>=switchAtSnapshot?'另一個聊天室':chatName}]};
    if(name==='get_window_state')return {snapshot_id:`s${++snapshots}`,
      window:{title:snapshots>=switchAtSnapshot?'另一個聊天室':chatName},elements:[
      {element_index:1,element_token:`e${snapshots}`,role:'Edit',semantic_role:'composer',enabled:true,value:draft}]};
    if(name==='set_value'){writes++;if(setError)throw setError;draft=input.value;return {effect:'confirmed'};}
    if(name==='press_key'){presses++;draft='';if(pressError)throw new Error('synthetic post-dispatch transport failure');return {effect:'confirmed'};}
    throw new Error(`Unexpected tool ${name}`);
  }};
  const ui={automation:{},withClient:fn=>backendError?Promise.reject(backendError):fn(api)};
  const dependencies={
    async readMessages(scope){reads++;readScopes.push(scope);if(identityError)throw identityError;
      return {...baseline(),
        chatRef:reads>=identityChangeAtRead?'chat:ffffffffffffffffffffffff':chatRef,
        ownSenderRef:reads>=senderChangeAtRead?'sender:ffffffffffffffffffffffff':ownSenderRef,
        messages:presses && emitReceipt ? [receipt()] : []};},
    async readRecord(){return journal;},
    async writeRecord(_file,value){journalWrites++;if(saveError)throw new Error('synthetic journal failure');journal=structuredClone(value);},
  };
  return {ui,api,dependencies,stats:()=>({reads,presses,writes,snapshots,journalWrites,readScopes,draft,journal})};
}

function oldRecord(status){return {status,operationId:'previous',startedAt:Date.now()-300000,
  messageDigest:createHash('sha256').update(message).digest('hex'),
  scope:{chatName,chatType:'group',dateFrom:day(),dateTo:day()},before:baseline(),receipt:receipt()};}

test('receipt rejects invalid/stale timestamps, wrong sender and nonunique records',()=>{
  const before=baseline(),startedAt=Date.now()-100;
  const after={...baseline(),messages:[receipt()]};
  assert(matchReceipt(before,after,message,startedAt));
  assert.equal(matchReceipt(before,{...after,freshness:{clockOrderValid:true,snapshotCapturedAt:'invalid'}},message,startedAt),null);
  assert.equal(matchReceipt(before,{...after,messages:[{...receipt(),senderRef:'someone-else'}]},message,startedAt),null);
  assert.equal(matchReceipt(before,{...after,messages:[receipt(),{...receipt(),sourceRef:'message:other'}]},message,startedAt),null);
  assert.equal(matchReceipt(before,{...after,freshness:{clockOrderValid:true,snapshotCapturedAt:new Date(startedAt-1).toISOString()}},message,startedAt),null);
});

test('Return transport failure still verifies the local record; one input dispatch',async()=>{
  const env=environment({pressError:true});
  const result=await sendPlainText(env.ui,args,env.dependencies);
  assert.equal(result.status,'RECORDED_LOCAL');
  assert.equal(result.deliveryVerified,false);
  assert.equal(env.stats().presses,1);
  assert.equal(env.stats().writes,1);
  assert.equal(env.stats().reads,4);
});

test('paired globally unique direct sends retain automatic opening and bounded receipts across midnight',async t=>{
  let clock=Date.parse('2026-09-24T23:59:59.500+08:00');
  t.mock.method(Date,'now',()=>clock);
  const env=environment();
  const read=env.dependencies.readMessages;
  env.dependencies.readMessages=async scope=>{
    assert.ok((Date.parse(scope.dateTo)-Date.parse(scope.dateFrom))/86400000<=2);
    const result=await read(scope);
    return {...result,chatIdentity:{kind:'direct',knownNameUnique:true,globalNameUnique:true},
      freshness:{clockOrderValid:true,snapshotCapturedAt:new Date(clock).toISOString()}};
  };
  const call=env.api.call;
  env.api.call=async(name,input)=>{const result=await call(name,input);if(name==='press_key')clock+=1000;return result;};
  const result=await sendPlainText(env.ui,{...args,chatType:'direct',expectedChatRef:chatRef,
    expectedOwnSenderRef:ownSenderRef},env.dependencies);
  assert.equal(result.status,'RECORDED_LOCAL');
  assert.equal(env.stats().presses,1);
  assert.equal(env.stats().writes,1);
  assert.equal(env.stats().readScopes.at(-1).dateTo,'2026-09-25');
  assert.equal(env.stats().readScopes.at(-1).dateFrom,'2026-09-23');
});

test('paired uncertain reconciliation remains bounded and never replays an older intent',async()=>{
  const old=oldRecord('UNCERTAIN');
  old.scope.dateFrom='2020-01-01';
  const env=environment({old});
  const read=env.dependencies.readMessages;
  env.dependencies.readMessages=async scope=>{
    assert.ok((Date.parse(scope.dateTo)-Date.parse(scope.dateFrom))/86400000<=2);
    return {...await read(scope),chatIdentity:{kind:'direct',knownNameUnique:true,globalNameUnique:true}};
  };
  await assert.rejects(sendPlainText(env.ui,{...args,chatType:'direct',expectedChatRef:chatRef,
    expectedOwnSenderRef:ownSenderRef},env.dependencies),{code:'LINE_SEND_UNCERTAIN'});
  assert.equal(env.stats().presses,0);assert.equal(env.stats().writes,0);assert.equal(env.stats().snapshots,0);
});

test('a truncated 30-record receipt page must overlap the pre-send tail',()=>{
  const before={...baseline(),messages:[{sourceRef:'message:old',sourceTimestamp:Date.now()-100}]};
  const after={...baseline(),messages:[...Array.from({length:29},(_,i)=>({sourceRef:`message:${i}`,text:'other'})),receipt()],
    pagination:{hasMore:true}};
  assert.equal(matchReceipt(before,after,message,Date.now()-100),null);
  after.messages[0]=before.messages[0];
  assert.ok(matchReceipt(before,after,message,Date.now()-100));
});

test('a chat switch after draft input refuses Return and records a staged draft',async()=>{
  const env=environment({switchAtSnapshot:4});
  await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>
    error.details.status==='DRAFTED' && error.details.sendDispatched===false
      && error.details.draftMayBeStaged===true);
  assert.equal(env.stats().presses,0);
  assert.equal(env.stats().writes,1);
});

test('a pre-Return input refusal preserves staged-draft uncertainty without dispatch',async()=>{
  const env=environment({setError:Object.assign(new Error('synthetic schema refusal'),
    {code:'LINE_UI_INVALID_ARGUMENT',operationMayHaveCompleted:false})});
  await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>
    error.code==='LINE_UI_INVALID_ARGUMENT' && error.details.status==='DRAFTED'
      && error.details.sendDispatched===false && error.details.draftMayBeStaged===true);
  assert.equal(env.stats().presses,0);
  assert.equal(env.stats().writes,1);
});

test('changed group identity after navigation refuses all composer input',async()=>{
  for(const change of [{identityChangeAtRead:2},{senderChangeAtRead:2}]){
    const env=environment(change);
    await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>
      error.code==='LINE_CHAT_IDENTITY_CHANGED' && error.details.status==='NOT_SENT'
        && error.details.sendDispatched===false && error.details.draftMayBeStaged===false);
    assert.equal(env.stats().writes,0);
    assert.equal(env.stats().presses,0);
  }
});

test('wrong detached title refuses group composer input',async()=>{
  const env=environment({switchAtSnapshot:1});
  await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>
    error.code==='LINE_CHAT_UNVERIFIED' && error.details.status==='NOT_SENT');
  assert.equal(env.stats().writes,0);
  assert.equal(env.stats().presses,0);
});

test('changed local chat or signed-in sender after drafting refuses Return',async()=>{
  for(const change of [{identityChangeAtRead:3},{senderChangeAtRead:3}]){
    const env=environment(change);
    await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>
      error.code==='LINE_CHAT_IDENTITY_CHANGED' && error.details.status==='DRAFTED'
      && error.details.sendDispatched===false);
    assert.equal(env.stats().reads,3);
    assert.equal(env.stats().presses,0);
    assert.equal(env.stats().draft,message);
  }
});

test('missing local identity or UI backend cannot reach LINE input',async()=>{
  const identityError=Object.assign(new Error('synthetic identity refused'),{code:'CHAT_AMBIGUOUS'});
  const missingIdentity=environment({identityError});
  await assert.rejects(sendPlainText(missingIdentity.ui,args,missingIdentity.dependencies),
    error=>error.code==='CHAT_AMBIGUOUS');
  assert.equal(missingIdentity.stats().snapshots,0);
  assert.equal(missingIdentity.stats().writes,0);

  const backendError=Object.assign(new Error('synthetic CUA unavailable'),
    {code:'LINE_UI_BACKEND_UNAVAILABLE'});
  const missingBackend=environment({backendError});
  await assert.rejects(sendPlainText(missingBackend.ui,args,missingBackend.dependencies),
    error=>error.code==='LINE_UI_BACKEND_UNAVAILABLE' && error.details.status==='NOT_SENT'
      && error.details.sendDispatched===false);
  assert.equal(missingBackend.stats().reads,1);
  assert.equal(missingBackend.stats().presses,0);
  assert.equal(missingBackend.stats().writes,0);
});

test('selected chat and account mismatch refuse before GUI and journal writes',async()=>{
  for(const selected of [
    {expectedChatRef:'chat:ffffffffffffffffffffffff'},
    {expectedOwnSenderRef:'sender:ffffffffffffffffffffffff'},
  ]) {
    const env=environment();
    await assert.rejects(sendPlainText(env.ui,{...args,...selected},env.dependencies),error=>
      error.code===(selected.expectedChatRef?'CHAT_IDENTITY_CHANGED':'CHAT_ACCOUNT_CHANGED'));
    assert.equal(env.stats().reads,1);
    assert.equal(env.stats().snapshots,0);
    assert.equal(env.stats().writes,0);
    assert.equal(env.stats().journalWrites,0);
    assert.equal(env.stats().presses,0);
  }
  const env=environment({identityError:Object.assign(new Error('duplicate name'),{code:'CHAT_AMBIGUOUS'})});
  await assert.rejects(sendPlainText(env.ui,{...args,expectedChatRef:chatRef,
    expectedOwnSenderRef:ownSenderRef},env.dependencies),{code:'CHAT_AMBIGUOUS'});
  assert.equal(env.stats().journalWrites,0);
  assert.equal(env.stats().snapshots,0);
});

test('selected refs remain bound through pre-Return and receipt reads',async()=>{
  const env=environment();
  const selected={expectedChatRef:chatRef,expectedOwnSenderRef:ownSenderRef};
  const sent=await sendPlainText(env.ui,{...args,...selected},env.dependencies);
  assert.equal(sent.status,'RECORDED_LOCAL');
  assert.equal(env.stats().readScopes.length,4);
  assert.equal(env.stats().readScopes[0].requireUniqueName,true);
  assert.equal(env.stats().readScopes[1].requireUniqueName,true);
  for(const scope of env.stats().readScopes){
    assert.equal(scope.expectedChatRef,chatRef);
    assert.equal(scope.expectedOwnSenderRef,ownSenderRef);
  }
});

test('a post-Return chat switch still verifies an exact local receipt without replay',async()=>{
  const env=environment({switchAtSnapshot:6});
  const result=await sendPlainText(env.ui,args,env.dependencies);
  assert.equal(result.status,'RECORDED_LOCAL');
  assert.equal(env.stats().presses,1);
  assert.equal(env.stats().reads,4);
});

test('identical preexisting user draft is preserved and reported NOT_SENT',async()=>{
  const env=environment({draft:message});
  await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>error.code==='LINE_DRAFT_CONFLICT' && error.details.status==='NOT_SENT');
  assert.equal(env.stats().presses,0);
  assert.equal(env.stats().writes,0);
  assert.equal(env.stats().draft,message);
});

test('journal failure prevents input and does not obscure the operation state',async()=>{
  const env=environment({saveError:true});
  await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>error.details.status==='NOT_SENT' && error.details.journalPersisted===false);
  assert.equal(env.stats().presses,0);assert.equal(env.stats().writes,0);
});

test('completed identical intent remains deduplicated past two minutes, without a key',async()=>{
  const env=environment({old:oldRecord('RECORDED_LOCAL')});
  const result=await sendPlainText(env.ui,{...args,idempotencyKey:undefined},env.dependencies);
  assert.equal(result.reused,true);assert.equal(env.stats().presses,0);assert.equal(env.stats().snapshots,0);
});

test('draft and uncertain retries retain distinct states and reuse the first DB read',async()=>{
  for(const status of ['DRAFTING','DRAFTED','RETURN_INTENT','UNCERTAIN']){
    const env=environment({old:oldRecord(status)});
    const uncertain=['RETURN_INTENT','UNCERTAIN'].includes(status);
    await assert.rejects(sendPlainText(env.ui,args,env.dependencies),error=>
      error.details.status===(uncertain?'UNCERTAIN':'DRAFTED') && error.details.operationMayHaveCompleted===uncertain);
    assert.equal(env.stats().reads,1);assert.equal(env.stats().snapshots,0);assert.equal(env.stats().presses,0);
  }
});

test('open/draft/group state reuse exact detached identity without the global inventory or proof',async()=>{
  const env=environment({draft:'使用者草稿'});
  const ui=new LineUi({automation:{},withClient:env.ui.withClient,runOperation:(_name,fn)=>fn(),
    readNamedIdentity:async()=>({chatRef,kind:'group'}),
    readChatIdentity:async()=>{throw new Error('Unrelated blank contact must not be queried');}});
  assert.equal((await ui.openChat({chatName})).chatType,'group');
  assert.equal((await ui.getDraft({chatName,chatType:'group'})).draft,'使用者草稿');
  assert.equal((await ui.getState({chatName})).success,true);
  assert.equal(env.stats().writes,0);assert.equal(env.stats().presses,0);
});

test('named detached identity requires complete contact-name uniqueness before UI use',async()=>{
  let requested;
  const readIdentity=async scope=>{
    requested=scope;
    throw Object.assign(new Error('same-name unopened contact'),{code:'CHAT_AMBIGUOUS'});
  };
  await assert.rejects(readPlainIdentity({chatName},{readIdentity}),
    error=>error.code==='CHAT_AMBIGUOUS');
  assert.equal(requested.chatName,chatName);
  assert.equal(requested.chatType,'auto');
  assert.equal(requested.requireUniqueName,true);
});

test('active catalogue is compact and versioned; legacy handlers remain callable with validation',async()=>{
  const extension=createLineExtensions({}, {ui:{}});
  assert.equal(extension.tools,ACTIVE_TOOL_DESCRIPTORS);
  const result=await extension.call('get_line_capabilities',{});
  const caps=JSON.parse(result.content[0].text);
  assert.equal(caps.version,EXTENSION_VERSION);assert.equal(caps.toolCount,33);
  assert.deepEqual(caps.tools,extension.tools.map(tool=>tool.name));
  for(const name of ['prepare_line_group_chat','prepare_line_direct_chat','get_line_chatroom_history_short']){
    assert(!caps.tools.includes(name));assert(extension.handles(name));
    const invalid=await extension.call(name,{});assert.equal(invalid.isError,true);
    assert.equal(JSON.parse(invalid.content[0].text).code,'LINE_INVALID_ARGUMENT');
  }
  const manual=extension.tools.find(tool=>tool.name==='send_message_manual');
  assert(manual.inputSchema.properties.chatType.enum.includes('group'));
});
