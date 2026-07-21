#!/usr/bin/env python3
"""Query Arabidopsis genes via TAIR, PlantConnectome, and PubMed.

Workflow:
1. Use TAIR public search API to resolve gene symbol/alias/AGI ID.
2. If multiple exact candidates exist, return ambiguous status for user confirmation.
3. Deterministically clean and deduplicate the TAIR ``other_names`` list.
4. Query PlantConnectome and PubMed once for every retained name.

Only Python standard library is required.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import html
import http.client
import json
import math
import re
import signal
import socket
import time
import tokenize
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

TAIR_API_BASE = "https://www.arabidopsis.org/api"
PLANTCONNECTOME_BASE = "https://plant.connectome.tools"
DEFAULT_PUBMED_API_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
PLANTCONNECTOME_MAX_EDGE_PAYLOAD_CHARS = 8 * 1024 * 1024
PLANTCONNECTOME_MAX_AST_NODES = 500_000
PLANTCONNECTOME_MAX_STRUCTURAL_TOKENS = 500_000
PLANTCONNECTOME_MAX_NESTING_DEPTH = 200
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


class _WallClockDeadline:
    def __init__(self, seconds: Optional[float]) -> None:
        self.seconds = seconds
        self.previous_handler: Any = None
        self.previous_timer: Optional[Tuple[float, float]] = None
        self.enabled = False

    def __enter__(self) -> None:
        if (
            self.seconds is None
            or self.seconds <= 0
            or not math.isfinite(self.seconds)
            or not hasattr(signal, "setitimer")
        ):
            return
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        self.previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, self._raise_deadline)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        self.enabled = True

    def __exit__(self, *args: Any) -> None:
        if not self.enabled:
            return
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous_handler)
        if self.previous_timer and self.previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *self.previous_timer)

    @staticmethod
    def _raise_deadline(signum: int, frame: Any) -> None:
        raise QueryDeadlineExceeded("command wall-clock deadline exceeded")


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
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be greater than zero")
        if retries < 0:
            raise ValueError("retries must be zero or greater")
        if retries > 10:
            raise ValueError("retries must be 10 or fewer")
        if retry_backoff < 0 or not math.isfinite(retry_backoff):
            raise ValueError("retry_backoff must be zero or greater")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")
        if deadline is None:
            deadline = timeout * (retries + 1) + retry_backoff * (2 ** retries - 1)
        if deadline <= 0 or not math.isfinite(deadline):
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


def _decompress_gzip_limited(data: bytes, *, policy: HttpPolicy, url: str) -> bytes:
    decoded = bytearray()
    try:
        with gzip.GzipFile(fileobj=BytesIO(data)) as stream:
            while True:
                policy.remaining()
                chunk = stream.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                decoded.extend(chunk)
                if len(decoded) > policy.max_response_bytes:
                    raise ResponseTooLarge(
                        f"decompressed response from {url} exceeded "
                        f"{policy.max_response_bytes} bytes"
                    )
    except QueryError:
        raise
    except (EOFError, OSError) as exc:
        raise ResponseParseError(f"invalid gzip response from {url}: {exc}") from exc
    policy.remaining()
    return bytes(decoded)


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
        decoded = _decompress_gzip_limited(decoded, policy=policy, url=url)
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


def _validate_tair_doc(doc: Dict[str, Any], index: int) -> None:
    list_fields = (
        "gene_name",
        "gene_model_ids",
        "other_names",
        "description",
        "keywords",
        "keyword_types",
        "phenotypes",
        "gene_model_type",
    )
    for field in list_fields:
        if field not in doc:
            continue
        value = doc[field]
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ResponseParseError(
                f"TAIR search doc {index} field {field!r} is not a string array"
            )
    gene_names = doc.get("gene_name")
    if not isinstance(gene_names, list) or not gene_names or not gene_names[0].strip():
        raise ResponseParseError(
            f"TAIR search doc {index} is missing a non-empty gene_name"
        )


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
    if any(not isinstance(doc, dict) for doc in docs or []):
        raise ResponseParseError("TAIR search docs contain a non-object entry")
    for index, doc in enumerate(docs or []):
        _validate_tair_doc(doc, index)
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
        is_transcript = "." in q
        for d in docs:
            gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
            gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
            if (is_transcript and q in gene_models) or (
                not is_transcript and q == gene_id
            ):
                return {"status": "ok", "selected": d, "reason": "AGI ID uniquely identifies the candidate"}
        return {
            "status": "not_found",
            "message": f"TAIR returned no exact AGI match for {query}",
        }

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


def _preflight_structured_literal(value: str, label: str) -> None:
    if len(value) > PLANTCONNECTOME_MAX_EDGE_PAYLOAD_CHARS:
        raise ResponseTooLarge(
            f"PlantConnectome {label} exceeded "
            f"{PLANTCONNECTOME_MAX_EDGE_PAYLOAD_CHARS} characters"
        )
    structural_tokens = 0
    nesting_depth = 0
    quote: Optional[str] = None
    escaped = False
    for char in value:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "[{":
            structural_tokens += 1
            nesting_depth += 1
            if nesting_depth > PLANTCONNECTOME_MAX_NESTING_DEPTH:
                raise ResponseTooLarge(
                    f"PlantConnectome {label} nesting exceeded "
                    f"{PLANTCONNECTOME_MAX_NESTING_DEPTH}"
                )
        elif char in "]}":
            nesting_depth = max(0, nesting_depth - 1)
        elif char == ",":
            structural_tokens += 1
        if structural_tokens > PLANTCONNECTOME_MAX_STRUCTURAL_TOKENS:
            raise ResponseTooLarge(
                f"PlantConnectome {label} structure count exceeded "
                f"{PLANTCONNECTOME_MAX_STRUCTURAL_TOKENS}"
            )


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
    payload = m.group(1).strip()
    _preflight_structured_literal(payload, "preview payload")
    try:
        rows = json.loads(payload)
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
    _preflight_structured_literal(decoded, "edge payload")

    def parse_python_literal(value: str) -> Any:
        try:
            for token_count, _ in enumerate(
                tokenize.generate_tokens(StringIO(value).readline), 1
            ):
                if token_count > PLANTCONNECTOME_MAX_AST_NODES:
                    raise ResponseTooLarge(
                        "PlantConnectome edge payload token count exceeded "
                        f"{PLANTCONNECTOME_MAX_AST_NODES}"
                    )
        except ResponseTooLarge:
            raise
        except (IndentationError, tokenize.TokenError) as exc:
            raise ResponseParseError(
                f"PlantConnectome edge payload tokenization failed: {exc}"
            ) from exc
        tree = ast.parse(value, mode="eval")
        for node_count, _ in enumerate(ast.walk(tree), 1):
            if node_count > PLANTCONNECTOME_MAX_AST_NODES:
                raise ResponseTooLarge(
                    "PlantConnectome edge payload AST exceeded "
                    f"{PLANTCONNECTOME_MAX_AST_NODES} nodes"
                )
        return ast.literal_eval(tree)

    errors = []
    for parser_name, parser in (
        ("JSON", json.loads),
        ("Python literal", parse_python_literal),
    ):
        try:
            val = parser(decoded)
        except QueryError:
            raise
        except Exception as exc:
            errors.append(f"{parser_name} parse failed: {exc}")
            continue
        if not isinstance(val, list):
            errors.append(f"{parser_name} type was {type(val).__name__}")
            continue
        if any(not isinstance(x, dict) for x in val):
            raise ResponseParseError(
                "PlantConnectome edge payload contains non-object entries"
            )
        return val
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
            "pmids": sorted({str(e.get("publication")) for e in kept_edges if e.get("publication")}),
            "relation_counts": Counter(
                str(e.get("inter_type") or e.get("edge_disamb") or "")
                for e in kept_edges
            ).most_common(20),
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


def clean_text(value: Any, max_chars: int = 0) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def arabidopsis_gene_name_candidates(selected: Any) -> List[str]:
    """Return TAIR other_names in stable order without semantic filtering."""
    if not isinstance(selected, dict):
        return []
    values = selected.get("other_names")
    if not isinstance(values, list):
        return []
    names: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        name = clean_text(value)
        key = unicodedata.normalize("NFKC", name).casefold()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def literature_query(gene_name: str, gene_id: str) -> str:
    if gene_name and gene_name.casefold() != gene_id.casefold():
        return f"({gene_name} OR {gene_id}) AND (Arabidopsis thaliana)"
    return f"{gene_id} AND (Arabidopsis thaliana)"


def _xml_text(element: Optional[ET.Element]) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def fetch_pubmed(query: str, *, base_url: str = DEFAULT_PUBMED_API_BASE,
                 limit: int = 20, timeout: int = 60,
                 policy: Optional[HttpPolicy] = None) -> Dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("PubMed query must not be empty")
    if limit <= 0:
        raise ValueError("PubMed limit must be greater than zero")
    policy = policy or HttpPolicy.create(timeout=timeout)
    base_url = base_url.rstrip("/")
    search_url = base_url + "/esearch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "term": query,
        "retmax": limit,
        "retmode": "json",
    })
    search_data = http_json(
        search_url,
        timeout=timeout,
        referer="https://pubmed.ncbi.nlm.nih.gov/",
        policy=policy,
    )
    if not isinstance(search_data, dict):
        raise ResponseParseError("PubMed ESearch response is not a JSON object")
    search_result = search_data.get("esearchresult")
    if not isinstance(search_result, dict):
        raise ResponseParseError("PubMed ESearch response is missing esearchresult")
    raw_ids = search_result.get("idlist")
    if not isinstance(raw_ids, list) or any(
        not isinstance(value, str) or not value.isdigit()
        for value in raw_ids
    ):
        raise ResponseParseError("PubMed ESearch idlist is malformed")
    ids = raw_ids[:limit]
    if not ids:
        return {"total": 0, "data": []}

    fetch_url = base_url + "/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
        "rettype": "abstract",
    })
    xml_text, _ = http_text(
        fetch_url,
        timeout=timeout,
        referer="https://pubmed.ncbi.nlm.nih.gov/",
        policy=policy,
    )
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ResponseParseError(f"PubMed EFetch returned invalid XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "PubmedArticleSet":
        raise ResponseParseError(
            f"PubMed EFetch returned unexpected root element {root.tag!r}"
        )

    papers: List[Dict[str, Any]] = []
    articles = [
        child
        for child in root
        if child.tag.rsplit("}", 1)[-1]
        in {"PubmedArticle", "PubmedBookArticle"}
    ]
    for article in articles:
        abstract_parts = []
        for abstract_element in article.findall(".//AbstractText"):
            abstract = _xml_text(abstract_element)
            if not abstract:
                continue
            label = clean_text(abstract_element.get("Label"))
            abstract_parts.append(f"{label}: {abstract}" if label else abstract)

        year: Optional[int] = None
        year_text = _xml_text(article.find(".//PubDate/Year"))
        if year_text.isdigit():
            year = int(year_text)
        if year is None:
            medline_date = _xml_text(article.find(".//PubDate/MedlineDate"))
            year_match = re.search(r"\b(?:18|19|20)\d{2}\b", medline_date)
            if year_match:
                year = int(year_match.group(0))

        doi = ""
        for article_id in article.findall(".//ArticleId"):
            if article_id.get("IdType") == "doi":
                doi = _xml_text(article_id)
                break
        if not doi:
            for location_id in article.findall(".//ELocationID"):
                if location_id.get("EIdType") == "doi":
                    doi = _xml_text(location_id)
                    break
        authors = []
        for author in article.findall(".//Author"):
            name = " ".join(
                value
                for value in (
                    _xml_text(author.find("ForeName")),
                    _xml_text(author.find("LastName")),
                )
                if value
            )
            if name:
                authors.append(name)
        pmid = _xml_text(article.find(".//PMID"))
        papers.append({
            "id": pmid,
            "pmid": pmid,
            "doi": doi,
            "title": _xml_text(article.find(".//ArticleTitle")),
            "year": year,
            "authors": authors,
            "abstract": " ".join(abstract_parts),
            "venue": _xml_text(article.find(".//Journal/Title")),
            "source": "pubmed",
        })
    returned_ids = [paper["pmid"] for paper in papers]
    if (
        len(returned_ids) != len(ids)
        or any(not pmid.isdigit() for pmid in returned_ids)
        or set(returned_ids) != set(ids)
    ):
        raise ResponseParseError(
            "PubMed EFetch article PMIDs do not match the ESearch idlist"
        )
    return {"total": len(papers), "data": papers}


def compact_pubmed(data: Dict[str, Any], *, max_abstract_chars: int = 1800) -> List[Dict[str, Any]]:
    papers = data.get("data")
    if not isinstance(papers, list):
        return []
    output = []
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        output.append({
            "pmid": clean_text(paper.get("pmid") or paper.get("id")),
            "doi": clean_text(paper.get("doi")),
            "title": clean_text(paper.get("title"), 500),
            "year": paper.get("year"),
            "abstract": clean_text(paper.get("abstract"), max_abstract_chars),
        })
    return output


def compact_arabidopsis(data: Dict[str, Any]) -> Dict[str, Any]:
    tair = data.get("tair") if isinstance(data.get("tair"), dict) else {}
    selected = tair.get("selected") if isinstance(tair.get("selected"), dict) else None
    compact_searches = []
    for search in data.get("plantconnectome_searches") or []:
        if not isinstance(search, dict):
            continue
        pc = search.get("plantconnectome")
        if not isinstance(pc, dict):
            pc = {}
        relationships = []
        for entity in pc.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            for edge in entity.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                relationships.append({
                    "entity_1": clean_text(edge.get("entity1") or edge.get("id")),
                    "relationship": clean_text(
                        edge.get("inter_type") or edge.get("edge_disamb")
                    ),
                    "entity_2": clean_text(edge.get("entity2") or edge.get("target")),
                    "citation": clean_text(edge.get("publication")),
                })
        compact_searches.append({
            "gene_name": clean_text(search.get("gene_name")),
            "relationships": relationships,
        })
    return {
        "tair_selected": selected,
        "plantconnectome_searches": compact_searches,
    }


def build_result(query: str, *, mode: str, forced_gene_id: Optional[str],
                 max_candidates: int, max_entities: int, max_edges: int,
                 snippets: int, timeout: int, include_aliases: bool = True,
                 max_alias_queries: Optional[int] = None,
                 max_gene_names: Optional[int] = None,
                 pubmed_limit: int = 20,
                 pubmed_base_url: str = DEFAULT_PUBMED_API_BASE,
                 retries: int = DEFAULT_RETRIES,
                 retry_backoff: float = DEFAULT_RETRY_BACKOFF,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                 deadline: Optional[float] = None) -> Dict[str, Any]:
    if max_candidates <= 0:
        raise ValueError("max_candidates must be greater than zero")
    if max_entities <= 0:
        raise ValueError("max_entities must be greater than zero")
    if max_edges <= 0:
        raise ValueError("max_edges must be greater than zero")
    if snippets < 0:
        raise ValueError("snippets must be zero or greater")
    if max_gene_names is None:
        max_gene_names = max_alias_queries
    if max_gene_names is not None and max_gene_names < 0:
        raise ValueError("max_gene_names must be zero or greater")
    if pubmed_limit <= 0:
        raise ValueError("pubmed_limit must be greater than zero")
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
        candidate_names = arabidopsis_gene_name_candidates(selected)
        retrieval_names = candidate_names if include_aliases else []
        if max_gene_names is not None:
            retrieval_names = retrieval_names[:max_gene_names]
        out["candidate_gene_names"] = candidate_names
        out["retrieval_gene_names"] = retrieval_names

        if deadline is None:
            attempt_budget = (
                timeout * (retries + 1)
                + retry_backoff * (2 ** retries - 1)
            )
            plant_operations = 1 + max_entities + snippets
            remaining_operations = (
                plant_operations * (1 + len(retrieval_names))
                + 2 * len(retrieval_names)
            )
            policy = HttpPolicy.create(
                timeout=timeout,
                retries=retries,
                retry_backoff=retry_backoff,
                max_response_bytes=max_response_bytes,
                deadline=attempt_budget * remaining_operations,
            )

        out["plantconnectome"] = plant_details(
            gene_id,
            max_entities=max_entities,
            max_edges=max_edges,
            snippets=snippets,
            timeout=timeout,
            policy=policy,
        )
        primary_status = out["plantconnectome"].get("status")
        if primary_status not in {"ok", "not_found"}:
            raise QueryError(
                out["plantconnectome"].get(
                    "message", "PlantConnectome AGI query returned an invalid status"
                )
            )

        out["plantconnectome_searches"] = []
        out["pubmed"] = []

        for gene_name in retrieval_names:
            if gene_name.casefold() == str(gene_id).casefold():
                name_plant = out["plantconnectome"]
            else:
                name_plant = plant_details(
                    gene_name,
                    max_entities=max_entities,
                    max_edges=max_edges,
                    snippets=snippets,
                    timeout=timeout,
                    policy=policy,
                )
            name_status = name_plant.get("status")
            if name_status not in {"ok", "not_found"}:
                raise QueryError(
                    name_plant.get(
                        "message",
                        f"PlantConnectome query returned an invalid status for {gene_name}",
                    )
                )
            out["plantconnectome_searches"].append({
                "gene_name": gene_name,
                "status": name_status,
                "plantconnectome": name_plant,
            })

            query_text = literature_query(gene_name, str(gene_id))
            pubmed_result = fetch_pubmed(
                query_text,
                base_url=pubmed_base_url,
                limit=pubmed_limit,
                timeout=timeout,
                policy=policy,
            )
            out["pubmed"].append({
                "gene_name": gene_name,
                "query": query_text,
                "papers": compact_pubmed(pubmed_result),
            })
        out["database_evidence"] = compact_arabidopsis(out)
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
    if pc.get("status") != "ok":
        lines.append(
            f"PlantConnectome 状态：{pc.get('status')}; {pc.get('message', '')}"
        )
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


def pubmed_summary_lines(item: Dict[str, Any]) -> List[str]:
    papers = item.get("papers") or []
    lines = [
        f"PubMed 查询词：{item.get('gene_name')}",
        f"检索式：{item.get('query')}",
        f"返回论文数：{len(papers)}",
    ]
    for paper in papers[:10]:
        pmid = paper.get("pmid") or ""
        doi = paper.get("doi") or ""
        year = paper.get("year") or ""
        title = paper.get("title") or ""
        lines.append(f"- {title} ({year}) PMID:{pmid} DOI:{doi}")
        abstract = clean_text(paper.get("abstract"), 350)
        if abstract:
            lines.append(f"  摘要：{abstract}")
    return lines


def summary_lines(result: Dict[str, Any]) -> List[str]:
    status = result.get("status")
    mode = result.get("mode")
    if mode == "plant":
        if not result.get("plantconnectome"):
            return [f"状态：{status}; {result.get('message', '')}"]
        lines = plant_summary_lines(result.get("plantconnectome", {}))
        if status != "ok":
            lines.insert(0, f"状态：{status}; {result.get('plantconnectome', {}).get('message', '')}")
        return lines
    lines: List[str] = []
    lines.append(f"查询：{result.get('query')}")
    tair = result.get("tair", {})
    if tair:
        lines.append(
            f"TAIR 返回候选：{tair.get('candidate_count')} / total={tair.get('total')}"
        )
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
        lines.append("AGI ID 直接查询（兼容性证据，身份仍以 TAIR 为准）：")
        lines.extend(plant_summary_lines(result["plantconnectome"]))
        lines.append("")
        lines.append(
            "TAIR 候选名称："
            + (", ".join(result.get("candidate_gene_names") or []) or "无")
        )
        lines.append(
            "实际检索名称："
            + (", ".join(result.get("retrieval_gene_names") or []) or "无")
        )
        name_searches = result.get("plantconnectome_searches") or []
        if name_searches:
            lines.append("")
            lines.append("按 TAIR 名称分组的 PlantConnectome 查询：")
            for item in name_searches:
                lines.append(f"\n## Gene-name query: {item.get('gene_name')}")
                lines.extend(
                    plant_summary_lines(item.get("plantconnectome", {}))
                )
        literature = result.get("pubmed") or []
        if literature:
            lines.append("")
            lines.append("按 TAIR 名称分组的 PubMed 查询：")
            for item in literature:
                lines.append(f"\n## Gene-name query: {item.get('gene_name')}")
                lines.extend(pubmed_summary_lines(item))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["tair", "plant", "full"],
        help="tair only, raw PlantConnectome only, or full TAIR/PlantConnectome/PubMed workflow",
    )
    parser.add_argument(
        "query",
        help="gene symbol/alias/AGI ID; plant mode accepts a raw PlantConnectome query",
    )
    parser.add_argument("--gene-id", help="confirmed AGI ID to use when query is ambiguous")
    parser.add_argument("--format", choices=["json", "summary"], default="json")
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--max-entities", type=int, default=3)
    parser.add_argument("--max-edges", type=int, default=50)
    parser.add_argument("--snippets", type=int, default=0, help="fetch snippets for first N p_source values")
    parser.add_argument(
        "--include-aliases",
        dest="include_aliases",
        action="store_true",
        help="query all TAIR other_names in full mode (enabled by default)",
    )
    parser.add_argument(
        "--no-name-searches",
        dest="include_aliases",
        action="store_false",
        help="skip per-name PlantConnectome and PubMed queries in full mode",
    )
    parser.set_defaults(include_aliases=True)
    parser.add_argument(
        "--max-gene-names",
        "--max-alias-queries",
        dest="max_gene_names",
        type=int,
        help="maximum TAIR other_names to query; default queries every deduplicated name",
    )
    parser.add_argument(
        "--pubmed-limit",
        type=int,
        default=20,
        help="maximum PubMed records per gene-name query (default: 20)",
    )
    parser.add_argument(
        "--pubmed-base-url",
        default=DEFAULT_PUBMED_API_BASE,
        help="NCBI E-utilities base URL",
    )
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
    result_context = {"mode": args.mode, "query": args.query}
    try:
        with _WallClockDeadline(args.deadline):
            result = build_result(
                args.query,
                mode=args.mode,
                forced_gene_id=args.gene_id,
                max_candidates=args.max_candidates,
                max_entities=args.max_entities,
                max_edges=args.max_edges,
                snippets=args.snippets,
                timeout=args.timeout,
                include_aliases=args.include_aliases,
                max_gene_names=args.max_gene_names,
                pubmed_limit=args.pubmed_limit,
                pubmed_base_url=args.pubmed_base_url,
                retries=args.retries,
                retry_backoff=args.retry_backoff,
                max_response_bytes=args.max_response_bytes,
                deadline=args.deadline,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(2001).decode("utf-8", "replace")[:2000]
        result = {
            **result_context,
            "status": "http_error",
            "code": exc.code,
            "reason": exc.reason,
            "body": body,
            "message": f"HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        result = {
            **result_context,
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    result = to_jsonable(result)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(summary_lines(result)))
    return {"ok": 0, "ambiguous": 2, "not_found": 3}.get(result.get("status"), 1)


if __name__ == "__main__":
    raise SystemExit(main())
