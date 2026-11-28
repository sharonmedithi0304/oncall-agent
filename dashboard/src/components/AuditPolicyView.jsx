import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ShieldCheck,
  ShieldAlert,
  Shield,
  FileCheck2,
  RefreshCw,
  Search,
  CheckCircle2,
  XCircle,
  Clock,
  User,
  Hash,
  ChevronDown,
  ChevronRight,
  Filter,
  GitPullRequest,
  ExternalLink,
  Send,
  Bot,
} from 'lucide-react'
import { api } from '../api'

const EVENT_COLORS = {
  ingested: 'bg-purple-500/15 border-purple-500/30 text-purple-300',
  detected: 'bg-cyan-500/15 border-cyan-500/30 text-cyan-300',
  diagnosed: 'bg-blue-500/15 border-blue-500/30 text-blue-300',
  policy_decision: 'bg-amber-500/15 border-amber-500/30 text-amber-300',
  approved: 'bg-teal-500/15 border-teal-500/30 text-teal-300',
  applied: 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300',
  verified: 'bg-green-500/15 border-green-500/30 text-green-300',
  rolled_back: 'bg-rose-500/15 border-rose-500/30 text-rose-300',
}

const TIER_COLORS = {
  low: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300',
  medium: 'bg-amber-400/10 border-amber-400/30 text-amber-300',
  high: 'bg-rose-400/10 border-rose-400/30 text-rose-300',
}

