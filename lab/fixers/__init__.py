"""Registry of fixers. Order matters only for the order of commits in the generated branch."""
from .base import Context, FixError, Fixer, FixResult  # noqa: F401
from .canonical import CanonicalFixer
from .h1 import H1Fixer
from .links404 import Broken4xxFixer
from .noindex import NoindexFixer
from .redirects import RedirectChainFixer
from .titles import DuplicateTitleFixer

FIXERS = [
    CanonicalFixer(),
    H1Fixer(),
    NoindexFixer(),
    RedirectChainFixer(),
    Broken4xxFixer(),
    DuplicateTitleFixer(),
]


def fixer_for(issue_id):
    for fixer in FIXERS:
        if issue_id in fixer.issue_ids:
            return fixer
    return None
