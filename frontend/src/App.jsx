import React, { useEffect, useState, useCallback, useRef } from 'react'
import {
  fetchWatchlistFull,
  fetchStock,
  fetchTimeframes,
  fetchPositions,
  fetchCompare,
} from './api'
import { WatchlistRow, SignalDetail, Positions } from './components/Panels'
import { CompareTable } from './components/Compare'
import { PositionForm } from './components/PositionForm'

// バックエンドのキャッシュTTLに合わせた自動更新間隔
const REFRESH_INTERVAL_MS = { '1d': 300_000 }

export default function App() {
  const [timeframe, setTimeframe] = useState('1d')
  const [timeframeInfo, setTimeframeInfo] = useState([])
  const [stocks, setStocks] = useState([])
  const [selectedCode, setSelectedCode] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastFetched, setLastFetched] = useState(null)
  const [positions, setPositions] = useState(null)
  const [compare, setCompare] = useState(null)
  const [view, setView] = useState('detail') // 'detail' | 'compare'
  const timerRef = useRef(null)

  useEffect(() => {
    fetchTimeframes().then(setTimeframeInfo).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    try {
      const data = await fetchWatchlistFull(timeframe)
      setStocks(data)
      setLastFetched(new Date())
      setError(null)
      setSelectedCode((prev) => prev || data[0]?.code || null)
      // 補助パネルは失敗してもダッシュボード本体は表示したいので握りつぶす
      fetchPositions(timeframe).then(setPositions).catch(() => setPositions(null))
      fetchCompare(timeframe, 20).then(setCompare).catch(() => setCompare(null))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [timeframe])

  useEffect(() => {
    setLoading(true)
    load()
    clearInterval(timerRef.current)
    timerRef.current = setInterval(load, REFRESH_INTERVAL_MS[timeframe] ?? 60_000)
    return () => clearInterval(timerRef.current)
  }, [load, timeframe])

  // 詳細はAI信頼度（バックテスト）を含むため、選択した銘柄だけ個別に取得する
  useEffect(() => {
    if (!selectedCode) return
    let cancelled = false
    setDetailLoading(true)
    setDetail(null)
    fetchStock(selectedCode, timeframe)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch((e) => {
        if (!cancelled) setDetail({ code: selectedCode, error: e.message })
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedCode, timeframe])

  // 詳細の取得が終わるまでは一覧側のデータで先に描画する
  const fallback = stocks.find((s) => s.code === selectedCode) || null
  const selectedStock = detail && detail.code === selectedCode ? detail : fallback
  const activeInfo = timeframeInfo.find((t) => t.key === timeframe)

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1>日本株テクニカル分析ダッシュボード</h1>
          <p className="app__subtitle">
            グランビルの法則・パーフェクトオーダー・ボリンジャーバンド・RSIに基づく自動判定
          </p>
        </div>
        <div className="app__status">
          {/* 時間軸が1つだけのときは切り替えUIを出さない */}
          {timeframeInfo.length > 1 && (
            <div className="tf-switch" role="group" aria-label="時間軸の切り替え">
              {timeframeInfo.map((t) => (
                <button
                  key={t.key}
                  className={`tf-btn ${t.key === timeframe ? 'tf-btn--active' : ''}`}
                  onClick={() => setTimeframe(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
          {lastFetched && (
            <span className="mono">最終更新 {lastFetched.toLocaleTimeString('ja-JP')}</span>
          )}
          <button className="refresh-btn" onClick={load} disabled={loading}>
            {loading ? '更新中…' : '今すぐ更新'}
          </button>
        </div>
      </header>

      {activeInfo && (
        <p className="tf-info">
          <strong>{activeInfo.label}の設定：</strong>
          移動平均 {Object.values(activeInfo.ma_labels).join(' / ')}
          ／RSI {activeInfo.rsi.short}本・{activeInfo.rsi.long}本（過熱{activeInfo.rsi.overheat}
          ・売られすぎ{activeInfo.rsi.oversold}）
          ／BB {activeInfo.bb_period}本
          {activeInfo.notes?.length > 0 && (
            <span className="tf-info__notes">　※{activeInfo.notes.join('／')}</span>
          )}
        </p>
      )}

      {error && <div className="app__error">{error}</div>}

      <Positions data={positions} />
      <PositionForm onSaved={setPositions} />

      <div className="app__body">
        <aside className="watchlist">
          {stocks.map((s) => (
            <WatchlistRow
              key={s.code}
              stock={s}
              active={s.code === selectedCode}
              onSelect={setSelectedCode}
            />
          ))}
          {loading && stocks.length === 0 && <p className="watchlist__loading">読み込み中…</p>}
        </aside>

        <main className="main-panel">
          <div className="view-switch" role="group" aria-label="表示の切り替え">
            <button
              className={`tf-btn ${view === 'detail' ? 'tf-btn--active' : ''}`}
              onClick={() => setView('detail')}
            >
              銘柄の詳細
            </button>
            <button
              className={`tf-btn ${view === 'compare' ? 'tf-btn--active' : ''}`}
              onClick={() => setView('compare')}
            >
              価格ライン比較
            </button>
          </div>

          {view === 'detail' ? (
            <>
              <SignalDetail stock={selectedStock} />
              {detailLoading && <p className="detail__loading">AI信頼度を計算中…</p>}
            </>
          ) : (
            <CompareTable
              data={compare}
              onSelect={(code) => {
                setSelectedCode(code)
                setView('detail')
              }}
            />
          )}
        </main>
      </div>
    </div>
  )
}
