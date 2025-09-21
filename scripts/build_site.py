#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import datetime as dt
import json, os, re, hashlib, pathlib
from typing import Dict, List, Tuple

FR_MONTHS = {
    "janvier":1,"février":2,"fevrier":2,"mars":3,"avril":4,"mai":5,"juin":6,
    "juillet":7,"août":8,"aout":8,"septembre":9,"octobre":10,"novembre":11,
    "décembre":12,"decembre":12
}

def clean(s:str)->str:
    return re.sub(r"[ \t\r\f\v]+"," ",(s or "").replace("\xa0"," ")).strip()

def parse_day_label(label:str)->Tuple[int,int]:
    lab = clean(label).lower()
    m = re.search(r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})\s+([a-zéèêàâîôûùç]+)", lab)
    if not m: raise ValueError("bad day label")
    day = int(m.group(2))
    mon = (m.group(3)
           .replace("é","e").replace("è","e").replace("ê","e")
           .replace("à","a").replace("â","a").replace("î","i")
           .replace("ô","o").replace("û","u").replace("ù","u")
           .replace("ç","c"))
    month = FR_MONTHS[mon]
    return month, day

def parse_time(s:str)->Tuple[int,int]:
    s = s.replace("h",":")
    h,m = s.split(":")
    return int(h), int(m)

def ical_dt(d:dt.datetime)->str:
    return d.strftime("%Y%m%dT%H%M%S")

def ics_escape(s:str)->str:
    s = s.replace("\\","\\\\").replace(";","\\;").replace(",","\\,")
    s = s.replace("\r\n","\n").replace("\r","\n").replace("\n","\\n")
    return s

def fold_ics_line(line:str)->str:
    # RFC 5545: fold >75 octets -> on laisse simple (Outlook tolère)
    return line

def make_event(uid_seed:str, start:dt.datetime, end:dt.datetime,
               summary:str, location:str, description:str)->str:
    uid = hashlib.sha1(uid_seed.encode("utf-8")).hexdigest()+"@edt-scrap"
    lines = [
        "BEGIN:VEVENT",
        "UID:"+uid,
        "DTSTAMP:"+ical_dt(dt.datetime.utcnow()),
        "DTSTART;TZID=Europe/Paris:"+ical_dt(start),
        "DTEND;TZID=Europe/Paris:"+ical_dt(end),
        "SUMMARY:"+ics_escape(summary or "Cours"),
        "LOCATION:"+ics_escape(location or ""),
        "DESCRIPTION:"+ics_escape(description or ""),
        "END:VEVENT",
    ]
    return "\r\n".join(fold_ics_line(x) for x in lines)

def write_ics(path:str, events_ics:List[str]):
    head = [
        "BEGIN:VCALENDAR",
        "PRODID:-//EDT-scrap//TheoDlcq//FR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:EDT (jusqu'à 8 semaines)",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    body = "\r\n".join(events_ics)
    data = "\r\n".join(head) + "\r\n" + body + "\r\nEND:VCALENDAR\r\n"
    with open(path,"wb") as f:
        f.write(data.encode("utf-8"))

def render_index(path:str, weeks:List[str], n_events:int, site_base:str):
    weeks = sorted(weeks)
    html = f"""<!doctype html>
<html lang="fr"><meta charset="utf-8">
<title>EDT – 8 semaines</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 16px;line-height:1.5">
  <h1>Emploi du temps (jusqu'à 8 semaines)</h1>
  <p><a href="webcal://{site_base.split('://')[-1]}/ical.ics">S’abonner (Outlook/Apple)</a> ·
     <a href="{site_base}/ical.ics">Télécharger l’ICS</a></p>
  <p><b>{n_events}</b> événements · Semaines: {", ".join(weeks)}</p>
  <p><small>Dernière mise à jour: {dt.datetime.now().strftime("%Y-%m-%d %H:%M")}</small></p>
</body></html>"""
    with open(path,"w",encoding="utf-8") as f:
        f.write(html)

def main():
    import argparse, glob
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="EDT (8 semaines)")
    ap.add_argument("--site-base", required=True)
    args = ap.parse_args()

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    files = sorted(glob.glob(os.path.join(args.json_dir, "*.json")))
    events: List[Tuple[dt.datetime, str]] = []
    weeks = []

    for jf in files:
        week_monday = pathlib.Path(jf).stem  # YYYY-MM-DD
        weeks.append(week_monday)
        with open(jf,"r",encoding="utf-8") as f:
            data: Dict[str, List[Dict[str,str]]] = json.load(f)

        # année probable à partir du lundi
        year = dt.date.fromisoformat(week_monday).year

        for day_label, items in data.items():
            try:
                month, day = parse_day_label(day_label)
            except Exception:
                continue
            for ev in items:
                if not ev.get("start") or not ev.get("end"):
                    continue
                try:
                    sh, sm = parse_time(ev["start"])
                    eh, em = parse_time(ev["end"])
                except Exception:
                    continue
                start = dt.datetime(year, month, day, sh, sm)
                end   = dt.datetime(year, month, day, eh, em)
                title = ev.get("title") or "Cours"
                site  = ev.get("site") or ""
                room  = ev.get("room") or ""
                teacher = ev.get("teacher") or ""
                location = (f"{room} ({site})" if site and room else site or room)
                desc = "\n".join([l for l in [teacher, ev.get("raw","")] if l]).strip()
                uid_seed = f"{week_monday}|{month:02d}-{day:02d}|{ev.get('start')}|{ev.get('end')}|{title}|{room}|{site}"
                ics = make_event(uid_seed, start, end, title, location, desc)
                events.append((start, ics))

    events.sort(key=lambda x: x[0])
    write_ics(os.path.join(args.out, "ical.ics"), [e[1] for e in events])
    render_index(os.path.join(args.out, "index.html"), weeks, len(events), args.site_base)

if __name__ == "__main__":
    main()
