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
        # Store sanitized URL for logging (without token)
        self.repo_url_safe = repo_url
        self.local_path = Path(local_path)
        self.branch = branch
        self._last_sync: Optional[datetime] = None
        logger.info(f"GitManager initialized: repo={self.repo_url_safe}, path={self.local_path}, branch={self.branch}")

    def _inject_token(self, url: str, token: str) -> str:
        if url.startswith("https://"):
            return url.replace("https://", f"https://{token}@")
        return url

    def _run_git(self, *args, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        work_dir = cwd or self.local_path
        logger.debug(f"Running git command: {' '.join(cmd)} in {work_dir}")

        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Log the result
        if result.returncode != 0:
            logger.error(f"Git command failed: {' '.join(cmd)}")
            logger.error(f"  returncode: {result.returncode}")
            logger.error(f"  stdout: {result.stdout}")
            logger.error(f"  stderr: {result.stderr}")
        else:
            logger.debug(f"Git command succeeded: {' '.join(cmd)}")
            if result.stdout:
                logger.debug(f"  stdout: {result.stdout[:500]}")

        return result

    def clone(self) -> Dict[str, Any]:
        """Clone repo or pull if already exists. Returns detailed status."""
        logger.info(f"clone() called - checking if repo exists at {self.local_path}")

        if self.local_path.exists() and (self.local_path / ".git").exists():
            logger.info(f"Repo already exists at {self.local_path}, pulling latest")
            return self.pull()

        logger.info(f"Cloning repo to {self.local_path}")
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
            return {
                "success": False,
                "action": "clone",
                "error": result.stderr,
            }

        self._last_sync = datetime.now()
        logger.info(f"Successfully cloned repo to {self.local_path}")
        return {
            "success": True,
            "action": "clone",
            "path": str(self.local_path),
        }

    def pull(self) -> Dict[str, Any]:
        """Pull latest changes from remote. Returns detailed status."""
        logger.info(f"pull() called")

        if not self.local_path.exists():
            logger.info("Local path doesn't exist, falling back to clone")
            return self.clone()

        # Stash any uncommitted changes first
        logger.info("Stashing any local changes...")
        stash_result = self._run_git("stash", "push", "-m", "auto-stash before pull")
        had_stash = "No local changes to save" not in stash_result.stdout

        logger.info(f"Pulling from origin/{self.branch} with rebase...")
        result = self._run_git("pull", "--rebase", "origin", self.branch)

        if result.returncode != 0:
            if had_stash:
                logger.info("Pull failed, restoring stash...")
                self._run_git("stash", "pop")
            return {
                "success": False,
                "action": "pull",
                "error": result.stderr,
            }

        # Restore stashed changes if any
        if had_stash:
            logger.info("Restoring stashed changes...")
            self._run_git("stash", "pop")

        self._last_sync = datetime.now()
        logger.info("Pull completed successfully")
        return {
            "success": True,
            "action": "pull",
        }

    def push(self, message: str = "Update from MCP server") -> Dict[str, Any]:
        """Push local changes to remote. Returns detailed status."""
        logger.info(f"push() called with message: {message}")

        # Step 1: Check current status before doing anything
        logger.info("Step 1: Checking git status before operations...")
        status_result = self._run_git("status", "--porcelain")
        logger.info(f"Git status (porcelain): '{status_result.stdout}'")

        if not status_result.stdout.strip():
            logger.info("Working directory is clean - no changes to push")
            return {
                "success": True,
                "action": "no_changes",
                "message": "Working directory clean, nothing to push",
                "changes_pushed": False,
            }

        # Step 2: Stage all changes
        logger.info("Step 2: Staging all changes with 'git add -A'...")
        add_result = self._run_git("add", "-A")
        if add_result.returncode != 0:
            return {
                "success": False,
                "action": "push",
                "error": f"git add failed: {add_result.stderr}",
                "step": "add",
            }

        # Step 3: Check what's staged
        logger.info("Step 3: Checking staged changes...")
        diff_result = self._run_git("diff", "--cached", "--stat")
        logger.info(f"Staged changes:\n{diff_result.stdout}")

        if not diff_result.stdout.strip():
            logger.warning("Nothing staged after 'git add -A' - files might be ignored")
            # Check if files are ignored
            ignored_result = self._run_git("status", "--ignored", "--porcelain")
            logger.info(f"Ignored files check: {ignored_result.stdout}")
            return {
                "success": True,
                "action": "no_changes",
                "message": "Nothing staged - files may be in .gitignore",
                "changes_pushed": False,
            }

        # Step 4: Commit
        logger.info(f"Step 4: Committing with message: {message}")
        commit_result = self._run_git("commit", "-m", message)
        if commit_result.returncode != 0:
            return {
                "success": False,
                "action": "push",
                "error": f"git commit failed: {commit_result.stderr}",
                "step": "commit",
            }
        logger.info(f"Commit successful: {commit_result.stdout}")

        # Step 5: Check local vs remote before push
        logger.info("Step 5: Checking local vs remote status...")
        self._run_git("fetch", "origin", self.branch)
        log_result = self._run_git("log", f"origin/{self.branch}..HEAD", "--oneline")
        logger.info(f"Commits to push: {log_result.stdout}")

        # Step 6: Push
        logger.info(f"Step 6: Pushing to origin/{self.branch}...")
        push_result = self._run_git("push", "origin", self.branch)

        if push_result.returncode != 0:
            return {
                "success": False,
                "action": "push",
                "error": f"git push failed: {push_result.stderr}",
                "step": "push",
            }

        # Step 7: Verify push succeeded
        logger.info("Step 7: Verifying push succeeded...")
        self._run_git("fetch", "origin", self.branch)
        verify_result = self._run_git("log", f"origin/{self.branch}..HEAD", "--oneline")

        if verify_result.stdout.strip():
            logger.error(f"Push may have failed - still have unpushed commits: {verify_result.stdout}")
            return {
                "success": False,
                "action": "push",
                "error": "Push command succeeded but commits still not on remote",
                "step": "verify",
            }

        self._last_sync = datetime.now()
        logger.info("Push completed and verified successfully!")
        return {
            "success": True,
            "action": "push",
            "message": "Changes pushed successfully",
            "changes_pushed": True,
            "commit_output": commit_result.stdout,
        }

    def sync(self, message: str = "Update from MCP server") -> Dict[str, Any]:
        """Pull latest changes, then push local changes. Returns detailed status."""
        logger.info(f"sync() called with message: {message}")

        # First pull to get any remote changes
        pull_result = self.pull()
        if not pull_result.get("success"):
            logger.error(f"Sync failed at pull step: {pull_result}")
            return {
                "success": False,
                "action": "sync",
                "error": f"Pull failed: {pull_result.get('error')}",
                "step": "pull",
                "pull_result": pull_result,
            }

        # Then push our changes
        push_result = self.push(message)
        if not push_result.get("success"):
            logger.error(f"Sync failed at push step: {push_result}")
            return {
                "success": False,
                "action": "sync",
                "error": f"Push failed: {push_result.get('error')}",
                "step": "push",
                "push_result": push_result,
            }

        logger.info("Sync completed successfully")
        return {
            "success": True,
            "action": "sync",
            "changes_pushed": push_result.get("changes_pushed", False),
            "pull_result": pull_result,
            "push_result": push_result,
        }

    def get_status(self) -> Dict[str, Any]:
        """Get detailed git status for debugging."""
        logger.info("get_status() called")

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

        # Get various git info
        status_result = self._run_git("status", "--porcelain")
        status["working_directory_changes"] = status_result.stdout.strip() if status_result.stdout else "(clean)"

        branch_result = self._run_git("branch", "--show-current")
        status["current_branch"] = branch_result.stdout.strip()

        log_result = self._run_git("log", "-1", "--oneline")
        status["last_commit"] = log_result.stdout.strip()

        remote_result = self._run_git("remote", "-v")
        status["remotes"] = remote_result.stdout.strip()

        # Check if we're ahead/behind remote
        self._run_git("fetch", "origin", self.branch)
        ahead_result = self._run_git("rev-list", f"origin/{self.branch}..HEAD", "--count")
        behind_result = self._run_git("rev-list", f"HEAD..origin/{self.branch}", "--count")
        status["commits_ahead"] = ahead_result.stdout.strip()
        status["commits_behind"] = behind_result.stdout.strip()

        return status

    @property
    def last_sync(self) -> Optional[datetime]:
        return self._last_sync
