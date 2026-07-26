import React from 'react'

// 日本の慣習に合わせ、上昇=赤、下落=青 で色分け
function changeColor(change) {
  if (change > 0) return 'var(--bull)'
  if (change < 0) return 'var(--bear)'
  return 'var(--text-muted)'
}

export function WatchlistRow({ stock, active, onSelect }) {
  if (stock.error) {
    return (
      <button className="watch-row watch-row--error" onClick={() => onSelect(stock.code)}>
        <span className="watch-row__name">{stock.name}</span>
        <span className="watch-row__error">取得エラー</span>
      </button>
    )
  }

  return (
    <button
      className={`watch-row ${active ? 'watch-row--active' : ''}`}
      onClick={() => onSelect(stock.code)}
    >
      <div className="watch-row__id">
        <span className="watch-row__name">{stock.name}</span>
        <span className="watch-row__code">{stock.code}</span>
      </div>
      <div className="watch-row__price">
        <span className="mono">{stock.price?.toLocaleString()}</span>
        <span className="mono watch-row__change" style={{ color: changeColor(stock.change) }}>
          {stock.change > 0 ? '+' : ''}
          {stock.change} ({stock.change_pct > 0 ? '+' : ''}
          {stock.change_pct}%)
        </span>
      </div>
      <StarBadge stars={stock.signal?.stars} judgement={stock.signal?.judgement} compact />
    </button>
  )
}

export function StarBadge({ stars = 0, judgement, compact = false }) {
  const judgementClass =
    judgement === '買い' ? 'badge--buy' : judgement?.includes('売り') ? 'badge--sell' : 'badge--watch'

  return (
    <div className={`star-badge ${compact ? 'star-badge--compact' : ''}`}>
      <span className={`badge ${judgementClass}`}>{judgement || '---'}</span>
      <span className="stars" aria-label={`条件の揃い具合 ${stars} / 5`}>
        {[1, 2, 3, 4, 5].map((i) => (
          <span key={i} className={i <= stars ? 'star star--on' : 'star'}>
            ★
          </span>
        ))}
      </span>
    </div>
  )
}

function PriceLadder({ signal, price }) {
  const rows = [
    {
      label: '利確目標②',
      value: signal.take_profit_2,
      kind: 'target',
      note: signal.take_profit_2_note,
    },
    {
      label: '利確目標①',
      value: signal.take_profit_1,
      kind: 'target',
      note: signal.take_profit_1_note,
    },
    { label: '現在値', value: price, kind: 'current' },
    { label: 'エントリー目安', value: signal.entry_price, kind: 'entry', note: signal.entry_note },
    { label: '損切りライン', value: signal.stop_loss, kind: 'stop', note: signal.stop_loss_note },
  ].filter((r) => r.value !== null && r.value !== undefined)

  return (
    <div className="ladder">
      {rows.map((r) => (
        <div key={r.label} className={`ladder__row ladder__row--${r.kind}`}>
          <span className="ladder__label">{r.label}</span>
          <span className="ladder__value mono">{r.value.toLocaleString()}円</span>
          {r.note && <span className="ladder__note">{r.note}</span>}
        </div>
      ))}
    </div>
  )
}

// リスクリワード比＝(利確目標①までの値幅) ÷ (損切りまでの値幅)
function RiskReward({ signal }) {
  const { entry_price: entry, stop_loss: stop, take_profit_1: target } = signal
  if (!entry || !stop || !target) return null

  const risk = entry - stop
  const reward = target - entry
  if (risk <= 0 || reward <= 0) return null

  const ratio = reward / risk
  // 勝率がこの水準を超えないと期待値がプラスにならない、という損益分岐点
  const breakEven = (risk / (risk + reward)) * 100
  const poor = ratio < 1

  return (
    <p className={`rr ${poor ? 'rr--poor' : ''}`}>
      リスクリワード比 <strong className="mono">1 : {ratio.toFixed(2)}</strong>
      <span className="rr__note">
        （損失{Math.round(risk).toLocaleString()}円に対し利益{Math.round(reward).toLocaleString()}円。
        勝率が{breakEven.toFixed(0)}%を超えないと期待値はプラスになりません）
      </span>
    </p>
  )
}

