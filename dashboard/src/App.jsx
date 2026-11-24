import { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Timer, TriangleAlert, Database, Brain, Flame, RotateCcw, Activity, ArrowUpRight, X, Siren, HardDrive, Code2 } from 'lucide-react'
import { api } from './api'
import Sidebar from './components/Sidebar'
import StatsCard from './components/StatsCard'
import Sparkline from './components/Sparkline'
import Pipeline from './components/Pipeline'
import DiagnosisCard from './components/DiagnosisCard'
import CodeFixCard from './components/CodeFixCard'
import MemoryView from './components/MemoryView'
import BenchmarkView from './components/BenchmarkView'

const FAULT_OPTIONS = [
  { id: 'pool_exhaustion', label: 'Pool Exhaustion' },
  { id: 'slow_query', label: 'Slow Query' },
  { id: 'bad_deploy', label: 'Bad Deploy (v3.8.3)' },
  { id: 'memory_leak', label: 'Memory Leak' },
  { id: 'downstream_timeout', label: 'Downstream Timeout' },
  { id: 'cache_disabled', label: 'Cache Disabled' },
  { id: 'discount_null', label: 'Code Bug: Discount Null (AttributeError)' },
  { id: 'off_by_one', label: 'Code Bug: Off-By-One (IndexError)' },
  { id: 'key_error', label: 'Code Bug: Missing Dict Key (KeyError)' },
]

