/** What scripts/main-session.py badge prints for this session. */
export type IsuconMainBadge = {
  role: 'main' | 'other' | 'unclaimed'
  branch: string
  worktree: string
  is_root: boolean
  main: { agent: string; session: string; branch: string; claimed_at: string } | null
  waiting: number
  proposal: {
    status: string
    commit: string
    decision: { decision: string; run: string; note: string } | null
  } | null
}

declare module 'claude-code' {
  interface PluginState {
    'isucon-main': { badge: IsuconMainBadge | null }
  }
}
