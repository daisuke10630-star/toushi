import React, { useState } from 'react'

// 株価水準の違う銘柄を並べて比べるため、すべて現在値からの乖離率(%)で表示する。
// 価格そのものは行をクリックすると開く。
export function CompareTable({ data, onSelect }) {
  const [expanded, setExpanded] = useState(null)

  if (!data?.results?.length) return null

  const gapColor = (v) => (v > 0 ? 'var(--bull)' : v < 0 ? 'var(--bear)' : 'var(--text-muted)')

  return (
    <section className="compare">
      <div className="compare__head">
        <h3>価格ライン比較（{data.count}銘柄）</h3>
        <span className="compare__hint">エントリーまでの距離が近い順。行をクリックで実額表示</span>
      </div>

      <div className="compare__scroll">
        <table className="compare__table">
          <thead>
            <tr>
              <th>銘柄</th>
              <th className="num">現在値</th>
              <th className="num">エントリー</th>
              <th className="num">損切り</th>
              <th className="num">利確①</th>
              <th className="num">利確②</th>
              <th className="num">RR</th>
              <th>★</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((r) => (
              <React.Fragment key={r.code}>
                <tr
                  className={`compare__row ${r.reachable ? 'compare__row--reachable' : ''}`}
                  onClick={() => setExpanded(expanded === r.code ? null : r.code)}
                >
                  <td>
                    <button
                      className="compare__name"
                      onClick={(e) => {
                        e.stopPropagation()
                        onSelect?.(r.code)
                      }}
                    >
                      {r.name}
                    </button>
                    <span className="mono compare__code">{r.code}</span>
                  </td>
                  <td className="num mono">{r.price.toLocaleString()}</td>
                  <td className="num mono" style={{ color: gapColor(r.entry_gap_pct) }}>
                    {r.entry_gap_pct > 0 ? '+' : ''}
                    {r.entry_gap_pct}%
                  </td>
                  <td className="num mono" style={{ color: gapColor(r.stop_gap_pct) }}>
                    {r.stop_gap_pct}%
                  </td>
                  <td className="num mono" style={{ color: gapColor(r.tp1_gap_pct) }}>
                    +{r.tp1_gap_pct}%
                  </td>
                  <td className="num mono" style={{ color: gapColor(r.tp2_gap_pct) }}>
                    {r.tp2_gap_pct != null ? `+${r.tp2_gap_pct}%` : '—'}
                  </td>
                  <td className="num mono">1:{r.risk_reward}</td>
                  <td className="mono compare__stars">{'★'.repeat(r.stars || 0)}</td>
                </tr>
                {expanded === r.code && (
                  <tr className="compare__detail">
                    <td colSpan={8}>
                      <span className="mono">
                        エントリー {r.entry_price?.toLocaleString()}円 ／ 損切り{' '}
                        {r.stop_loss?.toLocaleString()}円 ／ 利確①{' '}
                        {r.take_profit_1?.toLocaleString()}円 ／ 利確②{' '}
                        {r.take_profit_2?.toLocaleString()}円
                      </span>
                      {r.sector && r.sector !== 'unknown' && (
                        <span className="compare__sector">{r.sector}</span>
                      )}
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <p className="compare__note">※{data.note}</p>
    </section>
  )
}
