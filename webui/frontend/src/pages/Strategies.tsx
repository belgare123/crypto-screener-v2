import { useState, useEffect } from 'react'
import { Layers, ToggleLeft, ToggleRight, RefreshCw, Search } from 'lucide-react'

interface Strategy {
  name: string
  description: string
  category: string
  enabled: boolean
  default_score: number
  cooldown: number
  timeframes: string[] | null
}

type FilterMode = 'all' | 'enabled' | 'disabled'

export default function Strategies() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterMode>('all')
  const [search, setSearch] = useState('')
  const [categories, setCategories] = useState<Record<string, number>>({})
  const [categoryFilter, setCategoryFilter] = useState<string>('all')

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [sRes, cRes] = await Promise.all([
        fetch('/api/strategies'),
        fetch('/api/strategies/categories'),
      ])
      if (!sRes.ok) throw new Error(`HTTP ${sRes.status}`)
      setStrategies(await sRes.json())
      if (cRes.ok) setCategories(await cRes.json())
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const toggleSignal = async (name: string, enabled: boolean) => {
    try {
      const res = await fetch(`/api/strategies/${encodeURIComponent(name)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (res.ok) {
        setStrategies(prev =>
          prev.map(s => s.name === name ? { ...s, enabled } : s)
        )
      }
    } catch {}
  }

  const filtered = strategies
    .filter(s => filter === 'all' || (filter === 'enabled' ? s.enabled : !s.enabled))
    .filter(s => categoryFilter === 'all' || s.category === categoryFilter)
    .filter(s => !search || s.name.toLowerCase().includes(search.toLowerCase()) || s.description.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => a.name.localeCompare(b.name))

  const getCategoryColor = (cat: string) => {
    const colors: Record<string, string> = {
      volume: 'bg-blue-500/20 text-blue-300',
      whale: 'bg-purple-500/20 text-purple-300',
      breakout: 'bg-green-500/20 text-green-300',
      smart_money: 'bg-cyan-500/20 text-cyan-300',
      liquidation: 'bg-orange-500/20 text-orange-300',
      orderbook: 'bg-yellow-500/20 text-yellow-300',
      candle: 'bg-pink-500/20 text-pink-300',
      pattern: 'bg-indigo-500/20 text-indigo-300',
      trade_flow: 'bg-red-500/20 text-red-300',
      correlation: 'bg-teal-500/20 text-teal-300',
      strength: 'bg-emerald-500/20 text-emerald-300',
      rotation: 'bg-violet-500/20 text-violet-300',
      market: 'bg-gray-500/20 text-gray-300',
      general: 'bg-gray-500/20 text-gray-300',
    }
    return colors[cat] || 'bg-gray-500/20 text-gray-300'
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Layers className="w-6 h-6 text-purple-400" />
          Strategies
        </h1>
        <button
          onClick={load}
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-6 items-center">
        {/* Status filter */}
        <div className="flex bg-dark-800 rounded-lg border border-gray-800 overflow-hidden">
          {(['all', 'enabled', 'disabled'] as FilterMode[]).map(mode => (
            <button
              key={mode}
              onClick={() => setFilter(mode)}
              className={`px-3 py-1.5 text-sm transition-colors ${
                filter === mode ? 'bg-blue-600/30 text-blue-300' : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              {mode.charAt(0).toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </div>

        {/* Category filter */}
        <select
          value={categoryFilter}
          onChange={e => setCategoryFilter(e.target.value)}
          className="bg-dark-800 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-gray-300"
        >
          <option value="all">All categories</option>
          {Object.entries(categories)
            .sort((a, b) => b[1] - a[1])
            .map(([cat, count]) => (
              <option key={cat} value={cat}>{cat} ({count})</option>
            ))}
        </select>

        {/* Search */}
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            type="text"
            placeholder="Search signals…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-dark-800 border border-gray-800 rounded-lg pl-9 pr-3 py-1.5 text-sm text-gray-200 placeholder:text-gray-500 focus:outline-none focus:border-gray-600"
          />
        </div>

        <div className="text-sm text-gray-500">{filtered.length} / {strategies.length}</div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-300 mb-4">
          Failed to load strategies: {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="text-center py-12 text-gray-500">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-2" />
          Loading strategies…
        </div>
      )}

      {/* Strategy List */}
      {!loading && (
        <div className="space-y-2">
          {filtered.map(s => (
            <div
              key={s.name}
              className={`bg-dark-800 rounded-xl border transition-colors ${
                s.enabled ? 'border-gray-800' : 'border-gray-800/50 opacity-60'
              } p-4`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <h3 className="font-medium text-gray-200">{s.name}</h3>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${getCategoryColor(s.category)}`}>
                      {s.category}
                    </span>
                    {s.timeframes && (
                      <span className="text-xs text-gray-500">
                        {s.timeframes.join(', ')}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-500 truncate">{s.description}</p>
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                    <span>Score: {s.default_score}</span>
                    <span>Cooldown: {s.cooldown}s</span>
                  </div>
                </div>
                <button
                  onClick={() => toggleSignal(s.name, !s.enabled)}
                  className={`flex-shrink-0 p-2 rounded-lg transition-colors ${
                    s.enabled
                      ? 'text-green-400 hover:bg-green-500/10'
                      : 'text-gray-500 hover:bg-gray-500/10'
                  }`}
                  title={s.enabled ? 'Disable' : 'Enable'}
                >
                  {s.enabled ? <ToggleRight className="w-6 h-6" /> : <ToggleLeft className="w-6 h-6" />}
                </button>
              </div>
            </div>
          ))}

          {filtered.length === 0 && !loading && (
            <div className="text-center py-12 text-gray-500">
              No strategies found matching your filters.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
