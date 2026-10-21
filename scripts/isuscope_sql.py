"""Read isuscope's index with `isuscope sql`, one dict per row.

The scripts read runs, analyses and change decisions here instead of parsing isuscope-data files,
whose layout is isuscope's to change.
"""
import json
import os
import subprocess


class Unavailable(RuntimeError):
    """isuscope is missing, has no settings here, or refused the query."""


def rows(root, query, limit=100000):
    """Rows of a read-only SELECT, run in `root` (the project with .isuscope/config.toml)."""
    try:
        done = subprocess.run(['isuscope', 'sql', '--limit', str(limit), query], cwd=root, capture_output=True,
                              text=True, timeout=60, env={**os.environ, 'ISUSCOPE_OUTPUT_BYTES': '0'})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(str(error)) from error
    if done.returncode:
        raise Unavailable(done.stderr.strip()[:500] or f'isuscope sql exit {done.returncode}')
    table = json.loads(done.stdout)
    return [dict(zip(table['columns'], row)) for row in table['rows']]
