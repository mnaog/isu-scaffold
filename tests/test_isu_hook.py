import concurrent.futures
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
import isu_hook  # noqa: E402

TRICKY = ['a && b|c;d', "echo 'a b' \"c\\\"d\" e\\ f", "x ''", 'a\nb', '"a\\nb"', "'a", 'a\\', 'a&&&b',
          'cat <<EOF\nx\nEOF', "printf '%s\\n' \"$x\" | grep -q y", 'a;;b|&c', '"" b', "a'b'c\"d\"e", '\\',
          'isuscope lock -- make deploy', 'echo é ünï “quoted”', '(cd x && y) &', 'a\tb\rc']


def python_tokens(text):
    """The guard's tokens from Python's shlex, as tool-guard.py split commands before isu-hook."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        return list(lexer)
    except ValueError:
        return 'error'


class IsuHookTest(unittest.TestCase):
    def test_commands_split_like_pythons_shlex(self):
        binary = isu_hook.build()
        rng = random.Random(14)
        texts = TRICKY + [''.join(rng.choice('a b\'"\\;&|()\n\t\r$-=xé') for _ in range(rng.randint(0, 16)))
                          for _ in range(600)]

        def rust_tokens(text):
            output = json.loads(subprocess.run([str(binary), 'lex'], input=text, capture_output=True, text=True,
                                               check=True).stdout)
            return 'error' if isinstance(output, dict) else output
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            for text, tokens in zip(texts, pool.map(rust_tokens, texts)):
                self.assertEqual(tokens, python_tokens(text), repr(text))

    def test_without_a_build_only_the_build_gets_through(self):
        # The rules stay on: a fresh clone refuses every call until `make hook`, and post adds nothing.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'scripts').mkdir()
            shutil.copy(ROOT / 'scripts/hook.sh', repo / 'scripts/hook.sh')
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            env = {k: v for k, v in os.environ.items() if k not in ('ISU_HOOK_BIN', 'TOOL_GUARD_ROOT')}

            def run(mode, command):
                payload = {'tool_name': 'Bash', 'tool_input': {'command': command}}
                result = subprocess.run([str(repo / 'scripts/hook.sh'), mode], input=json.dumps(payload),
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else None
            refused = run('pre', 'ls')
            self.assertEqual(refused['permissionDecision'], 'deny')
            self.assertIn('make hook', refused['permissionDecisionReason'])
            self.assertEqual(run('guard', 'git status')['permissionDecision'], 'deny')
            for command in ('make hook', 'make -s hook', f'cd {repo} && make hook'):
                self.assertIsNone(run('pre', command), command)
            self.assertEqual(run('pre', 'make hook; ssh bench')['permissionDecision'], 'deny')
            self.assertIsNone(run('post', 'isuscope run --hypothesis x'))


if __name__ == '__main__':
    unittest.main()
