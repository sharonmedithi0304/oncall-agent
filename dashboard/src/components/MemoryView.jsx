import { motion } from 'framer-motion'
import { Trash2, CheckCircle2, XCircle, BrainCircuit } from 'lucide-react'

const ACTION_LABEL = {
  rollback_config: 'Roll back config',
  scale_pool: 'Scale DB pool',
  restart_service: 'Restart service',
  add_index: 'Add DB index',
  rollback_deploy: 'Roll back deploy',
  enable_circuit_breaker: 'Enable circuit breaker',
  enable_cache: 'Enable cache',
}

export default function MemoryView({ items, onClear }) {
  const worked = items.filter((m) => m.worked).length
  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold">Agent Memory</h1>
          <p className="text-white/50 mt-1">Every incident the agent has handled. Failed fixes are never repeated.</p>
        </div>
        <button onClick={onClear} className="flex items-center gap-2 px-4 py-2 rounded-xl glass-soft text-sm text-white/70 hover:text-white hover:bg-white/10 transition">
          <Trash2 size={15} /> Clear memory
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[['Incidents stored', items.length], ['Fixes that worked', worked], ['Fixes that failed', items.length - worked]].map(([t, v]) => (
          <motion.div whileHover={{ y: -5 }} key={t} className="glass-card rounded-2xl p-5">
            <p className="text-sm text-white/60">{t}</p>
            <p className="text-3xl font-bold mt-1">{v}</p>
          </motion.div>
        ))}
      </div>

      {items.length === 0 ? (
        <div className="glass-card rounded-2xl p-10 text-center text-white/50">
          <BrainCircuit className="mx-auto mb-3 text-white/30" size={36} />
          Nothing remembered yet. Resolve an incident and it shows up here.
        </div>
      ) : (
        <div className="space-y-3">
          {[...items].reverse().map((m, i) => (
            <motion.div key={m.ts + '-' + i} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="glass-card rounded-2xl p-5 flex items-center gap-4">
              {m.worked ? <CheckCircle2 className="text-emerald-400 shrink-0" /> : <XCircle className="text-rose-400 shrink-0" />}
              <div className="flex-1 min-w-0">
                <p className="font-semibold">{ACTION_LABEL[m.action] || m.action} <span className={m.worked ? 'text-emerald-300' : 'text-rose-300'}>· {m.worked ? 'worked' : 'failed'}</span></p>
                <p className="text-sm text-white/55 truncate">{m.root_cause}</p>
              </div>
              <div className="text-right text-sm text-white/70 shrink-0">
                <p>p95 {m.before?.p95_ms}ms → {m.after?.p95_ms ?? 'n/a'}ms</p>
                <p className="text-xs text-white/40">
                  {m.memory_on ? 'memory ON' : 'memory OFF'}
                  {m.wrong_attempts ? ` · ${m.wrong_attempts} wrong attempt(s)` : ''} · {new Date(m.ts * 1000).toLocaleTimeString()}
                </p>
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
}
