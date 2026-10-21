"""
museumsjob.py - finds part-time student jobs in the Copenhagen area for
ethnology (etnologi) / anthropology (antropologi) students, with a focus on
museums: communication/guiding (formidling), working at a museum, and research.

Sources:
  * Jobindex.dk       (studiejob, praktik, frivillig, timeansat + plain search)
  * frivilligjob.dk   (volunteer positions - always unpaid)

Rules:
  * Must be relevant: mentions etnologi/antropologi, or is museum/kulturarv work
  * Must be open to bachelor students or anyone - jobs requiring a finished
    MSc (kandidat) or PhD are dropped
  * No full-time jobs - part-time / student / volunteer only
  * Paid jobs are shown in GREEN, unpaid in RED

Usage:
  python museumsjob.py                 # Storkøbenhavn + Nordsjælland
  python museumsjob.py --area all      # also Region Sjælland
  python museumsjob.py --show-rejected # also list what was filtered out and why
  python museumsjob.py --no-html       # skip writing/opening the HTML report
  python museumsjob.py --email --no-open   # what the daily scheduled run does

Daily run: GitHub Actions (.github/workflows/daily.yml) runs it every morning and emails
the result; email settings come from the EMAIL_FROM / EMAIL_TO / EMAIL_APP_PASSWORD secrets.
Running locally: copy email_config.example.json to email_config.json and fill it in.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import webbrowser
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
DELAY = 0.3  # seconds between requests, to be polite

# Jobindex geo area ids
AREAS = {
    "kbh": {"Storkøbenhavn": 15182, "Nordsjælland": 15187},
    "all": {"Storkøbenhavn": 15182, "Nordsjælland": 15187, "Region Sjælland": 4},
}
# Jobindex employment types we care about -> label
EMPLOYMENT_TYPES = {3: "Studiejob", 6: "Praktik", 9: "Frivillig", 12: "Timeansat"}

JOBINDEX_KEYWORDS = [
    "etnologi", "etnolog", "antropologi", "antropolog",
    "museum", "museer", "kulturarv", "formidling", "omviser", "udstilling",
]
# frivilligjob.dk: its keyword search also matches words deep in the ad text, so
# we browse the "Kulturelt arbejde" category (fag 14) in these Copenhagen-area
# municipalities and filter on museum terms ourselves.
FRIVILLIG_CATEGORY = 14
FRIVILLIG_MUNICIPALITIES = {
    "kbh": [26, 11, 15, 16, 27, 22, 29, 30, 8, 20, 5, 7, 17, 3, 23, 25, 31, 28, 14, 24, 19, 21, 10, 4, 9],
    "all": [26, 11, 15, 16, 27, 22, 29, 30, 8, 20, 5, 7, 17, 3, 23, 25, 31, 28, 14, 24, 19, 21, 10, 4, 9,
            43, 37, 38, 33, 45, 12, 13, 18, 35],
}
FRIVILLIG_MAX_PAGES = 15

# ---------------------------------------------------------------- classifiers

ETNO_ANTRO = re.compile(r"etnolog|antropolog|ethnolog|anthropolog|kulturforsk|europæisk etnologi", re.I)
# museum-type work, matched against the job title / employer name
MUSEUM = re.compile(r"muse(?:um|et|er|ets)|musée|kulturarv|kulturhistori|udstilling|formidl|omvis|"
                    r"heritage|exhibition|kunsthal|kurator|curator|konservator|conservator", re.I)
# well-known museums & heritage institutions in/around Copenhagen whose names don't say "museum"
INSTITUTIONS = re.compile(r"nationalmuseet|\bsmk\b|statens museum|glyptotek|louisiana|arken|rundetaarn|"
                          r"kongelige samling|kongernes samling|rosenborg|amalienborg|davids samling|"
                          r"hirschsprung|thorvaldsen|ordrupgaard|nivaagaard|experimentarium|"
                          r"naturhistorisk|botanisk have|frilandsmuseet|cisternerne|kronborg|frederiksborg|"
                          r"kgl\.? bibliotek|kongelige bibliotek|rigsarkiv|slots- og kulturstyrelsen|"
                          r"cinematek|enigma|tøjhus|vikingeskib|kunstforening|kunsthal", re.I)
RESEARCH = re.compile(r"forsknings?|videnskabelig assistent|research assistant|feltarbejde|fieldwork|"
                      r"interview|etnografi|ethnograph", re.I)

# Requirements we can't meet with a BSc
HIGH_DEGREE_REQ = re.compile(
    r"(?:har|have|hold|holds|med|with|afsluttet|færdiguddannet|uddannet)\s+(?:en\s+|a\s+)?"
    r"(?:relevant\s+)?(?:kandidatgrad|kandidatuddannelse|cand\.|master'?s|ph\.?d|phd|doctoral)"
    r"|(?:kandidatgrad|ph\.?d\.?-?grad|phd degree|master'?s degree|doktorgrad)"
    r"|(?:krav|required|requirement|forudsætning)[^.]{0,60}(?:kandidat|cand\.|master|ph\.?d)"
    r"|(?:kandidat|cand\.|master|ph\.?d)[^.]{0,40}(?:er et krav|is required|is a requirement)",
    re.I)
MSC_STUDENT_ONLY = re.compile(r"kandidatstuderende|studerende på (?:din |en )?kandidat(?:uddannelse|delen)|"
                              r"enrolled in a master|master'?s student", re.I)
BSC_OK = re.compile(r"bachelor|\bba\b|\bbsc\b|b\.sc|uanset uddannelse|alle uddannelser|"
                    r"ingen (?:særlige )?krav|no (?:formal )?requirements|1\.?\s*-\s*3\.? semester|"
                    r"(?:1|2|3)\. (?:år|semester)", re.I)
SENIOR_TITLE = re.compile(r"postdoc|post\.doc|ph\.?d\.?-?(?:stipendi|studerende|student|fellow)|phd (?:fellow|position)|"
                          r"adjunkt|lektor|professor|museumsinspektør|forskningschef|leder|chef\b|director", re.I)

STUDENT = re.compile(r"student|studerende|studiejob|praktik|frivillig|volunteer|deltid|part[- ]time|"
                     r"timelønnet|omviser|tilkaldevikar|guide\b", re.I)
FULLTIME = re.compile(r"\b37 timer|37 hours|fuldtid|fuld tid|full[- ]time|fuldtidsstilling", re.I)
PARTTIME = re.compile(r"deltid|part[- ]time|studiejob|studenterjob|studentermedhjælp|student assistant|"
                      r"studentermedarbejder|(?:\b[1-9]|1\d|2[0-5])\s*(?:-\s*\d+\s*)?timer (?:om|pr\.?|per|i) ?(?:ugen|uge)|"
                      r"(?:\b[1-9]|1\d|2[0-5])\s*(?:-\s*\d+\s*)?hours (?:a|per) week|frivillig|volunteer|"
                      r"timeløn|tilkalde|weekend|aftenvagt|efter behov", re.I)

UNPAID = re.compile(r"ulønnet|unpaid|frivillig(?!t)|volunteer|uden løn|uden vederlag|ingen løn|"
                    r"no salary|ects|studiepoint|projektorienteret forløb|meritgivende|"
                    r"praktik(?:ophold|forløb)?\s+(?:på|i)\s+(?:dit|din|uddannelsen)", re.I)
PAID = re.compile(r"\bløn\b|lønnet|timeløn|lønniveau|aflønning|overenskomst|\bkr\.|kroner|"
                  r"salary|paid|\bhk\b|dm/|ac-?overenskomst|honorar|vederlag|lønramme", re.I)


# "Pink" jobs: hands-on museum work - presenting to an audience, helping with research,
# or building exhibitions. Matched in the title, or at least twice in the ad text.
FOCUS = {
    "formidling": re.compile(r"formidl|omvis|rundvis|guide|present|publikum|undervis|fortæl|talks?\b|"
                             r"skoletjeneste|familieaktivitet|workshop|audience|tour\b", re.I),
    "forskning": re.compile(r"forskning|research|videnskabelig assistent|feltarbejde|fieldwork|"
                            r"indsamling|registrering|interview|etnografi|ethnograph|dataindsamling", re.I),
    "udstilling": re.compile(r"udstilling|exhibition|kurat|curat|scenografi|ophængning|montering|"
                             r"installation of objects|samlingsarbejde", re.I),
}


def focus_areas(job):
    found = []
    for name, rx in FOCUS.items():
        if rx.search(job["title"]) or len(rx.findall(job.get("body") or job.get("snippet") or "")) >= 2:
            found.append(name)
    return found


_NUM = r"(\d{1,2}(?:[,.]5)?)(?:\s*(?:-|–|til|to)\s*(\d{1,2}(?:[,.]5)?))?"
HOURS = [  # (pattern, unit) - first match wins
    (re.compile(_NUM + r"\s*(?:timer|t\.)\s*(?:om|pr\.?|per|i|/)\s*(?:ugen|uge)\b", re.I), "uge"),
    (re.compile(_NUM + r"\s*(?:arbejds)?timer\s*ugentlig", re.I), "uge"),
    (re.compile(r"ugentlig(?:e|t)?\s*(?:arbejdstid|timetal)?\s*(?:er|på|af)?\s*(?:ca\.|cirka|op til|gennemsnitlig)?\s*"
                + _NUM + r"\s*timer", re.I), "uge"),
    (re.compile(_NUM + r"\s*hours?\s*(?:a|per|each|/)\s*week", re.I), "uge"),
    (re.compile(_NUM + r"\s*hours?\s*weekly", re.I), "uge"),
    (re.compile(_NUM + r"\s*timer\s*(?:om|pr\.?|per|i|/)\s*(?:måneden|måned)\b", re.I), "måned"),
    (re.compile(_NUM + r"\s*hours?\s*(?:a|per|/)\s*month", re.I), "måned"),
]


def weekly_hours(text):
    """Find the hours stated in the ad, e.g. '15 t/uge' or '10-15 t/uge'. '' if not stated."""
    for rx, unit in HOURS:
        m = rx.search(text)
        if m and m.group(1):
            lo, hi = m.group(1), m.group(2)
            return f"{lo}-{hi} t/{unit}" if hi else f"{lo} t/{unit}"
    return ""


def clean(text):
    text = html.unescape(text or "")
    return re.sub(r"\s+", " ", text).strip()


def evaluate(job):
    """Return (keep: bool, reason: str). Also sets job['paid'] and job['why']."""
    head = f"{job['title']} {job['company']}"
    text = f"{head} {job.get('snippet', '')} {job.get('body', '')}"

    # 1) relevance
    if not job.get("etype") and re.search(r"praktik", job["title"], re.I):
        job["etype"] = "Praktik"
    hits = []
    if ETNO_ANTRO.search(text):
        hits.append("etnologi/antropologi")
    if MUSEUM.search(head) or INSTITUTIONS.search(head):
        hits.append("museum/kulturarv")
    if RESEARCH.search(text) and hits:
        hits.append("forskning")
    if not hits:
        return False, "not museum/etnologi/antropologi related"
    job["why"] = ", ".join(hits)

    # 2) degree level
    if SENIOR_TITLE.search(job["title"]):
        return False, "senior/academic title (needs MSc/PhD)"
    if HIGH_DEGREE_REQ.search(text):
        return False, "requires a finished MSc/PhD"
    if MSC_STUDENT_ONLY.search(text) and not BSC_OK.search(text):
        return False, "only for MSc (kandidat) students"

    # 3) part-time only
    is_volunteer = job["source"] == "frivilligjob.dk" or job.get("etype") == "Frivillig"
    student_job = (job.get("etype") in ("Studiejob", "Frivillig", "Timeansat", "Praktik")
                   or is_volunteer or STUDENT.search(job["title"]))
    if not student_job:
        return False, "not a student/part-time job"
    if FULLTIME.search(text) and not PARTTIME.search(text):
        return False, "full-time position"
    job["hours"] = weekly_hours(text)
    if job["hours"].endswith("t/uge") and float(re.match(r"[\d.,]+", job["hours"]).group().replace(",", ".")) >= 30:
        return False, f"full-time position ({job['hours']})"

    job["focus"] = focus_areas(job)

    # 4) paid / unpaid
    if is_volunteer:
        job["paid"] = False
    elif job.get("etype") == "Praktik":
        job["paid"] = bool(PAID.search(text)) and not UNPAID.search(text)
    elif UNPAID.search(text) and not PAID.search(text):
        job["paid"] = False
    else:
        job["paid"] = True
    return True, "ok"

# ---------------------------------------------------------------- scraping

session = requests.Session()
session.headers.update(UA)


def get(url, **params):
    time.sleep(DELAY)
    r = session.get(url, params=params or None, timeout=30)
    r.raise_for_status()
    return r


def jobindex_search(query, geoareaid, etype=None, max_pages=3):
    """Yield raw result dicts from a Jobindex search."""
    for page in range(1, max_pages + 1):
        params = {"q": query, "geoareaid": geoareaid, "page": page}
        if etype:
            params["employment_type"] = etype
        try:
            h = get("https://www.jobindex.dk/jobsoegning?" + urlencode(params)).text
            i = h.index("Stash = ") + len("Stash = ")
            stash = json.JSONDecoder().raw_decode(h, i)[0]
            resp = stash["jobsearch/result_app/list"]["storeData"]["searchResponse"]
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"  ! Jobindex search failed ({query}, page {page}): {e}", file=sys.stderr)
            return
        yield from resp.get("results", [])
        if page >= (resp.get("total_pages") or 1):
            return


def jobindex_body(url):
    """Fetch the full ad text (Jobindex-hosted or external)."""
    try:
        r = get(url)
    except requests.RequestException:
        return "", url
    soup = BeautifulSoup(r.text, "html.parser")
    node = soup.select_one(".jobtext-jobad__body") or soup.select_one("main") or soup.body or soup
    for t in node(["script", "style", "nav", "footer", "header", "form"]):
        t.decompose()
    return clean(node.get_text(" "))[:15000], r.url


def collect_jobindex(areas):
    jobs = {}
    total = len(JOBINDEX_KEYWORDS) * len(areas) * (1 + len(EMPLOYMENT_TYPES))
    n = 0
    for kw in JOBINDEX_KEYWORDS:
        for area_name, area_id in areas.items():
            for etype in [None, *EMPLOYMENT_TYPES]:
                n += 1
                print(f"\r  Jobindex search {n}/{total}: {kw!r} in {area_name}        ", end="", flush=True)
                for r in jobindex_search(kw, area_id, etype):
                    tid = r.get("tid")
                    job = jobs.get(tid)
                    if job is None:
                        company = (r.get("company") or {}).get("name") or r.get("companytext") or ""
                        job = jobs[tid] = {
                            "id": tid, "source": "Jobindex",
                            "title": clean(r.get("headline")),
                            "company": clean(company),
                            "area": clean(r.get("area")) or area_name,
                            "url": r.get("url"), "share_url": r.get("share_url") or r.get("url"),
                            "deadline": (r.get("lastdate") or "")[:10],
                            "posted": r.get("firstdate") or "",
                            # drop Jobindex's button labels ("Vis interesse … LinkedIn") before the ad text
                            "snippet": re.sub(r"^.*?Via apps Facebook LinkedIn\s*", "",
                                              clean(re.sub(r"<[^>]+>", " ", r.get("html") or ""))),
                            "etype": None,
                        }
                    if etype and not job["etype"]:
                        job["etype"] = EMPLOYMENT_TYPES[etype]
    print()
    return list(jobs.values())


def collect_frivilligjob(area):
    jobs = {}
    base = [("key", ""), ("sprog", ""), ("fag[]", FRIVILLIG_CATEGORY)]
    base += [("geografi[]", g) for g in FRIVILLIG_MUNICIPALITIES[area]]
    for page in range(1, FRIVILLIG_MAX_PAGES + 1):
        print(f"\r  frivilligjob.dk: culture volunteering page {page}        ", end="", flush=True)
        try:
            time.sleep(DELAY)
            r = session.get("https://frivilligjob.dk/jobs", params=base + [("page", page)], timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"\n  ! frivilligjob.dk failed (page {page}): {e}", file=sys.stderr)
            break
        items = BeautifulSoup(r.text, "html.parser").select("div.job-item")
        new = 0
        for it in items:
            a = it.find("a", href=re.compile(r"/job/\d+"))
            if not a:
                continue
            url = a["href"]
            jid = re.search(r"/job/(\d+)", url).group(1)
            if jid in jobs:
                continue
            new += 1
            parts = [p for p in (clean(s) for s in it.stripped_strings) if p]
            # card layout: title | org | description | location | duration | updated ...
            jobs[jid] = {
                "id": "fj" + jid, "source": "frivilligjob.dk",
                "title": parts[0] if parts else "",
                "company": parts[1] if len(parts) > 1 else "",
                "snippet": parts[2] if len(parts) > 2 else "",
                "area": re.sub(r"(,\s*)+", ", ", parts[3]).strip(" ,") if len(parts) > 3 else "",
                "url": url, "share_url": url, "deadline": "", "posted": "", "etype": "Frivillig",
            }
        if not new:
            break
    print()
    return list(jobs.values())

# ---------------------------------------------------------------- output

def ansi_ok():
    if os.name == "nt":
        os.system("")  # enables ANSI colours in Windows terminals
    return sys.stdout is not None and sys.stdout.isatty()


def print_jobs(jobs, colour):
    G, R, P, D, B, X = (("\033[92m", "\033[91m", "\033[95m", "\033[2m", "\033[1m", "\033[0m") if colour
                        else ("",) * 6)
    for j in jobs:
        pay = G if j["paid"] else R
        c = P if j.get("focus") else pay
        tag = "LØNNET " if j["paid"] else "ULØNNET"
        new = f"{B}NY!{X} " if j.get("new") else ""
        focus = f"{P}{B}[{'/'.join(j['focus']).upper()}]{X} " if j.get("focus") else ""
        print(f"{pay}{B}[{tag}]{X} {focus}{new}{c}{j['title']}{X}")
        meta = " · ".join(x for x in [j["company"], j["area"], j.get("etype") or "",
                                       j.get("hours") or "timer ikke oplyst",
                                       f"frist {j['deadline']}" if j["deadline"] else ""] if x)
        print(f"   {meta}")
        print(f"   {D}match: {j['why']}  ·  {j['source']}{X}")
        print(f"   {j['share_url']}\n")


def write_html(jobs, rejected, path):
    def card(j):
        cls = ("paid" if j["paid"] else "unpaid") + (" focus" if j.get("focus") else "")
        label = "Lønnet" if j["paid"] else "Ulønnet"
        if j.get("focus"):
            label += f'</span><span class="badge fbadge">{html.escape(" · ".join(j["focus"]))}'
        meta = " · ".join(html.escape(x) for x in [j["company"], j["area"], j.get("etype") or ""] if x)
        dl = f'<span class="dl">Frist {html.escape(j["deadline"])}</span>' if j["deadline"] else ""
        new = '<span class="new">Ny</span>' if j.get("new") else ""
        hours = (f'<span class="hours">⏱ {html.escape(j["hours"])}</span>' if j.get("hours")
                 else '<span class="hours none">⏱ timer ikke oplyst</span>')
        snip = html.escape((j.get("body") or j.get("snippet") or "")[:260])
        return f"""<article class="job {cls}">
  <div class="top"><span><span class="badge">{label}</span>{new}</span>{dl}</div>
  <h2><a href="{html.escape(j['share_url'])}" target="_blank" rel="noopener">{html.escape(j['title'])}</a></h2>
  <p class="meta">{meta}</p>
  <p class="meta">{hours}</p>
  <p class="snip">{snip}…</p>
  <p class="why">Match: {html.escape(j['why'])} · {html.escape(j['source'])}</p>
