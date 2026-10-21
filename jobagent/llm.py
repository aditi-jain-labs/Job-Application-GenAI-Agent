"""Claude through the `claude -p` CLI, so calls use the Claude subscription rather than an API key.

Job descriptions are untrusted text, so every call runs with no tools, no MCP servers and no
settings, from an empty working directory. Replies are forced into a JSON schema.
Results are cached by a hash of the full request.
"""
import hashlib
import json
import shutil
import subprocess
import threading

from .settings import CACHE_DIR

WORKDIR = CACHE_DIR / "claude_cwd"
RESULTS = CACHE_DIR / "claude"
USE_CACHE = True                                # cache_llm_results in config.yaml
CALLS = {"made": 0, "cached": 0}                # for the end of run summary
_lock = threading.Lock()


class LLMError(Exception):
    pass


def _count(kind: str) -> None:
    with _lock:
        CALLS[kind] += 1


def ask(prompt: str, system: str, schema: dict, model: str, timeout: int = 600, use_cache: bool | None = None) -> dict:
    if not shutil.which("claude"):
        raise LLMError("the claude CLI is not on PATH")
    key = hashlib.sha256(json.dumps([prompt, system, schema, model]).encode()).hexdigest()[:24]
    cached = RESULTS / f"{key}.json"
    if (USE_CACHE if use_cache is None else use_cache) and cached.exists():
        _count("cached")
        return json.loads(cached.read_text())

    WORKDIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "claude", "-p",
        "--model", model,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", system,
        "--json-schema", json.dumps(schema),
    ]
    last = ""
    for _ in range(2):
        _count("made")
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=WORKDIR, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
            continue
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last = (proc.stderr or proc.stdout)[-500:]
            continue
        if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
            last = str(out.get("result") or out.get("subtype"))[-500:]
            continue
        result = out["structured_output"]
        RESULTS.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(result))
        return result
    raise LLMError(last)
