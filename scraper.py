"""
╔══════════════════════════════════════════════════════════════════╗
║        ADVANCED SMART PROFESSOR + KEYWORD EXTRACTOR TOOL        ║
║              Multi-threaded | Smart NLP | Rich Output           ║
╚══════════════════════════════════════════════════════════════════╝
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import urllib3
import csv
import time
import re
import json
import logging
import argparse
import sys
import os
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
from dataclasses import dataclass, field, asdict
from typing import Optional
from queue import Queue, Empty

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── ANSI Colors ────────────────────────────────────────────────────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GREY    = "\033[90m"

def banner():
    print(f"""
{C.CYAN}{C.BOLD}
╔══════════════════════════════════════════════════════════════════╗
║        ADVANCED SMART PROFESSOR + KEYWORD EXTRACTOR TOOL        ║
║    Multi-threaded │ Smart Extraction │ Rich CLI │ JSON + CSV     ║
╚══════════════════════════════════════════════════════════════════╝
{C.RESET}""")

# ─── Logging ─────────────────────────────────────────────────────────────────
def setup_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("extractor")
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(f"{C.GREY}[%(levelname)s] %(message)s{C.RESET}"))
    logger.addHandler(handler)
    file_handler = logging.FileHandler("extractor.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    return logger

# ─── Data Model ──────────────────────────────────────────────────────────────
@dataclass
class PageResult:
    url: str
    names: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    departments: list = field(default_factory=list)
    matched_snippets: list = field(default_factory=list)
    keyword_count: int = 0
    page_title: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    depth: int = 0

# ─── Extraction Patterns ─────────────────────────────────────────────────────
EMAIL_RE    = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE    = re.compile(r"(?:\+?\d[\d\s\-().]{7,}\d)")

# Captures full name after title: handles "Dr. A.K. Singh", "Prof. Rama Murthy",
# "Dr. S. K. Sharma", "Prof. Mary-Jane O'Brien" — up to 5 name parts
NAME_RE = re.compile(
    r"\b((?:Prof(?:essor)?\.?|Dr\.?|Assoc(?:iate)?\.?\s+Prof(?:essor)?\.?|"
    r"Asst\.?\s+Prof(?:essor)?\.?|Emeritus\s+Prof(?:essor)?\.?)"
    r"\s+"
    r"(?:[A-Z][a-zA-Z'\-]*\.?\s+){1,5}"   # 1-5 name parts (initials or full words)
    r"[A-Z][a-zA-Z'\-]+)"                  # last part must be a real word (no trailing dot)
    , re.UNICODE
)

DEPT_RE = re.compile(
    r"(?:Department|Dept\.?|School|Faculty|Division)\s+of\s+"
    r"([A-Z][A-Za-z\s&,]+?)(?:\.|,|\n|<)",
    re.IGNORECASE
)

def _make_word_pattern(keyword: str) -> re.Pattern:
    """Build a whole-word regex for a keyword. Handles short abbreviations like IoT, 5G."""
    escaped = re.escape(keyword)
    # Use word boundaries; for keywords ending/starting with non-word chars (5G, C++),
    # fall back to lookaround boundaries
    try:
        return re.compile(r"\b" + escaped + r"\b", re.IGNORECASE)
    except re.error:
        return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.IGNORECASE)

# Cache compiled patterns per keyword
_KW_PATTERN_CACHE: dict = {}

def _get_kw_pattern(keyword: str) -> re.Pattern:
    if keyword not in _KW_PATTERN_CACHE:
        _KW_PATTERN_CACHE[keyword] = _make_word_pattern(keyword)
    return _KW_PATTERN_CACHE[keyword]

def extract_emails(text: str) -> list:
    return list(dict.fromkeys(EMAIL_RE.findall(text)))

def extract_phones(text: str) -> list:
    raw = PHONE_RE.findall(text)
    cleaned = []
    for p in raw:
        p = p.strip()
        digits = re.sub(r"\D", "", p)
        if 7 <= len(digits) <= 15:
            cleaned.append(p)
    return list(dict.fromkeys(cleaned))[:5]

def extract_names(text: str) -> list:
    raw = [m.group(0).strip() for m in NAME_RE.finditer(text)]
    # Clean up excessive whitespace inside names
    cleaned = [re.sub(r"\s{2,}", " ", n) for n in raw]
    return list(dict.fromkeys(cleaned))[:10]

def extract_departments(text: str) -> list:
    return list(dict.fromkeys(m.group(1).strip() for m in DEPT_RE.finditer(text)))[:5]

def extract_snippets(text: str, keyword: str, window: int = 120, max_snippets: int = 5) -> list:
    """Extract keyword-in-context snippets using whole-word matching."""
    snippets = []
    pattern = _get_kw_pattern(keyword)
    for m in pattern.finditer(text):
        if len(snippets) >= max_snippets:
            break
        pos   = m.start()
        start = max(0, pos - window)
        end   = min(len(text), pos + len(keyword) + window)
        snippet = text[start:end].replace("\n", " ").strip()
        snippets.append(snippet)
    return snippets

def count_keyword(text: str, keyword: str) -> int:
    """Count whole-word occurrences only — avoids 'IoT' matching 'BIoTechnology'."""
    return len(_get_kw_pattern(keyword).findall(text))

# ─── URL Helpers ─────────────────────────────────────────────────────────────
SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".zip", ".tar", ".gz", ".mp4", ".mp3", ".avi", ".mov",
    ".css", ".js", ".woff", ".woff2", ".ttf",
}

def is_valid_url(url: str, base_domain: str, allow_subdomains: bool = False) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        domain = parsed.netloc.lower()
        if allow_subdomains:
            if not (domain == base_domain or domain.endswith("." + base_domain)):
                return False
        else:
            if domain != base_domain:
                return False
        ext = os.path.splitext(parsed.path.lower())[1]
        if ext in SKIP_EXTENSIONS:
            return False
        return True
    except Exception:
        return False

def normalize_url(url: str) -> str:
    return url.split("#")[0].rstrip("/")

# ─── HTTP Session ─────────────────────────────────────────────────────────────
def make_session(timeout: int = 10) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    })
    return session

def fetch_page(session: requests.Session, url: str, timeout: int = 10,
               retries: int = 3, logger: logging.Logger = None) -> Optional[requests.Response]:
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout, verify=False, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 404, 410):
                return None
            if resp.status_code == 429:
                wait = 2 ** attempt
                if logger: logger.warning(f"Rate limited on {url}, waiting {wait}s")
                time.sleep(wait)
        except requests.exceptions.Timeout:
            if logger: logger.debug(f"Timeout on {url} (attempt {attempt})")
        except requests.exceptions.ConnectionError:
            if logger: logger.debug(f"Connection error on {url} (attempt {attempt})")
        except Exception as e:
            if logger: logger.debug(f"Error fetching {url}: {e}")
            return None
        if attempt < retries:
            time.sleep(0.5 * attempt)
    return None

# ─── Core Crawler ─────────────────────────────────────────────────────────────
class Crawler:
    def __init__(self, start_url: str, keyword: str, max_depth: int = 2,
                 max_workers: int = 5, rate_limit: float = 0.3,
                 allow_subdomains: bool = False, verbose: bool = False,
                 timeout: int = 10, max_pages: int = 200):
        self.start_url       = start_url
        self.keyword         = keyword  # original string (for display)
        # Split comma/semicolon separated keywords, strip whitespace, remove empty
        self.keywords        = [k.strip() for k in re.split(r"[,;]+", keyword) if k.strip()]
        self.max_depth       = max_depth
        self.max_workers     = max_workers
        self.rate_limit      = rate_limit
        self.allow_subdomains = allow_subdomains
        self.timeout         = timeout
        self.max_pages       = max_pages
        self.verbose         = verbose

        parsed = urlparse(start_url)
        self.base_domain = parsed.netloc.lower()

        self.visited: set       = set()
        self.results: list      = []
        self.queue              = Queue()
        self.lock               = Lock()
        self.rate_sem           = Semaphore(max_workers)
        self.pages_crawled      = 0
        self.logger             = setup_logger(verbose)
        self.stats              = defaultdict(int)
        self.session            = make_session(timeout)

    def _already_visited(self, url: str) -> bool:
        with self.lock:
            if url in self.visited:
                return True
            self.visited.add(url)
            return False

    def _crawl_page(self, url: str, depth: int):
        with self.rate_sem:
            time.sleep(self.rate_limit)

        print(f"{C.GREY}🔍 Crawling [{depth}/{self.max_depth}]: {url}{C.RESET}")
        self.logger.debug(f"Crawling depth={depth}: {url}")
        self.stats["crawled"] += 1

        resp = fetch_page(self.session, url, self.timeout, logger=self.logger)
        if resp is None:
            self.stats["failed"] += 1
            return []

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return []

        soup  = BeautifulSoup(resp.text, "lxml")
        text  = soup.get_text(separator=" ")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        kw_count = sum(count_keyword(text, kw) for kw in self.keywords)
        new_links = []

        if kw_count > 0:
            # Gather snippets for each matched keyword
            all_snippets = []
            matched_kws = []
            for kw in self.keywords:
                if count_keyword(text, kw) > 0:
                    matched_kws.append(kw)
                    all_snippets.extend(extract_snippets(text, kw, max_snippets=2))

            result = PageResult(
                url=url,
                names=extract_names(text),
                emails=extract_emails(text),
                phones=extract_phones(text),
                departments=extract_departments(text),
                matched_snippets=all_snippets,
                keyword_count=kw_count,
                page_title=title,
                depth=depth,
            )
            result.matched_keywords = matched_kws  # store which keywords hit
            with self.lock:
                self.results.append(result)
            self.stats["matched"] += 1
            self._print_result(result)

        if depth < self.max_depth:
            for tag in soup.find_all("a", href=True):
                full_url = normalize_url(urljoin(url, tag["href"]))
                if is_valid_url(full_url, self.base_domain, self.allow_subdomains):
                    new_links.append(full_url)

        return new_links

    def _print_result(self, r: PageResult):
        sep = f"{C.CYAN}{'─'*68}{C.RESET}"
        matched_kws = getattr(r, "matched_keywords", self.keywords)
        print(f"\n{sep}")
        print(f"{C.BOLD}{C.GREEN}✅ MATCH FOUND{C.RESET}  {C.GREY}(depth={r.depth}, hits={r.keyword_count}){C.RESET}")
        print(f"{C.YELLOW}🌐 URL      :{C.RESET} {r.url}")
        print(f"{C.YELLOW}📄 Title    :{C.RESET} {r.page_title or 'N/A'}")
        print(f"{C.YELLOW}🔑 Keywords :{C.RESET} {C.GREEN}{', '.join(matched_kws)}{C.RESET}")
        if r.names:
            print(f"{C.YELLOW}👤 Names    :{C.RESET} {'; '.join(r.names[:3])}")
        if r.emails:
            print(f"{C.YELLOW}📧 Emails   :{C.RESET} {', '.join(r.emails[:3])}")
        if r.phones:
            print(f"{C.YELLOW}📞 Phones   :{C.RESET} {', '.join(r.phones[:2])}")
        if r.departments:
            print(f"{C.YELLOW}🏛  Depts    :{C.RESET} {'; '.join(r.departments[:2])}")
        if r.matched_snippets:
            snippet = r.matched_snippets[0][:200]
            # Highlight ALL keywords in the snippet — whole-word only
            for kw in self.keywords:
                snippet = _get_kw_pattern(kw).sub(
                    lambda m: f"{C.RED}{C.BOLD}{m.group(0)}{C.RESET}", snippet
                )
            print(f"{C.YELLOW}🔎 Snippet  :{C.RESET} …{snippet}…")
        print(sep)

    def run(self):
        kw_display = " | ".join(f"{C.BOLD}{k}{C.RESET}{C.CYAN}" for k in self.keywords)
        print(f"\n{C.CYAN}🚀 Starting crawl of {C.BOLD}{self.start_url}{C.RESET}")
        print(f"{C.CYAN}   Keywords : {kw_display}{C.RESET}")
        print(f"{C.CYAN}   Max depth: {self.max_depth}  |  Workers: {self.max_workers}{C.RESET}\n")

        # BFS with thread pool
        frontier = [(self.start_url, 0)]
        self.visited.add(normalize_url(self.start_url))

        while frontier and self.pages_crawled < self.max_pages:
            batch = []
            while frontier and len(batch) < self.max_workers * 2:
                item = frontier.pop(0)
                batch.append(item)

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self._crawl_page, url, depth): (url, depth)
                    for url, depth in batch
                    if self.pages_crawled < self.max_pages
                }
                for future in as_completed(futures):
                    url, depth = futures[future]
                    self.pages_crawled += 1
                    try:
                        new_links = future.result()
                        if depth < self.max_depth:
                            for link in new_links:
                                if not self._already_visited(link):
                                    frontier.append((link, depth + 1))
                    except Exception as e:
                        self.logger.error(f"Error processing {url}: {e}")

        self._print_summary()

    def _print_summary(self):
        print(f"\n{C.CYAN}{'═'*68}{C.RESET}")
        print(f"{C.BOLD}{C.WHITE}📊 CRAWL SUMMARY{C.RESET}")
        print(f"  Pages crawled  : {C.GREEN}{self.stats['crawled']}{C.RESET}")
        print(f"  Pages matched  : {C.GREEN}{self.stats['matched']}{C.RESET}")
        print(f"  Failed/Skipped : {C.YELLOW}{self.stats['failed']}{C.RESET}")
        total_emails = sum(len(r.emails) for r in self.results)
        total_names  = sum(len(r.names)  for r in self.results)
        print(f"  Unique emails  : {C.GREEN}{total_emails}{C.RESET}")
        print(f"  Names found    : {C.GREEN}{total_names}{C.RESET}")
        print(f"{C.CYAN}{'═'*68}{C.RESET}\n")

# ─── Output Saving ────────────────────────────────────────────────────────────
def save_csv(results: list, filename: str):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "URL", "Page Title", "Keyword Hits", "Names", "Emails",
            "Phones", "Departments", "Top Snippet", "Depth", "Timestamp"
        ])
        for r in results:
            writer.writerow([
                r.url,
                r.page_title,
                r.keyword_count,
                " | ".join(r.names),
                " | ".join(r.emails),
                " | ".join(r.phones),
                " | ".join(r.departments),
                r.matched_snippets[0][:300] if r.matched_snippets else "",
                r.depth,
                r.timestamp,
            ])
    print(f"{C.GREEN}✅ CSV saved → {filename}{C.RESET}")

def save_json(results: list, filename: str):
    data = [asdict(r) for r in results]
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"{C.GREEN}✅ JSON saved → {filename}{C.RESET}")

def save_email_list(results: list, filename: str):
    """Deduplicated email list for easy copy-paste."""
    all_emails = []
    for r in results:
        all_emails.extend(r.emails)
    unique = list(dict.fromkeys(all_emails))
    with open(filename, "w", encoding="utf-8") as f:
        for email in unique:
            f.write(email + "\n")
    print(f"{C.GREEN}✅ Email list saved → {filename} ({len(unique)} unique emails){C.RESET}")

# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(
        description="Advanced Smart Professor + Keyword Extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python smart_extractor.py
  python smart_extractor.py --url https://cs.mit.edu --keyword "machine learning" --depth 3 --workers 8
  python smart_extractor.py --url https://ee.stanford.edu --keyword wireless --depth 2 --subdomains --verbose
        """
    )
    parser.add_argument("--url",        help="Starting URL to crawl")
    parser.add_argument("--keyword",    help="Keyword to search for")
    parser.add_argument("--depth",      type=int, default=2,   help="Max crawl depth (default: 2)")
    parser.add_argument("--workers",    type=int, default=5,   help="Concurrent threads (default: 5)")
    parser.add_argument("--rate",       type=float, default=0.3, help="Delay between requests (default: 0.3s)")
    parser.add_argument("--timeout",    type=int, default=10,  help="Request timeout seconds (default: 10)")
    parser.add_argument("--max-pages",  type=int, default=200, help="Max pages to crawl (default: 200)")
    parser.add_argument("--subdomains", action="store_true",   help="Also crawl subdomains")
    parser.add_argument("--verbose",    action="store_true",   help="Show debug logs")
    parser.add_argument("--no-json",    action="store_true",   help="Skip JSON output")
    parser.add_argument("--no-emails",  action="store_true",   help="Skip email list output")
    return parser.parse_args()

