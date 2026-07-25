"""Build Zotero items from resolved candidates, and create/reuse them.

Metadata quality matters more than it looks: the Scannable Cite marker only
carries author+year, and Zotero renders the real citation from the stored item.
So a match that is *correct* but stored with initials-only creators still
produces a bad bibliography. Where a DOI exists we therefore re-fetch the
canonical Crossref record and build the item from that, rather than from
whichever search result happened to win.
"""
from __future__ import annotations

import httpx

from ..models import Candidate, ParsedRef
from ..search import crossref
from ..utils import norm_doi


def _creators_from_raw(cand: Candidate) -> list[dict]:
    """Full names where the provider record has them; surnames otherwise."""
    raw = cand.raw if isinstance(cand.raw, dict) else {}

    # Crossref: given/family
    if raw.get("author"):
        out = []
        for a in raw["author"]:
            if a.get("family"):
                out.append({
                    "creatorType": "author",
                    "firstName": a.get("given", ""),
                    "lastName": a["family"],
                })
            elif a.get("name"):
                out.append({"creatorType": "author", "name": a["name"]})
        if out:
            return out

    # Europe PMC: authorList.author[].firstName/lastName/collectiveName
    epmc = (raw.get("authorList") or {}).get("author") if raw else None
    if epmc:
        out = []
        for a in epmc:
            if a.get("collectiveName"):
                out.append({"creatorType": "author", "name": a["collectiveName"]})
            elif a.get("lastName"):
                out.append({
                    "creatorType": "author",
                    "firstName": a.get("firstName", "") or a.get("initials", ""),
                    "lastName": a["lastName"],
                })
        if out:
            return out

    # PubMed esummary: "Murray CJL" / CollectiveName
    if raw.get("authors"):
        out = []
        for a in raw["authors"]:
            nm = a.get("name") or ""
            if not nm:
                continue
            if a.get("authtype") == "CollectiveName":
                out.append({"creatorType": "author", "name": nm})
                continue
            parts = nm.rsplit(" ", 1)
            if len(parts) == 2 and parts[1].isupper() and len(parts[1]) <= 4:
                out.append({"creatorType": "author", "firstName": parts[1], "lastName": parts[0]})
            else:
                out.append({"creatorType": "author", "name": nm})
        if out:
            return out

    # Fall back to whatever surnames the scorer used.
    out = [{"creatorType": "author", "firstName": "", "lastName": s} for s in cand.authors if s]
    if cand.corporate:
        out.insert(0, {"creatorType": "author", "name": cand.corporate})
    return out


def to_item(cand: Candidate, ref: ParsedRef) -> dict:
    """Zotero item dict. Reference-derived fields win for books, since for those
    the manuscript is the authoritative source."""
    item: dict = {
        "itemType": cand.item_type or "journalArticle",
        "title": cand.title or ref.title,
        "creators": _creators_from_raw(cand),
        "date": str(cand.year or ref.year or ""),
    }
    if cand.doi:
        item["DOI"] = norm_doi(cand.doi) or ""
    extra = []
    if cand.pmid:
        extra.append(f"PMID: {cand.pmid}")
    if extra:
        item["extra"] = "\n".join(extra)

    if item["itemType"] == "journalArticle":
        item["publicationTitle"] = cand.journal or ref.journal
        item["journalAbbreviation"] = cand.journal_abbrev or ref.journal
        item["volume"] = cand.volume or ref.volume or ""
        item["issue"] = cand.issue or ref.issue or ""
        fp = cand.first_page or ref.first_page
        lp = cand.last_page or ref.last_page
        item["pages"] = f"{fp}-{lp}" if fp and lp else (fp or "")
    elif item["itemType"] in ("book", "bookSection"):
        item["publisher"] = cand.publisher or ref.publisher or ""
        item["place"] = cand.place or ref.place or ""
        if ref.edition:
            item["edition"] = ref.edition
        if item["itemType"] == "bookSection":
            # Zotero needs the containing volume's title, distinct from the
            # chapter title already in item["title"].
            item["bookTitle"] = cand.book_title or ref.book_title
            fp = cand.first_page or ref.first_page
            lp = cand.last_page or ref.last_page
            item["pages"] = f"{fp}-{lp}" if fp and lp else (fp or "")
    return item


async def enrich(client: httpx.AsyncClient, cand: Candidate, mailto: str) -> Candidate:
    """Replace a search hit with the canonical Crossref record for its DOI."""
    if not cand.doi or (isinstance(cand.raw, dict) and cand.raw.get("author")):
        return cand
    better = await crossref.by_doi(client, norm_doi(cand.doi), mailto)
    if better and better.title:
        better.providers = cand.providers | {"crossref:canonical"}
        # keep the item type we decided on; Crossref's is occasionally wrong
        if cand.item_type in ("book", "bookSection"):
            better.item_type = cand.item_type
        return better
    return cand


class ZoteroWriter:
    """Thin pyzotero wrapper that reuses existing items instead of duplicating."""

    def __init__(self, userid: str, api_key: str, dry_run: bool = False):
        self.dry_run = dry_run
        self.userid = userid
        self._by_doi: dict[str, str] = {}
        self._by_title: dict[str, str] = {}
        self.zot = None
        if dry_run:
            return
        from pyzotero import zotero

        self.zot = zotero.Zotero(userid, "user", api_key)

    def load_library(self) -> None:
        """Index the existing library once, so duplicate checks are local."""
        if self.dry_run or not self.zot:
            return
        from ..utils import norm_text

        start = 0
        while True:
            batch = self.zot.items(start=start, limit=100)
            if not batch:
                break
            for it in batch:
                data = it.get("data", {})
                key = data.get("key")
                doi = norm_doi(data.get("DOI"))
                if doi:
                    self._by_doi.setdefault(doi, key)
                title = norm_text(data.get("title", ""))
                if title:
                    self._by_title.setdefault(f"{title}|{(data.get('date') or '')[:4]}", key)
            start += len(batch)

    def existing_key(self, item: dict) -> str | None:
        from ..utils import norm_text

        doi = norm_doi(item.get("DOI"))
        if doi and doi in self._by_doi:
            return self._by_doi[doi]
        tk = f"{norm_text(item.get('title',''))}|{(item.get('date') or '')[:4]}"
        return self._by_title.get(tk)

    def create(self, items_by_n: list[tuple[int, dict]]) -> dict[int, str]:
        """Returns {ref_number: zotero_item_key}."""
        keys: dict[int, str] = {}
        pending: list[tuple[int, dict]] = []
        for n, item in items_by_n:
            hit = self.existing_key(item)
            if hit:
                keys[n] = hit
            else:
                pending.append((n, item))

        if self.dry_run:
            for i, (n, _) in enumerate(pending):
                keys[n] = f"DRYRUN{n:03d}"
            return keys

        for start in range(0, len(pending), 50):
            chunk = pending[start : start + 50]
            resp = self.zot.create_items([it for _, it in chunk])
            for idx, key in (resp.get("success") or {}).items():
                keys[chunk[int(idx)][0]] = key
            for idx, info in (resp.get("failed") or {}).items():
                print(f"  [{chunk[int(idx)][0]}] Zotero create failed: {info}")
        return keys
