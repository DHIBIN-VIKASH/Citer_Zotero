/**
 * Port of zotprep/models.py — the three objects that move through the pipeline:
 *
 *     raw reference string  --extractor-->  ParsedRef
 *     ParsedRef             --providers-->  [Candidate]
 *     ParsedRef+Candidate   --scorer----->  Resolution
 *
 * Defaults are reproduced exactly, including the distinction between the fields
 * that default to `null` and those that default to `""`. It is load-bearing:
 * the extractor does `re.sub(r"\s+", " ", ref.title)` unconditionally at the
 * end, which only works because `title` and `journal` start as strings while
 * `volume` and `publisher` start as None.
 */

export class ParsedRef {
  /**
   * Everything we could pull out of the raw reference text itself. This is
   * ground truth: it comes from the manuscript, not from an API, so the scorer
   * treats it as the thing candidates are measured against.
   */
  constructor(n, raw) {
    this.n = n;
    this.raw = raw;
    // deterministic identifiers found in the text
    this.doi = null;
    this.pmid = null;
    this.pmcid = null;
    // bibliographic fields
    this.authors = []; // surnames, in order
    this.corporate = null; // "GBD 2021 Diseases and Injuries Collaborators"
    this.title = '';
    this.journal = ''; // as abbreviated in the reference
    this.year = null;
    this.volume = null;
    this.issue = null;
    this.first_page = null;
    this.last_page = null;
    // book-shaped references
    this.is_book = false;
    this.is_chapter = false; // "In: <editors>, eds. <book title>" -> bookSection
    this.book_title = ''; // containing volume, when is_chapter
    this.publisher = null;
    this.place = null;
    this.edition = null;
  }

  get has_identifier() {
    return Boolean(this.doi || this.pmid || this.pmcid);
  }

  /** First personal-author surname, or null for consortium references. */
  get lead_author() {
    return this.authors.length ? this.authors[0] : null;
  }
}

export class Candidate {
  /** A normalized search hit from any provider. */
  constructor(init = {}) {
    this.provider = init.provider ?? '';
    this.title = init.title ?? '';
    this.doi = init.doi ?? null;
    this.pmid = init.pmid ?? null;
    this.authors = init.authors ?? []; // surnames
    this.corporate = init.corporate ?? null;
    this.year = init.year ?? null;
    this.journal = init.journal ?? ''; // full container title
    this.journal_abbrev = init.journal_abbrev ?? ''; // provider-supplied abbreviation
    this.volume = init.volume ?? null;
    this.issue = init.issue ?? null;
    this.first_page = init.first_page ?? null;
    this.last_page = init.last_page ?? null;
    this.item_type = init.item_type ?? 'journalArticle'; // Zotero item type
    this.book_title = init.book_title ?? ''; // containing volume, for bookSection
    this.publisher = init.publisher ?? null;
    this.place = init.place ?? null;
    this.raw = init.raw ?? null;
    // filled in during aggregation; mirrors __post_init__
    this.providers = init.providers instanceof Set
      ? init.providers
      : new Set(init.providers ?? []);
    if (!this.providers.size) this.providers = new Set([this.provider]);
  }
}

export class Resolution {
  /** Outcome for one reference. */
  constructor(init = {}) {
    this.n = init.n;
    this.status = init.status; // "ACCEPTED" | "REVIEW" | "MANUAL" | "FROM_TEXT"
    this.confidence = init.confidence ?? 0.0;
    this.tier = init.tier ?? ''; // which rule fired: "identifier", "fingerprint", ...
    this.candidate = init.candidate ?? null;
    this.alternatives = init.alternatives ?? [];
    this.reason = init.reason ?? ''; // why it failed / which signal disagreed
    this.notes = init.notes ?? []; // advisories worth the author's eye
    this.signals = init.signals ?? {};
    this.zotero_key = init.zotero_key ?? null;
  }
}
