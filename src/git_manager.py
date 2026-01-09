import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Any
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
        self.repo_url_safe = repo_url
        self.local_path = Path(local_path)
        self.branch = branch
        self._last_sync: Optional[datetime] = None
        self._git_configured = False
        logger.info(f"GitManager initialized: repo={self.repo_url_safe}, path={self.local_path}, branch={self.branch}")

    def _ensure_git_config(self):
        if self._git_configured:
            return
        logger.info("Configuring git user identity...")
        self._run_git("config", "user.email", "brucewayne@gotham.com")
        self._run_git("config", "user.name", "Poke")
        self._git_configured = True

    def _inject_token(self, url: str, token: str) -> str:
        if url.startswith("https://"):
            return url.replace("https://", f"https://{token}@")
        return url

    def _run_git(self, *args, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        work_dir = cwd or self.local_path
        logger.debug(f"Running: {' '.join(cmd)} in {work_dir}")

        result = subprocess.run(
            cmd, cwd=work_dir, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            logger.error(f"Git failed: {' '.join(cmd)} | rc={result.returncode} | stderr={result.stderr}")
        else:
            logger.debug(f"Git ok: {' '.join(cmd)}")

        return result

    def clone(self) -> Dict[str, Any]:
        logger.info(f"clone() - checking {self.local_path}")

        if self.local_path.exists() and (self.local_path / ".git").exists():
            logger.info("Repo exists, pulling instead")
            return self.pull()

        self.local_path.parent.mkdir(parents=True, exist_ok=True)

        result = self._run_git(
            "clone", "--branch", self.branch, "--single-branch", "--depth", "1",
            self.repo_url, str(self.local_path), cwd=self.local_path.parent
        )

        if result.returncode != 0:
            return {"success": False, "action": "clone", "error": result.stderr}

        self._last_sync = datetime.now()
        return {"success": True, "action": "clone", "path": str(self.local_path)}

    def pull(self) -> Dict[str, Any]:
        logger.info("pull()")

        if not self.local_path.exists():
            return self.clone()

        stash_result = self._run_git("stash", "push", "-m", "auto-stash")
        had_stash = "No local changes to save" not in stash_result.stdout

        result = self._run_git("pull", "--rebase", "origin", self.branch)

        if result.returncode != 0:
            if had_stash:
                self._run_git("stash", "pop")
            return {"success": False, "action": "pull", "error": result.stderr}

        if had_stash:
            self._run_git("stash", "pop")

        self._last_sync = datetime.now()
        return {"success": True, "action": "pull"}

    def push(self, message: str = "Update from MCP server") -> Dict[str, Any]:
        logger.info(f"push() - {message}")

        status_result = self._run_git("status", "--porcelain")
        logger.info(f"Status: '{status_result.stdout.strip()}'")

        if not status_result.stdout.strip():
            return {"success": True, "action": "no_changes", "changes_pushed": False}

        add_result = self._run_git("add", "-A")
        if add_result.returncode != 0:
            return {"success": False, "action": "push", "error": add_result.stderr, "step": "add"}

        diff_result = self._run_git("diff", "--cached", "--stat")
        logger.info(f"Staged:\n{diff_result.stdout}")

        if not diff_result.stdout.strip():
            ignored = self._run_git("status", "--ignored", "--porcelain")
            logger.warning(f"Nothing staged. Ignored: {ignored.stdout}")
            return {"success": True, "action": "no_changes", "changes_pushed": False}

        self._ensure_git_config()
        commit_result = self._run_git("commit", "-m", message)
        if commit_result.returncode != 0:
            return {"success": False, "action": "push", "error": commit_result.stderr, "step": "commit"}

        self._run_git("fetch", "origin", self.branch)
        push_result = self._run_git("push", "origin", self.branch)

        if push_result.returncode != 0:
            return {"success": False, "action": "push", "error": push_result.stderr, "step": "push"}

        self._run_git("fetch", "origin", self.branch)
        verify = self._run_git("log", f"origin/{self.branch}..HEAD", "--oneline")

        if verify.stdout.strip():
            return {"success": False, "action": "push", "error": "Commits not on remote after push", "step": "verify"}

        self._last_sync = datetime.now()
        return {"success": True, "action": "push", "changes_pushed": True}

    def sync(self, message: str = "Update from MCP server") -> Dict[str, Any]:
        logger.info(f"sync() - {message}")

        pull_result = self.pull()
        if not pull_result.get("success"):
            return {"success": False, "action": "sync", "error": pull_result.get("error"), "step": "pull"}

        push_result = self.push(message)
        if not push_result.get("success"):
            return {"success": False, "action": "sync", "error": push_result.get("error"), "step": "push"}

        return {"success": True, "action": "sync", "changes_pushed": push_result.get("changes_pushed", False)}

    def get_status(self) -> Dict[str, Any]:
        status = {
            "repo_url": self.repo_url_safe,
            "local_path": str(self.local_path),
            "branch": self.branch,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "path_exists": self.local_path.exists(),
        }

        if not self.local_path.exists():
            status["error"] = "Local path does not exist"
            return status

        if not (self.local_path / ".git").exists():
            status["error"] = "Not a git repository"
            return status

        status["working_directory_changes"] = self._run_git("status", "--porcelain").stdout.strip() or "(clean)"
        status["current_branch"] = self._run_git("branch", "--show-current").stdout.strip()
        status["last_commit"] = self._run_git("log", "-1", "--oneline").stdout.strip()
        status["remotes"] = self._run_git("remote", "-v").stdout.strip()

        self._run_git("fetch", "origin", self.branch)
        status["commits_ahead"] = self._run_git("rev-list", f"origin/{self.branch}..HEAD", "--count").stdout.strip()
        status["commits_behind"] = self._run_git("rev-list", f"HEAD..origin/{self.branch}", "--count").stdout.strip()

        return status

    @property
    def last_sync(self) -> Optional[datetime]:
        return self._last_sync
