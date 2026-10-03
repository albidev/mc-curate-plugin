"""Curate advisor prompt: build the request and validate the model's answer.

Pure module (stdlib only) so it is importable both by the plugin process
(tests, telemetry Python) and by ``advisor_worker`` inside the Hermes runtime.

What counts as signal is NOT defined here. It comes from two places:
- the per-vault ``signal`` / ``noise`` profile in ``curate-vaults.yaml`` (written by Albi);
- Albi's own past decisions in the same vault (``select_precedents``), which the
  prompt ranks above the profile and above the model's own taste.
The system prompt only fixes the decision mechanics (approve / merge / reject).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

PROMPT_VERSION = "2026-10-03.3"
VERDICTS = ("approve", "merge", "reject")
ADVISABLE_STATUSES = ("pending_review", "pre_approved")
HUMAN_APPROVED_STATUSES = ("applied", "approved", "promoted")
MAX_REASON_CHARS = 280
MAX_PRECEDENTS = 6
# Rejections justified by provenance (how the candidate was produced), not by
# content: the model never sees provenance, so they would teach the wrong rule.
_PROVENANCE_REASON = re.compile(r"\bartifact\b|not a daemon synthesis", re.I)

SYSTEM_PROMPT = """You are the Vault Curator for Albi's knowledge vault. A session-synthesis extractor proposed a CANDIDATE note. Your job is to separate SIGNAL from NOISE the way Albi does, and advise what he should do; he confirms with one click, so be decisive and precise.

What counts as signal is defined, in order of authority, by:
1. PAST DECISIONS BY ALBI in this vault: real verdicts, including cases where he overruled an earlier curator. When the candidate resembles one of them, decide the same way and say so.
2. The vault's SIGNAL / NOISE profile.
3. Only when both are silent: your own judgement of durability and specificity.

Verdicts:
- approve: signal for this vault, and NOT already owned by an existing note. It deserves its own note.
- merge: signal, but the core idea is already owned by ONE existing note; the candidate adds something concrete (a case, a symptom, a constraint, a fix, evidence) worth appending to it.
- reject: noise for this vault, or a duplicate that adds nothing over an existing note or another pending candidate.

Rules:
1. Signal vs noise first, duplication second. A near-duplicate that carries a concrete new detail is a merge, not a reject.
2. Never approve as a separate note what an existing note already owns: that is merge (it adds detail) or reject (it adds nothing). This holds even when a PAST DECISION kept a near-duplicate as its own note; past decisions tell you what is signal, not how to file it.
3. Judge the CONTENT of existing notes, not their titles. Shared words do not mean shared concept; different words can be the same concept.
4. merge_target MUST be a node_id copied verbatim from EXISTING NOTES. If none fits, you cannot answer merge.
5. Jev gate verdict and extractor confidence are weak signals (Jev underrates principles from live sessions: 0.4-0.7 is often promotable). Overrule them when the content disagrees.
6. Use only the inputs given. Lower your confidence when the evidence is thin; do not invent facts.

