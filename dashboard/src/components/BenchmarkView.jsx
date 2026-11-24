import { useEffect, useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Play, Loader2, CheckCircle2, XCircle, ArrowUpRight, TrendingUp, Sparkles, RefreshCw, Award } from 'lucide-react'
import { api } from '../api'

export default function BenchmarkView() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState({ running: false, percent: 0 })
  const [rounds, setRounds] = useState(3)
  const [seed, setSeed] = useState(42)
  const pollingRef = useRef(null)

  const loadResults = async () => {
    try {
      const res = await api.benchmark()
      if (res && res.summary) {
        setData(res)
      }
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => {
    loadResults()
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [])

  const pollStatus = () => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(async () => {
      try {
        const s = await api.benchmarkStatus()
        setStatus(s)
        if (!s.running) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
          setLoading(false)
          loadResults()
        }
      } catch {
        // ignore
      }
    }, 800)
  }

  const handleStart = async () => {
    setLoading(true)
    try {
      await api.runBenchmark(rounds, seed)
      pollStatus()
    } catch (e) {
      console.error(e)
      setLoading(false)
    }
  }

  const summary = data?.summary
  const off = summary?.memory_off
  const on = summary?.memory_on
  const perRound = data?.per_round || []

  // Dynamic headline computed strictly from the data
  let headline = summary?.headline
  if (!headline && off && on) {
    const wrongCut = off.avg_wrong_attempts > 0 ? Math.round(((off.avg_wrong_attempts - on.avg_wrong_attempts) / off.avg_wrong_attempts) * 100) : 0
    const timeCut = off.avg_seconds_to_recovery > 0 ? Math.round(((off.avg_seconds_to_recovery - on.avg_seconds_to_recovery) / off.avg_seconds_to_recovery) * 100) : 0
    headline = `Memory cut wrong attempts by ${wrongCut}% and time-to-fix by ${timeCut}%`
  }

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-bold">Benchmark: Memory ON vs OFF</h1>
          <p className="text-white/50 mt-1">
            Empirical evaluation of identical offline heuristics across 6 simulated faults.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 glass-soft px-3 py-2 rounded-xl text-xs text-white/70">
            <span>Rounds:</span>
            <select
              value={rounds}
              onChange={(e) => setRounds(Number(e.target.value))}
              disabled={loading || status.running}
              className="bg-transparent text-white border-0 font-semibold focus:outline-none cursor-pointer"
            >
              <option value={1} className="bg-slate-900">1 (6 tests/arm)</option>
              <option value={2} className="bg-slate-900">2 (12 tests/arm)</option>
              <option value={3} className="bg-slate-900">3 (18 tests/arm)</option>
            </select>
          </div>

          <button
            onClick={handleStart}
            disabled={loading || status.running}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 text-sm font-semibold hover:brightness-110 transition disabled:opacity-40 shadow-lg shadow-cyan-500/20"
          >
            {loading || status.running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
            {loading || status.running ? 'Running…' : 'Run benchmark'}
          </button>
        </div>
      </div>

      {/* Progress Bar when running */}
      <AnimatePresence>
        {(loading || status.running) && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="glass-card rounded-2xl p-4 border-cyan-400/40 bg-cyan-500/10 space-y-2"
          >
            <div className="flex justify-between items-center text-xs">
              <span className="font-semibold text-cyan-300 flex items-center gap-1.5">
                <Loader2 size={13} className="animate-spin" />
                Evaluating {status.arm ? (status.arm === 'memory_on' ? 'Memory ON' : 'Memory OFF') : 'benchmark'} · {status.fault || 'preparing'}
              </span>
              <span className="text-white/60 font-mono">
                {status.completed || 0}/{status.total || (rounds * 12)} ({status.percent || 0}%)
              </span>
            </div>
            <div className="h-2 rounded-full bg-white/10 overflow-hidden">
              <motion.div
                className="h-full bg-gradient-to-r from-cyan-400 to-blue-500 rounded-full"
                animate={{ width: `${status.percent || 5}%` }}
                transition={{ ease: 'easeOut', duration: 0.3 }}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Headline Banner */}
      {headline && (
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          className="glass-card rounded-2xl p-5 border-emerald-400/30 bg-emerald-500/10 flex items-center justify-between gap-4"
        >
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-emerald-400/20 flex items-center justify-center text-emerald-300">
              <Award size={22} />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wider text-emerald-300/80 font-semibold">Key Finding</p>
              <p className="text-lg font-bold text-white mt-0.5">{headline}</p>
            </div>
          </div>
          <span className="text-xs px-3 py-1 rounded-full bg-white/10 text-white/60 shrink-0">
            Seed {data?.seed || seed} · {data?.rounds || rounds} rounds
          </span>
        </motion.div>
      )}

      {/* Side-by-Side Comparison Cards */}
      {off && on ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Metric 1: First-try Success Rate */}
          <div className="glass-card rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <p className="text-xs uppercase tracking-wider text-white/40 mb-1">First-Try Success Rate</p>
              <p className="text-2xl font-bold">
                {Math.round(on.first_try_success_rate * 100)}%
                <span className="text-sm font-normal text-emerald-400 ml-2">
                  (+{Math.round((on.first_try_success_rate - off.first_try_success_rate) * 100)}%)
                </span>
              </p>
            </div>

            <div className="space-y-3 mt-4 text-xs">
              <div>
                <div className="flex justify-between text-white/70 mb-1">
                  <span>Memory ON</span>
                  <span className="font-semibold text-cyan-300">{Math.round(on.first_try_success_rate * 100)}%</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div className="h-full bg-cyan-400 rounded-full" style={{ width: `${Math.round(on.first_try_success_rate * 100)}%` }} />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-white/50 mb-1">
                  <span>Memory OFF</span>
                  <span>{Math.round(off.first_try_success_rate * 100)}%</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div className="h-full bg-slate-500 rounded-full" style={{ width: `${Math.round(off.first_try_success_rate * 100)}%` }} />
                </div>
              </div>
            </div>
          </div>

          {/* Metric 2: Average Wrong Attempts */}
          <div className="glass-card rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <p className="text-xs uppercase tracking-wider text-white/40 mb-1">Avg Wrong Attempts / Incident</p>
              <p className="text-2xl font-bold">
                {on.avg_wrong_attempts}
                <span className="text-sm font-normal text-emerald-400 ml-2">
                  (vs {off.avg_wrong_attempts} OFF)
                </span>
              </p>
            </div>

            <div className="space-y-3 mt-4 text-xs">
              <div>
                <div className="flex justify-between text-white/70 mb-1">
                  <span>Memory ON</span>
                  <span className="font-semibold text-emerald-400">{on.avg_wrong_attempts} attempts</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="h-full bg-emerald-400 rounded-full"
                    style={{ width: `${Math.max(10, Math.min(100, (on.avg_wrong_attempts / 2.5) * 100))}%` }}
                  />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-white/50 mb-1">
                  <span>Memory OFF</span>
                  <span>{off.avg_wrong_attempts} attempts</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="h-full bg-rose-400/80 rounded-full"
                    style={{ width: `${Math.min(100, (off.avg_wrong_attempts / 2.5) * 100)}%` }}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Metric 3: Average Recovery Time */}
          <div className="glass-card rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <p className="text-xs uppercase tracking-wider text-white/40 mb-1">Avg Time to Resolution</p>
              <p className="text-2xl font-bold">
                {on.avg_seconds_to_recovery}s
                <span className="text-sm font-normal text-emerald-400 ml-2">
                  (-{Math.round(((off.avg_seconds_to_recovery - on.avg_seconds_to_recovery) / off.avg_seconds_to_recovery) * 100)}%)
                </span>
              </p>
            </div>

            <div className="space-y-3 mt-4 text-xs">
              <div>
                <div className="flex justify-between text-white/70 mb-1">
                  <span>Memory ON</span>
                  <span className="font-semibold text-cyan-300">{on.avg_seconds_to_recovery}s</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="h-full bg-cyan-400 rounded-full"
                    style={{ width: `${Math.max(10, Math.min(100, (on.avg_seconds_to_recovery / (off.avg_seconds_to_recovery || 1)) * 100))}%` }}
                  />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-white/50 mb-1">
                  <span>Memory OFF</span>
                  <span>{off.avg_seconds_to_recovery}s</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div className="h-full bg-slate-500 rounded-full" style={{ width: '100%' }} />
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="glass-card rounded-2xl p-10 text-center text-white/50">
          <p className="text-base mb-2">No benchmark results found yet.</p>
          <p className="text-xs text-white/40">Click "Run benchmark" above to run an automated evaluation.</p>
        </div>
      )}

      {/* Per-Round Progression Chart */}
      {perRound.length > 0 && (
        <div className="glass-card rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="font-semibold text-white flex items-center gap-2">
                <TrendingUp size={16} className="text-cyan-400" />
                First-Try Success Rate Over Time (Rounds 1 → {perRound.length})
              </p>
              <p className="text-xs text-white/40">
                Memory ON learns from early failures and approaches 100% first-try success; Memory OFF repeats initial guesses.
              </p>
            </div>
            <div className="flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1.5 text-cyan-300 font-medium">
                <span className="h-2.5 w-2.5 rounded-full bg-cyan-400" /> Memory ON
              </span>
              <span className="flex items-center gap-1.5 text-slate-400 font-medium">
                <span className="h-2.5 w-2.5 rounded-full bg-slate-500" /> Memory OFF
              </span>
            </div>
          </div>

          {/* SVG Line / Bar visualization */}
          <div className="pt-4 pb-2">
            <svg viewBox="0 0 600 180" className="w-full h-44 overflow-visible">
              {/* Grid Lines */}
              {[0, 25, 50, 75, 100].map((pct) => {
                const y = 150 - (pct / 100) * 130
                return (
                  <g key={pct}>
                    <line x1="50" y1={y} x2="570" y2={y} stroke="rgba(255,255,255,0.08)" strokeDasharray="4 4" />
                    <text x="35" y={y + 4} fill="rgba(255,255,255,0.3)" fontSize="10" textAnchor="end">
                      {pct}%
                    </text>
                  </g>
                )
              })}

              {/* Data points and lines */}
              {(() => {
                const step = (570 - 70) / Math.max(1, perRound.length - 1)
                const onPoints = perRound.map((pr, idx) => {
                  const x = perRound.length === 1 ? 310 : 70 + idx * step
                  const y = 150 - pr.memory_on.first_try_success_rate * 130
                  return { x, y, val: pr.memory_on.first_try_success_rate }
                })
                const offPoints = perRound.map((pr, idx) => {
                  const x = perRound.length === 1 ? 310 : 70 + idx * step
                  const y = 150 - pr.memory_off.first_try_success_rate * 130
                  return { x, y, val: pr.memory_off.first_try_success_rate }
                })

                const onPath = onPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
                const offPath = offPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')

                return (
                  <>
                    {/* Paths */}
                    {perRound.length > 1 && (
                      <>
                        <path d={offPath} fill="none" stroke="#64748b" strokeWidth="2.5" strokeDasharray="3 3" />
                        <path d={onPath} fill="none" stroke="#22d3ee" strokeWidth="3" />
                      </>
                    )}

                    {/* Nodes */}
                    {offPoints.map((p, i) => (
                      <g key={`off-${i}`}>
                        <circle cx={p.x} cy={p.y} r="5" fill="#0f172a" stroke="#64748b" strokeWidth="2.5" />
                        <text x={p.x} y={p.y - 10} fill="#94a3b8" fontSize="10" textAnchor="middle" fontWeight="bold">
                          {Math.round(p.val * 100)}%
                        </text>
                        <text x={p.x} y="170" fill="rgba(255,255,255,0.6)" fontSize="11" textAnchor="middle">
                          Round {perRound[i].round}
                        </text>
                      </g>
                    ))}

                    {onPoints.map((p, i) => (
                      <g key={`on-${i}`}>
                        <circle cx={p.x} cy={p.y} r="6" fill="#22d3ee" stroke="#ffffff" strokeWidth="2" />
                        <text x={p.x} y={p.y - 10} fill="#22d3ee" fontSize="10" textAnchor="middle" fontWeight="bold">
                          {Math.round(p.val * 100)}%
                        </text>
                      </g>
                    ))}
                  </>
                )
              })()}
            </svg>
          </div>
        </div>
      )}

      {/* Incidents Breakdown Table */}
      {data?.incidents && data.incidents.length > 0 && (
        <div className="glass-card rounded-2xl p-6">
          <p className="font-semibold text-white mb-3">Individual Incident Executions ({data.incidents.length} total)</p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-white/40 border-b border-white/10 uppercase tracking-wider">
                <tr>
                  <th className="py-2.5 px-3">Arm</th>
                  <th className="py-2.5 px-3">Round</th>
                  <th className="py-2.5 px-3">Fault</th>
                  <th className="py-2.5 px-3">Attempts</th>
                  <th className="py-2.5 px-3">Outcome</th>
                  <th className="py-2.5 px-3">Actions Tried</th>
                  <th className="py-2.5 px-3 text-right">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-white/80">
                {data.incidents.slice(0, 24).map((inc, i) => (
                  <tr key={i} className="hover:bg-white/[0.02]">
                    <td className="py-2.5 px-3 font-medium">
                      <span className={`px-2 py-0.5 rounded-full ${inc.arm === 'memory_on' ? 'bg-cyan-400/10 text-cyan-300' : 'bg-slate-400/10 text-slate-300'}`}>
                        {inc.arm === 'memory_on' ? 'Memory ON' : 'Memory OFF'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">R{inc.round}</td>
                    <td className="py-2.5 px-3 font-mono text-white/90">{inc.fault}</td>
                    <td className="py-2.5 px-3">{inc.attempts}</td>
                    <td className="py-2.5 px-3">
                      {inc.first_try_success ? (
                        <span className="text-emerald-400 flex items-center gap-1 font-semibold">
                          <CheckCircle2 size={13} /> 1st Try
                        </span>
                      ) : inc.recovered ? (
                        <span className="text-amber-300 flex items-center gap-1">
                          <CheckCircle2 size={13} /> {inc.wrong_attempts} wrong
                        </span>
                      ) : (
                        <span className="text-rose-400 flex items-center gap-1">
                          <XCircle size={13} /> Failed
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-white/60 truncate max-w-xs" title={inc.tried_actions.join(', ')}>
                      {inc.tried_actions.join(' → ')}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono">{inc.seconds_to_resolve}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

