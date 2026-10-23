import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { IsuconMainBadge } from '../types'

// The session that ran /main gets a MAIN band above its prompt and a status line entry, so a
// person with several Claude Code windows sees at a glance which one deploys and benchmarks.
// Every other session leads with its worktree's name, so each window says where it works, and
// says it is not main and how its proposal stands.
// While a benchmark holds the operation lock, every session's band says so first.
// scripts/main-session.py badge holds the logic; this module only draws what it prints.

const badge = atom({ plugin: 'isucon-main', key: 'badge' } as const, null)

const REFRESH_MS = 10_000

const STATUS_WORDS: Record<string, string> = {
  submitted: '投稿済み（mainの次のベンチで渡る）',
  delivered: 'mainへ渡した',
  'needs-sync': 'mainと衝突。追従して投稿し直す',
  merged: '次のベンチに入った',
  measuring: '測定中',
  held: '保留（条件を待つ）',
  accepted: '採用',
  rejected: '不採用',
  deferred: '保留',
  withdrawn: '取り下げ',
}

function benchmarkText(value: IsuconMainBadge): string | null {
  const bench = value.benchmark
  if (!bench) return null
  const s = bench.elapsed_seconds
  const since = s == null ? '' : s < 60 ? ` ${s}秒` : ` ${Math.floor(s / 60)}分${s % 60}秒`
  return `▶ ベンチ実行中${since}`
}

async function refresh($: EngineInterface): Promise<void> {
  const session = await $.session.id()
  const top = await $.process.run(['git', 'rev-parse', '--show-toplevel'])
  if (top.exitCode !== 0) {
    await update($, badge, () => null)
    return
  }
  const root = top.stdout.trim()
  const result = await $.process.run(
    ['python3', `${root}/scripts/main-session.py`, 'badge', '--session', session],
    { cwd: root, timeoutMs: 10_000 },
  )
  const value: IsuconMainBadge | null =
    result.exitCode === 0 ? (JSON.parse(result.stdout) as IsuconMainBadge) : null
  await update($, badge, () => value)
  const running = value?.benchmark ? ' · ▶ ベンチ実行中' : ''
  $.ui.status(
    value === null ? undefined : value.role === 'main' ? `★ MAIN${running}` : `⎇ ${value.name}${running}`,
  )
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    // Another session can claim main at any time; look again every few seconds for the session's life.
    void (async () => {
      for (;;) {
        await refresh($).catch(() => undefined)
        await $.clock.sleep(REFRESH_MS)
      }
    })()
    return started
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    await refresh($).catch(() => undefined)
    return result
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const value = await read($, badge)
    if (value === null || e.props.hasSurvey) {
      return next(e)
    }
    const { Box, Text } = $.ui.resolve(e)
    const bench = benchmarkText(value)
    const benchBadge = bench ? (
      <Text bold inverse color="warning">
        {` ${bench} `}
      </Text>
    ) : null

    if (value.role === 'main') {
      return (
        <Box borderStyle="bold" borderColor={bench ? 'warning' : 'success'} paddingX={1} gap={2}>
          {benchBadge}
          <Text bold inverse color="success">
            {' ★ MAIN '}
          </Text>
          <Text bold>{value.branch}</Text>
          <Text dimColor>deploy・ベンチ・採否はこのセッション</Text>
          {value.waiting > 0 ? <Text color="warning">投稿 {value.waiting}件</Text> : null}
        </Box>
      )
    }

    const where = (
      <Text bold inverse color="suggestion">
        {` ⎇ ${value.name} `}
      </Text>
    )

    if (value.role === 'unclaimed') {
      return (
        <Box paddingX={1} gap={2}>
          {benchBadge}
          {where}
          <Text color="warning">mainは未宣言</Text>
          <Text dimColor>
            {value.is_root ? 'mainにするなら /main' : 'repoのrootのセッションで /main'}
          </Text>
        </Box>
      )
    }

    const main = value.main
    const own = value.proposal
    return (
      <Box borderStyle="round" borderColor="suggestion" paddingX={1} gap={2}>
        {benchBadge}
        {where}
        <Text bold color="subtle">
          {value.is_root ? 'NOT MAIN' : 'WORKTREE'}
        </Text>
        <Text>{value.branch}</Text>
        <Text dimColor>
          main: {main?.agent} {main?.session.slice(0, 8)}
        </Text>
        {own ? (
          <Text color={own.status === 'needs-sync' || own.status === 'rejected' ? 'warning' : 'suggestion'}>
            投稿: {STATUS_WORDS[own.status] ?? own.status}
            {own.decision?.run ? ` (run ${own.decision.run})` : ''}
          </Text>
        ) : value.is_root ? null : (
          <Text dimColor>変更はcommitして proposal.py submit</Text>
        )}
      </Box>
    )
  })
}
