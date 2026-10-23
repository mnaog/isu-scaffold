/** What scripts/main-session.py badge prints for this session. */
export type IsuconMainBadge = {
  role: 'main' | 'other' | 'unclaimed'
  branch: string
  worktree: string
  /** The worktree's short name: its directory without the root's name and dash, or 'root'. */
  name: string
  is_root: boolean
  main: { agent: string; session: string; branch: string; claimed_at: string } | null
  waiting: number
  proposal: {
    status: string
    commit: string
    decision: { decision: string; run: string; note: string } | null
  } | null
  /** The benchmark holding the operation lock now (isuscope run / survey-run), or null. */
  benchmark: { kind: 'run' | 'survey-run'; started_at: string | null; elapsed_seconds: number | null } | null
}

declare module 'claude-code' {
  interface PluginState {
    'isucon-main': { badge: IsuconMainBadge | null }
  }
}