</article>"""

    rej = "".join(f"<li><a href='{html.escape(j['share_url'])}' target='_blank'>{html.escape(j['title'])}</a> "
                  f"<span>— {html.escape(j['company'])}: {html.escape(why)}</span></li>"
                  for j, why in rejected)
    paid = sum(j["paid"] for j in jobs)
    doc = f"""<!doctype html><html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Museumsjob København</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1f; --mute:#6b6b70; --green:#1f8a4c; --greenbg:#e7f5ec;
        --red:#c0392b; --redbg:#fbeaea; --pink:#d63384; --pinkbg:#fbe7f1; --line:#e3e1dc; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#151515; --card:#1f1f1f; --ink:#eee; --mute:#a0a0a0;
        --green:#4cd283; --greenbg:#17301f; --red:#ff6b5e; --redbg:#3a1b19; --pink:#ff7eb9; --pinkbg:#3a1a2b;
        --line:#333; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, sans-serif; }}
main {{ max-width:900px; margin:0 auto; padding:32px 16px; }}
h1 {{ margin:0 0 4px; }} .sub {{ color:var(--mute); margin:0 0 20px; }}
.legend span {{ display:inline-block; margin-right:16px; font-weight:600; }}
.legend .g {{ color:var(--green); }} .legend .r {{ color:var(--red); }}
.filters {{ margin:16px 0 24px; display:flex; gap:8px; flex-wrap:wrap; }}
.filters button {{ border:1px solid var(--line); background:var(--card); color:var(--ink); padding:6px 14px;
        border-radius:999px; cursor:pointer; font:inherit; }}
