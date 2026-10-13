#!/usr/bin/env python3
"""Check that the Codex or Claude Code session running this writes its history into this repo.

isuscope links every benchmark run to the conversation that started it, from
docs/agent-history/ of this repo (`[context.agent]`). A session started in another directory
(practice-12-sixth: the scaffold) writes its history there, and `isuscope doctor` then fails
in phase1-check, minutes into Phase 1. kickoff runs this first so that shows at once.

Outside Codex and Claude Code (a plain terminal) there is no session to check; the benchmark
must still be run later from a session started in this repo. AGENT_SESSION_CHECK=0 skips it.
"""
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get('REPO_DIR') or Path(__file__).resolve().parent.parent)


def current_sessions():
    """The same environment isuscope reads (src/agent_context.rs)."""
    sessions = []
    codex = os.environ.get('CODEX_SESSION_ID', '').strip() or os.environ.get('CODEX_THREAD_ID', '').strip()
    if codex:
        sessions.append(('codex', codex))
    claude = os.environ.get('CLAUDE_CODE_SESSION_ID', '').strip()
    if claude:
        sessions.append(('claude', claude))
    return sessions


def header(text, name):
    prefix = f'- {name}: `'
    for line in text.splitlines()[:16]:
        if line.startswith(prefix) and line.endswith('`'):
            return line[len(prefix):-1]
    return None


def belongs(text, agent, session):
    return header(text, 'Session') == session and (header(text, 'Agent') or 'codex') == agent


def started_in(agent, session):
    """Where Claude Code keeps the raw log names the directory the session was started in."""
    if agent != 'claude':
        return None
    for log in (Path.home() / '.claude/projects').glob(f'*/{session}.jsonl'):
        return log.parent.name
    return None


def main():
    if os.environ.get('AGENT_SESSION_CHECK') == '0':
        print('agent session check skipped (AGENT_SESSION_CHECK=0)')
        return 0
    sessions = current_sessions()
    if not sessions:
        print('not run from Codex or Claude Code; run the benchmarks later from a session started in '
              f'{ROOT}, so isuscope can link them to the conversation')
        return 0
    history = ROOT / 'docs/agent-history'
    texts = []
    for path in sorted(history.glob('*.md')) if history.is_dir() else []:
        try:
            texts.append(path.read_text(errors='replace'))
        except OSError:
            continue
    for agent, session in sessions:
        if any(belongs(text, agent, session) for text in texts):
            print(f'{agent} session {session} records its history in {history}')
            return 0
    agent, session = sessions[-1]
    where = started_in(agent, session)
    print(f'the {agent} session {session} does not record its history in {history}'
          + (f' (it was started in {where})' if where else '') + '.\n'
          f'Start a new Codex or Claude Code session inside {ROOT} and run make kickoff from it: '
          'isuscope links each benchmark to that conversation, and `isuscope doctor` in phase1-check '
          'refuses a session whose history is elsewhere. AGENT_SESSION_CHECK=0 skips this check.',
          file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
