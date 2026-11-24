import { motion } from 'framer-motion'
import { Brain, BrainCog, Loader2, ShieldCheck, ShieldAlert, Shield, Wrench, History, RotateCcw } from 'lucide-react'

const ACTION_LABEL = {
  investigate: 'Investigate alert',
  rollback_config: 'Roll back config',
  scale_pool: 'Scale DB pool',
  restart_service: 'Restart service',
  add_index: 'Add DB index',
  rollback_deploy: 'Roll back deploy',
  enable_circuit_breaker: 'Enable circuit breaker',
  enable_cache: 'Enable cache',
}

export default function DiagnosisCard({
  memoryOn,
  data,
  busy,
  canRun,
  onRun,
  onApprove,
  applying,
  result,
  onRollback,
  rollingBack,
}) {
  const accent = memoryOn ? 'from-cyan-400 to-blue-500' : 'from-slate-400 to-slate-600'
  const d = data?.diagnosis
  const pol = d?.policy_decision || data?.policy

  return (
    <motion.div whileHover={{ y: -4 }} transition={{ type: 'spring', stiffness: 300, damping: 22 }} className="glass-card rounded-2xl p-6 flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`h-10 w-10 rounded-xl bg-gradient-to-br ${accent} flex items-center justify-center`}>
            {memoryOn ? <Brain size={20} /> : <BrainCog size={20} />}
          </div>
          <div>
            <p className="font-semibold">{memoryOn ? 'Memory ON' : 'Memory OFF'}</p>
            <p className="text-xs text-white/50">{memoryOn ? 'Recalls past incidents' : 'Fresh start every time'}</p>
          </div>
        </div>
        {d && <span className="text-[11px] px-2 py-1 rounded-full bg-white/5 border border-white/10 text-white/60">{d.model_used}</span>}
      </div>

      {!d && (
        <div className="flex-1 flex flex-col items-center justify-center py-8 text-center">
          <p className="text-sm text-white/50 mb-4">Run the agent on the current incident.</p>
          <button
            disabled={!canRun || busy}
            onClick={onRun}
            className={`px-5 py-2.5 rounded-xl text-sm font-semibold bg-gradient-to-r ${accent} disabled:opacity-40 hover:brightness-110 transition flex items-center gap-2`}
          >
            {busy && <Loader2 size={15} className="animate-spin" />}
            {busy ? 'Investigating…' : 'Diagnose'}
          </button>
        </div>
      )}

      {d && (
        <div className="space-y-4 text-sm">
          <div>
            <p className="text-xs uppercase tracking-wider text-white/40 mb-1">Root cause</p>
            <p className="text-white/90">{d.root_cause}</p>
          </div>

          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-cyan-400/10 border border-cyan-400/30 text-cyan-200 text-xs font-semibold">
              <Wrench size={13} /> {ACTION_LABEL[d.action] || d.action}
            </span>
            <div className="flex-1">
              <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
                <div className={`h-full rounded-full bg-gradient-to-r ${accent}`} style={{ width: `${Math.round((d.confidence || 0) * 100)}%` }} />
              </div>
              <p className="text-[11px] text-white/40 mt-1">{Math.round((d.confidence || 0) * 100)}% confidence</p>
            </div>
          </div>

          <p className="text-white/70">{d.proposed_fix}</p>

          {/* Policy Decision Chip & Reasons */}
          {pol && (
            <div className="rounded-xl p-3 bg-white/5 border border-white/10 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs uppercase tracking-wider text-white/40 flex items-center gap-1.5">
                  <Shield size={12} className="text-cyan-400" /> Policy Decision
                </span>
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${
                      pol.decision === 'AUTO_APPLY'
                        ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
                        : pol.decision === 'BLOCKED'
                        ? 'bg-rose-400/10 border-rose-400/30 text-rose-300'
                        : 'bg-amber-400/10 border-amber-400/30 text-amber-300'
                    }`}
                  >
                    {pol.decision}
                  </span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-white/50 uppercase font-mono">
                    {pol.risk_tier} risk
                  </span>
                </div>
              </div>
              {pol.reasons && (
                <ul className="text-xs text-white/60 space-y-1 pl-1">
                  {pol.reasons.map((r, i) => (
                    <li key={i}>• {r}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className={`rounded-xl p-3 border ${memoryOn ? 'bg-cyan-400/10 border-cyan-400/25' : 'bg-white/5 border-white/10'}`}>
            <p className="text-xs uppercase tracking-wider text-white/40 mb-1 flex items-center gap-1.5"><History size={12} /> Why this plan</p>
            <p className="text-white/85">{d.why_plan_changed}</p>
            {d.memories_recalled > 0 && d.past && (
              <ul className="mt-2 space-y-1">
                {d.past.map((m, i) => (
                  <li key={i} className="text-xs text-white/60">
                    • {ACTION_LABEL[m.action] || m.action}: <span className={m.worked ? 'text-emerald-300' : 'text-rose-300'}>{m.worked ? 'worked' : 'failed'}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {!result && (
            d.action === 'investigate' ? (
              <div className="w-full py-2.5 px-4 rounded-xl bg-amber-400/10 border border-amber-400/30 text-amber-300 font-semibold text-xs flex items-center justify-center gap-2">
                <ShieldAlert size={15} />
                <span>Unconfirmed alert</span>
              </div>
            ) : (
              <button
                onClick={onApprove}
                disabled={applying}
                className="w-full py-2.5 rounded-xl bg-gradient-to-r from-emerald-400 to-teal-500 text-slate-900 font-semibold hover:brightness-110 disabled:opacity-60 transition flex items-center justify-center gap-2"
              >
                {applying ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
                {applying ? 'Applying & verifying…' : 'Approve & apply fix'}
              </button>
            )
          )}

          {result && (
            <div className="space-y-3">
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className={`rounded-xl p-4 border ${result.recovered ? 'bg-emerald-400/10 border-emerald-400/30' : 'bg-rose-400/10 border-rose-400/30'}`}>
                <p className={`font-bold ${result.recovered ? 'text-emerald-300' : 'text-rose-300'}`}>
                  {result.recovered ? 'RECOVERED' : result.auto_rolled_back ? 'FAILED - AUTO-ROLLED BACK' : 'NOT RECOVERED'}
                </p>
                <p className="text-white/80 mt-1">
                  p95 {result.before.p95_ms}ms → {result.after.p95_ms ?? 'n/a'}ms · errors {result.before.errors} → {result.after.errors}
                </p>
                {result.seconds_to_resolve != null && <p className="text-xs text-white/50 mt-1">{result.seconds_to_resolve}s from alert to verified outcome</p>}
                {result.wrong_attempts != null && result.wrong_attempts > 0 && (
                  <p className="text-xs text-amber-300/80 mt-1">{result.wrong_attempts} wrong attempt(s) recorded for this incident</p>
                )}
                {!result.recovered && <p className="text-xs text-white/60 mt-2">Saved to memory as a failed fix, so the next incident won't repeat it.</p>}
              </motion.div>

              {onRollback && (
                <button
                  onClick={onRollback}
                  disabled={rollingBack}
                  className="w-full py-2.5 rounded-xl border border-rose-400/30 bg-rose-500/10 text-rose-300 text-xs font-semibold hover:bg-rose-500/20 transition flex items-center justify-center gap-2"
                >
                  <RotateCcw size={14} className={rollingBack ? 'animate-spin' : ''} />
                  {rollingBack ? 'Rolling back…' : 'Manual Rollback'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}
