import { readFileSync } from 'node:fs';
import { execFileSync, spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
const source=readFileSync('scripts/prepare-instance.mjs','utf8');
const args=['run','--rm','-i','--tmpfs','/instance','-e','PUID=1000','-e','PGID=1000','-e','INSTANCE_HOST_PATH=/test-instance','-e','WEB_PORT=3013','node:24.22.1-bookworm-slim','node','--input-type=module'];
const check=`
import {statSync,readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
for(const p of ['/instance/data','/instance/secrets','/instance/secrets/encryption_key','/instance/compose.env'])assert.equal(statSync(p).uid,1000);
assert.equal(statSync('/instance/secrets/encryption_key').mode & 0o777,0o600);
assert.ok(readFileSync('/instance/compose.env','utf8').includes('APP_URL="http://localhost:3013"'));
console.log('Root preparation: non-root ownership and custom port origin passed');
`;
console.log(execFileSync('docker',args,{input:source+'\n'+check,encoding:'utf8'}).trim());
const invalid=spawnSync('docker',args.map(v=>v==='PUID=1000'?'PUID=0':v),{input:source,encoding:'utf8'});assert.notEqual(invalid.status,0);assert.ok(invalid.stderr.includes('PUID 必须为非 root'));
console.log('Explicit root PUID rejected');
