import { useState, useEffect, useCallback } from 'react'
import { Activity, Shield, Send, AlertTriangle, Zap, TrendingUp } from 'lucide-react'

interface Overview {
  total_signals: number
  total_blocked: number
  total_dispatched: number
  total_errors: number
  signal_rate_5m: number
  prometheus_up: boolean | null
  screener_up: boolean | null
}

interface MetricRow {
  metric: Record<string, string>
  value: [number, string]
}

interface MetricData {
  signals_total?: MetricRow[]
  signals_blocked?: MetricRow[]
  signal_errors?: MetricRow[]
  dispatched_signals?: MetricRow[]
  signals_rate?: MetricRow[]
}

type WsMessage = { type: 'metrics'; data: MetricData } | { type: 'pong' }

export default function Dashboard() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [wsData, setWsData] = useState<MetricData | null>(null)
  const [loading, setLoading] = useState(true)

  // Fetch overview on mount
  useEffect(() => {
    fetch('/api/overview')
      .then(r => r.json())
      .then(d => setOverview(d))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  // WebSocket for real-time updates
  useEffect(() => {
    let ws: WebSocket
    let timer: ReturnType<typeof setTimeout>

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${window.location.host}/api/ws/metrics`)
      ws.onmessage = (e) => {
        try {
          const msg: WsMessage = JSON.parse(e.data)
          if (msg.type === 'metrics') {
            setWsData(msg.data)
          }
        } catch {}
      }
      ws.onclose = () => { timer = setTimeout(connect, 3000) }
    }

    connect()
    return () => { ws?.close(); clearTimeout(timer) }
  }, [])

  // Compute totals from wsData
  const totalSignals = wsData?.signals_total?.reduce((s, r) => s + parseFloat(r.value[1] || '0'), 0) ?? overview?.total_signals ?? 0
  const totalBlocked = wsData?.signals_blocked?.reduce((s, r) => s + parseFloat(r.value[1] || '0'), 0) ?? overview?.total_blocked ?? 0
  const totalDispatched = wsData?.dispatched_signals?.[0]?.value[1] ?? overview?.total_dispatched ?? 0
  const totalErrors = wsData?.signal_errors?.reduce((s, r) => s + parseFloat(r.value[1] || '0'), 0) ?? overview?.total_errors ?? 0
  const signalRate = wsData?.signals_rate?.reduce((s, r) => s + parseFloat(r.value[1] || '0'), 0) ?? overview?.signal_rate_5m ?? 0

  const cards = [
    { label: 'Total Signals', value: Math.round(totalSignals), icon: TrendingUp, color: 'text-blue-400', bg: 'bg-blue-500/10' },
    { label: 'Signals / 5m', value: signalRate.toFixed(1), icon: Zap, color: 'text-green-400', bg: 'bg-green-500/10' },
    { label: 'Blocked', value: Math.round(totalBlocked), icon: Shield, color: 'text-yellow-400', bg: 'bg-yellow-500/10' },
    { label: 'Dispatched', value: Math.round(totalDispatched), icon: Send, color: 'text-purple-400', bg: 'bg-purple-500/10' },
    { label: 'Errors', value: Math.round(totalErrors), icon: AlertTriangle, color: 'text-red-400', bg: 'bg-red-500/10' },
  ]

  // Last 10 signals
  const recentSignals = wsData?.signals_total?.slice(-10)?.reverse() ?? []

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Activity className="w-6 h-6 text-blue-400" />
        Dashboard
      </h1>

      {/* Metric Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
        {cards.map(({ label, value, icon: Icon, color, bg }) => (
          <div key={label} className={`${bg} rounded-xl p-4 border border-gray-800`}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-500 uppercase tracking-wider">{label}</span>
              <Icon className={`w-4 h-4 ${color}`} />
            </div>
            <div className={`text-2xl font-bold metric-glow ${color}`}>
              {loading ? '…' : value}
            </div>
          </div>
        ))}
      </div>

      {/* Status */}
      <div className="flex flex-wrap gap-4 mb-8">
        <StatusBadge label="Prometheus" ok={overview?.prometheus_up ?? null} />
        <StatusBadge label="Screener" ok={overview?.screener_up ?? null} />
        <StatusBadge label="WebSocket" ok={wsData !== null} />
      </div>

      {/* Signals by symbol */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top Signals by Symbol */}
        <div className="bg-dark-800 rounded-xl border border-gray-800 p-4">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">Signals by Symbol</h2>
          <div className="space-y-2">
            {wsData?.signals_total
              ?.reduce((acc: Record<string, number>, r) => {
                const sym = r.metric.symbol || 'unknown'
                acc[sym] = (acc[sym] || 0) + parseFloat(r.value[1] || '0')
                return acc
              }, {})
              ?.let?.(obj => {
                if (!obj) return <div className="text-gray-500 text-sm">No data yet</div>
                const entries = Object.entries(obj).sort((a, b) => b[1] - a[1]).slice(0, 10)
                if (entries.length === 0) return <div className="text-gray-500 text-sm">No data yet</div>
                const maxVal = entries[0][1]
                return entries.map(([sym, count]) => (
                  <div key={sym} className="flex items-center gap-3">
                    <span className="text-sm font-mono text-gray-300 w-28 truncate">{sym}</span>
                    <div className="flex-1 bg-dark-600 rounded-full h-2">
                      <div
                        className="bg-blue-500 h-2 rounded-full transition-all"
                        style={{ width: `${(count / maxVal) * 100}%` }}
                      />
                    </div>
                    <span className="text-sm text-gray-400 w-12 text-right">{Math.round(count)}</span>
                  </div>
                ))
              })
            }
          </div>
        </div>

        {/* Blocked by Reason */}
        <div className="bg-dark-800 rounded-xl border border-gray-800 p-4">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">Blocked by Reason</h2>
          <div className="space-y-2">
            {wsData?.signals_blocked
              ?.map((r, i) => ({ reason: r.metric.reason || 'unknown', count: parseFloat(r.value[1] || '0') }))
              .sort((a, b) => b.count - a.count)
              .map(({ reason, count }, _, arr) => {
                const maxVal = arr[0]?.count || 1
                return (
                  <div key={reason} className="flex items-center gap-3">
                    <span className="text-sm text-gray-300 w-28 capitalize">{reason}</span>
                    <div className="flex-1 bg-dark-600 rounded-full h-2">
                      <div
                        className="bg-yellow-500 h-2 rounded-full transition-all"
                        style={{ width: `${(count / maxVal) * 100}%` }}
                      />
                    </div>
                    <span className="text-sm text-gray-400 w-12 text-right">{Math.round(count)}</span>
                  </div>
                )
              })
            }
            {(!wsData?.signals_blocked || wsData.signals_blocked.length === 0) && (
              <div className="text-gray-500 text-sm">No blocked signals</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ label, ok }: { label: string; ok: boolean | null }) {
  return (
    <div className="flex items-center gap-2 bg-dark-800 rounded-lg px-3 py-1.5 border border-gray-800">
      <div className={`w-2 h-2 rounded-full ${ok === null ? 'bg-gray-500' : ok ? 'bg-green-500' : 'bg-red-500'}`} />
      <span className="text-sm text-gray-400">{label}</span>
    </div>
  )
}