export default function AuditPolicyView() {
  const [auditLog, setAuditLog] = useState([])
  const [verifyStatus, setVerifyStatus] = useState(null)
  const [policies, setPolicies] = useState({})
  const [integrations, setIntegrations] = useState({})
  const [verifying, setVerifying] = useState(false)
  const [selectedIncident, setSelectedIncident] = useState('all')
  const [filterText, setFilterText] = useState('')
  const [expandedId, setExpandedId] = useState(null)

  const loadData = async () => {
    try {
      const [entries, pol, integ] = await Promise.all([
        api.audit().catch(() => []),
        api.policies().catch(() => ({})),
        api.integrations().catch(() => ({})),
      ])
      setAuditLog(Array.isArray(entries) ? entries : [])
      setPolicies(pol || {})
      setIntegrations(integ || {})
    } catch (e) {
      console.error('Audit view fetch error', e)
    }
  }

  const checkVerification = async () => {
    setVerifying(true)
    try {
      const v = await api.auditVerify()
      setVerifyStatus(v)
    } catch (e) {
      setVerifyStatus({ verified: false, error: String(e) })
    }
    setVerifying(false)
  }

  useEffect(() => {
    loadData()
    checkVerification()
    const timer = setInterval(() => {
      loadData()
    }, 3000)
    return () => clearInterval(timer)
  }, [])

  // Unique incident IDs for filter
  const incidentIds = Array.from(new Set(auditLog.map((e) => e.incident_id).filter(Boolean)))

  const filteredEntries = auditLog.filter((entry) => {
    if (selectedIncident !== 'all' && entry.incident_id !== selectedIncident) {
      return false
    }
    if (filterText) {
      const q = filterText.toLowerCase()
      const matchEvent = (entry.event || '').toLowerCase().includes(q)
      const matchActor = (entry.actor || '').toLowerCase().includes(q)
      const matchInc = (entry.incident_id || '').toLowerCase().includes(q)
      const matchDetails = JSON.stringify(entry.details || {}).toLowerCase().includes(q)
      return matchEvent || matchActor || matchInc || matchDetails
    }
    return true
  })

  return (
    <div className="space-y-6 pb-12">
      {/* Header & Verification Badge */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3 text-white">
            <FileCheck2 className="text-cyan-400" size={30} /> Audit Log & Policy Engine
          </h1>
          <p className="text-white/50 text-sm mt-1">
            Tamper-evident append-only hash chain and safety policy rules.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div
            className={`px-4 py-2 rounded-2xl flex items-center gap-2.5 border text-sm font-medium ${
              verifyStatus?.verified
                ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
                : 'bg-rose-400/10 border-rose-400/30 text-rose-300'
            }`}
          >
            {verifyStatus?.verified ? (
              <>
                <ShieldCheck size={18} className="text-emerald-400" />
                <span>
                  Chain Verified ({verifyStatus.total_entries} blocks intact)
                </span>
              </>
            ) : (
              <>
                <ShieldAlert size={18} className="text-rose-400 animate-pulse" />
                <span>
                  {verifyStatus?.error ? `Tamper Alert: ${verifyStatus.error}` : 'Verifying chain...'}
                </span>
              </>
            )}
          </div>

          <button
            onClick={checkVerification}
            disabled={verifying}
            className="flex items-center gap-2 px-3.5 py-2 rounded-xl glass-soft text-sm text-white/80 hover:text-white transition disabled:opacity-40"
            title="Re-verify cryptographic hash chain"
          >
            <RefreshCw size={14} className={verifying ? 'animate-spin' : ''} />
            Re-verify
          </button>
        </div>
      </div>

      {/* Settings / Integrations Panel */}
      <div className="glass-card rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot className="text-cyan-400" size={20} />
            <h2 className="text-lg font-bold text-white">External Integrations & Webhooks</h2>
          </div>
          <span className="text-xs text-white/40">Status & Graceful Degradation</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* GitHub */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold text-white">
                <GitPullRequest size={18} className="text-indigo-400" /> GitHub
              </div>
              <span
                className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${
                  integrations.github?.configured
                    ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
                    : 'bg-white/5 border-white/10 text-white/40'
                }`}
              >
                {integrations.github?.configured ? 'Configured' : 'Not configured'}
              </span>
            </div>
            <p className="text-xs text-white/60">
              {integrations.github?.feature || 'Pull Request creation & automated branch pushing'}
            </p>
            <div className="text-[11px] font-mono text-white/40 pt-1 border-t border-white/5">
              Env: GITHUB_TOKEN
            </div>
          </div>

          {/* Telegram */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold text-white">
                <Send size={18} className="text-cyan-400" /> Telegram
              </div>
              <span
                className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${
                  integrations.telegram?.configured
                    ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
                    : 'bg-white/5 border-white/10 text-white/40'
                }`}
              >
                {integrations.telegram?.configured ? 'Configured' : 'Not configured'}
              </span>
            </div>
            <p className="text-xs text-white/60">
              {integrations.telegram?.feature || '2-way interactive approval via Telegram Bot'}
            </p>
            <div className="text-[11px] font-mono text-white/40 pt-1 border-t border-white/5">
              Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
            </div>
          </div>

          {/* n8n */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold text-white">
                <ShieldCheck size={18} className="text-amber-400" /> n8n Hooks
              </div>
              <span
                className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${
                  integrations.n8n?.configured
                    ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
                    : 'bg-white/5 border-white/10 text-white/40'
                }`}
              >
                {integrations.n8n?.configured ? 'Configured' : 'Not configured'}
              </span>
            </div>
            <p className="text-xs text-white/60">
              {integrations.n8n?.feature || 'HMAC signed webhook hooks for workflow automation'}
            </p>
            <div className="text-[11px] font-mono text-white/40 pt-1 border-t border-white/5">
              Env: N8N_WEBHOOK_URL, N8N_WEBHOOK_SECRET
            </div>
          </div>
        </div>
      </div>

      {/* Safety Policy Engine Table */}
      <div className="glass-card rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="text-cyan-400" size={20} />
            <h2 className="text-lg font-bold text-white">Active Policy Engine Rules</h2>
          </div>
          <span className="text-xs text-white/40">Configured in policies.yaml</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm border-collapse">
            <thead>
              <tr className="border-b border-white/10 text-white/40 text-xs uppercase tracking-wider">
                <th className="py-2.5 px-3">Remediation Action</th>
                <th className="py-2.5 px-3">Risk Tier</th>
                <th className="py-2.5 px-3">Auto-Apply</th>
                <th className="py-2.5 px-3">Min Confidence</th>
                <th className="py-2.5 px-3">Hourly Rate Cap</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-white/80">
              {Object.entries(policies).map(([action, rule]) => (
                <tr key={action} className="hover:bg-white/5 transition">
                  <td className="py-3 px-3 font-mono text-cyan-200">{action}</td>
                  <td className="py-3 px-3">
                    <span
                      className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase border ${
                        TIER_COLORS[rule.risk_tier] || 'bg-white/10 border-white/20 text-white'
                      }`}
                    >
                      {rule.risk_tier}
                    </span>
                  </td>
                  <td className="py-3 px-3">
                    {rule.auto_apply_allowed ? (
                      <span className="inline-flex items-center gap-1 text-emerald-300 text-xs">
                        <CheckCircle2 size={13} /> Allowed (Low Risk Only)
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-white/40 text-xs">
                        <XCircle size={13} /> Needs Approval
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-3 font-mono">{Math.round((rule.min_confidence || 0) * 100)}%</td>
                  <td className="py-3 px-3 text-white/60">{rule.max_auto_per_hour} / hr max</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Audit Log Timeline */}
      <div className="glass-card rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Hash size={18} className="text-cyan-400" /> Cryptographic Audit Trail
            </h2>
            <p className="text-xs text-white/50">
              Every ingestion, diagnosis, approval, fix, and rollback is hash-chained.
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            {/* Filter by incident */}
            <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-xl px-2.5 py-1.5 text-xs text-white/70">
              <Filter size={13} className="text-cyan-400" />
              <span>Incident:</span>
              <select
                value={selectedIncident}
                onChange={(e) => setSelectedIncident(e.target.value)}
                className="bg-transparent text-white focus:outline-none cursor-pointer"
              >
                <option value="all" className="bg-slate-900 text-white">All Incidents</option>
                {incidentIds.map((id) => (
                  <option key={id} value={id} className="bg-slate-900 text-white font-mono">{id}</option>
                ))}
              </select>
            </div>

            {/* Search */}
            <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-xl px-2.5 py-1.5 text-xs text-white/70">
              <Search size={13} />
              <input
                type="text"
                placeholder="Search audit events..."
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                className="bg-transparent text-white placeholder-white/30 focus:outline-none w-36"
              />
            </div>
          </div>
        </div>

        {filteredEntries.length === 0 ? (
          <div className="text-center py-12 text-white/40 text-sm">
            No audit records match the selected filter.
          </div>
        ) : (
          <div className="space-y-3">
            {filteredEntries.map((entry, idx) => {
              const isExpanded = expandedId === entry.id
              const dateStr = new Date(entry.timestamp * 1000).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
              })
              const badgeClass = EVENT_COLORS[entry.event] || 'bg-white/10 border-white/20 text-white'

              return (
                <motion.div
                  key={entry.id || idx}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="rounded-xl border border-white/10 bg-white/5 p-4 hover:border-white/20 transition"
                >
                  <div className="flex items-center justify-between flex-wrap gap-2 text-xs">
                    <div className="flex items-center gap-2">
                      <span className={`px-2.5 py-0.5 rounded-full font-semibold uppercase tracking-wider border ${badgeClass}`}>
                        {entry.event}
                      </span>
                      {entry.incident_id && (
                        <span className="font-mono bg-black/40 px-2 py-0.5 rounded text-white/80">
                          {entry.incident_id}
                        </span>
                      )}
                      <span className="text-white/50 flex items-center gap-1">
                        <User size={12} /> {entry.actor}
                      </span>
                    </div>

                    <div className="flex items-center gap-3 text-white/40">
                      <span className="font-mono text-[11px]" title={`Block Hash: ${entry.hash}`}>
                        #{entry.hash ? entry.hash.slice(0, 8) : '00000000'}
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock size={12} /> {dateStr}
                      </span>
                      <button
                        onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                        className="text-white/60 hover:text-white transition p-1"
                        title="Toggle raw payload"
                      >
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </button>
                    </div>
                  </div>

                  {/* Summary row */}
                  {entry.details && Object.keys(entry.details).length > 0 && (
                    <div className="mt-2 text-xs text-white/70 flex flex-wrap gap-x-4 gap-y-1">
                      {entry.details.action && (
                        <span>
                          <span className="text-white/40">Action:</span> <span className="font-mono text-cyan-200">{entry.details.action}</span>
                        </span>
                      )}
                      {entry.details.decision && (
                        <span>
                          <span className="text-white/40">Decision:</span>{' '}
                          <span className={entry.details.decision === 'AUTO_APPLY' ? 'text-emerald-300' : 'text-amber-300 font-semibold'}>
                            {entry.details.decision}
                          </span>
                        </span>
                      )}
                      {entry.details.title && (
                        <span>
                          <span className="text-white/40">Title:</span> {entry.details.title}
                        </span>
                      )}
                      {entry.details.service && (
                        <span>
                          <span className="text-white/40">Service:</span> {entry.details.service}
                        </span>
                      )}
                      {entry.details.reason && (
                        <span>
                          <span className="text-white/40">Reason:</span> {entry.details.reason}
                        </span>
                      )}
                      {entry.details.pr_url && (
                        <a
                          href={entry.details.pr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-indigo-300 hover:text-indigo-200 underline font-semibold ml-1"
                        >
                          <GitPullRequest size={12} /> View PR <ExternalLink size={10} />
                        </a>
                      )}
                    </div>
                  )}

                  {/* Expanded JSON details */}
                  <AnimatePresence>
                    {isExpanded && (
                      <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        className="mt-3 pt-3 border-t border-white/10 text-xs font-mono space-y-2 overflow-hidden"
                      >
                        <div className="text-[11px] text-white/50 space-y-0.5">
                          <p>prev_hash: {entry.prev_hash}</p>
                          <p>hash: {entry.hash}</p>
                        </div>
                        <pre className="p-3 rounded-lg bg-black/50 text-cyan-200 overflow-x-auto text-[11px]">
                          {JSON.stringify(entry.details || {}, null, 2)}
                        </pre>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
