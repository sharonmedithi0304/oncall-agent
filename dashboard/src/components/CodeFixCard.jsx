import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Code2,
  Terminal,
  ShieldCheck,
  Loader2,
  CheckCircle2,
  XCircle,
  GitBranch,
  GitPullRequest,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  Sparkles,
} from 'lucide-react'

export default function CodeFixCard({
  proposal,
  applying,
  onApprove,
  result,
}) {
  const [showFullTrace, setShowFullTrace] = useState(false)
  const [showSandboxOutput, setShowSandboxOutput] = useState(false)

  if (!proposal) return null

  const tb = proposal.traceback_info || {}
  const diffLines = (proposal.diff || '').split('\n')
  const sandbox = proposal.sandbox_test || {}
  const isOffline = proposal.model_used === 'offline-patcher'

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card rounded-2xl p-6 border-indigo-500/30 bg-indigo-950/20 shadow-2xl space-y-6"
    >
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <div className="h-11 w-11 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white shadow-lg shadow-indigo-500/25">
            <Code2 size={22} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-lg font-bold text-white">Automated Source Code Fix</h3>
              <span className="text-xs px-2.5 py-0.5 rounded-full font-mono bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                {proposal.incident_id || 'active'}
              </span>
            </div>
            <p className="text-xs text-white/50">
              Agent located exception in source code, generated unified diff, and verified in isolated sandbox.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {proposal.recalled_from_memory && (
            <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-cyan-400/15 text-cyan-300 border border-cyan-400/30">
              <Sparkles size={13} /> Memory Recalled
            </span>
          )}
          <span
            className={`text-xs px-3 py-1 rounded-full font-medium border ${
              isOffline
                ? 'bg-amber-400/10 border-amber-400/30 text-amber-300'
                : 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
            }`}
          >
            {isOffline ? 'offline-patcher (rule-based)' : proposal.model_used}
          </span>
        </div>
      </div>

      {/* Root Cause & Traceback Location */}
      <div className="rounded-xl p-4 bg-white/5 border border-white/10 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2 text-xs">
          <div className="flex items-center gap-2 text-rose-300 font-mono font-semibold">
            <XCircle size={15} />
            <span>{tb.exception || 'Exception'}: {tb.message}</span>
          </div>
          <span className="text-white/60 font-mono bg-black/40 px-2 py-0.5 rounded">
            {tb.file}:{tb.line} in {tb.function}()
          </span>
        </div>

        <p className="text-sm text-white/80">{proposal.explanation}</p>

        {tb.traceback && (
          <div>
            <button
              onClick={() => setShowFullTrace(!showFullTrace)}
              className="text-xs text-white/40 hover:text-white/80 flex items-center gap-1 transition"
            >
              {showFullTrace ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              {showFullTrace ? 'Hide stack trace' : 'View stack trace'}
            </button>
            <AnimatePresence>
              {showFullTrace && (
                <motion.pre
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="mt-2 text-[11px] font-mono p-3 rounded-lg bg-black/60 text-white/70 overflow-x-auto leading-relaxed border border-white/5"
                >
                  {tb.traceback}
                </motion.pre>
              )}
            </AnimatePresence>
          </div>
        )}
      </div>

      {/* Red/Green Unified Diff Viewer */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-white/60">
          <span className="font-semibold uppercase tracking-wider text-white/40">Unified Diff Patch</span>
          <span className="font-mono">{tb.file}</span>
        </div>
        <div className="rounded-xl overflow-hidden border border-white/10 bg-black/70 font-mono text-xs leading-relaxed max-h-72 overflow-y-auto">
          {diffLines.map((line, idx) => {
            let lineClass = 'text-white/60 hover:bg-white/5'
            if (line.startsWith('+++') || line.startsWith('---')) {
              lineClass = 'text-white/40 font-bold bg-white/5'
            } else if (line.startsWith('@@')) {
              lineClass = 'text-cyan-400 bg-cyan-950/30'
            } else if (line.startsWith('+')) {
              lineClass = 'bg-emerald-500/20 text-emerald-300 font-semibold'
            } else if (line.startsWith('-')) {
              lineClass = 'bg-rose-500/20 text-rose-300 font-semibold'
            }
            return (
              <div key={idx} className={`px-4 py-0.5 whitespace-pre ${lineClass}`}>
                {line || ' '}
              </div>
            )
          })}
        </div>
      </div>

      {/* Sandbox Test Results (Before vs After) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <div className="rounded-xl p-3 bg-white/5 border border-white/10 flex items-center justify-between">
          <span className="text-white/60">Sandbox Test Before Patch:</span>
          <span
            className={`flex items-center gap-1.5 font-semibold px-2.5 py-1 rounded-full ${
              sandbox.before?.passed
                ? 'bg-emerald-400/15 text-emerald-300'
                : 'bg-rose-400/15 text-rose-300'
            }`}
          >
            {sandbox.before?.passed ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
            {sandbox.before?.passed ? 'PASSED' : 'FAILED (bug reproduced)'}
          </span>
        </div>

        <div className="rounded-xl p-3 bg-white/5 border border-white/10 flex items-center justify-between">
          <span className="text-white/60">Sandbox Test After Patch:</span>
          <span
            className={`flex items-center gap-1.5 font-semibold px-2.5 py-1 rounded-full ${
              sandbox.after?.passed
                ? 'bg-emerald-400/20 text-emerald-300 border border-emerald-400/30'
                : 'bg-rose-400/15 text-rose-300'
            }`}
          >
            {sandbox.after?.passed ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
            {sandbox.after?.passed ? 'ALL PASSED (clean fix)' : 'FAILED'}
          </span>
        </div>
      </div>

      {/* Test Output Toggle */}
      {sandbox.after?.output && (
        <div>
          <button
            onClick={() => setShowSandboxOutput(!showSandboxOutput)}
            className="text-xs text-white/40 hover:text-white/80 flex items-center gap-1 transition"
          >
            {showSandboxOutput ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {showSandboxOutput ? 'Hide pytest output' : 'Show pytest sandbox output'}
          </button>
          <AnimatePresence>
            {showSandboxOutput && (
              <motion.pre
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="mt-2 text-[11px] font-mono p-3 rounded-lg bg-black/60 text-white/70 overflow-x-auto leading-relaxed border border-white/5"
              >
                {sandbox.after.output}
              </motion.pre>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Action / Result */}
      {!result ? (
        <button
          onClick={onApprove}
          disabled={applying}
          className="w-full py-3 rounded-xl bg-gradient-to-r from-emerald-400 via-teal-400 to-cyan-400 text-slate-950 font-bold hover:brightness-110 disabled:opacity-50 transition shadow-lg shadow-emerald-500/20 flex items-center justify-center gap-2 cursor-pointer"
        >
          {applying ? <Loader2 size={18} className="animate-spin" /> : <ShieldCheck size={18} />}
          {applying ? 'Applying patch & running verification burst…' : 'Approve & Apply Code Patch'}
        </button>
      ) : (
        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          className={`rounded-xl p-5 border space-y-3 ${
            result.recovered
              ? 'bg-emerald-400/10 border-emerald-400/40'
              : 'bg-rose-400/10 border-rose-400/40'
          }`}
        >
          <div className="flex items-center justify-between flex-wrap gap-2">
            <span
              className={`text-sm font-bold flex items-center gap-1.5 ${
                result.recovered ? 'text-emerald-300' : 'text-rose-300'
              }`}
            >
              {result.recovered ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
              {result.recovered ? 'VERIFIED RECOVERED IN PRODUCTION' : 'VERIFICATION FAILED'}
            </span>
            {result.report_file && (
              <span className="text-xs text-white/60 font-mono">
                Report: {result.report_file}
              </span>
            )}
          </div>

          {result.metrics && (
            <div className="text-xs text-white/80 grid grid-cols-3 gap-2 py-2 px-3 bg-black/30 rounded-lg">
              <div>
                <span className="text-white/40 block">Requests Burst</span>
                <span className="font-semibold">{result.metrics.requests}</span>
              </div>
              <div>
                <span className="text-white/40 block">Errors</span>
                <span className="font-semibold text-emerald-300">{result.metrics.errors}</span>
              </div>
              <div>
                <span className="text-white/40 block">p95 Latency</span>
                <span className="font-semibold">{result.metrics.p95_ms}ms</span>
              </div>
            </div>
          )}

          {/* Git Branch & PR */}
          {result.git && (
            <div className="pt-2 border-t border-white/10 flex items-center justify-between flex-wrap gap-3 text-xs">
              <div className="flex items-center gap-2 font-mono text-cyan-300">
                <GitBranch size={14} />
                <span>{result.git.branch}</span>
              </div>

              {result.git.pr_url ? (
                <a
                  href={result.git.pr_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/30 transition"
                >
                  <GitPullRequest size={13} /> View Pull Request <ExternalLink size={12} />
                </a>
              ) : (
                <div className="flex items-center gap-2 text-white/60">
                  <span className="px-2.5 py-1 rounded bg-white/10 font-mono">
                    Committed to {result.git.branch}
                  </span>
                  {result.git.compare_url && (
                    <a
                      href={result.git.compare_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-cyan-300 underline flex items-center gap-1 hover:text-cyan-200"
                    >
                      Compare <ExternalLink size={11} />
                    </a>
                  )}
                </div>
              )}
            </div>
          )}
        </motion.div>
      )}
    </motion.div>
  )
}
