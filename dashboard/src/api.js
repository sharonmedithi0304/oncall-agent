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
  benchmark: () => call('/api/benchmark'),
  runBenchmark: (rounds = 3, seed = 42) => call('/api/benchmark/run', 'POST', { rounds, seed }),
  benchmarkStatus: () => call('/api/benchmark/status'),
  codefixPropose: (incidentId) => call('/api/codefix/propose', 'POST', { incident_id: incidentId }),
  codefixApprove: (proposal) => call('/api/codefix/approve', 'POST', { proposal }),
  codefixStatus: () => call('/api/codefix/status'),
  injectBug: (name) => call(`/api/bug/inject?name=${encodeURIComponent(name)}`, 'POST'),
  restoreBug: () => call('/api/bug/restore', 'POST'),
  audit: (incidentId) => call(incidentId ? `/api/audit?incident_id=${encodeURIComponent(incidentId)}` : '/api/audit'),
  auditVerify: () => call('/api/audit/verify'),
  policies: () => call('/api/policies'),
  services: () => call('/api/services'),
  rollback: (incidentId) => call('/api/rollback', 'POST', { incident_id: incidentId }),
  resetDemoData: () => call('/api/admin/reset-demo-data', 'POST'),
}
