import assert from 'node:assert/strict';
import test from 'node:test';
import worker, {dispatchCheck, WORKFLOW_URL} from './worker.mjs';

const when = Date.parse('2026-11-23T00:00:00Z');
const env = {GITHUB_WORKFLOW_TOKEN: 'unit-test-secret'};

test('dispatches only the selected workflow on main, with source and slot', async () => {
  let received;
  await dispatchCheck(env, when, async (url, options) => {received = {url, options}; return {status: 204};});
  assert.equal(received.url, WORKFLOW_URL);
  assert.equal(received.options.method, 'POST');
  assert.equal(received.options.headers.Authorization, 'Bearer unit-test-secret');
  assert.equal(received.options.redirect, 'error');
  assert.deepEqual(JSON.parse(received.options.body), {ref:'main',inputs:{trigger_source:'cloudflare',scheduled_at:'2026-11-23T00:00:00.000Z',test_telegram:false}});
});

test('supports the current GitHub 200 response', async () => {
  assert.equal(await dispatchCheck(env, when, async () => ({status: 200})), 200);
});

test('missing credentials never result in an anonymous dispatch', async () => {
  let called = false;
  await assert.rejects(dispatchCheck({}, when, async () => {called = true;}), /not configured/);
  assert.equal(called, false);
});

test('rejected dispatch is recorded as a failure without leaking response content', async () => {
  await assert.rejects(dispatchCheck(env, when, async () => ({status: 403, body: 'private-data'})), {message:'GitHub rejected dispatch (HTTP 403)'});
});

test('transport errors do not leak credentials into logs', async () => {
  await assert.rejects(dispatchCheck(env, when, async () => {throw new Error(env.GITHUB_WORKFLOW_TOKEN);}), {message:'GitHub dispatch request failed'});
});

test('public health endpoint exposes no token and does not dispatch', async () => {
  const response = await worker.fetch(new Request('https://example.workers.dev/health'), env);
  const body = await response.text();
  assert.equal(response.status, 200);
  assert.equal(JSON.parse(body).configured, true);
  assert.equal(body.includes(env.GITHUB_WORKFLOW_TOKEN), false);
});

test('public HTTP callers cannot trigger paid or repeated workflow runs', async () => {
  assert.equal((await worker.fetch(new Request('https://example.workers.dev/run',{method:'POST'}),env)).status,405);
  assert.equal((await worker.fetch(new Request('https://example.workers.dev/run'),env)).status,404);
});

test('scheduled handler passes the dispatch promise to Cloudflare', async () => {
  let work;
  await worker.scheduled({scheduledTime:when},{},{waitUntil(promise){work=promise;}});
  await assert.rejects(work, /not configured/);
});