Return ONLY a JSON object, no prose, no code fence:
{"verdict": "approve" | "merge" | "reject", "confidence": <0.0-1.0>, "merge_target": "<node_id>" | null, "reason": "<una o due frasi in italiano, max 200 caratteri: perché è segnale o rumore per questo vault; cita la nota esistente o la decisione passata quando ti ci appoggi>"}"""


class AdviceError(ValueError):
    """The model answer is unusable; the message is fed back for one retry."""


def note_path_from_node_id(node_id: str) -> str:
    """BDH vault node ids are ``vault:<relative note path>``."""
    return node_id.split(":", 1)[1] if node_id.startswith("vault:") else node_id


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def _extra(raw: Mapping[str, Any]) -> Dict[str, Any]:
    value = raw.get("extra")
    return value if isinstance(value, dict) else {}


def similar_pending(candidate: Mapping[str, Any], pending: Iterable[Mapping[str, Any]], limit: int = 4) -> List[Mapping[str, Any]]:
    """Other pending candidates sharing title words, most overlapping first."""
    mine = _tokens(str(candidate.get("title") or ""))
    if not mine:
        return []
    scored = []
    for other in pending:
        if other.get("candidate_id") == candidate.get("candidate_id"):
            continue
        overlap = len(mine & _tokens(str(other.get("title") or "")))
        if overlap:
            scored.append((overlap, str(other.get("title") or ""), other))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [other for _, _, other in scored[:limit]]


def precedent_of(raw: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """Turn one reviewed candidate into a decision by Albi worth showing, or None.

    Kept: approvals that overruled a curator ``reject``, rejections that overruled a
    curator ``approve``, and every rejection with a content reason. Plain agreements
    and pre-gate auto-applies carry no information about Albi's bar and are skipped.
    """
    status = str(raw.get("status") or "")
    extra = _extra(raw)
    curator = str(extra.get("curator_verdict") or "")
    reason = " ".join(str(raw.get("rejection_reason") or extra.get("rejection_reason") or "").split())
    if status in HUMAN_APPROVED_STATUSES and curator == "reject":
        note = " ".join(str(extra.get("curator_note") or "").split())[:160]
        return {"decision": "approved", "why": f"overruled the curator, who wanted to reject it: \"{note}\""}
    if status == "rejected":
        if reason and _PROVENANCE_REASON.search(reason):
            return None
        if curator == "approve":
            return {"decision": "rejected", "why": f"overruled the curator, who wanted to approve it. Reason: {reason[:200] or 'none given'}"}
        if reason:
            return {"decision": "rejected", "why": reason[:200]}
    return None


def select_precedents(candidate: Mapping[str, Any], reviewed: Iterable[Mapping[str, Any]], limit: int = MAX_PRECEDENTS) -> List[Dict[str, str]]:
    """Albi's decisions most related to this candidate, then the most recent ones.

    At most ``limit // 2`` of each decision so the examples never all point one way.
    """
    mine = _tokens(f"{candidate.get('title') or ''} {candidate.get('definition') or ''}")
    scored = []
    for raw in reviewed:
        if raw.get("candidate_id") == candidate.get("candidate_id"):
            continue
        precedent = precedent_of(raw)
        if not precedent:
            continue
        overlap = len(mine & _tokens(f"{raw.get('title') or ''} {raw.get('definition') or ''}"))
        when = str(raw.get("rejected_at") or raw.get("applied_at") or raw.get("created_at") or "")
        scored.append((overlap, when, {**precedent, "title": str(raw.get("title") or ""),
                                        "definition": str(raw.get("definition") or "")[:220]}))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    cap = max(1, limit // 2)
    picked: List[Dict[str, str]] = []
    per_kind: Dict[str, int] = {}
    for _, _, precedent in scored:
        if per_kind.get(precedent["decision"], 0) >= cap:
            continue
        per_kind[precedent["decision"]] = per_kind.get(precedent["decision"], 0) + 1
        picked.append(precedent)
        if len(picked) >= limit:
            break
    return picked


def _profile_lines(label: str, items: Sequence[str]) -> List[str]:
    return [f"{label}:"] + ([f"- {item}" for item in items] if items else ["- (not defined)"])


def build_user_prompt(
    *,
    vault_id: str,
    description: str,
    candidate: Mapping[str, Any],
    notes: Sequence[Mapping[str, Any]],
    siblings: Sequence[Mapping[str, Any]],
    context_mode: str = "semantic",
    signal: Sequence[str] = (),
    noise: Sequence[str] = (),
    precedents: Sequence[Mapping[str, str]] = (),
) -> str:
    extra = _extra(candidate)
    raw_provenance = candidate.get("provenance")
    provenance: Dict[str, Any] = raw_provenance if isinstance(raw_provenance, dict) else {}
    source_notes = [str(s) for s in (provenance.get("source_notes") or []) if s]
    jev = (f"{extra['jev_choice']} {extra.get('jev_confidence') or ''}".strip()
           if extra.get("jev_choice") else "not classified")
    lines = [f"VAULT: {vault_id} — {description}" if description else f"VAULT: {vault_id}", ""]
    lines += _profile_lines("SIGNAL in this vault", signal)
    lines += _profile_lines("NOISE in this vault", noise)
    lines += ["", "PAST DECISIONS BY ALBI IN THIS VAULT (most related first)"]
    if precedents:
        for p in precedents:
            lines.append(f"- {p['decision'].upper()}: {p['title']} — {p['definition']}")
            lines.append(f"    why: {p['why']}")
    else:
        lines.append("- none recorded yet")
    lines += [
        "",
        "CANDIDATE",
        f"title: {candidate.get('title') or ''}",
        f"definition: {candidate.get('definition') or ''}",
        f"extractor_confidence: {candidate.get('confidence') or '?'}",
        f"source_notes (activated during extraction): {', '.join(source_notes) or 'none'}",
        f"jev_gate: {jev}",
        "",
        "EXISTING NOTES (top semantic matches in this vault)" if context_mode == "semantic"
        else "EXISTING NOTES (title matches in this vault; semantic search unavailable)",
    ]
    if notes:
        for index, note in enumerate(notes, 1):
            lines.append(f"[{index}] node_id: {note['node_id']}")
            lines.append(f"    title: {note.get('title') or ''}")
            if note.get("score") is not None:
                lines.append(f"    similarity: {float(note['score']):.2f}")
            lines.append(f"    excerpt: {note.get('excerpt') or '(empty)'}")
    else:
        lines.append("- none found (merge is not possible)")
    lines += ["", "OTHER PENDING CANDIDATES IN THIS VAULT (duplicate check)"]
    if siblings:
        lines += [f"- {s.get('title') or ''}: {str(s.get('definition') or '')[:200]}" for s in siblings]
    else:
        lines.append("- none similar")
    return "\n".join(lines)


def parse_advice(raw: Optional[str], allowed_targets: Iterable[str]) -> Dict[str, Any]:
    """Validate one model answer. Raises AdviceError with a correctable message."""
    text = (raw or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AdviceError("The answer was not a JSON object. Return only the JSON object.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AdviceError(f"The JSON could not be parsed ({exc.msg}). Return only valid JSON.") from exc
    if not isinstance(data, dict):
        raise AdviceError("The answer must be a JSON object.")
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise AdviceError(f"verdict must be one of {', '.join(VERDICTS)}.")
    raw_confidence = data.get("confidence")
    try:
        if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float, str)):
            raise TypeError(raw_confidence)
        confidence = float(raw_confidence)
    except (TypeError, ValueError) as exc:
        raise AdviceError("confidence must be a number between 0 and 1.") from exc
    confidence = min(1.0, max(0.0, confidence))
    allowed = list(allowed_targets)
    target = data.get("merge_target")
    target = str(target).strip() if isinstance(target, str) and target.strip() else None
    if verdict == "merge":
        if target not in allowed:
            listed = ", ".join(allowed) or "(none)"
            raise AdviceError(
                f"merge_target {target!r} is not one of the EXISTING NOTES node_ids: {listed}. "
                "Pick one of them verbatim, or choose approve/reject.")
    else:
        target = None
    reason = " ".join(str(data.get("reason") or "").split())
    if not reason:
        raise AdviceError("reason is required.")
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[: MAX_REASON_CHARS - 1].rstrip() + "…"
    return {"verdict": verdict, "confidence": round(confidence, 2), "merge_target": target, "reason": reason}
