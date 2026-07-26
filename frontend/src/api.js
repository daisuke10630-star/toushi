const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function getJson(path, errorLabel) {
  const res = await fetch(`${BASE_URL}${path}`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `${errorLabel}（${res.status}）`)
  }
  return res.json()
}

// 一覧はバックテストを省いて高速に取得し、詳細を開いたときに計算する
export function fetchWatchlistFull(timeframe) {
  return getJson(
    `/api/watchlist/full?timeframe=${timeframe}&with_backtest=false`,
    'ダッシュボードの取得に失敗しました'
  )
}

export function fetchStock(code, timeframe) {
  return getJson(`/api/stock/${code}?timeframe=${timeframe}`, `${code}の取得に失敗しました`)
}

export function fetchPositions(timeframe) {
  return getJson(`/api/positions?timeframe=${timeframe}`, '保有ポジションの取得に失敗しました')
}

export function fetchCompare(timeframe, limit = 20) {
  return getJson(`/api/compare?timeframe=${timeframe}&limit=${limit}`, '比較リストの取得に失敗しました')
}

export async function savePosition(code, body, token) {
  const res = await fetch(`${BASE_URL}/api/positions/${code}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Write-Token': token } : {}),
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const b = await res.json().catch(() => ({}))
    throw new Error(b.detail || `保存に失敗しました（${res.status}）`)
  }
  return res.json()
}

export function fetchTimeframes() {
  return getJson('/api/timeframes', '時間軸設定の取得に失敗しました')
}

export function fetchScreen(timeframe, minStars) {
  return getJson(
    `/api/screen?timeframe=${timeframe}&min_stars=${minStars}`,
    'スクリーニングに失敗しました'
  )
}
