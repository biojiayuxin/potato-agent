#!/usr/bin/env python3
"""Query Arabidopsis genes via TAIR + PlantConnectome.

Workflow:
1. Use TAIR public search API to resolve gene symbol/alias/AGI ID.
2. If multiple exact candidates exist, return ambiguous status for user confirmation.
3. Use confirmed TAIR AGI ID to query PlantConnectome KG relationships and PMIDs.

Only Python standard library is required.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import html
import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

TAIR_API_BASE = "https://www.arabidopsis.org/api"
PLANTCONNECTOME_BASE = "https://plant.connectome.tools"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


class QueryError(RuntimeError):
    """Base class for failures that should stop the current source query."""


class QueryDeadlineExceeded(QueryError):
    """The wall-clock deadline for the query was exhausted."""


class ResponseTooLarge(QueryError):
    """An HTTP response exceeded the configured size limit."""


class ResponseParseError(QueryError):
    """A remote response no longer matches the expected data structure."""


@dataclass(frozen=True)
class HttpPolicy:
    timeout: float
    retries: int
    retry_backoff: float
    max_response_bytes: int
    deadline_at: float

    @classmethod
    def create(
        cls,
        *,
        timeout: float,
        retries: int = DEFAULT_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        deadline: Optional[float] = None,
    ) -> "HttpPolicy":
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if retries < 0:
            raise ValueError("retries must be zero or greater")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be zero or greater")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")
        if deadline is None:
            deadline = timeout * (retries + 1) + retry_backoff * (2 ** retries - 1)
        if deadline <= 0:
            raise ValueError("deadline must be greater than zero")
        return cls(
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            max_response_bytes=max_response_bytes,
            deadline_at=time.monotonic() + deadline,
        )

    def remaining(self) -> float:
        remaining = self.deadline_at - time.monotonic()
        if remaining <= 0:
            raise QueryDeadlineExceeded("query wall-clock deadline exceeded")
        return remaining


def _is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_HTTP_CODES
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return reason.errno == socket.EAI_AGAIN
        return isinstance(
            reason,
            (
                TimeoutError,
                socket.timeout,
                ConnectionError,
                ConnectionResetError,
                ConnectionAbortedError,
            ),
        )
    return isinstance(
        exc,
        (
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            ConnectionAbortedError,
            http.client.IncompleteRead,
        ),
    )


def _sleep_before_retry(policy: HttpPolicy, attempt: int, exc: BaseException) -> None:
    delay = policy.retry_backoff * (2 ** attempt)
    remaining = policy.remaining()
    if delay >= remaining:
        raise QueryDeadlineExceeded(
            "query wall-clock deadline would be exceeded before the next retry"
        ) from exc
    if delay:
        time.sleep(delay)


def _with_retries(operation: Any, policy: HttpPolicy) -> Any:
    for attempt in range(policy.retries + 1):
        policy.remaining()
        try:
            return operation()
        except Exception as exc:
            if attempt >= policy.retries or not _is_transient_error(exc):
                raise
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            _sleep_before_retry(policy, attempt, exc)
    raise AssertionError("unreachable")


def _set_response_timeout(response: Any, timeout: float) -> None:
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(timeout)


def _read_response(response: Any, *, policy: HttpPolicy, url: str) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > policy.max_response_bytes:
                raise ResponseTooLarge(
                    f"response from {url} declares {content_length} bytes; "
                    f"limit is {policy.max_response_bytes}"
                )
        except ValueError:
            pass

    body = bytearray()
    read_chunk = getattr(response, "read1", response.read)
    while True:
        remaining = policy.remaining()
        _set_response_timeout(response, max(0.001, min(policy.timeout, remaining)))
        chunk = read_chunk(READ_CHUNK_SIZE)
        policy.remaining()
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > policy.max_response_bytes:
            raise ResponseTooLarge(
                f"response from {url} exceeded {policy.max_response_bytes} bytes"
            )

    content_encoding = response.headers.get("Content-Encoding", "")
    encodings = [token.strip().lower() for token in content_encoding.split(",") if token.strip()]
    decoded = bytes(body)
    for encoding in reversed(encodings):
        if encoding in {"identity"}:
            continue
        if encoding != "gzip":
            raise QueryError(f"unsupported Content-Encoding {encoding!r} from {url}")
        try:
            decoded = gzip.decompress(decoded)
        except (EOFError, OSError) as exc:
            raise ResponseParseError(f"invalid gzip response from {url}: {exc}") from exc
        if len(decoded) > policy.max_response_bytes:
            raise ResponseTooLarge(
                f"decompressed response from {url} exceeded "
                f"{policy.max_response_bytes} bytes"
            )
    policy.remaining()
    return decoded


def _request_bytes(req: urllib.request.Request, *, policy: HttpPolicy) -> Tuple[bytes, str]:
    def request_once() -> Tuple[bytes, str]:
        timeout = max(0.001, min(policy.timeout, policy.remaining()))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _read_response(resp, policy=policy, url=req.full_url)
            return body, resp.geturl()

    return _with_retries(request_once, policy)


def http_json(url: str, *, method: str = "GET", payload: Optional[dict] = None,
              timeout: int = 60, referer: str = "https://www.arabidopsis.org/",
              policy: Optional[HttpPolicy] = None) -> Any:
    policy = policy or HttpPolicy.create(timeout=timeout)
    data = None
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    body, _ = _request_bytes(req, policy=policy)
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResponseParseError(f"invalid JSON response from {url}: {exc}") from exc


def http_text(url: str, *, method: str = "GET", payload: Optional[dict] = None,
              timeout: int = 60, referer: Optional[str] = None,
              accept_gzip: bool = False,
              policy: Optional[HttpPolicy] = None) -> Tuple[str, str]:
    policy = policy or HttpPolicy.create(timeout=timeout)
    data = None
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    if accept_gzip:
        headers["Accept-Encoding"] = "gzip"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    body, final_url = _request_bytes(req, policy=policy)
    return body.decode("utf-8", "replace"), final_url


def normalize_query(q: str) -> str:
    return q.strip().upper()


def is_agi_id(q: str) -> bool:
    return bool(re.fullmatch(r"AT[1-5CM]G\d{5}(?:\.\d+)?", q.strip().upper()))


def tair_search(query: str, *, timeout: int = 60,
                policy: Optional[HttpPolicy] = None) -> Dict[str, Any]:
    data = http_json(
        f"{TAIR_API_BASE}/search/gene",
        method="POST",
        payload={"searchText": query.strip()},
        timeout=timeout,
        referer="https://www.arabidopsis.org/search/genes",
        policy=policy,
    )
    if not isinstance(data, dict):
        raise ResponseParseError("TAIR search response is not a JSON object")
    if "docs" not in data:
        raise ResponseParseError("TAIR search response is missing the docs field")
    docs = data.get("docs")
    if docs is not None and not isinstance(docs, list):
        raise ResponseParseError("TAIR search response docs field is not a list")
    return {"query": query, "total": data.get("total"), "docs": docs or [], "raw": data}


def summarize_tair_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    desc = doc.get("description") or []
    return {
        "id": doc.get("id"),
        "gene_id": (doc.get("gene_name") or [None])[0],
        "gene_model_ids": doc.get("gene_model_ids") or [],
        "other_names": doc.get("other_names") or [],
        "description": desc[0] if desc else "",
        "keywords": doc.get("keywords") or [],
        "keyword_types": doc.get("keyword_types") or [],
        "phenotypes": doc.get("phenotypes") or [],
        "gene_model_type": doc.get("gene_model_type") or [],
        "locus_tairObjectId": doc.get("locus_tairObjectId"),
        "gene_tairObjectId": doc.get("gene_tairObjectId"),
        "has_publications": doc.get("has_publications"),
        "is_obselete": doc.get("is_obselete"),
    }


def exact_matches(docs: Sequence[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    q = normalize_query(query)
    out: List[Dict[str, Any]] = []
    for d in docs:
        gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
        gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
        aliases = [str(x).upper() for x in d.get("other_names") or []]
        if q == gene_id or q in gene_models or q in aliases:
            out.append(d)
    return out


def choose_tair_candidate(query: str, docs: Sequence[Dict[str, Any]], *,
                          forced_gene_id: Optional[str] = None,
                          timeout: int = 60,
                          policy: Optional[HttpPolicy] = None) -> Dict[str, Any]:
    if forced_gene_id:
        fg = forced_gene_id.upper()
        for d in docs:
            gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
            gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
            if fg == gene_id or fg in gene_models:
                return {"status": "ok", "selected": d, "reason": "forced_gene_id matched TAIR"}
        forced_docs = tair_search(
            forced_gene_id, timeout=timeout, policy=policy
        ).get("docs", [])
        for d in forced_docs:
            gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
            gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
            if fg == gene_id or fg in gene_models:
                return {"status": "ok", "selected": d, "reason": "forced_gene_id matched TAIR"}
        return {"status": "error", "message": f"forced_gene_id {forced_gene_id} not found in TAIR results"}

    if not docs:
        return {"status": "not_found", "message": "TAIR returned no gene candidates"}

    q = normalize_query(query)
    if is_agi_id(q):
        q_gene = q.split(".")[0]
        for d in docs:
            gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
            gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
            if q_gene == gene_id or q in gene_models:
                return {"status": "ok", "selected": d, "reason": "AGI ID uniquely identifies the candidate"}

    exact = exact_matches(docs, query)
    seen = set()
    uniq_exact = []
    for d in exact:
        gid = (d.get("gene_name") or [""])[0]
        if gid not in seen:
            seen.add(gid)
            uniq_exact.append(d)
    if len(uniq_exact) == 1:
        return {"status": "ok", "selected": uniq_exact[0], "reason": "single exact TAIR alias/gene match"}
    if len(uniq_exact) > 1:
        return {"status": "ambiguous", "candidates": uniq_exact, "reason": "multiple exact TAIR alias/gene matches"}
    if len(docs) == 1:
        return {"status": "ok", "selected": docs[0], "reason": "single TAIR search result"}
    return {"status": "ambiguous", "candidates": list(docs[:10]), "reason": "multiple TAIR candidates and no unique exact match"}


def parse_preview(html_text: str) -> Tuple[Optional[str], List[List[Any]]]:
    if re.search(
        r"<h2\b[^>]*>\s*No\s+hits\s+were\s+found\s+using\s+the\s+query\s*:",
        html_text,
        re.IGNORECASE,
    ):
        return None, []
    uid_match = re.search(r'const\s+unique_id\s*=\s*"([^"]+)"', html_text)
    if not uid_match:
        raise ResponseParseError("PlantConnectome preview is missing unique_id")
    uid = uid_match.group(1)
    m = re.search(
        r"allRowsData\s*=\s*cached\s*\?\s*cached\.preview_results\s*:\s*(.*?);\s*\n\s*/\*\s*build entityNodeMap",
        html_text,
        re.S,
    )
    if not m:
        raise ResponseParseError("PlantConnectome preview is missing allRowsData")
    try:
        rows = json.loads(m.group(1).strip())
    except json.JSONDecodeError as exc:
        raise ResponseParseError(
            f"PlantConnectome preview allRowsData is invalid JSON: {exc}"
        ) from exc
    if not isinstance(rows, list):
        raise ResponseParseError("PlantConnectome preview allRowsData is not a list")
    return uid, rows


def parse_kg_edges(html_text: str) -> List[Dict[str, Any]]:
    m = re.search(r'const\s+g\s*=\s*"(.*?)";\s*\n', html_text, re.S)
    if not m:
        raise ResponseParseError("PlantConnectome detail is missing the edge payload")
    decoded = html.unescape(m.group(1))
    errors = []
    try:
        val = ast.literal_eval(decoded)
        if isinstance(val, list):
            if any(not isinstance(x, dict) for x in val):
                raise ResponseParseError("PlantConnectome edge payload contains non-object entries")
            return val
        errors.append(f"Python literal type was {type(val).__name__}")
    except ResponseParseError:
        raise
    except Exception as exc:
        errors.append(f"Python literal parse failed: {exc}")
    try:
        val = json.loads(decoded)
        if isinstance(val, list):
            if any(not isinstance(x, dict) for x in val):
                raise ResponseParseError("PlantConnectome edge payload contains non-object entries")
            return val
        errors.append(f"JSON type was {type(val).__name__}")
    except ResponseParseError:
        raise
    except Exception as exc:
        errors.append(f"JSON parse failed: {exc}")
    raise ResponseParseError("PlantConnectome edge payload could not be parsed: " + "; ".join(errors))


def plant_preview(gene_id: str, *, timeout: int = 60,
                  policy: Optional[HttpPolicy] = None) -> Dict[str, Any]:
    encoded = urllib.parse.quote(gene_id, safe="")
    url = f"{PLANTCONNECTOME_BASE}/normal/{encoded}"
    text, final_url = http_text(
        url,
        timeout=timeout,
        referer=f"{PLANTCONNECTOME_BASE}/",
        accept_gzip=True,
        policy=policy,
    )
    uid, rows = parse_preview(text)
    return {"gene_id": gene_id, "url": final_url, "unique_id": uid, "rows": rows}


def entity_result_url(search_type: str, entity: str, entity_type: str, uid: Optional[str]) -> str:
    seg = "non_alpha" if search_type == "non-alphanumeric" else search_type
    url = (
        f"{PLANTCONNECTOME_BASE}/{seg}/"
        f"{urllib.parse.quote(entity, safe='')}/results/"
        f"{urllib.parse.quote(entity_type, safe='')}"
    )
    if uid:
        url += "?uid=" + urllib.parse.quote(uid)
    return url


def plant_snippet(p_source: str, *, timeout: int = 60,
                  policy: Optional[HttpPolicy] = None) -> Dict[str, Any]:
    return http_json(
        f"{PLANTCONNECTOME_BASE}/process-text-withoutapi",
        method="POST",
        payload={"p_source": p_source},
        timeout=timeout,
        referer=f"{PLANTCONNECTOME_BASE}/",
        policy=policy,
    )


def plant_details(gene_id: str, *, max_entities: int = 3, max_edges: int = 200,
                  snippets: int = 0, timeout: int = 60,
                  policy: Optional[HttpPolicy] = None) -> Dict[str, Any]:
    policy = policy or HttpPolicy.create(timeout=timeout)
    if max_entities <= 0:
        raise ValueError("max_entities must be greater than zero")
    if max_edges <= 0:
        raise ValueError("max_edges must be greater than zero")
    if snippets < 0:
        raise ValueError("snippets must be zero or greater")
    prev = plant_preview(gene_id, timeout=timeout, policy=policy)
    uid = prev.get("unique_id")
    rows = prev.get("rows") or []
    if not rows:
        return {
            "status": "not_found",
            "message": f"PlantConnectome returned no preview entities for {gene_id}",
            "gene_id": gene_id,
            "preview": {
                "url": prev.get("url"),
                "unique_id": uid,
                "row_count": 0,
                "rows": [],
            },
            "entities": [],
            "snippets": {},
        }
    entities = []
    seen_p_sources: List[str] = []
    for row in rows[:max_entities]:
        if not isinstance(row, list) or len(row) < 2:
            raise ResponseParseError(
                "PlantConnectome preview row is not a list with entity and entity type"
            )
        entity = str(row[0])
        entity_type = str(row[1])
        url = entity_result_url("normal", entity, entity_type, uid)
        detail_html, final_url = http_text(
            url,
            timeout=timeout,
            referer=prev.get("url") or None,
            accept_gzip=True,
            policy=policy,
        )
        edges = parse_kg_edges(detail_html)
        kept_edges = edges[:max_edges]
        for e in kept_edges:
            ps = e.get("p_source")
            if ps and ps not in seen_p_sources:
                seen_p_sources.append(ps)
        entities.append({
            "preview_row": row,
            "entity": entity,
            "entity_type": entity_type,
            "url": final_url,
            "edge_count_total": len(edges),
            "edges": kept_edges,
            "pmids": sorted({str(e.get("publication")) for e in edges if e.get("publication")}),
            "relation_counts": Counter(str(e.get("inter_type") or e.get("edge_disamb") or "") for e in edges).most_common(20),
        })
    snippet_map = {}
    if snippets > 0:
        for ps in seen_p_sources[:snippets]:
            snippet_map[ps] = plant_snippet(ps, timeout=timeout, policy=policy)
    if not entities:
        raise ResponseParseError("PlantConnectome preview contained no usable entity rows")
    if not any(entity.get("edge_count_total", 0) for entity in entities):
        status = "not_found"
        message = f"PlantConnectome returned no knowledge-graph edges for {gene_id}"
    else:
        status = "ok"
        message = ""
    return {
        "status": status,
        **({"message": message} if message else {}),
        "gene_id": gene_id,
        "preview": {"url": prev.get("url"), "unique_id": uid, "row_count": len(rows), "rows": rows[:max(20, max_entities)]},
        "entities": entities,
        "snippets": snippet_map,
    }


def compact_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    keep = ["id", "idtype", "target", "targettype", "inter_type", "edge_disamb", "publication", "p_source", "species", "basis", "source_extracted_definition", "target_extracted_definition"]
    return {k: edge.get(k) for k in keep if edge.get(k) not in (None, "")}


def preferred_alias_queries(selected: Dict[str, Any], *, max_alias_queries: int = 2) -> List[str]:
    """Pick TAIR-confirmed long aliases/full names that are safer than short symbols.

    Heuristics:
    - prefer multi-word descriptive aliases over short symbols;
    - prefer aliases whose word tokens occur in TAIR's description;
    - penalize allele-like or family-like names ending in digits;
    - keep final identity anchored to the selected TAIR gene_id.
    """
    aliases = selected.get("other_names") or []
    gene_id = selected.get("gene_id") or ""
    description = " ".join(selected.get("description") or []) if isinstance(selected.get("description"), list) else str(selected.get("description") or "")
    desc_words = set(re.findall(r"[A-Za-z]{4,}", description.upper()))
    bad = {gene_id.upper(), *(str(x).upper() for x in selected.get("gene_model_ids") or [])}
    ranked = []
    for alias in aliases:
        a = str(alias).strip()
        au = a.upper()
        if not a or au in bad:
            continue
        tokens = re.findall(r"[A-Za-z]{3,}", au)
        score = 0
        # Prefer long/full names and multi-word descriptive names.
        if " " in a:
            score += 5
        if len(a) >= 10:
            score += 3
        # Prefer aliases semantically reflected in the TAIR description.
        score += 3 * sum(1 for tok in tokens if tok in desc_words)
        # Avoid very short symbols and digit-suffixed family/allele-like aliases unless needed.
        if au.isupper() and len(a) <= 5:
            score -= 5
        if re.search(r"\b\d+$", au):
            score -= 2
        ranked.append((score, len(a), a))
    ranked.sort(reverse=True)
    return [a for _, __, a in ranked[:max_alias_queries]]


def build_result(query: str, *, mode: str, forced_gene_id: Optional[str],
                 max_candidates: int, max_entities: int, max_edges: int,
                 snippets: int, timeout: int, include_aliases: bool = False,
                 max_alias_queries: int = 2,
                 retries: int = DEFAULT_RETRIES,
                 retry_backoff: float = DEFAULT_RETRY_BACKOFF,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                 deadline: Optional[float] = None) -> Dict[str, Any]:
    policy = HttpPolicy.create(
        timeout=timeout,
        retries=retries,
        retry_backoff=retry_backoff,
        max_response_bytes=max_response_bytes,
        deadline=deadline,
    )
    if mode == "plant":
        plantconnectome = plant_details(
            query,
            max_entities=max_entities,
            max_edges=max_edges,
            snippets=snippets,
            timeout=timeout,
            policy=policy,
        )
        return {
            "status": plantconnectome.get("status", "error"),
            "mode": mode,
            "plantconnectome": plantconnectome,
        }

    tair_res = tair_search(query, timeout=timeout, policy=policy)
    docs = tair_res.get("docs", [])
    choice = choose_tair_candidate(
        query,
        docs,
        forced_gene_id=forced_gene_id,
        timeout=timeout,
        policy=policy,
    )
    out: Dict[str, Any] = {
        "mode": mode,
        "query": query,
        "tair": {
            "total": tair_res.get("total"),
            "candidate_count": len(docs),
            "candidates": [summarize_tair_doc(d) for d in docs[:max_candidates]],
            "exact_candidates": [summarize_tair_doc(d) for d in exact_matches(docs, query)[:max_candidates]],
            "choice": {k: v for k, v in choice.items() if k not in {"selected", "candidates"}},
        },
    }
    if choice.get("status") == "ambiguous":
        out["status"] = "ambiguous"
        out["message"] = "TAIR returned multiple plausible geneID candidates. Ask the user to confirm one geneID before querying PlantConnectome."
        out["tair"]["ambiguous_candidates"] = [summarize_tair_doc(d) for d in choice.get("candidates", [])[:max_candidates]]
        return out
    if choice.get("status") != "ok":
        out["status"] = choice.get("status", "error")
        out["message"] = choice.get("message", "TAIR candidate selection failed")
        return out

    selected = summarize_tair_doc(choice["selected"])
    out["status"] = "ok"
    out["tair"]["selected"] = selected
    gene_id = selected.get("gene_id")
    if mode == "full" and not gene_id:
        raise ResponseParseError("TAIR selected candidate is missing its AGI gene ID")
    if mode == "full":
        out["plantconnectome"] = plant_details(
            gene_id,
            max_entities=max_entities,
            max_edges=max_edges,
            snippets=snippets,
            timeout=timeout,
            policy=policy,
        )
        if out["plantconnectome"].get("status") != "ok":
            out["status"] = out["plantconnectome"].get("status", "error")
            out["message"] = out["plantconnectome"].get(
                "message", "PlantConnectome evidence retrieval failed"
            )
            return out
        if include_aliases:
            alias_queries = preferred_alias_queries(selected, max_alias_queries=max_alias_queries)
            out["plantconnectome_alias_queries"] = []
            for alias in alias_queries:
                alias_result = plant_details(
                    alias,
                    max_entities=max_entities,
                    max_edges=max_edges,
                    snippets=snippets,
                    timeout=timeout,
                    policy=policy,
                )
                out["plantconnectome_alias_queries"].append({
                    "query": alias,
                    "result": alias_result,
                })
                if alias_result.get("status") not in {"ok", "not_found"}:
                    out["status"] = alias_result.get("status", "error")
                    out["message"] = alias_result.get(
                        "message", f"PlantConnectome alias query failed for {alias}"
                    )
                    return out
    return out


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Counter):
        return obj.most_common()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, tuple):
        return [to_jsonable(v) for v in obj]
    return obj


def plant_summary_lines(pc: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append(f"PlantConnectome 查询词：{pc.get('gene_id')}")
    prev = pc.get("preview", {})
    lines.append(f"预览实体数：{prev.get('row_count')} | URL: {prev.get('url')}")
    rows = prev.get("rows") or []
    if rows:
        lines.append("预览前几项：")
        for row in rows[:5]:
            lines.append(f"- {row}")
    all_pmids = set()
    relation_counter = Counter()
    target_counter = Counter()
    edge_examples: List[Dict[str, Any]] = []
    for ent in pc.get("entities") or []:
        for pmid in ent.get("pmids") or []:
            all_pmids.add(pmid)
        for rel, n in ent.get("relation_counts") or []:
            if rel:
                relation_counter[rel] += n
        for e in ent.get("edges") or []:
            if e.get("target"):
                target_counter[str(e.get("target"))] += 1
            if len(edge_examples) < 8:
                edge_examples.append(compact_edge(e))
    lines.append(f"解析实体数：{len(pc.get('entities') or [])} | PMID 数：{len(all_pmids)}")
    if all_pmids:
        lines.append("PMID 示例：" + ", ".join(sorted(all_pmids)[:20]))
    if relation_counter:
        lines.append("高频关系：" + "; ".join(f"{k}({v})" for k, v in relation_counter.most_common(10)))
    if target_counter:
        lines.append("高频 target：" + "; ".join(f"{k}({v})" for k, v in target_counter.most_common(10)))
    if edge_examples:
        lines.append("关系示例：")
        for e in edge_examples:
            pmid = e.get("publication") or ""
            src = e.get("id") or ""
            rel = e.get("inter_type") or e.get("edge_disamb") or ""
            tgt = e.get("target") or ""
            basis = e.get("basis") or ""
            lines.append(f"- {src} --[{rel}]--> {tgt} PMID:{pmid} basis:{basis}")
    if pc.get("snippets"):
        lines.append("文献片段：")
        for ps, sn in pc["snippets"].items():
            text = sn.get("text_input") if isinstance(sn, dict) else None
            if text:
                lines.append(f"- {ps}: {text[:350].replace(chr(10), ' ')}")
            else:
                lines.append(f"- {ps}: {sn}")
    return lines


def summary_lines(result: Dict[str, Any]) -> List[str]:
    status = result.get("status")
    mode = result.get("mode")
    if mode == "plant":
        lines = plant_summary_lines(result.get("plantconnectome", {}))
        if status != "ok":
            lines.insert(0, f"状态：{status}; {result.get('plantconnectome', {}).get('message', '')}")
        return lines
    lines: List[str] = []
    lines.append(f"查询：{result.get('query')}")
    tair = result.get("tair", {})
    lines.append(f"TAIR 返回候选：{tair.get('candidate_count')} / total={tair.get('total')}")
    if status == "ambiguous":
        lines.append("状态：ambiguous，需要用户确认 geneID 后再查 PlantConnectome。")
        for i, c in enumerate(tair.get("ambiguous_candidates") or [], 1):
            desc = (c.get("description") or "")[:180]
            aliases = ", ".join((c.get("other_names") or [])[:8])
            lines.append(f"{i}. {c.get('gene_id')} | models={','.join(c.get('gene_model_ids') or [])} | aliases={aliases}")
            if desc:
                lines.append(f"   描述：{desc}")
        return lines
    if status != "ok":
        lines.append(f"状态：{status}; {result.get('message','')}")
        return lines
    sel = tair.get("selected") or {}
    lines.append(f"TAIR 确认 geneID：{sel.get('gene_id')}")
    if sel.get("gene_model_ids"):
        lines.append(f"Gene model：{', '.join(sel.get('gene_model_ids'))}")
    if sel.get("other_names"):
        lines.append(f"别名：{', '.join(sel.get('other_names')[:12])}")
    if sel.get("description"):
        lines.append(f"TAIR 描述：{sel.get('description')}")
    if mode == "full" and result.get("plantconnectome"):
        lines.append("")
        lines.extend(plant_summary_lines(result["plantconnectome"]))
        alias_results = result.get("plantconnectome_alias_queries") or []
        if alias_results:
            lines.append("")
            lines.append("TAIR 确认别名辅助查询（仍以 geneID 为准）：")
            for item in alias_results:
                lines.append(f"\n## Alias query: {item.get('query')}")
                if item.get("error"):
                    lines.append(f"错误：{item.get('error')}")
                else:
                    lines.extend(plant_summary_lines(item.get("result", {})))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["tair", "plant", "full"], help="tair only, PlantConnectome only, or full TAIR->PlantConnectome workflow")
    parser.add_argument("query", help="gene symbol/alias/AGI ID; in plant mode this should be confirmed AGI ID")
    parser.add_argument("--gene-id", help="confirmed AGI ID to use when query is ambiguous")
    parser.add_argument("--format", choices=["json", "summary"], default="json")
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--max-entities", type=int, default=3)
    parser.add_argument("--max-edges", type=int, default=50)
    parser.add_argument("--snippets", type=int, default=0, help="fetch snippets for first N p_source values")
    parser.add_argument("--include-aliases", action="store_true", help="in full mode, also query TAIR-confirmed long aliases/full names in PlantConnectome")
    parser.add_argument("--max-alias-queries", type=int, default=2, help="maximum TAIR-confirmed alias/full-name PlantConnectome queries")
    parser.add_argument("--timeout", type=float, default=60, help="per-socket-operation timeout in seconds (default: 60)")
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="retries after the initial HTTP attempt (default: 3; four total attempts)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=DEFAULT_RETRY_BACKOFF,
        help="initial exponential retry delay in seconds (default: 1)",
    )
    parser.add_argument(
        "--deadline",
        type=float,
        help=(
            "total wall-clock deadline for the complete query in seconds; "
            "default is enough for all configured attempts plus backoff"
        ),
    )
    parser.add_argument(
        "--max-response-bytes",
        type=int,
        default=DEFAULT_MAX_RESPONSE_BYTES,
        help="maximum compressed or decompressed HTTP response size (default: 16777216)",
    )
    args = parser.parse_args(argv)
    try:
        result = build_result(args.query, mode=args.mode, forced_gene_id=args.gene_id,
                              max_candidates=args.max_candidates, max_entities=args.max_entities,
                              max_edges=args.max_edges, snippets=args.snippets, timeout=args.timeout,
                              include_aliases=args.include_aliases,
                              max_alias_queries=args.max_alias_queries,
                              retries=args.retries,
                              retry_backoff=args.retry_backoff,
                              max_response_bytes=args.max_response_bytes,
                              deadline=args.deadline)
    except urllib.error.HTTPError as exc:
        body = exc.read(2001).decode("utf-8", "replace")[:2000]
        result = {"status": "http_error", "code": exc.code, "reason": exc.reason, "body": body}
    except Exception as exc:
        result = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
    result = to_jsonable(result)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(summary_lines(result)))
    return {"ok": 0, "ambiguous": 2, "not_found": 3}.get(result.get("status"), 1)


if __name__ == "__main__":
    raise SystemExit(main())
