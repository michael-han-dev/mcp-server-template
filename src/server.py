#!/usr/bin/env python3
import os
import sys
import base64
import mimetypes
import logging

from fastmcp import FastMCP

from config import ObsidianConfig
from git_manager import GitManager
from vault_manager import VaultManager, PathSecurityError

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

mcp = FastMCP("Obsidian Vault MCP Server")

config = None
git_manager = None

try:
    config = ObsidianConfig.from_env()
    git_manager = GitManager(
        repo_url=config.git_repo_url,
        local_path=config.vault_path,
        branch=config.git_branch,
        token=config.git_token,
    )
    clone_result = git_manager.clone()
    if not clone_result.get("success"):
        logger.error(f"Failed to clone vault: {clone_result}")
        sys.exit(1)
    logger.info(f"Vault initialized at {config.vault_path}")
except ValueError as e:
    logger.warning(f"Vault not configured: {e}")


def _get_vault() -> VaultManager:
    if not config:
        raise ValueError("Vault not configured. Set OBSIDIAN_GIT_REPO_URL env var.")
    return VaultManager(config.vault_path)


@mcp.tool(description="Read a note from the Obsidian vault. Returns content, frontmatter, and tags.")
def read_note(path: str) -> dict:
    try:
        vault = _get_vault()
        if not path.endswith((".md", ".markdown")):
            path = f"{path}.md"

        full_path = vault.validate_path(path)
        if not full_path.exists():
            return {"error": f"Note not found: {path}", "success": False}

        content = full_path.read_text(encoding="utf-8")
        frontmatter, body = vault.parse_frontmatter(content)
        tags = vault.extract_tags(content, frontmatter)

        return {
            "success": True,
            "path": path,
            "content": content,
            "body": body,
            "metadata": {"frontmatter": frontmatter, "tags": tags},
        }
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to read note: {e}", "success": False}


@mcp.tool(description="Create a new note. Fails if note already exists.")
def create_note(path: str, content: str, frontmatter: dict = None) -> dict:
    try:
        vault = _get_vault()
        if not path.endswith((".md", ".markdown")):
            path = f"{path}.md"

        full_path = vault.validate_path(path)
        if full_path.exists():
            return {"error": f"Note exists: {path}. Use update_note.", "success": False}

        final_content = vault.build_content(content, frontmatter)
        vault.ensure_parent_exists(full_path)
        full_path.write_text(final_content, encoding="utf-8")

        if config.auto_sync:
            git_manager.sync(f"Created: {path}")

        return {"success": True, "path": path}
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to create note: {e}", "success": False}


@mcp.tool(description="Update an existing note. Can replace or append content, merge frontmatter.")
def update_note(path: str, content: str = None, frontmatter: dict = None, append: bool = False) -> dict:
    try:
        vault = _get_vault()
        if not path.endswith((".md", ".markdown")):
            path = f"{path}.md"

        full_path = vault.validate_path(path)
        if not full_path.exists():
            return {"error": f"Note not found: {path}. Use create_note.", "success": False}

        existing = full_path.read_text(encoding="utf-8")
        existing_fm, existing_body = vault.parse_frontmatter(existing)

        if frontmatter:
            existing_fm.update(frontmatter)

        if content is not None:
            new_body = existing_body + "\n" + content if append else content
        else:
            new_body = existing_body

        final_content = vault.build_content(new_body, existing_fm if existing_fm else None)
        full_path.write_text(final_content, encoding="utf-8")

        if config.auto_sync:
            git_manager.sync(f"Updated: {path}")

        return {"success": True, "path": path}
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to update note: {e}", "success": False}


@mcp.tool(description="Delete a note from the vault.")
def delete_note(path: str) -> dict:
    try:
        vault = _get_vault()
        if not path.endswith((".md", ".markdown")):
            path = f"{path}.md"

        full_path = vault.validate_path(path)
        if not full_path.exists():
            return {"error": f"Note not found: {path}", "success": False}

        full_path.unlink()

        if config.auto_sync:
            git_manager.sync(f"Deleted: {path}")

        return {"success": True, "path": path}
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to delete note: {e}", "success": False}


@mcp.tool(description="List notes and folders in the vault.")
def list_vault(folder: str = "", recursive: bool = False, include_metadata: bool = False) -> dict:
    try:
        vault = _get_vault()
        base_path = vault.validate_path(folder) if folder else vault.vault_path

        if not base_path.exists():
            return {"error": f"Folder not found: {folder}", "success": False}
        if not base_path.is_dir():
            return {"error": f"Not a folder: {folder}", "success": False}

        notes = []
        folders = []
        pattern = "**/*" if recursive else "*"

        for item in base_path.glob(pattern):
            if any(part.startswith(".") for part in item.parts):
                continue

            relative = item.relative_to(vault.vault_path)

            if item.is_dir():
                folders.append(str(relative))
            elif item.suffix in vault.NOTE_EXTENSIONS:
                note_info = {"path": str(relative)}
                if include_metadata:
                    content = item.read_text(encoding="utf-8")
                    fm, _ = vault.parse_frontmatter(content)
                    note_info["frontmatter"] = fm
                    note_info["tags"] = vault.extract_tags(content, fm)
                notes.append(note_info)

        return {
            "success": True,
            "folder": folder or "/",
            "notes": sorted(notes, key=lambda x: x["path"]),
            "folders": sorted(folders),
        }
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to list vault: {e}", "success": False}


