#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds week.ics and index.html from week.json produced by fetch_edt_requests.py
Usage:
  python scripts/build_site.py --json public/week.json --monday YYYY-MM-DD --out public
"""
import argparse, json, datetime as dt, re, hashlib, pathlib, html, sys

DOW = ['lundi','mardi','mercredi','jeudi','vendredi','samedi','dimanche']

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json", required=True, help="Path to week.json")
    p.add_argument("--monday", required=True, help="ISO date for Monday (YYYY-MM-DD)")
    p.add_argument("--out", default="public", help="Output directory")
    args = p.parse_args()

    outdir = pathlib.Path(args.out)
    outdir.mkdir(exist_ok=True, parents=True)

    monday = dt.date.fromisoformat(args.monday)
    try:
        data = json.load(open(args.json, encoding="utf-8"))
    except Exception as e:
        print(f"Cannot read {args.json}: {e}", file=sys.stderr)
        sys.exit(1)

    def uid(s):
        return hashlib.md5(s.encode("utf-8")).hexdigest() + "@theodlcq"

    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//TheoDlcq//EDT//FR",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
    ]
    tz="Europe/Paris"

    for day_label, evs in data.items():
        m = re.match(r"\s*([A-Za-zéèêàâîôûùç]+)", day_label, flags=re.I)
        if not m:
            continue
        dow = m.group(1).lower()
        try:
            idx = DOW.index(dow)
        except ValueError:
            continue
        day_date = monday + dt.timedelta(days=idx)

        for ev in evs:
            start, end = ev.get("start"), ev.get("end")
            title = ev.get("title") or ev.get("raw") or "Cours"
            if not start or not end:
                continue
            h1, m1 = map(int, start.split(":")); h2, m2 = map(int, end.split(":"))
            dtstart = dt.datetime.combine(day_date, dt.time(h1, m1))
            dtend   = dt.datetime.combine(day_date, dt.time(h2, m2))
            room = ev.get("room",""); site = ev.get("site","")
            loc = ", ".join([p for p in [room, site] if p])
            desc = []
            if ev.get("teacher"): desc.append(f"Prof: {ev['teacher']}")
            if loc: desc.append(f"Salle/Site: {loc}")
            desc_txt = "\\n".join(desc)
            uid_key = f"{title}-{dtstart.isoformat()}-{dtend.isoformat()}-{loc}"
            lines += [
                "BEGIN:VEVENT",
                f"UID:{uid(uid_key)}",
                f"DTSTAMP:{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART;TZID={tz}:{dtstart.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID={tz}:{dtend.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{title}",
                *( [f"LOCATION:{loc}"] if loc else [] ),
                *( [f"DESCRIPTION:{desc_txt}"] if desc_txt else [] ),
                "END:VEVENT",
            ]

    lines.append("END:VCALENDAR")
    (outdir/'week.ics').write_text('\r\n'.join(lines), encoding='utf-8')

    # HTML listing
    def esc(s): return html.escape(s or '')
    rows=[]
    for day_label, evs in data.items():
        rows.append(f"<h2>{esc(day_label)}</h2><ul>")
        for ev in evs:
            t = ((ev.get('start') or '') + ('-' if ev.get('start') and ev.get('end') else '') + (ev.get('end') or ''))
            parts=[esc(t)]
            if ev.get('room'): parts.append("Salle: "+esc(ev['room']))
            if ev.get('site'): parts.append("Site: "+esc(ev['site']))
            if ev.get('teacher'): parts.append("Prof: "+esc(ev['teacher']))
            title = ev.get('title') or ev.get('raw') or ''
            rows.append("<li>"+' | '.join([p for p in parts if p])+" – "+esc(title)+"</li>")
        rows.append("</ul>")
    (outdir/'index.html').write_text(
        "<!doctype html><meta charset='utf-8'><title>EDT</title>"
        "<h1>Emploi du temps</h1><p><a href='week.ics'>⤓ S'abonner (ICS)</a></p>"
        + ''.join(rows),
        encoding='utf-8'
    )
    print("Built", outdir/'week.ics', outdir/'index.html')

if __name__ == "__main__":
    main()
