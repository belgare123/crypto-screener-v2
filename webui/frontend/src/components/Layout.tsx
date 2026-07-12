import { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { Activity, Layers, BarChart3, Menu, X } from 'lucide-react'

const nav = [
  { to: '/', label: 'Dashboard', icon: BarChart3 },
  { to: '/strategies', label: 'Strategies', icon: Layers },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected'>('disconnected')

  useEffect(() => {
    let ws: WebSocket
    let timer: ReturnType<typeof setTimeout>

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      ws = new WebSocket(`${proto}//${host}/api/ws/metrics`)

      ws.onopen = () => setWsStatus('connected')
      ws.onclose = () => {
        setWsStatus('disconnected')
        timer = setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      ws?.close()
      clearTimeout(timer)
    }
  }, [])

  return (
    <div className="flex h-screen bg-dark-900">
      {/* Sidebar */}
      <aside className={`${menuOpen ? 'block' : 'hidden'} md:flex md:flex-col w-64 bg-dark-800 border-r border-gray-800 p-4 fixed md:static inset-y-0 left-0 z-50`}>
        <div className="flex items-center gap-3 mb-8 mt-2">
          <Activity className="w-6 h-6 text-blue-400" />
          <span className="text-lg font-semibold">Crypto Screener</span>
        </div>

        <nav className="space-y-1 flex-1">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                  isActive
                    ? 'bg-blue-600/20 text-blue-400'
                    : 'text-gray-400 hover:bg-dark-700 hover:text-gray-200'
                }`
              }
            >
              <Icon className="w-5 h-5" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="flex items-center gap-2 text-xs text-gray-500 mt-4 pt-4 border-t border-gray-800">
          <div className={`w-2 h-2 rounded-full ${wsStatus === 'connected' ? 'bg-green-500' : 'bg-red-500'}`} />
          {wsStatus === 'connected' ? 'Live' : 'Reconnecting…'}
        </div>
      </aside>

      {/* Overlay for mobile */}
      {menuOpen && (
        <div className="fixed inset-0 bg-black/50 z-40 md:hidden" onClick={() => setMenuOpen(false)} />
      )}

      {/* Main */}
      <main className="flex-1 overflow-auto">
        <header className="flex items-center justify-between p-4 border-b border-gray-800 md:hidden">
          <button onClick={() => setMenuOpen(!menuOpen)} className="text-gray-400">
            {menuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
          <span className="text-sm font-medium">Crypto Screener</span>
          <div className={`w-2 h-2 rounded-full ${wsStatus === 'connected' ? 'bg-green-500' : 'bg-red-500'}`} />
        </header>
        <div className="p-4 md:p-6 max-w-7xl mx-auto">
          {children}
        </div>
      </main>
    </div>
  )
}
