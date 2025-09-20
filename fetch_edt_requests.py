#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scraper Wigor (WS-EDT) avec login CAS automatique.
- Détecte la page CAS (form#fm1), poste username/password + champs cachés requis,
- Suis les redirections, puis extrait la semaine complète (Lundi→Dimanche),
- Peut exporter un calendrier ICS (Outlook, Google, Apple) pour 1..N semaines,
- Sauvegarde la dernière page obtenue dans edt_page.html (debug).

Dépendances: requests, beautifulsoup4
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

# --------- Configuration par défaut ---------

DEFAULT_URL = (
    "https://ws-edt-cd.wigorservices.net/WebPsDyn.aspx?"
    "action=posEDTLMS&serverID=C&Tel=theo.declercq&date=09/15/2025&"
    "hashURL=582A121573DF46EF1403A9752280F436AC7C7A3920EB6B4A58C7F7B0C66F34786FE24E25E0E64CEB30C27FC8109539BAC3078CC08C7E1CBC8C8C7BEFEF8FAC77"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
}
DOW_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# -------------------- utilitaires date / url --------------------

def iso_monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())

def to_mmddyyyy(d: dt.date) -> str:
    return d.strftime("%m/%d/%Y")

def set_url_date(url: str, target_date: dt.date) -> str:
    """Remplace la date=MM/DD/YYYY dans l'URL (évite l'erreur 'invalid group reference')."""
    mmdd = to_mmddyyyy(target_date)
    if "date=" in url:
        return re.sub(r"(date=)\d{2}/\d{2}/\d{4}", lambda m: m.group(1) + mmdd, url, count=1)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}date={mmdd}"

# -------------------- réseau / login CAS --------------------

def save_debug(html: str, fname: str = "edt_page.html") -> None:
    try:
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass

def is_cas_login_page(html: str) -> bool:
    # Heuristique robuste: form id="fm1" + input name="execution"
    return ('id="fm1"' in html or "id='fm1'" in html) and 'name="execution"' in html

def collect_form_inputs(form: Tag) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "").lower()
        if itype in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        data[name] = inp.get("value") or ""
    return data

def login_cas_if_needed(s: requests.Session, first_resp: requests.Response, user: str, pwd: str) -> Optional[str]:
    html = first_resp.text
    if not is_cas_login_page(html):
        return html

    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="fm1")
    if not form:
        return None

    action = form.get("action") or ""
    login_url = urljoin(first_resp.url, action)
    payload = collect_form_inputs(form)
    payload["username"] = user
    payload["password"] = pwd
    payload.setdefault("_eventId", "submit")
    payload.setdefault("geolocation", "")
    payload.setdefault("deviceFingerprint", "0")

    print("🔐 CAS détecté – tentative d’authentification…")
    try:
        resp2 = s.post(login_url, data=payload, allow_redirects=True, timeout=30)
    except requests.RequestException as e:
        print(f"❌ POST CAS échoué: {e}", file=sys.stderr)
        save_debug(html, "edt_page.html")
        return None

    if is_cas_login_page(resp2.text):
        print("❌ Échec de connexion CAS (identifiant/mot de passe).", file=sys.stderr)
        save_debug(resp2.text, "edt_page.html")
        return None
    return resp2.text

