async function call(path, method = 'GET', body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`)
  return data
}

export const api = {
  state: () => call('/api/state'),
  breakIt: (fault = 'pool_exhaustion') => call(`/api/break?fault=${encodeURIComponent(fault)}`, 'POST'),
  reset: () => call('/api/reset', 'POST'),
  diagnose: (useMemory) => call(`/api/diagnose?use_memory=${useMemory}`, 'POST'),
  fix: (body) => call('/api/fix', 'POST', body),
  incidents: () => call('/api/incidents'),
  memory: () => call('/api/memory'),
  clearMemory: () => call('/api/memory', 'DELETE'),
}