function Confidence({ confidence }) {
  if (!confidence) {
    return (
      <section className="panel panel--wide">
        <h3>AI信頼度</h3>
        <p className="conf__pending">銘柄を選ぶと計算します…</p>
      </section>
    )
  }

  const {
    available, label, samples, wins, not_filled, avg_return_pct,
    benchmark_expectancy, edge, edge_label, horizon, method, caveats,
  } = confidence

  const avgClass = avg_return_pct > 0 ? 'conf__avg conf__avg--plus' : 'conf__avg conf__avg--minus'

  return (
    <section className="panel panel--wide">
      <h3>AI信頼度</h3>
      <p className={`conf__value ${available ? '' : 'conf__value--na'}`}>{label}</p>

      {available && (
        <>
          <p className="conf__detail mono">
            約定{samples}回 / 成功{wins}回 / 未約定{not_filled}回
            {avg_return_pct !== null && (
              <>
                {' '}
                ・平均損益 <span className={avgClass}>{avg_return_pct > 0 ? '+' : ''}
                {avg_return_pct}%</span>
              </>
            )}
          </p>
          {avg_return_pct !== null && avg_return_pct < 0 && (
            <p className="conf__alert">
              勝率に関わらず、平均損益はマイナスです。このルールをそのまま繰り返すと
              損失が積み上がる計算になります。
            </p>
          )}

          {/* シグナルを使わない場合との比較。ここが本当の判断材料 */}
          {benchmark_expectancy !== null && (
            <div className={`conf__edge ${edge > 0 ? '' : 'conf__edge--bad'}`}>
              <p className="conf__edge-head">対照実験：{edge_label}</p>
              <p className="conf__edge-body mono">
                このシグナル {avg_return_pct > 0 ? '+' : ''}{avg_return_pct}%／回
                　vs　 シグナルなしで同期間保有 {benchmark_expectancy > 0 ? '+' : ''}
                {benchmark_expectancy}%／回
              </p>
              {edge <= 0 && (
                <p className="conf__edge-note">
                  上昇相場では何を買っても勝つため、勝率や平均損益の絶対値だけでは
                  シグナルの良し悪しは判断できません。この差がプラスでない限り、
                  シグナルを待つ意味はありません。
                </p>
              )}
            </div>
          )}
        </>
      )}

      <p className="conf__method">
        <strong>算出方法：</strong>
        {method}（検証期間 {horizon}）
      </p>
      <ul className="conf__caveats">
        {caveats?.map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>
    </section>
  )
}

export function Positions({ data }) {
  if (!data || !data.positions?.length) return null

  const { positions: rows, summary, disclaimer } = data
  const pnlColor = (v) => (v > 0 ? 'var(--bull)' : v < 0 ? 'var(--bear)' : 'var(--text-muted)')

  return (
    <section className="positions">
      <div className="positions__head">
        <h3>保有ポジション</h3>
        {summary?.unrealized_pct !== null && summary?.counted > 0 && (
          <span className="mono positions__total" style={{ color: pnlColor(summary.unrealized_yen) }}>
            含み損益 {summary.unrealized_yen > 0 ? '+' : ''}
            {summary.unrealized_yen?.toLocaleString()}円（
            {summary.unrealized_pct > 0 ? '+' : ''}
            {summary.unrealized_pct}%）
          </span>
        )}
      </div>

      <div className="positions__grid">
        {rows.map((p) => (
          <div key={p.code} className={`pos pos--${p.status || 'none'}`}>
            <div className="pos__head">
              <span className="pos__name">{p.name}</span>
              <span className="mono pos__code">{p.code}</span>
            </div>

            {p.error && <p className="pos__note pos__note--warn">{p.error}</p>}

            {!p.error && !p.has_cost && <p className="pos__note pos__note--warn">{p.note}</p>}

            {!p.error && p.has_cost && (
              <>
                <div className="pos__row">
                  <span>{p.lots ? '平均取得単価' : '取得単価'}</span>
                  <span className="mono">{p.avg_cost.toLocaleString()}円 × {p.shares}株</span>
                </div>
                {p.lots && (
                  <div className="pos__lot-list">
                    {p.lots.map((l, i) => (
                      <div key={i} className="pos__row pos__row--lot">
                        <span className="mono">
                          {l.avg_cost.toLocaleString()}円 × {l.shares}株
                        </span>
                        <span className="mono" style={{ color: pnlColor(l.unrealized_yen) }}>
                          {l.unrealized_pct > 0 ? '+' : ''}
                          {l.unrealized_pct}%
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="pos__row">
                  <span>現在値</span>
                  <span className="mono">{p.price.toLocaleString()}円</span>
                </div>
                <div className="pos__row pos__row--pnl">
                  <span>含み損益</span>
                  <span className="mono" style={{ color: pnlColor(p.unrealized_yen) }}>
                    {p.unrealized_yen > 0 ? '+' : ''}
                    {p.unrealized_yen.toLocaleString()}円（
                    {p.unrealized_pct > 0 ? '+' : ''}
                    {p.unrealized_pct}%）
                  </span>
                </div>
                <div className="pos__lines">
                  {p.trailing_stop != null && (
                    <div className="pos__row pos__row--trail">
                      <span>トレーリングストップ</span>
                      <span className="mono">
                        {p.trailing_stop.toLocaleString()}円
                        {p.locked_in_pct != null && (
                          <span style={{ color: pnlColor(p.locked_in_pct) }}>
                            {' '}（{p.locked_in_pct > 0 ? '+' : ''}
                            {p.locked_in_pct}%で確定）
                          </span>
                        )}
                      </span>
                    </div>
                  )}
                  <div className="pos__row">
                    <span>初期の損切りライン</span>
                    <span className="mono">{p.stop_loss?.toLocaleString()}円</span>
                  </div>
                  <div className="pos__row">
                    <span>利確①／②</span>
                    <span className="mono">
                      {p.take_profit_1?.toLocaleString()} / {p.take_profit_2?.toLocaleString()}円
                    </span>
                  </div>
                  <p className="pos__note">{p.trailing_note || p.stop_loss_note}</p>
                </div>
                <p className={`pos__status pos__status--${p.status}`}>{p.status_label}</p>
              </>
            )}
          </div>
        ))}
      </div>

      <p className="positions__disclaimer">⚠ {disclaimer}</p>
    </section>
  )
}

export function SignalDetail({ stock }) {
  if (!stock) {
    return <div className="detail detail--empty">左のリストから銘柄を選んでください</div>
  }
  if (stock.error) {
    return (
      <div className="detail detail--empty">
        <p className="detail__error-title">データを取得できませんでした</p>
        <p className="detail__error-body">{stock.error}</p>
        <p className="detail__error-hint">
          backend/.env の DATA_SOURCE の設定と、ネットワーク接続を確認してください。
          （DATA_SOURCE=jquants の場合は JQUANTS_API_KEY も必要です）
        </p>
      </div>
    )
  }

  const { signal } = stock

  return (
    <div className="detail">
      <header className="detail__header">
        <div>
          <h2>{stock.name}</h2>
          <span className="mono detail__code">
            {stock.code}
            {stock.sector && stock.sector !== 'unknown' && `｜${stock.sector}`}
            ｜{stock.timeframe_label}｜{stock.updated_at} 時点
          </span>
        </div>
        <StarBadge stars={signal.stars} judgement={signal.judgement} />
      </header>

      <p className="stars-note">{signal.stars_note}</p>

      <div className="detail__grid">
        <section className="panel">
          <h3>価格ライン</h3>
          <PriceLadder signal={signal} price={stock.price} />
          <RiskReward signal={signal} />
        </section>

        <section className="panel">
          <h3>トレンド判定</h3>
          <p className="perfect-order">
            パーフェクトオーダー：
            <span
              className={
                signal.perfect_order === '上昇形成'
                  ? 'po po--up'
                  : signal.perfect_order === '下降形成'
                  ? 'po po--down'
                  : 'po po--mid'
              }
            >
              {signal.perfect_order}
            </span>
          </p>
          {signal.patterns_detected.length > 0 && (
            <p className="patterns">検出パターン：{signal.patterns_detected.join('、')}</p>
          )}
        </section>

        <Confidence confidence={stock.confidence} />

        <section className="panel panel--wide">
          <h3>根拠</h3>
          <ul className="reason-list">
            {signal.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </section>

        {signal.warnings.length > 0 && (
          <section className="panel panel--wide panel--warning">
            <h3>警戒材料</h3>
            <ul className="reason-list reason-list--warning">
              {signal.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <p className="disclaimer">
        ※これは投資助言ではありません。テクニカル分析の型に基づく参考情報です。最終判断はご自身で行ってください。
      </p>
    </div>
  )
}
