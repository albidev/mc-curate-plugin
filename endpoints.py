#!/usr/bin/env python3
"""Curate plugin endpoints — HTTP request/response translation layer.

This module exports handler functions that match the names in manifest.json.
Each handler receives:
  - body: parsed JSON body (dict) or {}
  - params: urllib.parse.parse_qs result (dict of lists)
  - auth: auth context dict (unused but reserved)

Each handler returns a dict that gets JSON-serialized to the HTTP response.

The plugin loader dispatches to these functions based on the manifest.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import handlers


class PluginError(Exception):
    """Raised for user-visible errors; maps to HTTP 4xx."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# GET handlers
# ---------------------------------------------------------------------------

def listCandidates(body: Dict[str, Any], params: Dict[str, List[str]], auth: Any = None) -> Dict[str, Any]:
    """GET /api/local/candidates?status=&"""
    status = (params.get("status") or [None])[0] or None
    vault = (params.get("vault") or [None])[0] or None
    cands = handlers.list_candidates(status=status, vault=vault)
    resolved_vault = vault or next((str(candidate.get("vault_id")) for candidate in cands if candidate.get("vault_id")), None)
    resolved_vault = resolved_vault or handlers.default_vault_id()
    return {"candidates": cands, "count": len(cands), "vault": resolved_vault}


def listVaults(body: Dict[str, Any], params: Dict[str, List[str]], auth: Any = None) -> Dict[str, Any]:
    """GET /api/local/candidates/vaults"""
    vaults = handlers.list_vaults()
    default_vault = next((vault["id"] for vault in vaults if vault.get("candidate_enabled")), None)
    return {"vaults": vaults, "default_vault": default_vault}


# ---------------------------------------------------------------------------
# POST handlers
# ---------------------------------------------------------------------------

def approveCandidate(body: Dict[str, Any], params: Dict[str, List[str]], auth: Any = None) -> Dict[str, Any]:
    """POST /api/local/candidates/approve"""
    cid = body.get("id", "")
    if not cid:
        raise PluginError(400, "bad_request", "Missing id.")
    filename = body.get("filename") or None
    vault = body.get("vault") or None
    if not handlers.can_curate(vault):
        raise PluginError(403, "vault_not_curable", "Candidate mutations are disabled for this vault.")
    cand = handlers.approve(cid, vault, filename)
    if not cand:
        raise PluginError(404, "not_found", f"Candidate {cid} not found.")
    return {"success": True, "candidate": cand}


def rejectCandidate(body: Dict[str, Any], params: Dict[str, List[str]], auth: Any = None) -> Dict[str, Any]:
    """POST /api/local/candidates/reject"""
    cid = body.get("id", "")
    if not cid:
        raise PluginError(400, "bad_request", "Missing id.")
    filename = body.get("filename") or None
    reason = body.get("reason", "")
    vault = body.get("vault") or None
    if not handlers.can_curate(vault):
        raise PluginError(403, "vault_not_curable", "Candidate mutations are disabled for this vault.")
    cand = handlers.reject(cid, reason, vault, filename)
    if not cand:
        raise PluginError(404, "not_found", f"Candidate {cid} not found.")
    return {"success": True, "candidate": cand}
