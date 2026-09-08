#!/usr/bin/env python3
"""Curate plugin handlers — candidate management for the nightly brain approval flow.

This module contains the business logic for the Curate plugin.
It is self-contained and can be tested independently.

Candidates are YAML-frontmatter .md files written by vault-brain-v2.py into
<vault-brain>/candidates/. Each has a status:
  pending      -> awaiting human review in Mission Control
  approved     -> human approved; enters quarantine (quarantine_until set)
  quarantined  -> approved + quarantine elapsed; ready to promote
  rejected     -> human rejected; rejection_reason is feedback for the model
  modified     -> human edited content, then approved

Quarantine is configurable (default 1 day) via VB_QUARANTINE_DAYS.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# When loaded as an external plugin, hermes_paths may not be on sys.path.
# The loader adds MC's server/ dir to sys.path before importing plugins.
try:
    from hermes_paths import get_hermes_home, hermes_vault_dir
except ImportError:
    # Fallback: compute paths directly for standalone usage
    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))

    def hermes_vault_dir() -> Path:
        return get_hermes_home() / "vault"


def _vault_brain_dir() -> Path:
    return get_hermes_home() / "vault-brain"


def _default_candidates_dir() -> Path:
    return _vault_brain_dir() / "candidates"


def _session_synthesis_proxy():
    """Return the Mission Control proxy for BDH-owned session candidates."""
    try:
        import synthesis_activity_proxy
    except ImportError:
        return None
    return synthesis_activity_proxy


def _normalize_session_synthesis_candidate(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt BDH's candidate contract to the Curate card contract."""
    provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
    source_notes = provenance.get("source_notes")
    if not isinstance(source_notes, list):
        source_notes = []
    return _json_safe({
        "id": str(raw.get("candidate_id") or ""),
        "candidate_id": str(raw.get("candidate_id") or ""),
        "synthesis_id": str(raw.get("synthesis_id") or ""),
        "session_id": str(raw.get("session_id") or ""),
        "vault_id": str(raw.get("vault_id") or "core"),
        "source": str(raw.get("source") or "session_synthesis"),
        "title": str(raw.get("title") or ""),
        "body": str(raw.get("definition") or ""),
        "description": str(raw.get("definition") or ""),
        "confidence": raw.get("confidence") or "",
        "status": str(raw.get("status") or "pending_review"),
        "created": raw.get("created_at"),
        "created_at": raw.get("created_at"),
        "sources": source_notes,
        "provenance": provenance,
        "extra": raw.get("extra") if isinstance(raw.get("extra"), dict) else {},
    })


def _load_session_synthesis_candidate(candidate_id: str, vault: Optional[str]):
    proxy = _session_synthesis_proxy()
    if proxy is None or (vault not in (None, "core")):
        return None, None
    candidate = proxy.get_synthesis_candidate(candidate_id, vault_id=vault or "core")
    return candidate, proxy


def _vaults_file() -> Path:
    return _vault_brain_dir() / "curate-vaults.yaml"


def _routing_file() -> Path:
    return get_hermes_home() / "vault-routing.yaml"


DEFAULT_CANDIDATES_DIR = _default_candidates_dir()
DEFAULT_QUARANTINE_DAYS = float(os.environ.get("VB_QUARANTINE_DAYS", "1"))


def _load_vaults() -> Dict[str, Dict[str, Any]]:
    """Load the local candidate map used by Curate."""
    path = _vaults_file()
    if not path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {str(k): dict(v) for k, v in (data.get("vaults") or {}).items()}
    except Exception:
        return {}


def _load_routing_vaults() -> Dict[str, Dict[str, Any]]:
    """Load the broader evidence-routing registry, if configured."""
    path = _routing_file()
    if not path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {str(k): dict(v) for k, v in (data.get("vaults") or {}).items()}
    except Exception:
        return {}


def _candidates_dir(vault: Optional[str] = None) -> Optional[Path]:
    """Resolve a candidate directory without falling back to Core for known
    non-candidate vaults."""
    if vault and vault != "core":
        mapping = _load_vaults().get(vault)
        if mapping and mapping.get("candidates_dir"):
            return Path(os.path.expanduser(str(mapping["candidates_dir"])))
        return None
    return Path(os.environ.get("VB_CANDIDATES", str(DEFAULT_CANDIDATES_DIR)))


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _display_vault_label(vault_id: str) -> str:
    return vault_id.replace("-", " ").title()


