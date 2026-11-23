import { Check, Loader2 } from 'lucide-react'

export default function Pipeline({ steps }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {steps.map((s, i) => (
        <div key={s.label} className="flex items-center gap-2">
          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium border ${
              s.state === 'done'
                ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300'
                : s.state === 'active'
                ? 'bg-cyan-400/10 border-cyan-400/40 text-cyan-200'
                : s.state === 'bad'
                ? 'bg-rose-400/10 border-rose-400/30 text-rose-300'
                : 'bg-white/5 border-white/10 text-white/40'
            }`}
          >
            {s.state === 'done' && <Check size={13} />}
            {s.state === 'active' && <Loader2 size={13} className="animate-spin" />}
            {s.label}
          </div>
          {i < steps.length - 1 && <div className="h-px w-5 bg-white/15" />}
        </div>
      ))}
    </div>
  )
}
