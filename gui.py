"""
Smart Professor + Keyword Extractor — GUI
Place this file in the same folder as scraper.py and run: python gui.py
Requires: pip install requests beautifulsoup4 lxml
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import csv
import os
import re
import sys
import time
from datetime import datetime
from collections import defaultdict
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from urllib.parse import urljoin, urlparse

# ── Import from scraper.py ────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from scraper import (
        PageResult, fetch_page, make_session,
        extract_names, extract_emails, extract_phones,
        extract_departments, extract_snippets, count_keyword,
        normalize_url, is_valid_url, _get_kw_pattern, setup_logger
    )
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError as e:
    import tkinter.messagebox as mb
    mb.showerror("Import Error",
        f"Cannot import scraper.py:\n{e}\n\n"
        "Make sure scraper.py is in the same folder as gui.py\n"
        "and run:  pip install requests beautifulsoup4 lxml")
    sys.exit(1)

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#0d1117"
BG2       = "#161b22"
BG3       = "#21262d"
BORDER    = "#30363d"
ACCENT    = "#00d4aa"
ACCENT2   = "#58a6ff"
DANGER    = "#f85149"
WARN      = "#e3b341"
TEXT      = "#e6edf3"
TEXT2     = "#8b949e"
GREEN     = "#3fb950"

FONT_MONO  = ("Consolas", 10)
FONT_MONO9 = ("Consolas", 9)
FONT_UI    = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_SM    = ("Segoe UI", 8)

# ── GUICrawler ────────────────────────────────────────────────────────────────
class GUICrawler:
    """Standalone crawler matching scraper.py logic, outputs to GUI callbacks."""

    def __init__(self, start_url, keyword, max_depth=2, max_workers=10,
                 rate_limit=0.05, allow_subdomains=False, timeout=10,
                 max_pages=300, log_cb=None, result_cb=None,
                 progress_cb=None, done_cb=None):

        self.start_url        = start_url
        self.keyword          = keyword
        self.keywords         = [k.strip() for k in re.split(r"[,;]+", keyword) if k.strip()]
        self.max_depth        = max_depth
        self.max_workers      = max_workers
        self.rate_limit       = rate_limit
        self.allow_subdomains = allow_subdomains
        self.timeout          = timeout
        self.max_pages        = max_pages

        self._log_cb      = log_cb      or (lambda m, t: None)
        self._result_cb   = result_cb   or (lambda r: None)
        self._progress_cb = progress_cb or (lambda c, m, t: None)
        self._done_cb     = done_cb     or (lambda: None)
        self._stop        = False

        parsed = urlparse(start_url)
        self.base_domain = parsed.netloc.lower()

        self.visited       = set()
        self.results       = []
        self.lock          = Lock()
        self.pages_crawled = 0
        self.stats         = defaultdict(int)
        self.session       = make_session(timeout)
        self.logger        = setup_logger(False)

    def stop(self):
        self._stop = True

    def _already_visited(self, url):
        with self.lock:
            if url in self.visited:
                return True
            self.visited.add(url)
            return False

    def _crawl_page(self, url, depth):
        if self._stop:
            return []

        time.sleep(self.rate_limit)
        self._log_cb(f"🔍 [{depth}/{self.max_depth}] {url}", "crawl")
        self.stats["crawled"] += 1

        resp = fetch_page(self.session, url, self.timeout, logger=self.logger)
        if resp is None:
            self.stats["failed"] += 1
            return []

        if "text/html" not in resp.headers.get("Content-Type", ""):
            return []

        soup  = BeautifulSoup(resp.text, "lxml")
        text  = soup.get_text(separator=" ")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        kw_count = sum(count_keyword(text, kw) for kw in self.keywords)
        new_links = []

        if kw_count > 0:
            all_snippets, matched_kws = [], []
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
            result.matched_keywords = matched_kws

            with self.lock:
                self.results.append(result)
            self.stats["matched"] += 1
            self._result_cb(result)
            self._log_cb(
                f"✅ MATCH — {title or url}  [{', '.join(matched_kws)}]  hits={kw_count}",
                "match"
            )

        self._progress_cb(self.stats["crawled"], self.stats["matched"], self.max_pages)

        if depth < self.max_depth:
            for tag in soup.find_all("a", href=True):
                full_url = normalize_url(urljoin(url, tag["href"]))
                if is_valid_url(full_url, self.base_domain, self.allow_subdomains):
                    new_links.append(full_url)

        return new_links

    def run(self):
        self._log_cb(f"🚀 Crawling: {self.start_url}", "info")
        self._log_cb(f"   Keywords: {' | '.join(self.keywords)}", "info")

        frontier = [(self.start_url, 0)]
        self.visited.add(normalize_url(self.start_url))
        pending = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}

            def fill():
                nonlocal pending
                while (frontier and not self._stop
                       and (len(futures) + pending) < self.max_workers * 4
                       and self.pages_crawled + pending < self.max_pages):
                    url, depth = frontier.pop(0)
                    f = executor.submit(self._crawl_page, url, depth)
                    futures[f] = (url, depth)
                    pending += 1

            fill()

            while futures and not self._stop:
                done, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    url, depth = futures.pop(f)
                    self.pages_crawled += 1
                    pending -= 1
                    try:
                        links = f.result()
                        if depth < self.max_depth:
                            for link in links:
                                if not self._already_visited(link):
                                    frontier.append((link, depth + 1))
                    except Exception as e:
                        self.logger.error(f"Error {url}: {e}")
                fill()

        status = "⛔ Stopped" if self._stop else "✔ Done"
        self._log_cb(
            f"{status} — crawled={self.stats['crawled']}  "
            f"matched={self.stats['matched']}  failed={self.stats['failed']}",
            "done"
        )
        self._done_cb()


# ── Main App ──────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smart Extractor")
        self.configure(bg=BG)
        self.geometry("1220x800")
        self.minsize(960, 620)

        self._crawler = None
        self._results = []
        self._running = False

        self._build_styles()
        self._build_ui()

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Treeview",
            background=BG2, foreground=TEXT, fieldbackground=BG2,
            rowheight=26, font=FONT_MONO9, borderwidth=0)
        s.configure("Treeview.Heading",
            background=BG3, foreground=ACCENT,
            font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Treeview",
            background=[("selected", BG3)],
            foreground=[("selected", ACCENT)])
        s.configure("TScrollbar",
            background=BG3, troughcolor=BG, borderwidth=0, arrowcolor=TEXT2)
        s.configure("bar.Horizontal.TProgressbar",
            background=ACCENT, troughcolor=BG3, borderwidth=0, thickness=3)

    def _build_ui(self):
        self._build_header()
        self._prog = ttk.Progressbar(self, style="bar.Horizontal.TProgressbar",
                                      mode="determinate")
        self._prog.pack(fill="x")

        pane = tk.PanedWindow(self, orient="horizontal", bg=BORDER,
                               sashwidth=3, sashrelief="flat")
        pane.pack(fill="both", expand=True)

        left  = tk.Frame(pane, bg=BG, width=320)
        right = tk.Frame(pane, bg=BG)
        pane.add(left,  minsize=270)
        pane.add(right, minsize=500)

        self._build_left(left)
        self._build_right(right)

    def _build_header(self):
        h = tk.Frame(self, bg=BG2, height=50)
        h.pack(fill="x")
        h.pack_propagate(False)
        tk.Label(h, text="⬡", fg=ACCENT, bg=BG2,
                 font=("Segoe UI", 18)).pack(side="left", padx=(14, 6))
        tk.Label(h, text="SMART EXTRACTOR", fg=TEXT, bg=BG2,
                 font=FONT_TITLE).pack(side="left")
        tk.Label(h, text="Professor + Keyword Crawler", fg=TEXT2, bg=BG2,
                 font=FONT_SM).pack(side="left", padx=10, pady=14)
        self._dot    = tk.Label(h, text="●", fg=TEXT2, bg=BG2, font=("Segoe UI", 13))
        self._status = tk.Label(h, text="idle",  fg=TEXT2, bg=BG2, font=FONT_SM)
        self._dot.pack(side="right", padx=(0, 10))
        self._status.pack(side="right")

    def _build_left(self, p):
        self._lbl(p, "TARGET", top=14)
        self._url_var = tk.StringVar()
        self._kw_var  = tk.StringVar()
        self._field(p, "Website URL",           self._url_var, "https://nitc.ac.in/")
        self._field(p, "Keywords (comma sep.)", self._kw_var,  "wireless, IoT, 5G")

        self._div(p)
        self._lbl(p, "CRAWL SETTINGS")
        self._depth_v   = tk.IntVar(value=2)
        self._workers_v = tk.IntVar(value=10)
        self._pages_v   = tk.IntVar(value=300)
        self._rate_v    = tk.DoubleVar(value=0.05)
        self._slider(p, "Depth",     self._depth_v,   1,  5,    int)
        self._slider(p, "Workers",   self._workers_v, 1,  20,   int)
        self._slider(p, "Max Pages", self._pages_v,   10, 1000, int,   res=10)
        self._slider(p, "Rate (s)",  self._rate_v,    0,  1.0,  float, res=0.05)

        self._div(p)
        self._lbl(p, "OPTIONS")
        self._sub_v = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="Include subdomains", variable=self._sub_v,
                       bg=BG, fg=TEXT2, activebackground=BG, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_UI, highlightthickness=0, bd=0
                       ).pack(anchor="w", padx=14, pady=2)

        self._div(p)
        self._btn_start  = self._mk_btn(p, "▶  START CRAWL",  self._start,  ACCENT,  BG)
        self._btn_stop   = self._mk_btn(p, "⏹  STOP",         self._stop,   DANGER,  BG)
        self._btn_export = self._mk_btn(p, "💾  EXPORT CSV",   self._export, ACCENT2, BG)
        self._btn_clear  = self._mk_btn(p, "✕  CLEAR",        self._clear,  TEXT2,   BG)
        for b in (self._btn_start, self._btn_stop, self._btn_export, self._btn_clear):
            b.pack(fill="x", padx=14, pady=3)
        self._btn_stop.config(state="disabled")
        self._btn_export.config(state="disabled")

        sf = tk.Frame(p, bg=BG2)
        sf.pack(fill="x", side="bottom")
        self._s_crawled = self._stat(sf, "Crawled", "0")
        self._s_matched = self._stat(sf, "Matched", "0")
        self._s_emails  = self._stat(sf, "Emails",  "0")
        self._s_names   = self._stat(sf, "Names",   "0")

    def _build_right(self, p):
        tabbar = tk.Frame(p, bg=BG2, height=36)
        tabbar.pack(fill="x")
        tabbar.pack_propagate(False)
        content = tk.Frame(p, bg=BG)
        content.pack(fill="both", expand=True)

        self._log_f    = tk.Frame(content, bg=BG)
        self._res_f    = tk.Frame(content, bg=BG)
        self._detail_f = tk.Frame(content, bg=BG)

        tab_labels = {"log": "📋 Live Log", "results": "📊 Results", "detail": "🔍 Detail"}
        tab_frames = {"log": self._log_f, "results": self._res_f, "detail": self._detail_f}
        tab_map = {}

        def switch(name):
            for n, (fr, btn) in tab_map.items():
                if n == name:
                    fr.pack(fill="both", expand=True)
                    btn.config(fg=ACCENT, bg=BG,
                               highlightbackground=ACCENT, highlightthickness=2)
                else:
                    fr.pack_forget()
                    btn.config(fg=TEXT2, bg=BG2,
                               highlightbackground=BG2, highlightthickness=0)

        for name in ("log", "results", "detail"):
            btn = tk.Button(tabbar, text=tab_labels[name],
                            font=("Segoe UI", 9), relief="flat", bd=0,
                            padx=14, pady=6, cursor="hand2",
                            bg=BG2, fg=TEXT2, activebackground=BG,
                            activeforeground=ACCENT, highlightthickness=0,
                            command=lambda n=name: switch(n))
            btn.pack(side="left")
            tab_map[name] = (tab_frames[name], btn)

        self._switch_tab = switch

        self._build_log(self._log_f)
        self._build_results(self._res_f)
        self._build_detail(self._detail_f)
        switch("log")

    def _build_log(self, p):
        self._log = tk.Text(p, bg=BG, fg=TEXT, font=FONT_MONO9,
                            relief="flat", bd=0, wrap="word",
                            insertbackground=ACCENT, state="disabled",
                            selectbackground=BG3)
        sb = ttk.Scrollbar(p, orient="vertical", command=self._log.yview)
        self._log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True, padx=8, pady=8)
        self._log.tag_config("crawl", foreground=TEXT2)
        self._log.tag_config("match", foreground=GREEN)
        self._log.tag_config("info",  foreground=ACCENT2)
        self._log.tag_config("done",  foreground=ACCENT, font=("Consolas", 10, "bold"))
        self._log.tag_config("error", foreground=DANGER)
        self._log.tag_config("ts",    foreground=BORDER)

    def _build_results(self, p):
        cols = ("title", "kw", "names", "emails", "hits", "d")
        self._tree = ttk.Treeview(p, columns=cols, show="headings", selectmode="browse")
        spec = [("title","Page Title",260),("kw","Keywords Hit",120),
                ("names","Names",180),("emails","Emails",160),
                ("hits","Hits",48),("d","D",32)]
        for col, label, w in spec:
            self._tree.heading(col, text=label)
            self._tree.column(col, width=w, minwidth=30)
        sby = ttk.Scrollbar(p, orient="vertical",   command=self._tree.yview)
        sbx = ttk.Scrollbar(p, orient="horizontal", command=self._tree.xview)
        self._tree.config(yscrollcommand=sby.set, xscrollcommand=sbx.set)
        sby.pack(side="right",  fill="y")
        sbx.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True, padx=4, pady=4)
        self._tree.tag_configure("row", background=BG2)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_detail(self, p):
        self._detail = tk.Text(p, bg=BG, fg=TEXT, font=FONT_MONO9,
                               relief="flat", bd=0, wrap="word",
                               insertbackground=ACCENT, state="disabled",
                               selectbackground=BG3)
        sb = ttk.Scrollbar(p, orient="vertical", command=self._detail.yview)
        self._detail.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._detail.pack(fill="both", expand=True, padx=8, pady=8)
        self._detail.tag_config("head",    foreground=ACCENT,  font=("Consolas", 10, "bold"))
        self._detail.tag_config("label",   foreground=ACCENT2, font=("Consolas", 9,  "bold"))
        self._detail.tag_config("value",   foreground=TEXT)
        self._detail.tag_config("snippet", foreground=WARN)
        self._detail.tag_config("url",     foreground=ACCENT2)
        self._detail.tag_config("sep",     foreground=BORDER)

    # ── Widget helpers ────────────────────────────────────────────────────────
    def _lbl(self, p, text, top=6):
        tk.Label(p, text=text, fg=ACCENT, bg=BG,
                 font=("Segoe UI", 8, "bold"), anchor="w"
                 ).pack(fill="x", padx=14, pady=(top, 2))

    def _div(self, p):
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=14, pady=6)

    def _field(self, p, label, var, placeholder=""):
        f = tk.Frame(p, bg=BG)
        f.pack(fill="x", padx=14, pady=4)
        tk.Label(f, text=label, fg=TEXT2, bg=BG, font=FONT_SM, anchor="w").pack(fill="x")
        e = tk.Entry(f, textvariable=var, bg=BG3, fg=TEXT,
                     insertbackground=ACCENT, relief="flat", font=FONT_MONO, bd=0,
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        e.pack(fill="x", ipady=6)
        if placeholder:
            var.set(placeholder); e.config(fg=TEXT2)
            def fi(ev, v=var, en=e, ph=placeholder):
                if v.get() == ph: v.set(""); en.config(fg=TEXT)
            def fo(ev, v=var, en=e, ph=placeholder):
                if not v.get(): v.set(ph); en.config(fg=TEXT2)
            e.bind("<FocusIn>", fi); e.bind("<FocusOut>", fo)

    def _slider(self, p, label, var, lo, hi, cast, res=1):
        f = tk.Frame(p, bg=BG); f.pack(fill="x", padx=14, pady=2)
        row = tk.Frame(f, bg=BG); row.pack(fill="x")
        tk.Label(row, text=label, fg=TEXT2, bg=BG, font=FONT_SM, anchor="w").pack(side="left")
        vl = tk.Label(row, text=str(var.get()), fg=ACCENT, bg=BG,
                      font=("Consolas", 9, "bold"), width=6, anchor="e")
        vl.pack(side="right")
        def upd(v, lbl=vl, c=cast): lbl.config(text=str(c(float(v))))
        tk.Scale(f, variable=var, from_=lo, to=hi, orient="horizontal",
                 bg=BG, fg=TEXT, troughcolor=BG3, activebackground=ACCENT,
                 highlightthickness=0, bd=0, sliderrelief="flat",
                 resolution=res, showvalue=False, command=upd,
                 sliderlength=14, width=6).pack(fill="x")

    def _mk_btn(self, p, text, cmd, fg, bg):
        return tk.Button(p, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=BG3, activeforeground=fg,
                         font=("Segoe UI", 9, "bold"), relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BORDER,
                         cursor="hand2", pady=7)

    def _stat(self, p, label, val):
        f = tk.Frame(p, bg=BG2); f.pack(side="left", expand=True, fill="x", padx=1)
        lbl = tk.Label(f, text=val, fg=ACCENT, bg=BG2, font=("Consolas", 14, "bold"))
        lbl.pack(pady=(8, 0))
        tk.Label(f, text=label, fg=TEXT2, bg=BG2, font=FONT_SM).pack(pady=(0, 8))
        return lbl

    # ── Actions ───────────────────────────────────────────────────────────────
    def _start(self):
        url = self._url_var.get().strip()
        kw  = self._kw_var.get().strip()
        PH  = {"https://nitc.ac.in/", "wireless, IoT, 5G", "https://"}
        if not url or url in PH:
            messagebox.showwarning("Missing", "Enter a website URL."); return
        if not kw or kw in PH:
            messagebox.showwarning("Missing", "Enter at least one keyword."); return
        if not url.startswith("http"):
            url = "https://" + url

        self._results.clear()
        self._tree.delete(*self._tree.get_children())
        self._log_clear(); self._detail_clear()
        self._set_stats(0, 0, 0, 0)
        self._prog["value"] = 0
        self._running = True
        self._set_status("running", ACCENT)
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._btn_export.config(state="disabled")

        self._crawler = GUICrawler(
            start_url=url, keyword=kw,
            max_depth=self._depth_v.get(),
            max_workers=self._workers_v.get(),
            rate_limit=self._rate_v.get(),
            allow_subdomains=self._sub_v.get(),
            max_pages=self._pages_v.get(),
            log_cb=self._log_append,
            result_cb=self._on_result,
            progress_cb=self._on_progress,
            done_cb=self._on_done,
        )
        threading.Thread(target=self._crawler.run, daemon=True).start()

    def _stop(self):
        if self._crawler: self._crawler.stop()
        self._btn_stop.config(state="disabled")
        self._set_status("stopping…", WARN)

    def _export(self):
        if not self._results:
            messagebox.showinfo("No data", "Nothing to export."); return
        kw_safe = re.sub(r"\W+", "_", self._kw_var.get())[:20]
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        path    = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"results_{kw_safe}_{ts}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["URL", "Page Title", "Keywords Hit", "Keyword Count",
                        "Names", "Emails", "Phones", "Departments",
                        "Top Snippet", "Depth", "Timestamp"])
            for r in self._results:
                w.writerow([
                    r.url, r.page_title,
                    " | ".join(getattr(r, "matched_keywords", [])),
                    r.keyword_count,
                    " | ".join(r.names),
                    " | ".join(r.emails),
                    " | ".join(r.phones),
                    " | ".join(r.departments),
                    r.matched_snippets[0][:300] if r.matched_snippets else "",
                    r.depth, r.timestamp,
                ])
        self._log_append(f"💾 Saved → {path}", "done")
        messagebox.showinfo("Exported", f"Saved {len(self._results)} rows to:\n{path}")

    def _clear(self):
        self._results.clear()
        self._tree.delete(*self._tree.get_children())
        self._log_clear(); self._detail_clear()
        self._set_stats(0, 0, 0, 0)
        self._prog["value"] = 0
        self._set_status("idle", TEXT2)
        self._btn_export.config(state="disabled")

    # ── Callbacks (thread-safe) ───────────────────────────────────────────────
    def _log_append(self, msg, tag="info"):
        def _do():
            self._log.config(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self._log.insert("end", f"[{ts}] ", "ts")
            self._log.insert("end", msg + "\n", tag)
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _on_result(self, r: PageResult):
        def _do():
            self._results.append(r)
            mkw = getattr(r, "matched_keywords", [])
            iid = self._tree.insert("", "end", tags=("row",), values=(
                r.page_title or r.url,
                ", ".join(mkw),
                "; ".join(r.names[:2])  or "—",
                ", ".join(r.emails[:2]) or "—",
                r.keyword_count, r.depth,
            ))
            self._tree.see(iid)
            te = sum(len(x.emails) for x in self._results)
            tn = sum(len(x.names)  for x in self._results)
            c  = self._crawler.stats["crawled"] if self._crawler else 0
            self._set_stats(c, len(self._results), te, tn)
        self.after(0, _do)

    def _on_progress(self, crawled, matched, total):
        def _do():
            self._prog["value"] = min(100, crawled / max(total, 1) * 100)
            self._s_crawled.config(text=str(crawled))
            self._s_matched.config(text=str(matched))
        self.after(0, _do)

    def _on_done(self):
        def _do():
            self._running = False
            self._btn_start.config(state="normal")
            self._btn_stop.config(state="disabled")
            if self._results: self._btn_export.config(state="normal")
            self._prog["value"] = 100
            self._set_status(f"done  ({len(self._results)} matches)", GREEN)
        self.after(0, _do)

    def _on_select(self, _):
        sel = self._tree.selection()
        if not sel: return
        idx = self._tree.index(sel[0])
        if idx >= len(self._results): return
        self._show_detail(self._results[idx])
        self._switch_tab("detail")

    def _show_detail(self, r: PageResult):
        d = self._detail
        d.config(state="normal"); d.delete("1.0", "end")
        mkw = getattr(r, "matched_keywords", [])

        def row(label, val, tag="value"):
            d.insert("end", f"  {label:<14}", "label")
            d.insert("end", f"{val}\n", tag)

        d.insert("end", "─" * 68 + "\n", "sep")
        d.insert("end", f"  {r.page_title or 'No Title'}\n", "head")
        d.insert("end", "─" * 68 + "\n", "sep")
        row("URL",      r.url, "url")
        row("Keywords", ", ".join(mkw))
        row("Hits",     str(r.keyword_count))
        row("Depth",    str(r.depth))
        row("Time",     r.timestamp[:19])
        d.insert("end", "\n")

        if r.names:
            d.insert("end", "  NAMES\n", "label")
            for n in r.names: d.insert("end", f"    • {n}\n", "value")
        if r.emails:
            d.insert("end", "  EMAILS\n", "label")
            for e in r.emails: d.insert("end", f"    • {e}\n", "value")
        if r.phones:
            d.insert("end", "  PHONES\n", "label")
            for ph in r.phones: d.insert("end", f"    • {ph}\n", "value")
        if r.departments:
            d.insert("end", "  DEPARTMENTS\n", "label")
            for dep in r.departments: d.insert("end", f"    • {dep}\n", "value")
        if r.matched_snippets:
            d.insert("end", "\n  SNIPPETS\n", "label")
            for i, s in enumerate(r.matched_snippets[:3], 1):
                d.insert("end", f"\n  [{i}] …{s[:400]}…\n", "snippet")

        d.insert("end", "\n" + "─" * 68 + "\n", "sep")
        d.config(state="disabled")

    def _log_clear(self):
        self._log.config(state="normal"); self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _detail_clear(self):
        self._detail.config(state="normal"); self._detail.delete("1.0", "end")
        self._detail.config(state="disabled")

    def _set_status(self, text, color):
        self._status.config(text=text, fg=color)
        self._dot.config(fg=color)

    def _set_stats(self, crawled, matched, emails, names):
        self._s_crawled.config(text=str(crawled))
        self._s_matched.config(text=str(matched))
        self._s_emails.config(text=str(emails))
        self._s_names.config(text=str(names))


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()