# ─── Entry Point ─────────────────────────────────────────────────────────────
def main():
    banner()
    args = parse_args()

    # Interactive input if not via CLI args
    start_url = args.url
    keyword   = args.keyword

    if not start_url:
        start_url = input(f"{C.BOLD}Enter Website URL : {C.RESET}").strip()
    if not keyword:
        keyword   = input(f"{C.BOLD}Enter Keyword     : {C.RESET}").strip()

    if not start_url.startswith("http"):
        start_url = "https://" + start_url

    if not start_url or not keyword:
        print(f"{C.RED}❌ URL and keyword are required.{C.RESET}")
        sys.exit(1)

    print(f"\n{C.GREY}Config → depth={args.depth} | workers={args.workers} | "
          f"rate={args.rate}s | max_pages={args.max_pages} | "
          f"subdomains={'yes' if args.subdomains else 'no'}{C.RESET}")

    crawler = Crawler(
        start_url=start_url,
        keyword=keyword,
        max_depth=args.depth,
        max_workers=args.workers,
        rate_limit=args.rate,
        allow_subdomains=args.subdomains,
        verbose=args.verbose,
        timeout=args.timeout,
        max_pages=args.max_pages,
    )

    try:
        crawler.run()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}⚠ Crawl interrupted by user.{C.RESET}")
        crawler._print_summary()

    results = crawler.results
    if not results:
        print(f"{C.YELLOW}⚠ No pages matched any of the keywords: {', '.join(crawler.keywords)}{C.RESET}")
        sys.exit(0)

    # Save outputs
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_kw = re.sub(r"\W+", "_", keyword)[:20]
    base = f"results_{safe_kw}_{ts}"

    print(f"\n{C.BOLD}💾 Saving results...{C.RESET}")
    save_csv(results, base + ".csv")

    print(f"\n{C.CYAN}{C.BOLD}🎯 Search Complete! Found {len(results)} matching pages.{C.RESET}\n")

if __name__ == "__main__":
    main()