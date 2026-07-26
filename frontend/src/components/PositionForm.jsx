import React, { useState } from 'react'
import { savePosition } from '../api'

const EMPTY_LOT = { avg_cost: '', shares: '' }

// 取得単価と株数を入力して損切り・利確ラインを出すフォーム。
// 複数回に分けて買った場合はロットを追加できる。
export function PositionForm({ onSaved }) {
  const [open, setOpen] = useState(false)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [acquiredOn, setAcquiredOn] = useState('')
  const [lots, setLots] = useState([{ ...EMPTY_LOT }])
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const setLot = (i, key, value) =>
    setLots((prev) => prev.map((l, j) => (j === i ? { ...l, [key]: value } : l)))

  const reset = () => {
    setCode('')
    setName('')
    setAcquiredOn('')
    setLots([{ ...EMPTY_LOT }])
  }

  const submit = async (e) => {
    e.preventDefault()
    setMessage(null)

    const parsed = lots
      .map((l) => ({ avg_cost: Number(l.avg_cost), shares: Number(l.shares) }))
      .filter((l) => l.avg_cost > 0 && l.shares > 0)

    if (!code.trim()) return setMessage({ type: 'error', text: '証券コードを入力してください' })
    if (!parsed.length)
      return setMessage({ type: 'error', text: '取得単価と株数を1件以上入力してください' })

    setBusy(true)
    try {
      const data = await savePosition(
        code.trim(),
        {
          code: code.trim(),
          name: name.trim() || code.trim(),
          lots: parsed,
          acquired_on: acquiredOn || null,
        },
        token
      )
      onSaved?.(data)
      const total = parsed.reduce((s, l) => s + l.shares, 0)
      setMessage({ type: 'ok', text: `${code.trim()} を保存しました（合計${total}株）` })
      reset()
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <div className="posform__toggle">
        <button className="posform__open" onClick={() => setOpen(true)}>
          ＋ 保有銘柄を追加・更新する
        </button>
      </div>
    )
  }

  return (
    <form className="posform" onSubmit={submit}>
      <div className="posform__head">
        <h4>保有銘柄の登録</h4>
        <button type="button" className="posform__close" onClick={() => setOpen(false)}>
          閉じる
        </button>
      </div>

      <div className="posform__row">
        <label>
          証券コード
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="8136"
            inputMode="numeric"
          />
        </label>
        <label>
          銘柄名（任意）
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="サンリオ" />
        </label>
        <label>
          取得日（任意）
          <input type="date" value={acquiredOn} onChange={(e) => setAcquiredOn(e.target.value)} />
        </label>
      </div>

      {lots.map((lot, i) => (
        <div className="posform__row posform__row--lot" key={i}>
          <label>
            取得単価
            <input
              value={lot.avg_cost}
              onChange={(e) => setLot(i, 'avg_cost', e.target.value)}
              placeholder="1185"
              inputMode="decimal"
            />
          </label>
          <label>
            株数
            <input
              value={lot.shares}
              onChange={(e) => setLot(i, 'shares', e.target.value)}
              placeholder="300"
              inputMode="numeric"
            />
          </label>
          {lots.length > 1 && (
            <button
              type="button"
              className="posform__remove"
              onClick={() => setLots((p) => p.filter((_, j) => j !== i))}
            >
              削除
            </button>
          )}
        </div>
      ))}

      <button
        type="button"
        className="posform__add-lot"
        onClick={() => setLots((p) => [...p, { ...EMPTY_LOT }])}
      >
        ＋ 買い増し分を追加
      </button>

      <div className="posform__row">
        <label>
          書き込みトークン（公開先で設定している場合のみ）
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="ローカル利用なら空欄"
          />
        </label>
      </div>

      <div className="posform__actions">
        <button type="submit" disabled={busy}>
          {busy ? '保存中…' : '保存する'}
        </button>
        <span className="posform__hint">株数を0にして保存すると削除されます</span>
      </div>

      {message && (
        <p className={`posform__msg posform__msg--${message.type}`}>{message.text}</p>
      )}
    </form>
  )
}
