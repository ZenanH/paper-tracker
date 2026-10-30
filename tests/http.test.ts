import { test } from 'node:test';
import assert from 'node:assert/strict';
import { gunzipSync } from 'node:zlib';
import { jsonResponse } from '../lib/http.ts';

test('慢网响应支持压缩与条件校验，未变更返回无正文 304', async () => {
 const url='http://localhost/api/papers?limit=20';
 const data={items:Array.from({length:20},(_,id)=>({id,title:'论文标题与 computational modeling '.repeat(10)}))};
 const response=jsonResponse(new Request(url,{headers:{'accept-encoding':'gzip'}}),data,{privateCache:true});
 assert.equal(response.headers.get('content-encoding'),'gzip');
 const compressed=Buffer.from(await response.arrayBuffer());
 assert.deepEqual(JSON.parse(gunzipSync(compressed).toString()),data);
 assert.ok(compressed.length<Buffer.byteLength(JSON.stringify(data))/4);
 const unchanged=jsonResponse(new Request(url,{headers:{'if-none-match':response.headers.get('etag')!}}),data,{privateCache:true});
 assert.equal(unchanged.status,304);assert.equal((await unchanged.arrayBuffer()).byteLength,0);
 const changed=jsonResponse(new Request(url,{headers:{'if-none-match':response.headers.get('etag')!}}),{items:[]},{privateCache:true});
 assert.equal(changed.status,200);
});