export default function App() {
  const [tab, setTab] = useState('overview')
  const [st, setSt] = useState({ online: false })
  const [hist, setHist] = useState([])
  const [diag, setDiag] = useState({ off: null, on: null })
  const [fixes, setFixes] = useState({ off: null, on: null })
  const [busy, setBusy] = useState('')
  const [mem, setMem] = useState([])
  const [incidents, setIncidents] = useState([])
  const [selectedFault, setSelectedFault] = useState('pool_exhaustion')
  const [toast, setToast] = useState('')
  const [codefixProposal, setCodefixProposal] = useState(null)
  const [codefixResult, setCodefixResult] = useState(null)
  const [codefixApplying, setCodefixApplying] = useState(false)

  const fail = (e) => {
    setToast(e.message || String(e))
    setTimeout(() => setToast(''), 5000)
  }

  const refreshMemory = useCallback(() => api.memory().then(setMem).catch(() => {}), [])
  const refreshIncidents = useCallback(() => api.incidents().then(setIncidents).catch(() => {}), [])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const [s, incList, cfStatus] = await Promise.all([
          api.state(),
          api.incidents(),
          api.codefixStatus().catch(() => ({})),
        ])
        if (!alive) return
        setSt(s)
        if (Array.isArray(incList)) setIncidents(incList)
        if (s.online) setHist((h) => [...h.slice(-29), s.p95_ms])
        if (cfStatus?.proposal && !codefixProposal) {
          setCodefixProposal(cfStatus.proposal)
          if (cfStatus.result) setCodefixResult(cfStatus.result)
        }
      } catch {
        if (alive) setSt((s) => ({ ...s, online: false }))
      }
    }
    tick()
    refreshMemory()
    const id = setInterval(tick, 1500)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [refreshMemory, codefixProposal])

  const resetRun = () => {
    setDiag({ off: null, on: null })
    setFixes({ off: null, on: null })
    setCodefixProposal(null)
    setCodefixResult(null)
  }

  const run = async (name, fn) => {
    setBusy(name)
    try {
      await fn()
    } catch (e) {
      fail(e)
    }
    setBusy('')
  }

  const breakIt = () => run('break', async () => { resetRun(); await api.breakIt(selectedFault) })
  const reset = () => run('reset', async () => { resetRun(); setHist([]); await api.reset(); refreshIncidents() })
  const diagnose = (useMemory) =>
    run(useMemory ? 'on' : 'off', async () => {
      const r = await api.diagnose(useMemory)
      setDiag((d) => ({ ...d, [useMemory ? 'on' : 'off']: r }))
      if (r.evidence?.has_code_bug) {
        try {
          const prop = await api.codefixPropose()
          setCodefixProposal(prop)
        } catch (e) {
          console.error('codefix proposal error', e)
        }
      }
    })
  const approve = (which) =>
    run('fix-' + which, async () => {
      const d = diag[which]
      const r = await api.fix({ action: d.diagnosis.action, diagnosis: d.diagnosis, evidence: d.evidence, use_memory: which === 'on' })
      setFixes((f) => ({ ...f, [which]: r }))
      refreshMemory()
      refreshIncidents()
    })
  const approveCodefix = async () => {
    if (!codefixProposal) return
    setCodefixApplying(true)
    try {
      const res = await api.codefixApprove(codefixProposal)
      setCodefixResult(res)
      refreshMemory()
      refreshIncidents()
    } catch (e) {
      fail(e)
    }
    setCodefixApplying(false)
  }
  const clearMemory = async () => {
    await api.clearMemory().catch(fail)
    refreshMemory()
  }

  const detectedIncident = incidents.find((i) => i.status === 'detected' || i.status === 'codefix_proposed')

  const openDetectedIncident = (inc) => {
    if (inc.type === 'codefix' && inc.codefix_proposal) {
      setCodefixProposal(inc.codefix_proposal)
      const el = document.getElementById('codefix-section')
      if (el) el.scrollIntoView({ behavior: 'smooth' })
      return
    }
    if (inc.diagnosis && inc.evidence) {
      setDiag((d) => ({ ...d, on: { diagnosis: inc.diagnosis, evidence: inc.evidence, use_memory: true } }))
    } else {
      diagnose(true)
    }
    const card = document.getElementById('diagnosis-section')
    if (card) card.scrollIntoView({ behavior: 'smooth' })
  }

  const degraded = st.fault || st.errors > 0 || st.p95_ms > 500
  const anyDiag = diag.off || diag.on
  const anyFix = fixes.off || fixes.on
  const recovered = (fixes.off && fixes.off.recovered) || (fixes.on && fixes.on.recovered)
  const evidence = (diag.on || diag.off)?.evidence
  const canDiagnose = st.online && (st.fault || st.errors > 0 || st.p95_ms > 500)

  const steps = [
    { label: 'Detect', state: degraded || anyDiag || detectedIncident ? 'done' : 'idle' },
    { label: 'Diagnose', state: busy === 'on' || busy === 'off' ? 'active' : anyDiag ? 'done' : 'idle' },
    { label: 'Approve', state: anyFix ? 'done' : anyDiag ? 'active' : 'idle' },
    { label: 'Apply fix', state: busy.startsWith('fix') ? 'active' : anyFix ? 'done' : 'idle' },
    { label: 'Verify', state: anyFix ? (recovered ? 'done' : 'bad') : 'idle' },
  ]

  return (
    <div className="relative h-screen w-full overflow-hidden">
      <div className="pointer-events-none absolute -top-40 -left-32 h-[28rem] w-[28rem] rounded-full bg-cyan-500/20 blur-[120px]" />
      <div className="pointer-events-none absolute top-1/3 -right-40 h-[32rem] w-[32rem] rounded-full bg-blue-600/20 blur-[140px]" />
      <div className="pointer-events-none absolute -bottom-40 left-1/3 h-[26rem] w-[26rem] rounded-full bg-teal-500/15 blur-[120px]" />

      <div className="relative flex h-full gap-5 p-5">
        <Sidebar tab={tab} setTab={setTab} online={st.online} mode={st.mode} />

        <main className="flex-1 h-full overflow-y-auto pr-1 pb-10">
          {tab === 'memory' ? (
            <MemoryView items={mem} onClear={clearMemory} />
          ) : tab === 'benchmark' ? (
            <BenchmarkView />
          ) : (
            <div className="space-y-6 pb-10">
              <div className="flex items-end justify-between flex-wrap gap-4">
                <div>
                  <h1 className="text-3xl font-bold">Incident Command</h1>
                  <p className="text-white/50 mt-1 flex items-center gap-2">
                    checkout-service {st.version || ''}
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${degraded ? 'bg-rose-400/10 border-rose-400/30 text-rose-300' : 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${degraded ? 'bg-rose-400 animate-pulse' : 'bg-emerald-400'}`} />
                      {!st.online ? 'Offline' : degraded ? 'Degraded' : 'Healthy'}
                    </span>
                  </p>
                </div>
                <div className="flex items-center gap-3 flex-wrap">
                  <button onClick={reset} disabled={!st.online || !!busy} className="flex items-center gap-2 px-4 py-2.5 rounded-xl glass-soft text-sm text-white/70 hover:text-white hover:bg-white/10 transition disabled:opacity-40">
                    <RotateCcw size={15} /> Reset
                  </button>

                  <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-xl p-1">
                    <select
                      value={selectedFault}
                      onChange={(e) => setSelectedFault(e.target.value)}
                      disabled={!st.online || !!busy}
                      className="bg-transparent text-sm text-white/90 px-3 py-1.5 rounded-lg border-0 focus:outline-none cursor-pointer"
                    >
                      {FAULT_OPTIONS.map((f) => (
                        <option key={f.id} value={f.id} className="bg-slate-900 text-white">
                          {f.label}
                        </option>
                      ))}
                    </select>

                    <button
                      onClick={breakIt}
                      disabled={!st.online || !!busy}
                      className="flex items-center gap-2 px-4 py-1.5 rounded-lg bg-gradient-to-r from-rose-500 to-orange-500 text-sm font-semibold hover:brightness-110 transition disabled:opacity-40 shadow-lg shadow-rose-500/20"
                    >
                      <Flame size={15} /> {busy === 'break' ? 'Breaking…' : 'Break service'}
                    </button>
                  </div>
                </div>
              </div>

              {detectedIncident && (
                <motion.div
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`glass-card rounded-2xl p-4 flex items-center justify-between flex-wrap gap-3 shadow-lg ${
                    detectedIncident.type === 'codefix'
                      ? 'border-indigo-500/40 bg-indigo-500/15 text-indigo-200 shadow-indigo-500/10'
                      : 'border-amber-400/40 bg-amber-500/10 text-amber-200 shadow-amber-500/10'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={`h-9 w-9 rounded-xl flex items-center justify-center ${
                        detectedIncident.type === 'codefix'
                          ? 'bg-indigo-500/25 text-indigo-300'
                          : 'bg-amber-500/20 text-amber-300'
                      }`}
                    >
                      <Siren size={20} className="animate-pulse" />
                    </div>
                    <div>
                      <p className="font-semibold text-white flex items-center gap-2">
                        {detectedIncident.type === 'codefix' ? 'Code Bug Incident Auto-Detected' : 'Incident Auto-Detected'}
                        <span className="text-xs px-2 py-0.5 rounded-full bg-white/10 text-white font-mono">
                          {detectedIncident.id}
                        </span>
                      </p>
                      <p className="text-xs text-white/70">
                        {detectedIncident.type === 'codefix'
                          ? 'Unhandled exception traceback detected in app.log. Sandbox tested patch generated.'
                          : 'Elevated latency or 5xx error rate sustained for 3 consecutive polls (6s). Background agent ran diagnosis automatically.'}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => openDetectedIncident(detectedIncident)}
                    className="px-4 py-2 rounded-xl bg-white text-slate-950 font-semibold text-xs hover:brightness-110 transition flex items-center gap-1.5 shadow-md"
                  >
                    {detectedIncident.type === 'codefix' ? <Code2 size={14} /> : <Brain size={14} />}
                    {detectedIncident.type === 'codefix' ? 'View Code Fix Patch' : 'View Diagnosis'}
                  </button>
                </motion.div>
              )}

              {!st.online && (
                <div className="glass-card rounded-2xl p-4 border-rose-400/30 text-rose-200 text-sm">
                  Can't reach the agent API. Start it with <code className="px-1.5 py-0.5 rounded bg-white/10">run.bat</code> (or see README), then this page connects automatically.
                </div>
              )}

              <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
                <StatsCard title="p95 latency" value={st.online ? `${st.p95_ms}ms` : '—'} sub={<><ArrowUpRight size={16} /> last 30s window</>} icon={Timer} gradient="from-cyan-400 to-blue-500" tone={st.p95_ms > 500 ? 'text-rose-400' : 'text-emerald-400'} />
                <StatsCard title="Errors" value={st.online ? st.errors : '—'} sub="5xx in last 30s" icon={TriangleAlert} gradient="from-rose-500 to-orange-500" tone={st.errors > 0 ? 'text-rose-400' : 'text-emerald-400'} />
                <StatsCard title="Memory" value={st.online && st.memory_mb != null ? `${st.memory_mb} MB` : '—'} sub="process memory" icon={HardDrive} gradient="from-teal-400 to-emerald-500" tone={st.memory_mb > 800 ? 'text-rose-400' : 'text-white/60'} />
                <StatsCard title="Incidents learned" value={mem.length} sub={`${mem.filter((m) => m.worked).length} fixes worked`} icon={Brain} gradient="from-violet-500 to-blue-500" tone="text-cyan-300" />
              </div>

              <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                <div className="glass-card rounded-2xl p-6 xl:col-span-2">
                  <div className="flex items-center justify-between mb-3">
                    <p className="font-semibold flex items-center gap-2"><Activity size={16} className="text-cyan-300" /> Latency (p95)</p>
                    <span className="text-xs text-white/40">live, 1.5s refresh</span>
                  </div>
                  <Sparkline values={hist.length ? hist : [0, 0]} color={degraded ? '#fb7185' : '#22d3ee'} />
                </div>
                <div className="glass-card rounded-2xl p-6">
                  <p className="font-semibold mb-3">Evidence</p>
                  {evidence ? (
                    <div className="space-y-3">
                      <dl className="space-y-2 text-sm">
                        {[
                          ['Version', evidence.version],
                          ['p95 before', `${evidence.p95_before_ms}ms`],
                          ['p95 during', `${evidence.p95_after_ms}ms`],
                          ['Errors', evidence.error_count],
                          ['Top error', evidence.top_errors?.[0]?.[0] || '—'],
                          ['Symptom', evidence.symptom_type || '—'],
                          evidence.has_code_bug && ['Code Bug', evidence.code_exception],
                          evidence.has_code_bug && ['Source File', `${evidence.code_file}:${evidence.code_line}`],
                          ['First error', evidence.first_error_secs_after_change != null ? `${evidence.first_error_secs_after_change}s after change` : '—'],
                          ['Config change', evidence.last_config_change ? `pool_hold_s=${evidence.last_config_change.pool_hold_s}` : '—'],
                        ].filter(Boolean).map(([k, v]) => (
                          <div key={k} className="flex justify-between gap-3">
                            <dt className="text-white/45 shrink-0">{k}</dt>
                            <dd className="text-white/85 text-right truncate" title={String(v)}>{v}</dd>
                          </div>
                        ))}
                      </dl>

                      {evidence.has_code_bug && !codefixProposal && (
                        <button
                          onClick={async () => {
                            try {
                              const p = await api.codefixPropose()
                              setCodefixProposal(p)
                            } catch (e) {
                              fail(e)
                            }
                          }}
                          className="w-full py-2 px-3 rounded-xl bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 text-xs font-semibold hover:bg-indigo-500/30 transition flex items-center justify-center gap-1.5"
                        >
                          <Code2 size={13} /> Propose Code Fix
                        </button>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-white/45">Parsed from logs and metrics when the agent investigates.</p>
                  )}
                </div>
              </div>

              <div className="glass-card rounded-2xl p-5">
                <Pipeline steps={steps} />
              </div>

              {codefixProposal && (
                <div id="codefix-section" className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h2 className="text-xl font-semibold flex items-center gap-2 text-white">
                      <Code2 className="text-indigo-400" size={20} /> Real Code Fix
                    </h2>
                    <span className="text-xs text-white/50">Verified in sandbox pytest</span>
                  </div>
                  <CodeFixCard
                    proposal={codefixProposal}
                    applying={codefixApplying}
                    onApprove={approveCodefix}
                    result={codefixResult}
                  />
                </div>
              )}

              <div id="diagnosis-section">
                <h2 className="text-xl font-semibold mb-3">Same incident, with and without memory</h2>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <DiagnosisCard memoryOn={false} data={diag.off} busy={busy === 'off'} canRun={canDiagnose} onRun={() => diagnose(false)} onApprove={() => approve('off')} applying={busy === 'fix-off'} result={fixes.off} />
                  <DiagnosisCard memoryOn data={diag.on} busy={busy === 'on'} canRun={canDiagnose} onRun={() => diagnose(true)} onApprove={() => approve('on')} applying={busy === 'fix-on'} result={fixes.on} />
                </div>
                {!canDiagnose && st.online && <p className="text-sm text-white/40 mt-3">Click “Break service” to inject a failure, then let the agent investigate.</p>}
              </div>
            </div>
          )}
        </main>
      </div>

      <AnimatePresence>
        {toast && (
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="fixed bottom-6 right-6 glass-card rounded-xl px-4 py-3 text-sm text-rose-200 flex items-center gap-3 max-w-sm">
            {toast}
            <button onClick={() => setToast('')}><X size={14} /></button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