def list_vaults() -> List[Dict[str, Any]]:
    """Return every configured routing vault plus candidate capabilities."""
    candidate_map = _load_vaults()
    routing_map = _load_routing_vaults()
    vault_ids = list(dict.fromkeys(["core", *routing_map.keys(), *candidate_map.keys()]))
    out: List[Dict[str, Any]] = []
    for vid in vault_ids:
        candidate_config = candidate_map.get(vid) or {}
        routing_config = routing_map.get(vid) or {}
        routes = routing_config.get("routes")
        routes = routes if isinstance(routes, dict) else {}
        candidate_enabled = vid == "core" or bool(candidate_config.get("candidates_dir"))
        writable = _as_bool(routing_config.get("writable"), default=True)
        candidate_dir = _candidates_dir(vid) if candidate_enabled else None
        candidates = list_candidates(vault=vid) if candidate_enabled else []
        review_enabled = "review_inbox" in routes
        if candidate_enabled:
            mode = "candidates"
        elif not writable:
            mode = "read_only"
        elif review_enabled:
            mode = "review_only"
        else:
            mode = "storage_only"
        out.append({
            "id": vid,
            "label": candidate_config.get("label") or routing_config.get("name") or _display_vault_label(vid),
            "candidates_dir": str(candidate_dir) if candidate_dir else "",
            "candidate_enabled": candidate_enabled,
            "review_enabled": review_enabled,
            "writable": writable,
            "read_only": not writable,
            "mode": mode,
            "candidate_count": len(candidates),
            "pending_count": sum(1 for c in candidates if c.get("status") in {"pending", "pending_review"}),
            "reviewed_count": sum(1 for c in candidates if c.get("status") not in {"pending", "pending_review"}),
        })
    return out


def can_curate(vault: Optional[str] = None) -> bool:
    """Return whether approve/reject mutations are allowed for a vault."""
    vault_id = vault or "core"
    if _candidates_dir(vault_id) is None:
        return False
    routing_config = _load_routing_vaults().get(vault_id) or {}
    return _as_bool(routing_config.get("writable"), default=True)


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse YAML frontmatter, with a conservative fallback for legacy files."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    raw = parts[1]
    try:
        import yaml
        parsed = yaml.safe_load(raw) or {}
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
    except Exception:
        pass

    # Some older candidates contain invalid quoted multiline values. Keep them
    # readable instead of dropping the whole candidate.
    meta: Dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def _clean_body(body: str) -> str:
    """Strip leftover YAML/markdown separators and per-concept headers from a
    candidate body so the UI shows readable content."""
    body = body.strip()
    body = re.sub(r"^```\s*|```\s*$", "", body)
    lines = [ln for ln in body.splitlines() if not re.match(r"^\s*#\s+\d+\.", ln)]
    body = "\n".join(lines)
    body = re.split(r"\n---(\n|$)", body)[0]
    return body.strip()


def _body_metadata(body: str) -> Dict[str, Any]:
    """Recover structured fields when the generator emitted YAML in the body."""
    candidate = body.strip().strip("`").strip()
    try:
        import yaml
        parsed = yaml.safe_load(candidate)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return {str(k): v for k, v in parsed[0].items()}
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
    except Exception:
        pass
    return {}


_SOURCE_PATH_CACHE: Dict[str, Optional[Path]] = {}
_SOURCE_INDEX: Optional[Dict[str, Path]] = None
_SOURCE_INDEX_ROOT: Optional[Path] = None
_SOURCE_NOTE_CACHE: Dict[str, Dict[str, Any]] = {}


def _source_index(vault: Path) -> Dict[str, Path]:
    """Build one basename index per vault instead of rescanning for every source."""
    global _SOURCE_INDEX, _SOURCE_INDEX_ROOT
    if _SOURCE_INDEX is not None and _SOURCE_INDEX_ROOT == vault:
        return _SOURCE_INDEX
    index: Dict[str, Path] = {}
    try:
        for candidate in vault.rglob("*.md"):
            if candidate.is_file():
                index.setdefault(candidate.name, candidate.resolve())
    except OSError:
        pass
    _SOURCE_INDEX_ROOT = vault
    _SOURCE_INDEX = index
    return index


