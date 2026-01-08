import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any, List, Tuple


class PathSecurityError(Exception):
    pass


class VaultManager:
    NOTE_EXTENSIONS = {".md", ".markdown"}
    ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".svg", ".mp3", ".mp4", ".webm"}

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path).resolve()
        if not self.vault_path.exists():
            raise ValueError(f"Vault path does not exist: {vault_path}")

    def validate_path(self, relative_path: str) -> Path:
        normalized = os.path.normpath(relative_path)

        if normalized.startswith("..") or "/.." in normalized or "\\.." in normalized:
            raise PathSecurityError(f"Path traversal detected: {relative_path}")

        full_path = (self.vault_path / normalized).resolve()

        try:
            full_path.relative_to(self.vault_path)
        except ValueError:
            raise PathSecurityError(f"Path escapes vault: {relative_path}")

        return full_path

    def ensure_parent_exists(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

    def parse_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        frontmatter = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    pass
                body = parts[2].lstrip()

        return frontmatter, body

    def extract_tags(self, content: str, frontmatter: Dict[str, Any]) -> List[str]:
        tags = set()

        if "tags" in frontmatter:
            fm_tags = frontmatter["tags"]
            if isinstance(fm_tags, list):
                tags.update(fm_tags)
            elif isinstance(fm_tags, str):
                tags.add(fm_tags)

        inline_tags = re.findall(r"#([a-zA-Z][a-zA-Z0-9_/-]*)", content)
        tags.update(inline_tags)

        return sorted(tags)

    def build_content(self, body: str, frontmatter: Dict[str, Any] = None) -> str:
        content = ""
        if frontmatter:
            content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n"
        content += body
        return content
