import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ObsidianConfig:
    git_repo_url: str
    vault_path: str
    git_branch: str = "main"
    git_token: Optional[str] = None
    auto_sync: bool = False

    @classmethod
    def from_env(cls) -> "ObsidianConfig":
        repo_url = os.environ.get("OBSIDIAN_GIT_REPO_URL")
        if not repo_url:
            raise ValueError("OBSIDIAN_GIT_REPO_URL environment variable is required")

        return cls(
            git_repo_url=repo_url,
            vault_path=os.environ.get("OBSIDIAN_VAULT_PATH", "/tmp/obsidian-vault"),
            git_branch=os.environ.get("OBSIDIAN_GIT_BRANCH", "main"),
            git_token=os.environ.get("OBSIDIAN_GIT_TOKEN"),
            auto_sync=os.environ.get("OBSIDIAN_AUTO_SYNC", "false").lower() == "true",
        )