def _source_path(source: Any) -> Optional[Path]:
    """Resolve a source reference inside the configured vault only."""
    value = str(source or "").strip().strip('"').strip("'")
    if not value:
        return None
    if value.startswith("vault:"):
        value = value[6:]
    if value in _SOURCE_PATH_CACHE:
        return _SOURCE_PATH_CACHE[value]
    vault = hermes_vault_dir().resolve()
    raw = Path(os.path.expanduser(value))
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend((vault / value, vault / "wiki" / "concepts" / Path(value).name))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and (resolved == vault or vault in resolved.parents):
                _SOURCE_PATH_CACHE[value] = resolved
                return resolved
        except OSError:
            continue
    resolved = _source_index(vault).get(Path(value).name)
    _SOURCE_PATH_CACHE[value] = resolved
    return resolved


def _read_source_note(path: Path) -> Dict[str, Any]:
    key = str(path)
    cached = _SOURCE_NOTE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"title": path.stem.replace("-", " ").title(), "body": ""}
    parsed = _parse_frontmatter(text)
    parts = text.split("---", 2)
    body = parts[2].strip() if len(parts) > 2 else text.strip()
    body = re.sub(r"^```\s*|```\s*$", "", body).strip()
    note = {"title": str(parsed.get("title") or path.stem.replace("-", " ").title()), "body": body[:12000]}
    _SOURCE_NOTE_CACHE[key] = note
    return note


def _source_notes(sources: Any) -> List[Dict[str, Any]]:
    """Load bounded context from source notes referenced by a candidate."""
    if isinstance(sources, str):
        sources = [item.strip() for item in sources.strip("[]").split(",") if item.strip()]
    if not isinstance(sources, (list, tuple)):
        return []
    notes: List[Dict[str, Any]] = []
    for source in sources:
        path = _source_path(source)
        if path is None:
            notes.append({"source": str(source), "found": False, "title": str(source), "body": ""})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        note = _read_source_note(path)
        notes.append({
            "source": str(source),
            "found": True,
            "title": note["title"],
            "path": str(path),
            "body": note["body"],
        })
    return notes


def _json_safe(value: Any) -> Any:
    """Convert YAML-native scalar objects into JSON-safe values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _read_candidate(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = _parse_frontmatter(text)
    if not meta:
        return None
    parts = text.split("---", 2)
    body = _clean_body(parts[2]) if len(parts) > 2 else ""
    structured = _body_metadata(body)
    for key in ("type", "tags", "confidence", "sources", "description"):
        if key not in meta and key in structured:
            meta[key] = structured[key]
    meta["sourceNotes"] = _source_notes(meta.get("sources") or structured.get("sources"))
    meta["_path"] = str(path)
    meta["_filename"] = path.name
    meta["body"] = body
    return _json_safe(meta)


def _write_candidate(path: Path, meta: Dict[str, Any], body: str) -> None:
    lines = ["---"]
    for k, v in meta.items():
        if k.startswith("_"):
            continue
        if v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f'{k}: "{v}"')
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def list_candidates(status: Optional[str] = None, vault: Optional[str] = None) -> List[Dict[str, Any]]:
    # Core session_synthesis candidates are owned by BDH, not the legacy
    # vault-brain markdown queue. Keep non-core/nightly candidates on the old
    # path until their producers migrate too.
    if vault in (None, "core"):
        proxy = _session_synthesis_proxy()
        if proxy is not None:
            try:
                snapshot = proxy.load_synthesis_candidates(vault_id="core", status=status)
                raw_candidates = snapshot.get("candidates", []) if isinstance(snapshot, dict) else []
                return [
                    _normalize_session_synthesis_candidate(candidate)
                    for candidate in raw_candidates
                    if isinstance(candidate, dict)
                ]
            except Exception:
                # BDH may be restarting; preserve the legacy queue as a
                # read-only fallback rather than blanking Curate entirely.
                pass

    d = _candidates_dir(vault)
    if d is None or not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        c = _read_candidate(p)
        if c and (status is None or c.get("status") == status):
            out.append(c)
    return out


def _find_by_id(cid: str, vault: Optional[str] = None, filename: Optional[str] = None) -> Optional[Path]:
    d = _candidates_dir(vault)
    if d is None or not d.exists():
        return None
    if filename:
        exact = d / Path(filename).name
        if exact.is_file():
            c = _read_candidate(exact)
            if c and c.get("id") == cid:
                return exact
    for p in d.glob("*.md"):
        c = _read_candidate(p)
        if c and c.get("id") == cid:
            return p
    return None


def _quarantine_delta(vault: Optional[str] = None) -> timedelta:
    """Quarantine window for a vault. Per-vault override (quarantine_hours in
    the local curate-vaults.yaml) wins; otherwise the global VB_QUARANTINE_DAYS
    (default 1 day)."""
    if vault and vault != "core":
        mapping = _load_vaults().get(vault) or {}
        qh = mapping.get("quarantine_hours")
        if qh is not None:
            return timedelta(hours=float(qh))
    days = float(os.environ.get("VB_QUARANTINE_DAYS", str(DEFAULT_QUARANTINE_DAYS)))
    return timedelta(days=days)


def approve(cid: str, vault: Optional[str] = None, filename: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Approve and apply a BDH session candidate, or use legacy quarantine."""
    candidate, proxy = _load_session_synthesis_candidate(cid, vault)
    if candidate is not None and proxy is not None:
        if candidate.get("status") in {"created", "merged", "applied", "noop"}:
            result = dict(candidate)
            result["status"] = "applied"
            return _normalize_session_synthesis_candidate(result)

        correlation = {
            "candidate_id": candidate.get("candidate_id", ""),
            "synthesis_id": candidate.get("synthesis_id", ""),
            "session_id": candidate.get("session_id", ""),
            "vault_id": candidate.get("vault_id") or vault or "core",
            "source": candidate.get("source") or "session_synthesis",
        }
        if candidate.get("status") != "approved":
            proxy.approve_synthesis_candidate(**correlation)
        applied = proxy.apply_synthesis_candidate(**correlation)
        result = dict(candidate)
        result.update(applied if isinstance(applied, dict) else {})
        result["status"] = str(result.get("status") or "applied")
        return _normalize_session_synthesis_candidate(result)

    p = _find_by_id(cid, vault, filename)
    if not p:
        return None
    c = _read_candidate(p)
    if not c:
        return None
    until = (datetime.now(timezone.utc) + _quarantine_delta(vault)).isoformat()
    c["status"] = "approved"
    c["approved_at"] = datetime.now(timezone.utc).isoformat()
    c["quarantine_until"] = until
    _write_candidate(p, c, c.get("body", ""))
    return _read_candidate(p)


