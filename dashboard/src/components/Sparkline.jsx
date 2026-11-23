export default function Sparkline({ values, color = '#22d3ee', height = 90 }) {
  const w = 300
  const max = Math.max(...values, 100)
  const pts = values.map((v, i) => {
    const x = values.length < 2 ? 0 : (i / (values.length - 1)) * w
    const y = height - 6 - (v / max) * (height - 14)
    return [x, y]
  })
  const line = pts.map((p) => p.join(',')).join(' ')
  const area = pts.length ? `0,${height} ${line} ${w},${height}` : ''
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <defs>
        <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {pts.length > 1 && <polygon points={area} fill="url(#spark)" />}
      {pts.length > 1 && <polyline points={line} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
    </svg>
  )
}
