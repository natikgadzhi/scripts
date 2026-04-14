#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic"]
# ///
"""
claude-session-export: Extract Claude Code session metadata into markdown.

Runs as a SessionEnd hook. Parses the session transcript JSONL, extracts
structured metadata, writes a markdown note to a staging directory
(~/.local/share/claude-sessions/), then calls the Anthropic API to generate
a summary and edits it into the note. obsidian-tools then syncs from the
staging directory into the Obsidian vault.

Can also be invoked manually:
    claude-session-export /path/to/transcript.jsonl [--project-path /path]
"""

import json
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path.home() / ".local" / "share" / "claude-sessions"
MAX_TRANSCRIPT_CHARS = 100_000
MIN_MESSAGES = 2


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------
def parse_transcript(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL transcript into a list of entries."""
    entries: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------
def _parse_timestamp(raw: Any) -> datetime | None:
    """Parse a timestamp that may be an ISO 8601 string or epoch millis."""
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def extract_metadata(
    entries: list[dict[str, Any]],
    session_id: str,
    cwd: str,
    transcript_path: str,
) -> dict[str, Any]:
    """Pull structured metadata out of transcript entries."""
    tools_used: set[str] = set()
    skills_used: set[str] = set()
    agent_types: set[str] = set()
    files_modified: set[str] = set()
    user_messages: list[str] = []
    assistant_texts: list[str] = []
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    for entry in entries:
        etype = entry.get("type")
        ts_raw = entry.get("timestamp")
        if ts_raw is not None:
            ts = _parse_timestamp(ts_raw)
            if ts is not None:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

        if etype == "user":
            _collect_user(entry, user_messages)
        elif etype == "assistant":
            _collect_assistant(
                entry, assistant_texts, tools_used, skills_used,
                agent_types, files_modified,
            )

    project_name = Path(cwd).name if cwd else "unknown"
    transcript_bytes = Path(transcript_path).read_bytes()
    content_hash = f"sha256:{hashlib.sha256(transcript_bytes).hexdigest()}"

    duration = None
    if first_ts and last_ts:
        delta = (last_ts - first_ts).total_seconds()
        duration = round(delta / 60)

    now = datetime.now(timezone.utc)

    return {
        "session_id": session_id,
        "project_name": project_name,
        "project_path": cwd,
        "transcript_path": transcript_path,
        "synced_at": now.isoformat(),
        "content_hash": content_hash,
        "date": now.strftime("%Y-%m-%d"),
        "started_at": first_ts.isoformat() if first_ts else now.isoformat(),
        "duration_minutes": duration,
        "tools_used": sorted(tools_used),
        "skills_used": sorted(skills_used),
        "agent_types": sorted(agent_types),
        "files_modified": sorted(files_modified),
        "user_messages": user_messages,
        "assistant_texts": assistant_texts,
        "message_count": len(user_messages),
    }


def _collect_user(entry: dict, user_messages: list[str]) -> None:
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, str):
        user_messages.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                user_messages.append(block["text"])


def _collect_assistant(
    entry: dict,
    assistant_texts: list[str],
    tools_used: set[str],
    skills_used: set[str],
    agent_types: set[str],
    files_modified: set[str],
) -> None:
    content = entry.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            assistant_texts.append(block["text"])
        elif btype == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", {})
            tools_used.add(name)

            if name == "Skill":
                skill = inp.get("skill", "")
                if skill:
                    skills_used.add(skill)
            elif name in ("Write", "Edit"):
                fp = inp.get("file_path", "")
                if fp:
                    files_modified.add(fp)
            elif name == "Agent":
                at = inp.get("subagent_type", inp.get("description", ""))
                if at:
                    agent_types.add(at)


# ---------------------------------------------------------------------------
# Condensed transcript for agent hook
# ---------------------------------------------------------------------------
def build_condensed_transcript(entries: list[dict[str, Any]]) -> str:
    """Build a human-readable transcript for summarization."""
    parts: list[str] = []
    for entry in entries:
        etype = entry.get("type")
        if etype == "user":
            texts = _text_blocks(entry)
            if texts:
                parts.append(f"**User**: {' '.join(texts)}")
        elif etype == "assistant":
            texts = _text_blocks(entry)
            tools = _tool_names(entry)
            pieces: list[str] = []
            if texts:
                pieces.append(" ".join(texts))
            if tools:
                pieces.append(f"[Used tools: {', '.join(tools)}]")
            if pieces:
                parts.append(f"**Assistant**: {' | '.join(pieces)}")

    transcript = "\n\n".join(parts)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        half = MAX_TRANSCRIPT_CHARS // 2
        transcript = (
            transcript[:half]
            + "\n\n[... transcript truncated ...]\n\n"
            + transcript[-half:]
        )
    return transcript


def _text_blocks(entry: dict) -> list[str]:
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, str):
        return [content] if content.strip() else []
    if isinstance(content, list):
        return [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
        ]
    return []


def _tool_names(entry: dict) -> list[str]:
    content = entry.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return []
    return [
        b["name"]
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


# ---------------------------------------------------------------------------
# Obsidian note writing
# ---------------------------------------------------------------------------
def yaml_value(v: Any) -> str:
    """Render a value for YAML frontmatter."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in ":{}[]#&*!|>'\",@`") or s.startswith(("~", "-", " ")):
        return f'"{s}"'
    return s


def render_frontmatter(fields: dict[str, Any]) -> str:
    """Render a dict as YAML frontmatter block."""
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {yaml_value(item)}")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def write_note(meta: dict[str, Any], entries: list[dict[str, Any]]) -> Path:
    """Write the Obsidian note (without summary) and return its path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    short_id = meta["session_id"][:8]
    filename = f"{meta['date']}-{meta['project_name']}-{short_id}.md"
    filepath = OUTPUT_DIR / filename

    # Session-specific frontmatter only. obsidian-tools adds synced_from,
    # synced_at, content_hash, sync_source during vault sync.
    fm = render_frontmatter({
        "session_id": meta["session_id"],
        "transcript": meta["transcript_path"],
        "project": meta["project_name"],
        "project_path": meta["project_path"],
        "date": meta["date"],
        "started_at": meta["started_at"],
        "duration_minutes": meta["duration_minutes"],
        "tools_used": meta["tools_used"],
        "skills_used": meta["skills_used"],
        "agent_types": meta["agent_types"],
        "files_modified": meta["files_modified"],
        "tags": ["claude-session", meta["project_name"]],
    })

    body_parts: list[str] = []

    # Info callout
    tools_count = len(meta["tools_used"])
    body_parts.append(
        f"> [!info] Claude Code Session\n"
        f"> **Project**: `{meta['project_path']}`\n"
        f"> **Date**: {meta['date']} | "
        f"**Duration**: ~{meta['duration_minutes'] or '?'}m | "
        f"**Tools**: {tools_count} distinct | "
        f"**Messages**: {meta['message_count']}"
    )

    # Placeholder for agent hook to fill in
    body_parts.append(
        "## Summary\n"
        "<!-- SUMMARY_PLACEHOLDER -->\n"
        "*Summary pending — will be generated by Claude Code agent hook.*"
    )

    # User prompts for quick scanning
    if meta["user_messages"]:
        body_parts.append("## User Prompts")
        for i, msg in enumerate(meta["user_messages"], 1):
            display = msg[:500] + "..." if len(msg) > 500 else msg
            display = display.replace("\n", "\n  ")
            body_parts.append(f"{i}. {display}")

    body = "\n\n".join(body_parts)
    filepath.write_text(f"{fm}\n\n{body}\n")
    return filepath


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """\
A Claude Code session just ended. Below is a condensed transcript.

Generate a structured summary with these sections (omit any that have nothing noteworthy):
- **Goal**: What was the user trying to accomplish? (1-2 sentences)
- **Outcome**: What was accomplished? Was the goal met? (1-3 sentences)
- **Key Decisions**: Important decisions and rationale — architectural choices, tool selections, trade-offs
- **Problems & Resolutions**: Bugs, errors, wrong approaches — what happened and how it was resolved
- **Learnings**: Non-obvious insights, patterns, techniques discovered
- **Skills & Tools Assessment**: How tools/skills performed — issues, limitations, effective combinations

Use bullet points. Be concise — this is a reference document, not a narrative. No preamble.

---

{transcript}
"""


def generate_summary(transcript: str, note_path: Path) -> None:
    """Call the Anthropic API to generate a summary and edit it into the note."""
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript)}],
    )
    summary = message.content[0].text.strip()

    note_text = note_path.read_text()
    note_text = note_text.replace(
        "<!-- SUMMARY_PLACEHOLDER -->\n*Summary pending — will be generated by Claude Code agent hook.*",
        summary,
    )
    note_path.write_text(note_text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # Manual mode: pass transcript path as CLI arg
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        transcript_path = sys.argv[1]
        project_path = ""
        if "--project-path" in sys.argv:
            idx = sys.argv.index("--project-path")
            if idx + 1 < len(sys.argv):
                project_path = sys.argv[idx + 1]

        if not Path(transcript_path).exists():
            print(f"File not found: {transcript_path}", file=sys.stderr)
            sys.exit(1)

        session_id = Path(transcript_path).stem
        cwd = project_path or str(Path.cwd())
    else:
        # Hook mode: read JSON from stdin
        try:
            raw = sys.stdin.read()
            if not raw.strip():
                sys.exit(0)
            hook_input = json.loads(raw)
        except (json.JSONDecodeError, EOFError):
            print("Invalid hook input on stdin", file=sys.stderr)
            sys.exit(0)

        session_id = hook_input.get("session_id", "unknown")
        transcript_path = hook_input.get("transcript_path", "")
        cwd = hook_input.get("cwd", "")

        if not transcript_path or not Path(transcript_path).exists():
            print(f"Transcript not found: {transcript_path}", file=sys.stderr)
            sys.exit(0)

    # Parse
    entries = parse_transcript(Path(transcript_path))
    if not entries:
        print("Empty transcript, skipping", file=sys.stderr)
        sys.exit(0)

    # Extract metadata
    meta = extract_metadata(entries, session_id, cwd, transcript_path)

    # Skip very short sessions
    if meta["message_count"] < MIN_MESSAGES:
        print("Session too short, skipping export", file=sys.stderr)
        sys.exit(0)

    # Write Obsidian note (without summary)
    filepath = write_note(meta, entries)
    print(f"Exported session {session_id[:8]} → {filepath}", file=sys.stderr)

    # Generate summary via Anthropic API and edit it into the note
    transcript_text = build_condensed_transcript(entries)
    try:
        generate_summary(transcript_text, filepath)
        print("Summary generated and written to note", file=sys.stderr)
    except Exception as e:
        print(f"Summary generation failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
