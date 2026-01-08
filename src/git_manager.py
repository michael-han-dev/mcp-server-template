import subprocess
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class GitManager:
    def __init__(
        self,
        repo_url: str,
        local_path: str,
        branch: str = "main",
        token: Optional[str] = None,
    ):
        self.repo_url = self._inject_token(repo_url, token) if token else repo_url
        self.local_path = Path(local_path)
        self.branch = branch
        self._last_sync: Optional[datetime] = None

    def _inject_token(self, url: str, token: str) -> str:
        if url.startswith("https://"):
            return url.replace("https://", f"https://{token}@")
        return url

    def _run_git(self, *args, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(
            cmd,
            cwd=cwd or self.local_path,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def clone(self) -> bool:
        if self.local_path.exists() and (self.local_path / ".git").exists():
            logger.info(f"Repo already exists at {self.local_path}")
            return True

        self.local_path.parent.mkdir(parents=True, exist_ok=True)

        result = self._run_git(
            "clone",
            "--branch", self.branch,
            "--single-branch",
            "--depth", "1",
            self.repo_url,
            str(self.local_path),
            cwd=self.local_path.parent,
        )

        if result.returncode != 0:
            logger.error(f"Clone failed: {result.stderr}")
            return False

        self._last_sync = datetime.now()
        logger.info(f"Cloned repo to {self.local_path}")
        return True

    def pull(self) -> bool:
        if not self.local_path.exists():
            return self.clone()

        result = self._run_git("fetch", "origin", self.branch)
        if result.returncode != 0:
            logger.error(f"Fetch failed: {result.stderr}")
            return False

        result = self._run_git("reset", "--hard", f"origin/{self.branch}")
        if result.returncode != 0:
            logger.error(f"Reset failed: {result.stderr}")
            return False

        self._last_sync = datetime.now()
        logger.info("Pulled latest changes")
        return True

    def push(self, message: str = "Update from MCP server") -> bool:
        result = self._run_git("add", "-A")
        if result.returncode != 0:
            logger.error(f"Add failed: {result.stderr}")
            return False

        result = self._run_git("diff", "--cached", "--quiet")
        if result.returncode == 0:
            logger.info("No changes to commit")
            return True

        result = self._run_git("commit", "-m", message)
        if result.returncode != 0:
            logger.error(f"Commit failed: {result.stderr}")
            return False

        result = self._run_git("push", "origin", self.branch)
        if result.returncode != 0:
            logger.error(f"Push failed: {result.stderr}")
            return False

        logger.info("Pushed changes")
        return True

    @property
    def last_sync(self) -> Optional[datetime]:
        return self._last_sync
