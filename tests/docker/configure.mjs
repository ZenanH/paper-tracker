import assert from 'node:assert/strict';
import { base, call } from './context.mjs';
let cursor=null;
do { const page=await call('/journals?all=1'+(cursor?'&cursor='+encodeURIComponent(cursor):''));for(const j of page.items)if(j.id!=='1')await call(`/journals/${j.id}/follow`,{active:false});cursor=page.nextCursor;}while(cursor);
const settings=await call('/settings');
await call('/settings',{baseURL:'http://audit-source:8099/v1',apiKey:'audit-dummy-key',model:'audit-model',filterPrompt:settings.filterPrompt,confirmReprocess:true});
await call('/settings/test',{});
await call('/journals/1/filter',{enabled:true});
const bad=await fetch(base+'/api/tasks/stop',{method:'POST',headers:{origin:'http://wrong.example'}});assert.equal(bad.status,403);
console.log('Configured: one followed journal, filter enabled, simulated API connection passed, invalid origin rejected');

assert.equal((await fetch(base+'/api/auth/session')).status,404);
