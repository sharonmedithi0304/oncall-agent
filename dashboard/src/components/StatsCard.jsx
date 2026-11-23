import { motion } from 'framer-motion'

export default function StatsCard({ title, value, sub, icon: Icon, gradient, tone = 'text-emerald-400' }) {
  return (
    <motion.div
      whileHover={{ y: -5 }}
      transition={{ type: 'spring', stiffness: 300, damping: 20 }}
      className="glass-card p-6 rounded-2xl"
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-white/60">{title}</p>
          <h2 className="text-3xl font-bold text-white mt-1">{value}</h2>
          <p className={`flex items-center gap-1 text-sm mt-2 ${tone}`}>{sub}</p>
        </div>
        <div className={`h-12 w-12 rounded-xl bg-gradient-to-br ${gradient} flex items-center justify-center shadow-lg`}>
          <Icon size={22} className="text-white" />
        </div>
      </div>
    </motion.div>
  )
}
