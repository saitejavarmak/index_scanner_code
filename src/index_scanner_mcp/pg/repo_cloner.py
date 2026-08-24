"""Repository cloner for fetching helm charts and build properties from Bitbucket.

Clones team-level repos ({team}-helm-charts, {team}-buildproperties) via SSH
to a local temp directory for reading DB connection info from values files.

Repos are cached at /tmp/index-scanner-repos/{repo_name}/ and re-used if
they already exist (with a git pull to refresh). Cleanup is handled via
cleanup_all() or by the caller.

Usage::

    cloner = RepoCloner(bitbucket_org="your-org")
    helm_path = cloner.clone_or_pull("myteam-helm-charts")
    # helm_path = "/tmp/index-scanner-repos/myteam-helm-charts"

    # Cleanup when done
    cloner.cleanup("myteam-helm-charts")
    # or cleanup everything
    cloner.cleanup_all()
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = "/tmp/index-scanner-repos"
_DEFAULT_BITBUCKET_ORG = "your-org"


class RepoCloneError(RuntimeError):
    """Raised when a git clone or pull operation fails."""


class RepoCloner:
    """Clone and cache Bitbucket repos locally for reading config files.

    Supports SSH-based cloning (uses whatever SSH key is configured in
    ~/.ssh/) and HTTPS with token auth.

    Args:
        bitbucket_org: Bitbucket workspace/org name (default: "your-org").
        cache_dir:     Local directory to store cloned repos
                       (default: /tmp/index-scanner-repos/).
        auth_token:    Optional Bitbucket App Password or token for HTTPS auth.
                       If provided, HTTPS is used instead of SSH.
        ssh_key_path:  Optional path to SSH private key. If not provided,
                       uses the system default (~/.ssh/id_rsa or ssh-agent).
    """

    def __init__(
        self,
        bitbucket_org: str = _DEFAULT_BITBUCKET_ORG,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        auth_token: str | None = None,
        ssh_key_path: str | None = None,
    ) -> None:
        self._org = bitbucket_org
        self._cache_dir = Path(cache_dir)
        self._auth_token = auth_token
        self._ssh_key_path = ssh_key_path
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def cache_dir(self) -> str:
        return str(self._cache_dir)

    def _repo_url(self, repo_name: str) -> str:
        """Build the clone URL for a repo."""
        if self._auth_token:
            return f"https://x-token-auth:{self._auth_token}@bitbucket.org/{self._org}/{repo_name}.git"
        return f"git@bitbucket.org:{self._org}/{repo_name}.git"

    def _git_env(self) -> dict[str, str]:
        """Build environment variables for git commands."""
        env = os.environ.copy()
        if self._ssh_key_path:
            env["GIT_SSH_COMMAND"] = f"ssh -i {self._ssh_key_path} -o StrictHostKeyChecking=no"
        else:
            env["GIT_SSH_COMMAND"] = "ssh -o StrictHostKeyChecking=no"
        return env

    def clone_or_pull(self, repo_name: str, branch: str = "master") -> str | None:
        """Clone a repo or pull latest if already cached.

        Args:
            repo_name: Name of the Bitbucket repo (e.g., "myteam-helm-charts").
            branch:    Branch to checkout (default: "master").

        Returns:
            Absolute path to the cloned repo directory, or None if clone fails.
        """
        local_path = self._cache_dir / repo_name

        if local_path.exists() and (local_path / ".git").exists():
            # Repo already cloned — pull latest
            return self._pull(local_path, branch)
        else:
            # Fresh clone
            return self._clone(repo_name, local_path, branch)

    def _clone(self, repo_name: str, local_path: Path, branch: str) -> str | None:
        """Clone a repo from Bitbucket."""
        url = self._repo_url(repo_name)
        logger.info("Cloning %s to %s (branch: %s)", url, local_path, branch)

        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", branch, url, str(local_path)],
                capture_output=True,
                text=True,
                timeout=120,
                env=self._git_env(),
            )
            if result.returncode != 0:
                # Try without --branch (repo might use 'main' instead of 'master')
                if "not found" in result.stderr.lower() or "not a valid" in result.stderr.lower():
                    alt_branch = "main" if branch == "master" else "master"
                    logger.info("Branch '%s' not found, trying '%s'", branch, alt_branch)
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", "--branch", alt_branch, url, str(local_path)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=self._git_env(),
                    )

            if result.returncode != 0:
                logger.warning(
                    "Failed to clone %s: %s",
                    repo_name,
                    result.stderr.strip()[:200],
                )
                return None

            logger.info("Successfully cloned %s", repo_name)
            return str(local_path)

        except subprocess.TimeoutExpired:
            logger.warning("Clone of %s timed out after 120s", repo_name)
            return None
        except Exception as exc:
            logger.warning("Unexpected error cloning %s: %s", repo_name, exc)
            return None

    def _pull(self, local_path: Path, branch: str) -> str | None:
        """Pull latest changes for an already-cloned repo."""
        logger.debug("Pulling latest for %s", local_path.name)

        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(local_path),
                env=self._git_env(),
            )
            if result.returncode != 0:
                logger.debug(
                    "Pull failed for %s (non-fatal): %s",
                    local_path.name,
                    result.stderr.strip()[:100],
                )
            return str(local_path)

        except Exception as exc:
            logger.debug("Pull error for %s (non-fatal): %s", local_path.name, exc)
            # Still return the path — cached version is usable
            return str(local_path)

    def clone_helm_charts(self, team_name: str) -> str | None:
        """Clone the team's helm charts repo.

        Tries ``{team}-helm-charts`` first. Falls back to
        ``{team}-buildproperties`` if helm charts don't exist.

        Args:
            team_name: Team name (e.g., "analytics", "platform", "search").

        Returns:
            Path to the cloned repo, or None if both fail.
        """
        helm_repo = f"{team_name}-helm-charts"
        path = self.clone_or_pull(helm_repo)
        if path:
            return path

        # Fallback to buildproperties
        bp_repo = f"{team_name}-buildproperties"
        logger.info("Helm charts not found, trying buildproperties: %s", bp_repo)
        return self.clone_or_pull(bp_repo)

    def clone_service_repo(self, repo_name: str) -> str | None:
        """Clone a service's source code repository.

        Args:
            repo_name: The service repo name on Bitbucket.

        Returns:
            Path to the cloned repo, or None if clone fails.
        """
        return self.clone_or_pull(repo_name)

    def cleanup(self, repo_name: str) -> None:
        """Remove a specific cached repo."""
        local_path = self._cache_dir / repo_name
        if local_path.exists():
            shutil.rmtree(local_path, ignore_errors=True)
            logger.debug("Cleaned up %s", local_path)

    def cleanup_all(self) -> None:
        """Remove all cached repos."""
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir, ignore_errors=True)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Cleaned up all cached repos at %s", self._cache_dir)

    def list_cached(self) -> list[str]:
        """List all currently cached repo names."""
        if not self._cache_dir.exists():
            return []
        return [
            d.name
            for d in sorted(self._cache_dir.iterdir())
            if d.is_dir() and (d / ".git").exists()
        ]
