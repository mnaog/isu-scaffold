"""On-demand, bounded board queries. Never used as scout input."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time
from inputs import command_json
from store import git


def selection(params):
    if set(params) - {'run', 'base', 'limit'} or any(len(v) != 1 for v in params.values()):
        raise ValueError('run / base / limitを1つずつ指定してください')
    run = params.get('run', [''])[0]
    base = params.get('base', [''])[0]
    for value in (run, base):
        if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
            raise ValueError('run IDが不正です')
    if not run:
        raise ValueError('表示runを指定してください')
    limit = params.get('limit', ['50'])[0]
    if limit not in ('25', '50', '100'):
        raise ValueError('取得件数は25 / 50 / 100です')
    return run, base, int(limit)


MYSQL_METRICS = ['mysql.threads_running', 'mysql.threads_connected',
                 'mysql.queries_per_second', 'mysql.row_lock_waits_per_second',
                 'mysql.row_lock_time_ms_per_second', 'mysql.log_waits_per_second',
                 'mysql.buffer_pool_reads_per_second', 'mysql.buffer_pool_read_requests_per_second',
                 'mysql.data_fsyncs_per_second']


def restore_full_text(read, brief):
    """brief cuts long analysis and decision text for AI readers (`full_text` marks a cut);
    the board shows the saved text in full."""
    review = brief.get('review') or {}
    analysis = review.get('latest_analysis') or {}
    run_id = (brief.get('run') or {}).get('id', '')
    if analysis.get('full_text') and re.fullmatch(r'[0-9a-f-]{36}', run_id):
        rows = read(['sql', f"SELECT body FROM run_analyses WHERE run_id='{run_id}' "
                     "ORDER BY created_at DESC, id DESC LIMIT 1"]).get('rows') or []
        if rows:
            analysis['body'] = rows[0]['body']
    for change in review.get('changes') or []:
        if not change.get('full_text'):
            continue
        history = read(['change', 'show', change['id']])
        if 'error' in history:
            continue
        change['description'] = history['change']['description']
        # Decisions are oldest first; brief shows the latest one.
        decisions = history.get('decisions') or []
        if decisions and not change.get('reason_same_as_analysis'):
            change['reason'] = decisions[-1]['reason']


def detail(repo, cfg, run, base='', limit=50, measured=None):
    def read(args):
        return command_json(repo, cfg['isuscope'] + args, max_bytes=2_000_000)
    brief = read(['brief', run, '--limit', '25'])
    if 'error' in brief:
        return {'error': brief['error']}
    candidate = brief.get('run', {})
    if candidate.get('state') not in ('complete', 'degraded', 'failed', 'aborted'):
        return {'error': '終了したrunを選んでください'}
    result = {'latest': candidate, 'brief': brief, 'base': None,
              'collected_at': time.time(), 'current_commit': git(repo, 'rev-parse', 'HEAD'),
              'dirty': bool(git(repo, 'status', '--porcelain'))}
    restore_full_text(read, brief)
    analysis = (brief.get('review') or {}).get('latest_analysis') or {}
    result['base_mode'] = 'analysis' if base == 'auto' else 'manual'
    if base == 'auto':
        base = analysis.get('base_run') or ''
    if base:
        baseline = read(['brief', base, '--limit', '1'])
        if 'error' in baseline or baseline.get('run', {}).get('state') not in ('complete', 'degraded', 'failed', 'aborted'):
            return {'error': baseline.get('error', '終了した比較元runを選んでください')}
        result['base'] = baseline['run']
    # A finished run's measurements never change; only the brief (analysis, notes) is re-read.
    key = (tuple(cfg['isuscope']), candidate['id'], result['base']['id'] if result['base'] else '', limit)
    measurements = measured.get(key) if measured is not None else None
    if measurements is None:
        measurements = measure(read, cfg['isuscope'], candidate['id'], result['base'], limit)
        if measured is not None and complete(measurements):
            measured.put(key, measurements)
    result.update(measurements)
    return result


def measure(read, prefix, run_id, base, limit):
    sections = {}
    for name, args in (('http', ['--view', 'http']),
                       ('sql', ['--view', 'database', '--window', 'load'])):
        argv = ['query', run_id, *args, '--limit', str(limit)]
        if base:
            argv += ['--base', base['id']]
        sections[name] = argv
    mysql_args = ['series', run_id, '--window', 'load', '--bucket', '5', '--limit', '3000']
    for metric in MYSQL_METRICS:
        mysql_args += ['--metric', metric]
    queries = {
        'graph_http': ['query', run_id, '--view', 'http', '--limit', '500'],
        'timeline': ['series', run_id, '--window', 'load', '--bucket', '5', '--limit', '1000'],
        'mysql': mysql_args,
    }
    # These reads are independent and never start collectors or a benchmark.
    with ThreadPoolExecutor(max_workers=4) as pool:
        section_futures = {name: pool.submit(read, argv) for name, argv in sections.items()}
        futures = {name: pool.submit(read, argv) for name, argv in queries.items()}
        futures['survey'] = pool.submit(survey, read)
        result = {name: future.result() for name, future in futures.items()}
        result['sections'] = {name: {'command': prefix + sections[name], 'data': future.result()}
                              for name, future in section_futures.items()}
    return result


def survey(read):
    index = read(['sql', "SELECT id, started_at FROM runs WHERE mode='survey-run' AND passed=1 "
                  "AND state IN ('complete','degraded') ORDER BY started_at, id LIMIT 1", '--limit', '1'])
    if not index.get('rows'):
        return {'error': index.get('error'), 'run': None}
    first = index['rows'][0]
    result = read(['brief', first['id'], '--limit', '200'])
    result['quality'] = read(['query', first['id'], '--metric-prefix', 'transition.', '--limit', '1000'])
    return result


def complete(measurements):
    """Errors may be transient and a later survey-run may appear, so neither is kept."""
    parts = [measurements['graph_http'], measurements['timeline'], measurements['mysql'], measurements['survey'],
             measurements['survey'].get('quality') or {},
             *(section['data'] for section in measurements['sections'].values())]
    return measurements['survey'].get('run') is not None and not any(part.get('error') for part in parts)


class BoundedCache:
    """Thread-safe LRU map holding at most `size` entries."""
    def __init__(self, size):
        self.size = size
        self.lock = threading.Lock()
        self.items = OrderedDict()

    def get(self, key):
        with self.lock:
            if key not in self.items:
                return None
            self.items.move_to_end(key)
            return self.items[key]

    def put(self, key, value):
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


class DetailCache:
    """Keep eight selections for 30s; one CLI read per selection, other selections never wait."""
    def __init__(self):
        self.results = BoundedCache(8)
        self.measured = BoundedCache(8)
        self.lock = threading.Lock()
        self.pending = {}

    def fresh(self, key):
        cached = self.results.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached
        return None

    def get(self, repo, cfg, run, base, limit):
        key = (tuple(cfg['isuscope']), run, base, limit)
        cached = self.fresh(key)
        if cached:
            return cached[1]
        with self.lock:
            gate = self.pending.setdefault(key, threading.Lock())
        with gate:
            cached = self.fresh(key)
            if cached:
                return cached[1]
            try:
                value = detail(repo, cfg, run, base, limit, self.measured)
                self.results.put(key, (time.monotonic(), value))
            finally:
                with self.lock:
                    self.pending.pop(key, None)
            return value
