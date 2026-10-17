"""Git source control: the person's own ``git``, run safely.

``cli`` runs git; ``repo`` is everything local (status, branches, staging, commits, history,
diffs); ``ops`` is the work that talks to a remote, in the background; ``remote`` checks
addresses and makes links to the provider's site; ``secrets`` finds and hides secrets.
Fabric's own workspace Git integration is a separate thing and isn't here.
"""

from .cli import GitError
from .repo import Repository, init

__all__ = ["GitError", "Repository", "init"]
