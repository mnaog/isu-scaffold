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


def detail(repo, cfg, run, base='', limit=50):
    def read(args):
        return command_json(repo, cfg['isuscope'] + args, max_bytes=2_000_000)
    brief = read(['brief', run, '--limit', '25'])
    if 'error' in brief:
        return {'error': brief['error']}
    candidate = brief.get('run', {})
    if candidate.get('state') not in ('complete', 'degraded', 'failed', 'aborted'):
        return {'error': '終了したrunを選んでください'}
    result = {'latest': candidate, 'brief': brief, 'base': None, 'sections': {},
              'collected_at': time.time(), 'current_commit': git(repo, 'rev-parse', 'HEAD'),
              'dirty': bool(git(repo, 'status', '--porcelain'))}
    analysis = (brief.get('review') or {}).get('latest_analysis') or {}
    result['base_mode'] = 'analysis' if base == 'auto' else 'manual'
    if base == 'auto':
        base = analysis.get('base_run_id') or ''
    if base:
        baseline = read(['brief', base, '--limit', '1'])
        if 'error' in baseline or baseline.get('run', {}).get('state') not in ('complete', 'degraded', 'failed', 'aborted'):
            return {'error': baseline.get('error', '終了した比較元runを選んでください')}
        result['base'] = baseline['run']
    for name, args in (('http', ['--view', 'http']),
                       ('sql', ['--view', 'database', '--window', 'load'])):
        argv = ['query', candidate['id'], *args, '--limit', str(limit)]
        if result['base']:
            argv += ['--base', result['base']['id']]
        result['sections'][name] = {'command': cfg['isuscope'] + argv, 'data': read(argv)}
    # These reads are independent and never start collectors or a benchmark.
    mysql_metrics = ['mysql.threads_running', 'mysql.threads_connected',
                     'mysql.queries_per_second', 'mysql.row_lock_waits_per_second',
                     'mysql.row_lock_time_ms_per_second', 'mysql.log_waits_per_second',
                     'mysql.buffer_pool_reads_per_second', 'mysql.buffer_pool_read_requests_per_second',
                     'mysql.data_fsyncs_per_second']
    mysql_args = ['series', candidate['id'], '--window', 'load', '--bucket', '5', '--limit', '3000']
    for metric in mysql_metrics:
        mysql_args += ['--metric', metric]
    queries = {
        'graph_http': ['query', candidate['id'], '--view', 'http', '--limit', '500'],
        'timeline': ['series', candidate['id'], '--window', 'load', '--bucket', '5', '--limit', '1000'],
        'mysql': mysql_args,
        'survey_index': ['sql', "SELECT id, started_at FROM runs WHERE mode='survey-run' AND passed=1 "
                         "AND state IN ('complete','degraded') ORDER BY started_at, id LIMIT 1", '--limit', '1'],
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {name: pool.submit(read, args) for name, args in queries.items()}
        for name, future in futures.items():
            result[name] = future.result()
    survey = result.pop('survey_index')
    if survey.get('rows'):
        first = survey['rows'][0]
        result['survey'] = read(['brief', first['id'], '--limit', '200'])
        result['survey']['quality'] = read(['query', first['id'], '--metric-prefix', 'transition.', '--limit', '1000'])
    else:
        result['survey'] = {'error': survey.get('error'), 'run': None}
    return result


class DetailCache:
    """Serialize expensive CLI reads and keep at most eight selections for 30s."""
    def __init__(self):
        self.lock = threading.Lock()
        self.items = OrderedDict()

    def get(self, repo, cfg, run, base, limit):
        key = (tuple(cfg['isuscope']), run, base, limit)
        with self.lock:
            now = time.monotonic()
            cached = self.items.get(key)
            if cached and now - cached[0] < 30:
                self.items.move_to_end(key)
                return cached[1]
            value = detail(repo, cfg, run, base, limit)
            self.items[key] = (time.monotonic(), value)
            self.items.move_to_end(key)
            while len(self.items) > 8:
                self.items.popitem(last=False)
            return value
