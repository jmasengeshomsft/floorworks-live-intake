// eslint-disable-next-line no-unused-expressions -- playwright-cli evaluates this function expression.
async (page) => {
  const first = '10000000-0000-0000-0000-000000000001'
  const second = '20000000-0000-0000-0000-000000000002'
  const equipment = '30000000-0000-0000-0000-000000000003'
  const replacement = '40000000-0000-0000-0000-000000000004'
  const contact = '50000000-0000-0000-0000-000000000005'
  const customers = [{ bdm_customerid: first, bdm_name: 'First' }, { bdm_customerid: second, bdm_name: 'Second' }]
  const patches = []
  let draft
  const reset = () => {
    draft = {
      bdm_callid: 'review', bdm_status: 105730000, bdm_customername: 'New customer',
      _bdm_customerid_value: null, _bdm_equipmentid_value: equipment, _bdm_contactid_value: contact,
    }
  }
  const ensure = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  reset()
  await page.addInitScript(() => {
    localStorage.setItem('intake-ui.settings', JSON.stringify({ baseUrl: 'http://gateway.invalid', apiKey: '' }))
  })
  await page.unroute('http://gateway.invalid/**')
  await page.route('http://gateway.invalid/**', async (route) => {
    const request = route.request()
    const path = request.url().slice('http://gateway.invalid'.length).split('?')[0]
    let body = {}
    let status = 200
    const call = {
      id: 'review', callerPhone: 'Test caller', status: 'ended', afterHours: false, repJoined: false,
      startedAt: 1, endedAt: 2, fields: {}, enrichment: null,
    }
    if (path === '/v1/config') {
      body = { voiceAgent: 'test', extractorAgent: 'test', liveNotes: 'off', enrichment: false,
        drafts: true, equipment: true, configWarnings: [], schema: {}, transferTargets: 0 }
    } else if (path === '/v1/stats') {
      body = { active: 0, maxConcurrent: 50 }
    } else if (path === '/v1/calls') {
      body = [call, { ...call, id: 'other', callerPhone: 'Other caller' }]
    } else if (path.endsWith('/transcript')) {
      body = []
    } else if (path.endsWith('/events')) {
      await route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
      return
    } else if (path.startsWith('/v1/calls/')) {
      body = call
    } else if (path === '/v1/customers') {
      body = [customers[1]]
    } else if (path === '/v1/equipment') {
      body = [
        { bdm_equipmentid: equipment, bdm_name: 'First equipment' },
        { bdm_equipmentid: replacement, bdm_name: 'Second equipment' },
      ]
    } else if (path.endsWith('/customer') && request.method() === 'POST') {
      ensure(!draft._bdm_equipmentid_value && !draft._bdm_contactid_value, 'Creation must follow explicit link clears')
      draft._bdm_customerid_value = first
      body = { status: 'ok' }
    } else if (path.startsWith('/v1/drafts/') && request.method() === 'POST') {
      const edit = request.postDataJSON()
      patches.push(edit)
      const effectiveCustomer = 'customerId' in edit ? edit.customerId : draft._bdm_customerid_value
      const effectiveEquipment = 'equipmentId' in edit ? edit.equipmentId : draft._bdm_equipmentid_value
      const effectiveContact = 'contactId' in edit ? edit.contactId : draft._bdm_contactid_value
      if ((effectiveEquipment && effectiveCustomer !== (effectiveEquipment === equipment ? first : second))
          || (effectiveContact && effectiveCustomer !== first)) {
        status = 400
        body = { detail: 'Replace or explicitly clear incompatible links' }
      } else {
        for (const [key, column] of [
          ['customerId', '_bdm_customerid_value'], ['equipmentId', '_bdm_equipmentid_value'],
          ['contactId', '_bdm_contactid_value'],
        ]) {
          if (key in edit) draft[column] = edit[key]
        }
        body = { status: 'ok' }
      }
    } else if (path.startsWith('/v1/drafts/')) {
      body = {
        draft, candidates: [customers[1]], ticket: null, enrichment: null,
        customer: customers.find((customer) => customer.bdm_customerid === draft._bdm_customerid_value) ?? null,
        contact: draft._bdm_contactid_value ? { bdm_contactid: contact, bdm_name: 'Linked contact' } : null,
      }
    } else {
      status = 404
    }
    await route.fulfill({ status, json: body, headers: { 'Access-Control-Allow-Origin': '*' } })
  })
  const openDraft = async () => {
    await page.goto('http://127.0.0.1:15173')
    await page.getByRole('button', { name: /Test caller/ }).click()
    await page.getByRole('heading', { name: /Service request draft/ }).waitFor()
  }
  await openDraft()
  const clearWithCustomer = page.getByRole('checkbox', { name: 'Clear equipment and contact links with the next customer change' })
  await page.getByRole('button', { name: 'Use', exact: true }).click()
  await page.getByText('Error: 400 Replace or explicitly clear incompatible links', { exact: true }).waitFor()
  ensure(!('equipmentId' in patches.at(-1)), 'Unchecked customer change must not silently clear links')
  await clearWithCustomer.check()
  await page.getByRole('button', { name: 'Use', exact: true }).click()
  await page.getByRole('button', { name: 'Clear contact', exact: true }).waitFor({ state: 'hidden' })
  ensure(JSON.stringify(patches.at(-1)) === JSON.stringify({ customerId: second, equipmentId: null, contactId: null }),
    'Customer change and explicit clears must use one request')
  await page.getByRole('combobox', { name: 'Equipment record' }).selectOption(replacement)
  await page.getByRole('button', { name: 'Clear equipment', exact: true }).waitFor()
  await page.getByRole('combobox', { name: 'Equipment record' }).selectOption('')
  await page.getByRole('button', { name: 'Clear equipment', exact: true }).waitFor({ state: 'hidden' })
  ensure(patches.at(-1).equipmentId === null, 'Empty selection must unbind equipment')

  reset()
  draft._bdm_customerid_value = first
  await openDraft()
  await page.getByRole('button', { name: 'Clear contact', exact: true }).click()
  await page.getByRole('button', { name: 'Clear contact', exact: true }).waitFor({ state: 'hidden' })
  ensure(draft._bdm_equipmentid_value === equipment, 'Clearing contact must preserve equipment')
  await page.getByRole('button', { name: 'Clear customer and linked records', exact: true }).click()
  await page.getByRole('button', { name: 'Clear customer and linked records', exact: true }).waitFor({ state: 'hidden' })
  ensure(draft._bdm_customerid_value === null && draft._bdm_equipmentid_value === null,
    'Explicit customer removal must clear linked records atomically')

  reset()
  await openDraft()
  ensure(await page.getByRole('button', { name: 'Create "New customer"', exact: true }).isDisabled(), 'Creation must require explicit clears')
  await page.getByRole('button', { name: 'Clear equipment and contact links', exact: true }).click()
  await page.getByRole('button', { name: 'Clear equipment and contact links', exact: true }).waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: 'Create "New customer"', exact: true }).click()
  await page.getByRole('button', { name: 'Clear customer and linked records', exact: true }).waitFor()
  ensure(draft._bdm_customerid_value === first, 'Customer creation must work after clearing both orphan links')

  reset()
  await openDraft()
  await clearWithCustomer.check()
  await page.getByRole('button', { name: /Other caller/ }).click()
  await page.getByRole('button', { name: 'Clear contact', exact: true }).waitFor()
  ensure(!(await clearWithCustomer.isChecked()), 'Clear permission must not carry across calls')
  draft.bdm_status = 105730001
  await openDraft()
  ensure(!(await page.getByRole('button', { name: 'Clear contact', exact: true }).count()), 'Submitted links must remain read-only')
  ensure(await page.getByRole('combobox', { name: 'Equipment record' }).isDisabled(), 'Submitted equipment must remain read-only')
  return 'Relationship UI regressions passed'
}
