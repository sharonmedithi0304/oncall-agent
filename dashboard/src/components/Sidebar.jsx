import { useState } from 'react'
import { LayoutDashboard, BrainCircuit, BarChart3, Siren, Wifi, WifiOff, Sparkles, ShieldCheck, RotateCcw } from 'lucide-react'
import { api } from '../api'

const items = [
  { id: 'overview', label: 'Incident Command', icon: LayoutDashboard },
  { id: 'audit_policy', label: 'Audit & Policy', icon: ShieldCheck },
  { id: 'memory', label: 'Agent Memory', icon: BrainCircuit },
  { id: 'benchmark', label: 'Benchmark', icon: BarChart3 },
]

export default function Sidebar({ tab, setTab, online, mode, onResetDemoData }) {
  const [resetting, setResetting] = useState(false)

  const handleResetDemoData = async () => {
    if (window.confirm('Reset all demo data (audit log, memory, incidents, ingested logs)?')) {
      setResetting(true)
      try {
        await api.resetDemoData()
        if (onResetDemoData) {
          onResetDemoData()
        } else {
          window.location.reload()
        }
      } catch (err) {
        alert('Failed to reset demo data: ' + err.message)
      } finally {
        setResetting(false)
      }
    }
  }
  return (
    <aside className="glass-card w-64 shrink-0 rounded-3xl p-5 flex flex-col h-full">
      <div className="flex items-center gap-3 px-2 mb-8">
        <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-cyan-400 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/30">
          <Siren size={20} />
        </div>
        <div>
          <p className="font-bold leading-tight">OnCall AI</p>
          <p className="text-xs text-white/50">Autonomous SRE</p>
        </div>
      </div>

      <p className="text-[11px] uppercase tracking-widest text-white/40 px-2 mb-2">Workspace</p>
      <nav className="space-y-1">
        {items.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition ${tab === id
                ? 'bg-gradient-to-r from-cyan-500/25 to-blue-500/10 text-white border border-cyan-400/30'
                : 'text-white/60 hover:text-white hover:bg-white/5 border border-transparent'
              }`}
          >
            <Icon size={18} />
            {label}
          </button>
        ))}
      </nav>

      <div className="mt-auto space-y-3">
        <div className="glass-soft rounded-xl p-3 text-xs">
          <div className="flex items-center gap-2">
            {online ? <Wifi size={14} className="text-emerald-400" /> : <WifiOff size={14} className="text-rose-400" />}
            <span className={online ? 'text-emerald-300' : 'text-rose-300'}>
              {online ? 'Service connected' : 'Service offline'}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-2 text-white/60">
            <Sparkles size={14} className="text-cyan-300" />
            {mode === 'gemini' ? 'Reasoning: Gemini LLM' : 'Reasoning: offline fallback'}
          </div>
        </div>

        <button
          onClick={handleResetDemoData}
          disabled={resetting}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold text-rose-300/90 hover:text-rose-100 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 transition disabled:opacity-50"
        >
          <RotateCcw size={13} className={resetting ? 'animate-spin' : ''} />
          {resetting ? 'Resetting…' : 'Reset demo data'}
        </button>
      </div>
    </aside>
  )
}
