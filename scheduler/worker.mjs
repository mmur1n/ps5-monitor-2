// Cloudflare only provides the timer. All scanning and Telegram secrets stay in GitHub.
export const WORKFLOW_URL = 'https://api.github.com/repos/mmur1n/ps5-monitor-2/actions/workflows/monitor.yml/dispatches';

export async function dispatchCheck(env, scheduledTime, request = fetch) {
  if (!env.GITHUB_WORKFLOW_TOKEN) throw new Error('GITHUB_WORKFLOW_TOKEN is not configured');
  let response;
  try {
    response = await request(WORKFLOW_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_WORKFLOW_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'PS5-Monitor-Scheduler',
        'X-GitHub-Api-Version': '2026-03-10',
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: {trigger_source: 'cloudflare', scheduled_at: new Date(scheduledTime).toISOString(), test_telegram: false},
      }),
      redirect: 'error',
      signal: AbortSignal.timeout(20000),
    });
  } catch {
    // Do not log the request, token, raw error or response body.
    throw new Error('GitHub dispatch request failed');
  }
  // GitHub API versions return either 200 (with run metadata) or 204.
  if (response.status !== 200 && response.status !== 204) {
    throw new Error(`GitHub rejected dispatch (HTTP ${response.status})`);
  }
  console.log(JSON.stringify({event: 'ps5_dispatch', scheduledAt: new Date(scheduledTime).toISOString(), status: response.status}));
  return response.status;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatchCheck(env, controller.scheduledTime));
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method not allowed', {status: 405, headers: {Allow: 'GET, HEAD'}});
    }
    if (url.pathname !== '/' && url.pathname !== '/health') return new Response('Not found', {status: 404});
    // HTTP requests never trigger a check. Only Cloudflare's scheduled event can.
    return Response.json({
      service: 'ps5-monitor-scheduler',
      configured: Boolean(env.GITHUB_WORKFLOW_TOKEN),
      intervalMinutes: 5,
      dashboard: 'https://mmur1n.github.io/ps5-monitor-2/',
      note: 'Health reports configuration only; verify scheduled runs in GitHub Actions.',
    }, {headers: {'Cache-Control': 'no-store'}});
  },
};
