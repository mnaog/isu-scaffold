"""Read local operation evidence without SSH, creating files, or reclaiming locks."""
import fcntl
import time


def read_text(path):
    try:
        return path.read_text()[:8192].strip()
    except (OSError, UnicodeError):
        return ''


def operation(repo):
    directory = repo / '.local' / 'operation.lock'
    try:
        with (directory / 'lock').open('rb') as handle:
            owner = read_text(directory / 'owner')
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                # A leftover owner file alone is never evidence of a live operation.
                if owner and owner == read_text(directory / 'owner'):
                    fields = dict(line.split('=', 1) for line in owner.splitlines() if '=' in line)
                    return {key: fields.get(key) for key in ('operation', 'started_at')}
                return {'operation': None, 'started_at': None}
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return None


def activity(repo):
    result = {'checked_at': time.time(), 'operation': operation(repo), 'deploy': None,
              'last_deploy_commit': read_text(repo / '.local' / 'current-deploy-commit') or None}
    try:
        states = sorted((repo / '.local' / 'deploy-transactions').glob('*.state'),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        if states:
            path = states[0]
            result['deploy'] = {'release': path.stem, 'phase': read_text(path),
                                'recorded_at': path.stat().st_mtime,
                                'commit': read_text(path.with_suffix('.commit')) or None}
    except OSError:
        pass
    return result
