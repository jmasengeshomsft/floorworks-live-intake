// eslint-disable-next-line no-unused-expressions -- playwright-cli evaluates this function expression.
async (page) => {
  let snapshots = 0
  let retries = 0
  const streams = []
  const ensure = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  await page.addInitScript(() => {
    localStorage.setItem('intake-ui.settings', JSON.stringify({ baseUrl: 'http://gateway.invalid', apiKey: '' }))
    window.__streams = []
    window.EventSource = class extends EventTarget {
      constructor(url) {
        super()
        this.url = url
        window.__streams.push(this)
        queueMicrotask(() => this.onopen?.(new Event('open')))
      }
      close() { this.closed = true }
      emit(type, data, id = 1) {
        this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify({ type, id, callId: 'resilience', data }) }))
      }
    }
  })
  await page.unroute('http://gateway.invalid/**')
  await page.route('http://gateway.invalid/**', async (route) => {
    const path = route.request().url().slice('http://gateway.invalid'.length).split('?')[0]
    let body = {}
    const call = {
      id: 'resilience', callerPhone: 'Resilience caller', status: 'active', afterHours: false, repJoined: false,
      startedAt: 1, endedAt: null, fields: { summary: snapshots ? 'Recovered snapshot' : 'Original snapshot' },
      enrichment: null, extractionStatus: 'failed', extractionError: 'ValidationError',
      pendingFunctions: snapshots ? [] : [{ name: 'lookup', functionCallId: 'f1', arguments: {} }],
    }
    if (path === '/v1/config') body = { drafts: false, schema: {}, enrichment: false, transferTargets: 0,
      configWarnings: [], voiceAgent: 'test', extractorAgent: 'test', liveNotes: 'off' }
    else if (path === '/v1/stats') body = { active: 1, maxConcurrent: 50 }
    else if (path === '/v1/calls') body = [call]
    else if (path.endsWith('/transcript')) body = [{ speaker: 'caller', text: 'Snapshot transcript', ts: 1 }]
    else if (path.endsWith('/extraction/retry')) {
      retries += 1
      body = { status: 'accepted' }
    } else if (path === '/v1/calls/resilience') {
      snapshots += 1
      body = call
    } else if (path.endsWith('/events')) streams.push(route.request().url())
    else throw new Error(`Unexpected request: ${path}`)
    await route.fulfill({ json: body, headers: { 'Access-Control-Allow-Origin': '*' } })
  })
  await page.goto('http://127.0.0.1:15173')
  await page.getByRole('button', { name: /Resilience caller/ }).click()
  await page.getByRole('button', { name: 'Retry extraction' }).waitFor()
  await page.getByRole('button', { name: 'Retry extraction' }).click()
  ensure(retries === 1, 'Retry must call the authenticated extraction endpoint')
  await page.getByRole('button', { name: 'Send result' }).waitFor()
  await page.evaluate(() => window.__streams.at(-1).emit('function.error', { functionCallId: 'f1' }))
  await page.getByRole('button', { name: 'Closed', exact: true }).waitFor()
  ensure(await page.getByRole('button', { name: 'Closed', exact: true }).isDisabled(), 'Terminal functions must not accept results')
  await page.evaluate(() => window.__streams.at(-1).emit('resync.required', {}, 101))
  await page.getByText('Recovered snapshot', { exact: true }).waitFor()
  await page.waitForFunction(() => window.__streams.at(-1).url.includes('afterEventId=101'))
  ensure(snapshots === 2, 'Replay gaps must reload the call snapshot')
  ensure(!(await page.getByRole('button', { name: 'Closed', exact: true }).count()), 'Resync must discard stale pending functions')
  ensure(await page.getByText('Snapshot transcript', { exact: true }).count() === 1, 'Resync must not duplicate transcript lines')
  await page.evaluate(() => {
    const old = window.__streams.at(-2)
    old.emit('resync.required', {}, 1)
    window.__streams.at(-1).emit('extraction.status', { status: 'complete', error: null }, 102)
  })
  await page.getByText('Extraction: complete', { exact: true }).waitFor()
  ensure(snapshots === 2 && !streams.length, 'Closed streams must not reconnect or issue stale snapshot requests')
  return 'Resilience UI regressions passed'
}