def reject(cid: str, reason: str = "", vault: Optional[str] = None, filename: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Reject a BDH session candidate into local Curate feedback, or legacy file."""
    candidate, proxy = _load_session_synthesis_candidate(cid, vault)
    if candidate is not None and proxy is not None:
        rejection_store = getattr(proxy, "session_synthesis_rejections", None)
        if rejection_store is None:
            import session_synthesis_rejections as rejection_store
        rejection_store.record_rejection(
            candidate_id=str(candidate.get("candidate_id") or cid),
            vault_id=str(candidate.get("vault_id") or vault or "core"),
            synthesis_id=str(candidate.get("synthesis_id") or ""),
            session_id=str(candidate.get("session_id") or ""),
            title=str(candidate.get("title") or ""),
            reason=reason,
        )
        result = dict(candidate)
        result["status"] = "rejected"
        result["rejection_reason"] = reason
        return _normalize_session_synthesis_candidate(result)

    p = _find_by_id(cid, vault, filename)
    if not p:
        return None
    c = _read_candidate(p)
    if not c:
        return None
    c["status"] = "rejected"
    c["rejected_at"] = datetime.now(timezone.utc).isoformat()
    c["rejection_reason"] = reason
    _write_candidate(p, c, c.get("body", ""))
    return _read_candidate(p)


def _all_candidate_dirs() -> List[Path]:
    """All configured candidate dirs to scan for promote/rejection-feedback."""
    default = _candidates_dir(None)
    dirs = [default] if default is not None else []
    for m in _load_vaults().values():
        if m.get("candidates_dir"):
            d = Path(os.path.expanduser(str(m["candidates_dir"])))
            if d not in dirs:
                dirs.append(d)
    return dirs


def _append_source_wikilinks(body: str) -> str:
    """Append a 'Sources' section of [[wikilinks]] derived from the body's
    `sources:` YAML entries, so the BDH graph creates edges from the promoted
    concept to the vault nodes that generated it.

    Only `vault:` sources map to vault notes (external: sources are repo/docs
    outside the vault and have no vault node to link). The wikilink target is
    the path after `vault:` with the `.md` stripped, e.g.
    `vault:wiki/entities/foo.md` -> `[[wiki/entities/foo]]`.
    """
    if not body or "[[wiki/" in body:
        return body  # already has wikilinks
    sources = re.findall(r"^\s*-\s*[\"']?vault:([^\s\"']+\.md)[\"']?\s*$", body, re.MULTILINE)
    if not sources:
        return body
    links = []
    for src in sources:
        target = src[:-3] if src.endswith(".md") else src  # strip .md
        links.append(f"- [[{target}]]")
    if not links:
        return body
    return body.rstrip() + "\n\n## Sources\n" + "\n".join(links) + "\n"


def promote_ready() -> List[Dict[str, Any]]:
    """Promote candidates whose quarantine has elapsed (status approved +
    quarantine_until <= now) to their vault's wiki/concepts. Scans every
    candidate dir (default + per-vault) and writes to the vault_dir for that
    vault. Returns promoted."""
    now = datetime.now(timezone.utc)
    promoted = []

    mapping = _load_vaults()

    def vault_target(vault_id: str) -> Path:
        m = mapping.get(vault_id) or {}
        if m.get("vault_dir"):
            return Path(os.path.expanduser(str(m["vault_dir"])))
        return Path(os.environ.get("VB_VAULT", str(hermes_vault_dir())))

    targets = [(str(_candidates_dir(None)), vault_target("core"))]
    for vid, m in mapping.items():
        if vid == "core":
            continue
        if m.get("candidates_dir"):
            targets.append((m["candidates_dir"], vault_target(vid)))

    for cand_dir, vault in targets:
        d = Path(os.path.expanduser(str(cand_dir)))
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            c = _read_candidate(p)
            if not c or c.get("status") != "approved":
                continue
            q = c.get("quarantine_until")
            if not q:
                continue
            try:
                qdt = datetime.fromisoformat(q)
            except ValueError:
                continue
            if qdt <= now:
                concepts_dir = vault / "wiki" / "concepts"
                concepts_dir.mkdir(parents=True, exist_ok=True)
                slug = re.sub(r"[^a-z0-9]+", "-", (c.get("title") or "concept").lower()).strip("-")
                dest = concepts_dir / f"{slug}.md"
                body = c.get("body", "")
                body = _append_source_wikilinks(body)
                dest.write_text(body + "\n", encoding="utf-8")
                c["status"] = "promoted"
                c["promoted_at"] = now.isoformat()
                _write_candidate(p, c, body)
                promoted.append(c)
    return promoted


def vault_dirs_with_promotions() -> list:
    """Return the vault_dir of every vault whose candidate dir currently holds
    at least one promoted candidate. Used by the promote cron to know which
    vault repos need a commit+push."""
    promoted_dirs = set()
    mapping = _load_vaults()
    for vid, m in mapping.items():
        cand_dir = m.get("candidates_dir")
        if not cand_dir:
            continue
        d = Path(os.path.expanduser(str(cand_dir)))
        if not d.exists():
            continue
        has_promoted = any(
            (_read_candidate(p) or {}).get("status") == "promoted"
            for p in d.glob("*.md")
        )
        if has_promoted:
            promoted_dirs.add(vault_dir_for(vid))
    return sorted(promoted_dirs)


def vault_dir_for(vault_id: str) -> Path:
    """Resolve the vault_dir for a vault id (default core -> VB_VAULT)."""
    mapping = _load_vaults()
    m = mapping.get(vault_id) or {}
    if m.get("vault_dir"):
        return Path(os.path.expanduser(str(m["vault_dir"])))
    return Path(os.environ.get("VB_VAULT", str(hermes_vault_dir())))


def rejection_feedback() -> str:
    """Collect rejection_reason from rejected candidates as human feedback
    for the model's next run. Scans every candidate dir (default + per-vault)."""
    reasons = []
    for vault in _load_vaults():
        for c in list_candidates(status="rejected", vault=vault):
            r = c.get("rejection_reason", "").strip()
            if r:
                reasons.append(f"- {c.get('title', c.get('id'))}: {r}")
    for c in list_candidates(status="rejected"):
        r = c.get("rejection_reason", "").strip()
        if r:
            reasons.append(f"- {c.get('title', c.get('id'))}: {r}")
    return "\n".join(reasons)