def get_authenticated_html(url: str, user: Optional[str], pwd: Optional[str]) -> Optional[str]:
    with requests.Session() as s:
        s.headers.update({"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
        try:
            r1 = s.get(url, allow_redirects=True, timeout=30)
        except requests.RequestException as e:
            print(f"❌ Requête initiale échouée: {e}", file=sys.stderr)
            return None

        html = r1.text
        if is_cas_login_page(html):
            user = user or os.getenv("WIGOR_USER")
            pwd = pwd or os.getenv("WIGOR_PASS")
            if not user or not pwd:
                # Invite en interactif si possible
                try:
                    import getpass
                    if not user:
                        user = input("Identifiant CAS: ").strip()
                    if not pwd:
                        pwd = getpass.getpass("Mot de passe CAS: ")
                except Exception:
                    print("❌ Identifiants CAS manquants (WIGOR_USER / WIGOR_PASS).", file=sys.stderr)
                    return None

            html = login_cas_if_needed(s, r1, user, pwd)
            if html is None:
                return None

            # Recharge la page cible pour initialiser la session côté service
            try:
                r_final = s.get(url, allow_redirects=True, timeout=30)
                html = r_final.text
            except requests.RequestException as e:
                print(f"❌ Erreur lors du chargement post-login: {e}", file=sys.stderr)
                return None

        return html

# -------------------- parsing EDT --------------------

def parse_pct_left(style: str) -> Optional[float]:
    if not style:
        return None
    m = re.search(r"left\s*:\s*([-\d.]+)\s*%", style, flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None

def text_clean(s: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", (s or "").replace("\xa0", " ")).strip()

def find_divs_by_class(soup: BeautifulSoup, class_name: str) -> List[Tag]:
    pat = re.compile(rf"(?:^|\s){re.escape(class_name)}(?:\s|$)", re.I)
    out: List[Tag] = []
    for d in soup.find_all("div"):
        classes = d.get("class")
        if isinstance(classes, list):
            joined = " ".join(c for c in classes if isinstance(c, str))
        elif isinstance(classes, str):
            joined = classes
        else:
            joined = ""
        if pat.search(joined):
            out.append(d)
    return out

def parse_day_header_date(label: str) -> Optional[dt.date]:
    lab = text_clean(label).lower()
    m = re.search(
        r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})\s+([a-zéèêàâîôûùç]+)",
        lab, flags=re.I
    )
    if not m:
        return None
    day = int(m.group(2))
    mon_str = (m.group(3)
               .replace("é", "e").replace("è", "e").replace("ê", "e")
               .replace("à", "a").replace("â", "a")
               .replace("î", "i").replace("ô", "o").replace("û", "u").replace("ù", "u")
               .replace("ç", "c"))
    month = FR_MONTHS.get(mon_str)
    if not month:
        return None
    try:
        return dt.date(1900, month, day)  # année factice pour comparer (mois, jour)
    except ValueError:
        return None

def week_panel_index_for_target(soup: BeautifulSoup, target_monday: dt.date) -> Optional[int]:
    jours = find_divs_by_class(soup, "Jour")
    panels: Dict[int, List[Tuple[float, str, Tuple[int, int]]]] = {}
    for j in jours:
        style = j.get("style", "")
        left = parse_pct_left(style)
        if left is None:
            continue
        panel = int(left // 100.0)
        td = j.find("td", class_=re.compile(r"\bTCJour\b", re.I))
        text_label = td.get_text(" ", strip=True) if td else j.get_text(" ", strip=True)
        d = parse_day_header_date(text_label)
        if not d:
            continue
        panels.setdefault(panel, []).append((left, text_label, (d.month, d.day)))

    if not panels:
        return None

    target_days_md = {((target_monday + dt.timedelta(days=i)).month,
                       (target_monday + dt.timedelta(days=i)).day) for i in range(7)}

    best_panel, best_score = None, -1
    for p, items in panels.items():
        md_in_panel = {(m, d) for _, _, (m, d) in items}
        score = len(md_in_panel & target_days_md)
        if score > best_score:
            best_score = score
            best_panel = p
    return best_panel

def day_headers_for_panel(soup: BeautifulSoup, panel_index: int) -> List[Tuple[float, str]]:
    res: List[Tuple[float, str]] = []
    for j in find_divs_by_class(soup, "Jour"):
        left = parse_pct_left(j.get("style", ""))
        if left is None:
            continue
        if int(left // 100.0) != panel_index:
            continue
        td = j.find("td", class_=re.compile(r"\bTCJour\b", re.I))
        label = td.get_text(" ", strip=True) if td else j.get_text(" ", strip=True)
        res.append((left, text_clean(label)))
    return sorted(res, key=lambda x: x[0])

def extract_cases_for_panel(soup: BeautifulSoup, panel_index: int) -> List[Tag]:
    all_cases = find_divs_by_class(soup, "Case")
    out: List[Tag] = []
    for c in all_cases:
        if c.get("id", "").lower() in {"avant", "apres"}:
            continue
        left = parse_pct_left(c.get("style", ""))
        if left is None or int(left // 100.0) != panel_index:
            continue
        # conserve même si pas de table (certains messages "Pas de cours" sont là)
        out.append(c)
    return out

def _time_to_minutes(s: str) -> int:
    if not s:
        return 99_999
    s = s.replace("h", ":")
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        return 99_999
    return int(m.group(1)) * 60 + int(m.group(2))

def case_payload(case: Tag) -> Dict[str, str]:
    txt_full = case.get_text("\n", strip=True)
    lines = [l.strip() for l in txt_full.splitlines() if l.strip()]
    full = "\n".join(lines)

    time_pat = r"(\d{1,2}[:h]\d{2})\s*-\s*(\d{1,2}[:h]\d{2})"
    start = end = ""
    m = re.search(time_pat, full, flags=re.I)
    if m:
        start = m.group(1).replace("h", ":")
        end = m.group(2).replace("h", ":")

    room = site = ""
    for l in lines:
        mm = re.search(r"^salle\s*:?\s*([A-Za-z0-9_\- ]+)(?:\(([^)]+)\))?$", l, flags=re.I)
        if mm:
            room = mm.group(1).strip()
            site = (mm.group(2) or "").strip()
            break

    def pick_title(ls: List[str]) -> str:
        time_pat_local = r"(\d{1,2}[:h]\d{2})\s*-\s*(\d{1,2}[:h]\d{2})"
        for s in ls:
            low = s.lower()
            if re.search(time_pat_local, low):
                continue
            if low.startswith(("salle", "site", "socle ", "promotion ", "groupe ")):
                continue
            return s
        return ""
    title = pick_title(lines)

    teacher = ""
    seen_title = False
    for s in lines:
        if not seen_title:
            if s == title:
                seen_title = True
            continue
        low = s.lower()
        if re.search(time_pat, low) or low.startswith(("salle", "site", "socle ", "promotion ", "groupe ")):
            continue
        if re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ' \-]{3,}$", s):
            teacher = s.strip()
            break

    return {
        "raw": text_clean(txt_full),
        "start": start,
        "end": end,
        "room": room,
        "site": site,
        "teacher": teacher,
        "title": title,
    }

def nearest_day_label_for_case(case: Tag, day_headers: List[Tuple[float, str]]) -> Optional[str]:
    left = parse_pct_left(case.get("style", "")) or 0.0
    left_mod = left % 100.0
    best, best_delta = None, 1e9
    for dl, label in day_headers:
        dmod = dl % 100.0
        delta = abs(left_mod - dmod)
        if delta < best_delta:
            best_delta = delta
            best = label
    return best

def build_week_map(cases: List[Tag], day_headers: List[Tuple[float, str]]) -> Dict[str, List[Dict[str, str]]]:
    if not day_headers:
        return {}
    by_day: Dict[str, List[Dict[str, str]]] = {label: [] for _, label in day_headers}
    for c in cases:
        inner_txt = text_clean(c.get_text(" ", strip=True)).lower()
        if "pas de cours" in inner_txt:
            first_label = day_headers[0][1]
            by_day[first_label].append(
                {"raw": "Pas de cours cette semaine", "start": "", "end": "", "room": "", "teacher": "", "title": ""}
            )
            continue
        label = nearest_day_label_for_case(c, day_headers) or day_headers[0][1]
        by_day.setdefault(label, []).append(case_payload(c))

    # tri intra-jour par heure
    for label in list(by_day.keys()):
        by_day[label].sort(key=lambda ev: _time_to_minutes(ev.get("start", "")))
    return by_day

def print_week(by_day: Dict[str, List[Dict[str, str]]]):
    for day_label, events in by_day.items():
        print(f"\n📅 {day_label}")
        for ev in events:
            t = (
                f"{ev.get('start','')}-{ev.get('end','')}"
                if ev.get('start') and ev.get('end')
                else "(horaire n/d)"
            )
            parts = [f"{t}"]
            if ev.get("room"):
                parts.append(f"Salle: {ev['room']}")
            if ev.get("site"):
                parts.append(f"Site: {ev['site']}")
            if ev.get("teacher"):
                parts.append(f"Prof: {ev['teacher']}")
            print("  • " + " | ".join(parts))
            if ev.get("title"):
                print(f"    ↳ {ev['title']}")

# -------------------- ICS export --------------------

def date_for_label_in_week(day_label: str, monday: dt.date) -> dt.date:
    low = (day_label or "").strip().lower()
    for i, name in enumerate(DOW_FR):
        if low.startswith(name):
            return monday + dt.timedelta(days=i)
    m = re.search(r"(\d{1,2}).+?(janvier|février|fevrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)", low)
    if m:
        day = int(m.group(1))
        month = FR_MONTHS[m.group(2).replace("û","u").replace("é","e")]
        try:
            return dt.date(monday.year, month, day)
        except ValueError:
            return monday
    return monday

def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def export_ics(weeks_payload: List[Tuple[dt.date, Dict[str, List[Dict[str, str]]]]],
               out_path: str, cal_name: str = "EDT Wigor") -> None:
    TZID = "Europe/Paris"
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//EDT-Wigor//Scraper//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
        "X-WR-TIMEZONE:Europe/Paris",
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Paris",
        "X-LIC-LOCATION:Europe/Paris",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0200",
        "TZNAME:CEST",
        "DTSTART:19700329T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0200",
        "TZOFFSETTO:+0100",
        "TZNAME:CET",
        "DTSTART:19701025T030000",
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    lines = header[:]
    nowstamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for monday, by_day in weeks_payload:
        for label, events in by_day.items():
            day = date_for_label_in_week(label, monday)
            for ev in events:
                if not ev.get("start") or not ev.get("end"):
                    continue
                dtstart = f"{day:%Y%m%d}T{ev['start'].replace(':','')}00"
                dtend   = f"{day:%Y%m%d}T{ev['end'].replace(':','')}00"
                title = _ics_escape(ev.get("title") or "Cours")
                loc = _ics_escape(" ".join([ev.get("room",""), f"({ev.get('site')})" if ev.get("site") else ""]).strip())
                descr = _ics_escape(ev.get("teacher") or "")
                uid_src = f"{dtstart}|{dtend}|{title}|{loc}|{descr}"
                uid = hashlib.md5(uid_src.encode("utf-8")).hexdigest() + "@edt-wigor"

                lines += [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{nowstamp}",
                    f"DTSTART;TZID={TZID}:{dtstart}",
                    f"DTEND;TZID={TZID}:{dtend}",
                    f"SUMMARY:{title}",
                    f"LOCATION:{loc}",
                    f"DESCRIPTION:{descr}",
                    "END:VEVENT",
                ]

    lines.append("END:VCALENDAR")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\r\n".join(lines))

# -------------------- main --------------------

def main():
    ap = argparse.ArgumentParser(description="Scraper Wigor (WS-EDT) avec login CAS – semaine(s) complète(s) + export ICS")
    ap.add_argument("--url", "-u", default=DEFAULT_URL, help="URL WS-EDT de base")
    ap.add_argument("--date", "-d", default=None, help="Date (YYYY-MM-DD) d’un jour de la semaine voulue (lundi conseillé)")
    ap.add_argument("--weeks", "-w", type=int, default=1, help="Nombre de semaines à exporter à partir de la date/lundi (défaut: 1)")
    ap.add_argument("--user", default=None, help="Identifiant CAS (sinon WIGOR_USER)")
    ap.add_argument("--password", "--pass", dest="password", default=None, help="Mot de passe CAS (sinon WIGOR_PASS)")
    ap.add_argument("--json", default=None, help="Chemin de sortie JSON (facultatif, première semaine uniquement)")
    ap.add_argument("--ics", default=None, help="Chemin de sortie .ics (facultatif)")
    args = ap.parse_args()

    # Date cible → lundi ISO
    if args.date:
        try:
            target = dt.date.fromisoformat(args.date)
        except ValueError:
            print("❌ --date doit être au format YYYY-MM-DD (ex: 2025-09-22)", file=sys.stderr)
            sys.exit(2)
    else:
        target = dt.date.today()
    monday0 = iso_monday(target)

    weeks_payload: List[Tuple[dt.date, Dict[str, List[Dict[str, str]]]]] = []

    for k in range(max(1, args.weeks)):
        monday = monday0 + dt.timedelta(days=7*k)
        url = set_url_date(args.url, monday)
        print(f"→ Récupération : {url}")

        html = get_authenticated_html(url, args.user, args.password)
        if html is None:
            sys.exit(1)

        save_debug(html, "edt_page.html")
        soup = BeautifulSoup(html, "html.parser")

        panel = week_panel_index_for_target(soup, monday)
        if panel is None:
            print("ℹ️ Impossible d’identifier la semaine (pas d’en-têtes).")
            print("ℹ️ Ouvre 'edt_page.html' pour vérifier si l’EDT s’est bien chargé (et pas une autre page).")
            continue

        day_headers = day_headers_for_panel(soup, panel)
        cases = extract_cases_for_panel(soup, panel)
        nb_tables = sum(1 for c in cases if c.find("table", class_=re.compile(r"\bTCase\b", re.I)))
        print(f"✔ Panneau {panel} sélectionné – {len(day_headers)} en-têtes de jour")
        print(f"✔ {len(day_headers)} entêtes de jour, {len(cases)} blocs Case (dont {nb_tables} avec tableau)")

        week_map = build_week_map(cases, day_headers)
        if k == 0:
            # affichage console sur la 1ère semaine
            print_week(week_map)
            if args.json:
                with open(args.json, "w", encoding="utf-8") as f:
                    json.dump(week_map, f, ensure_ascii=False, indent=2)
                print(f"💾 Export JSON : {args.json}")

        weeks_payload.append((monday, week_map))

    if args.ics:
        export_ics(weeks_payload, args.ics)
        print(f"💾 Export ICS : {args.ics}")

if __name__ == "__main__":
    main()
