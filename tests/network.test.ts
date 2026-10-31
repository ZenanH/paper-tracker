import assert from 'node:assert/strict';
import test from 'node:test';
import { publicIP, safeAIAddresses } from '../lib/network.ts';

test('public IP checks decode IPv4-mapped DNS answers', () => {
  assert.equal(publicIP('::ffff:0:c612:2a'), false);
  assert.equal(publicIP('::ffff:c612:2a'), false);
  assert.equal(publicIP('::ffff:0:808:808'), true);
});

test('AI DNS accepts VPN Fake-IP answers only for hostnames and only as a complete answer set', () => {
  const answers = [
    { address: '198.18.0.42', family: 4 },
    { address: '::ffff:0:c612:2a', family: 6 }
  ];
  assert.deepEqual(safeAIAddresses('api.example.com', answers), [
    { address: '198.18.0.42', family: 4 }
  ]);
  assert.equal(safeAIAddresses('198.18.0.42', [answers[0]]), null);
  assert.equal(safeAIAddresses('api.example.com', [...answers, { address: '10.0.0.2', family: 4 }]), null);
  assert.equal(safeAIAddresses('api.example.com', [{ address: '10.0.0.2', family: 4 }]), null);
  assert.deepEqual(safeAIAddresses('api.example.com', [{ address: '8.8.8.8', family: 4 }]), [
    { address: '8.8.8.8', family: 4 }
  ]);
  assert.deepEqual(safeAIAddresses('api.example.com', [{ address: '10.0.0.2', family: 4 }], true), [
    { address: '10.0.0.2', family: 4 }
  ]);
});
