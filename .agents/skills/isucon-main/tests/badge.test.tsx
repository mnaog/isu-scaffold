import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import type { IsuconMainBadge } from '../types'

const BAND = {
  plugin: 'isucon-main',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 4,
    bodyColumns: 100,
    scroll: { offset: 0, bodyRows: 4 },
    view: {},
  },
} as const

const TURN = { answer: '', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' } as const

function badge(over: Partial<IsuconMainBadge>): IsuconMainBadge {
  return {
    role: 'main',
    branch: 'main',
    worktree: '/repo',
    is_root: true,
    main: { agent: 'claude', session: 'S-main-0001', branch: 'main', claimed_at: '' },
    waiting: 0,
    proposal: null,
    benchmark: null,
    ...over,
  }
}

// What scripts/main-session.py badge would print, and the git call that finds the repository.
function host(on: On, value: IsuconMainBadge) {
  on('session.id', () => ({ value: 'S-main-0001' }))
  on('turn.complete', () => ({ text: '' }))
  on('process.run', ($, e) => {
    const isGit = e.argv[0] === 'git'
    return {
      value: {
        exitCode: 0,
        stdout: isGit ? '/repo\n' : JSON.stringify(value),
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })
}

test('main gets the MAIN band on every surface', async ($, on) => {
  host(on, badge({ waiting: 2 }))
  await $.turn.complete(TURN)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ type: 'Text', text: /MAIN/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /投稿 2件/ })).toBeDefined()
    await ui.unmount()
  }
})

test('a worktree session says it is not main and how its proposal stands', async ($, on) => {
  host(on, badge({
    role: 'other',
    branch: 'idea/cache',
    is_root: false,
    proposal: { status: 'needs-sync', commit: 'abc', decision: null },
  }))
  await $.turn.complete(TURN)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /WORKTREE/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /MAIN/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /衝突/ })).toBeDefined()
  await ui.unmount()
})

test('with no main declared the band says how to declare one', async ($, on) => {
  host(on, badge({ role: 'unclaimed', main: null }))
  await $.turn.complete(TURN)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /\/main/ })).toBeDefined()
  await ui.unmount()
})

test('a running benchmark is shown first, to main and to the other sessions', async ($, on) => {
  host(on, badge({ benchmark: { kind: 'run', started_at: '', elapsed_seconds: 135 } }))
  await $.turn.complete(TURN)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /ベンチ実行中 2分15秒/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /MAIN/ })).toBeDefined()
  await ui.unmount()
})

test('a worktree session also sees the benchmark run', async ($, on) => {
  host(on, badge({ role: 'other', is_root: false, branch: 'idea/x',
                   benchmark: { kind: 'survey-run', started_at: '', elapsed_seconds: 12 } }))
  await $.turn.complete(TURN)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /初回ベンチ実行中 12秒/ })).toBeDefined()
  await ui.unmount()
})

test('no benchmark, no running badge', async ($, on) => {
  host(on, badge({}))
  await $.turn.complete(TURN)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /実行中/ })).toBeUndefined()
  await ui.unmount()
})