@mcp.tool(description="Search notes by content, title, or tags.")
def search_notes(query: str, search_content: bool = True, search_titles: bool = True, search_tags: bool = True, max_results: int = 50) -> dict:
    try:
        vault = _get_vault()
        results = []
        query_lower = query.lower()

        for note_path in vault.vault_path.rglob("*.md"):
            if any(part.startswith(".") for part in note_path.parts):
                continue

            relative = note_path.relative_to(vault.vault_path)
            matches = []

            if search_titles and query_lower in note_path.stem.lower():
                matches.append({"type": "title", "match": note_path.stem})

            content = note_path.read_text(encoding="utf-8")
            fm, _ = vault.parse_frontmatter(content)

            if search_tags:
                tags = vault.extract_tags(content, fm)
                matching = [t for t in tags if query_lower in t.lower()]
                if matching:
                    matches.append({"type": "tags", "match": matching})

            if search_content and query_lower in content.lower():
                idx = content.lower().find(query_lower)
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 50)
                context = content[start:end]
                if start > 0:
                    context = "..." + context
                if end < len(content):
                    context = context + "..."
                matches.append({"type": "content", "context": context})

            if matches:
                results.append({"path": str(relative), "matches": matches})

            if len(results) >= max_results:
                break

        return {"success": True, "query": query, "results": results}
    except Exception as e:
        return {"error": f"Search failed: {e}", "success": False}


@mcp.tool(description="Read only frontmatter/metadata from a note.")
def read_metadata(path: str) -> dict:
    try:
        vault = _get_vault()
        if not path.endswith((".md", ".markdown")):
            path = f"{path}.md"

        full_path = vault.validate_path(path)
        if not full_path.exists():
            return {"error": f"Note not found: {path}", "success": False}

        content = full_path.read_text(encoding="utf-8")
        frontmatter, _ = vault.parse_frontmatter(content)
        tags = vault.extract_tags(content, frontmatter)
        stat = full_path.stat()

        return {
            "success": True,
            "path": path,
            "frontmatter": frontmatter,
            "tags": tags,
            "file_info": {"size_bytes": stat.st_size, "modified": stat.st_mtime},
        }
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to read metadata: {e}", "success": False}


@mcp.tool(description="Read an attachment (image, PDF) as base64.")
def read_attachment(path: str) -> dict:
    try:
        vault = _get_vault()
        full_path = vault.validate_path(path)

        if not full_path.exists():
            return {"error": f"Attachment not found: {path}", "success": False}
        if full_path.suffix.lower() not in vault.ATTACHMENT_EXTENSIONS:
            return {"error": f"Unsupported type: {full_path.suffix}", "success": False}

        content = full_path.read_bytes()
        mime_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"

        return {
            "success": True,
            "path": path,
            "content_base64": base64.b64encode(content).decode("utf-8"),
            "mime_type": mime_type,
            "size_bytes": len(content),
        }
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to read attachment: {e}", "success": False}


@mcp.tool(description="Write an attachment (base64 encoded) to the vault.")
def write_attachment(path: str, content_base64: str) -> dict:
    try:
        vault = _get_vault()
        full_path = vault.validate_path(path)

        if full_path.suffix.lower() not in vault.ATTACHMENT_EXTENSIONS:
            return {"error": f"Unsupported type: {full_path.suffix}", "success": False}

        content = base64.b64decode(content_base64)
        vault.ensure_parent_exists(full_path)
        full_path.write_bytes(content)

        if config.auto_sync:
            git_manager.sync(f"Added attachment: {path}")

        return {"success": True, "path": path, "size_bytes": len(content)}
    except PathSecurityError as e:
        return {"error": str(e), "success": False}
    except Exception as e:
        return {"error": f"Failed to write attachment: {e}", "success": False}


@mcp.tool(description="Sync vault with git: pull, push, sync (pull+push), status, debug")
def sync_vault(action: str = "sync") -> dict:
    try:
        if not git_manager:
            return {"error": "Git not configured", "success": False}

        if action == "pull":
            return git_manager.pull()
        elif action == "push":
            return git_manager.push("Manual sync from Poke")
        elif action == "sync":
            return git_manager.sync("Manual sync from Poke")
        elif action == "status":
            return {
                "success": True,
                "action": "status",
                "last_sync": git_manager.last_sync.isoformat() if git_manager.last_sync else None,
                "vault_path": str(git_manager.local_path),
                "branch": git_manager.branch,
            }
        elif action == "debug":
            return git_manager.get_status()
        else:
            return {"error": f"Unknown action: {action}", "success": False}
    except Exception as e:
        logger.exception("Sync failed")
        return {"error": f"Sync failed: {e}", "success": False}


@mcp.tool(description="Get server and vault status.")
def get_server_info() -> dict:
    info = {
        "server_name": "Obsidian Vault MCP Server",
        "version": "1.0.0",
        "environment": os.environ.get("ENVIRONMENT", "development"),
        "python_version": sys.version.split()[0],
        "vault_configured": config is not None,
    }
    if config:
        info["vault_path"] = config.vault_path
    return info


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port, stateless_http=True)
