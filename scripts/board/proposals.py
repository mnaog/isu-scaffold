"""The proposal queue for the top of the board, read as stored (no git, no writes)."""
import importlib.util
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'proposal.py'


def queue(repo):
    try:
        spec = importlib.util.spec_from_file_location('proposal_cli', SOURCE)
        proposal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proposal)
        return proposal.queue(repo)
    except (OSError, ImportError, ValueError, KeyError) as exc:
        return {'error': str(exc)[:500], 'items': []}