.filters button.on {{ background:var(--ink); color:var(--bg); }}
.job {{ background:var(--card); border:1px solid var(--line); border-left:6px solid; border-radius:10px;
        padding:14px 18px; margin-bottom:14px; }}
.job.paid {{ border-left-color:var(--green); }} .job.unpaid {{ border-left-color:var(--red); }}
.top {{ display:flex; justify-content:space-between; align-items:center; }}
.badge {{ font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; padding:2px 8px; border-radius:4px; }}
.paid .badge {{ background:var(--greenbg); color:var(--green); }} .unpaid .badge {{ background:var(--redbg); color:var(--red); }}
.dl {{ font-size:13px; color:var(--mute); }}
.new {{ margin-left:8px; font-size:12px; font-weight:700; text-transform:uppercase; padding:2px 8px;
        border-radius:4px; background:var(--ink); color:var(--bg); }}
.hours {{ font-weight:600; color:var(--ink); }} .hours.none {{ font-weight:400; color:var(--mute); }}
.job h2 {{ font-size:18px; margin:8px 0 2px; }} .job h2 a {{ color:inherit; }}
.paid h2 a {{ color:var(--green); }} .unpaid h2 a {{ color:var(--red); }}
.job.focus {{ border-left-color:var(--pink); }} .job.focus h2 a {{ color:var(--pink); }}
.fbadge {{ margin-left:8px; background:var(--pinkbg) !important; color:var(--pink) !important; }}
.legend .p {{ color:var(--pink); }}
.meta {{ margin:0; color:var(--mute); font-size:14px; }} .snip {{ margin:8px 0; font-size:14px; }}
.why {{ margin:0; font-size:12px; color:var(--mute); }}
details {{ margin-top:32px; color:var(--mute); font-size:14px; }} details li span {{ color:var(--mute); }}
details a {{ color:var(--ink); }}
</style></head><body><main>
<h1>Museumsjob · København</h1>
<p class="sub">Deltids-/studiejob for etnologi & antropologi · BSc eller ingen krav · fundet {date.today():%d-%m-%Y}</p>
<p class="legend"><span class="p">● Formidling / forskning / udstilling ({sum(bool(j.get("focus")) for j in jobs)})</span><span class="g">● Lønnet ({paid})</span><span class="r">● Ulønnet ({len(jobs) - paid})</span></p>
<div class="filters"><button class="on" data-f="all">Alle</button><button data-f="focus">Formidling/forskning/udstilling</button><button data-f="paid">Lønnet</button><button data-f="unpaid">Ulønnet</button></div>
<section id="list">{''.join(card(j) for j in jobs) or '<p>Ingen job fundet lige nu.</p>'}</section>
<details><summary>Frasorteret ({len(rejected)})</summary><ul>{rej}</ul></details>
</main><script>
document.querySelectorAll('.filters button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.filters button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.job').forEach(j => j.style.display =
    b.dataset.f === 'all' || j.classList.contains(b.dataset.f) ? '' : 'none');
}});
</script></body></html>"""
    path.write_text(doc, encoding="utf-8")


HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / "seen.json"
EMAIL_CONFIG = HERE / "email_config.json"


def mark_new(jobs):
    """Flag jobs not seen in earlier runs, and remember them for next time."""
    try:
        seen = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    first_run = not seen
    for j in jobs:
        j["new"] = not first_run and j["id"] not in seen
        seen.setdefault(j["id"], date.today().isoformat())
    SEEN_FILE.write_text(json.dumps(seen, indent=1), encoding="utf-8")


def send_email(jobs, report_path):
    """Email a summary (with inline colours) plus the full HTML report as an attachment."""
    import smtplib
    from email.message import EmailMessage

    if os.environ.get("EMAIL_APP_PASSWORD"):  # GitHub Actions: settings come from repository secrets
        cfg = {"from": os.environ.get("EMAIL_FROM", ""), "to": os.environ.get("EMAIL_TO", ""),
               "app_password": os.environ["EMAIL_APP_PASSWORD"]}
    else:
        try:
            cfg = json.loads(EMAIL_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"! Can't read {EMAIL_CONFIG.name} ({e}) - see email_config.example.json", file=sys.stderr)
            return False
    if not cfg.get("app_password") or "xxxx" in cfg["app_password"] or not cfg.get("from"):
        print(f"! Email not set up: fill in {EMAIL_CONFIG.name} or the EMAIL_* secrets", file=sys.stderr)
        return False

    new = [j for j in jobs if j.get("new")]
    paid = sum(j["paid"] for j in jobs)
    rows = []
    for j in jobs:
        colour = "#d63384" if j.get("focus") else "#1f8a4c" if j["paid"] else "#c0392b"
        label = "LØNNET" if j["paid"] else "ULØNNET"
        if j.get("focus"):
            label += " · " + " / ".join(j["focus"]).upper()
        badge = ' <b style="background:#1d1d1f;color:#fff;padding:1px 6px;border-radius:3px;font-size:11px">NY</b>' \
            if j.get("new") else ""
        meta = " · ".join(html.escape(x) for x in [j["company"], j["area"], j.get("hours") or "timer ikke oplyst",
                                                  f"frist {j['deadline']}" if j["deadline"] else ""] if x)
        rows.append(f'<div style="border-left:5px solid {colour};padding:6px 12px;margin:0 0 12px">'
                    f'<span style="color:{colour};font-size:11px;font-weight:700">{label}</span>{badge}<br>'
                    f'<a href="{html.escape(j["share_url"])}" style="color:{colour};font-size:16px;font-weight:600">'
                    f'{html.escape(j["title"])}</a><br>'
                    f'<span style="color:#6b6b70;font-size:13px">{meta}</span></div>')
    body = (f'<div style="font-family:system-ui,Arial,sans-serif;max-width:640px">'
            f'<h2 style="margin:0 0 4px">Museumsjob · {date.today():%d-%m-%Y}</h2>'
            f'<p style="color:#6b6b70;margin:0 0 16px">{len(jobs)} job · '
            f'<span style="color:#1f8a4c">{paid} lønnet</span> · '
            f'<span style="color:#c0392b">{len(jobs) - paid} ulønnet</span> · {len(new)} nye siden sidst</p>'
            f'{"".join(rows) or "<p>Ingen job fundet i dag.</p>"}'
            + (f'<p><a href="{html.escape(os.environ["REPORT_URL"])}" style="font-weight:600">Åbn rapporten på '
               f'hjemmesiden</a></p>' if os.environ.get("REPORT_URL") else "")
            + '<p style="color:#6b6b70;font-size:12px">Hele rapporten er også vedhæftet (museumsjob.html).</p></div>')

    msg = EmailMessage()
    msg["Subject"] = (f"Museumsjob: {len(new)} nye job" if new else f"Museumsjob: ingen nye job ({len(jobs)} i alt)")
    msg["From"] = cfg["from"]
    msg["To"] = cfg.get("to") or cfg["from"]
    msg.set_content(f"{len(jobs)} job fundet, {len(new)} nye. Se vedhæftet rapport.")
    msg.add_alternative(body, subtype="html")
    msg.add_attachment(report_path.read_bytes(), maintype="text", subtype="html", filename="museumsjob.html")
    try:
        with smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"), int(cfg.get("smtp_port", 587)), timeout=30) as s:
            s.starttls()
            s.login(cfg["from"], cfg["app_password"])
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        print(f"! Email failed: {e}", file=sys.stderr)
        return False
    print(f"Email sent to {msg['To']}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Find museum / etnologi / antropologi student jobs around Copenhagen.")
    ap.add_argument("--area", choices=AREAS, default="kbh", help="kbh = Storkøbenhavn + Nordsjælland (default)")
    ap.add_argument("--show-rejected", action="store_true", help="also print the jobs that were filtered out")
    ap.add_argument("--no-html", action="store_true", help="don't write/open the HTML report")
    ap.add_argument("--no-open", action="store_true", help="write the HTML report but don't open the browser")
    ap.add_argument("--email", action="store_true", help="email the results (settings in email_config.json)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("Searching…")
    candidates = collect_jobindex(AREAS[args.area]) + collect_frivilligjob(args.area)

    # quick pre-filter so we only download ads that can pass the relevance check
    def maybe_relevant(j):
        head = f"{j['title']} {j['company']}"
        return (ETNO_ANTRO.search(f"{head} {j['snippet']}") or MUSEUM.search(head) or INSTITUTIONS.search(head)
                or (j["source"] == "Jobindex" and MUSEUM.search(j["snippet"])))
    pre = [j for j in candidates if maybe_relevant(j)]
    print(f"  {len(candidates)} unique ads, {len(pre)} look relevant - reading full ads…")
    for i, j in enumerate(pre, 1):
        print(f"\r  reading ad {i}/{len(pre)}   ", end="", flush=True)
        j["body"], final = jobindex_body(j["url"])
        if j["source"] == "Jobindex" and "jobindex.dk" not in final:
            j["share_url"] = final  # link straight to the employer's page
    print("\n")

    kept, rejected = [], []
    for j in pre:
        ok, why = evaluate(j)
        (kept if ok else rejected).append(j if ok else (j, why))
    mark_new(kept)
    # pink (formidling/forskning/udstilling) first, then paid, then Copenhagen city before suburbs, then deadline
    kept.sort(key=lambda j: (not j["focus"], not j["paid"], "københavn" not in j["area"].lower(),
                             j["deadline"] or "9999"))

    colour = ansi_ok()
    print_jobs(kept, colour)
    paid = sum(j["paid"] for j in kept)
    print(f"Found {len(kept)} jobs: {paid} paid, {len(kept) - paid} unpaid "
          f"({len(rejected)} relevant-looking ads filtered out).")

    if args.show_rejected:
        print("\nFiltered out:")
        for j, why in rejected:
            print(f"  - {j['title']} ({j['company']}): {why}")

    out = HERE / "museumsjob.html"
    if not args.no_html or args.email:
        write_html(kept, rejected, out)
        print(f"\nReport: {out}")
        if not args.no_open and not args.no_html:
            webbrowser.open(out.as_uri())
    if args.email and not send_email(kept, out):
        sys.exit(1)  # makes the GitHub Actions run fail, so GitHub emails you about it


if __name__ == "__main__":
    main()
