#!/usr/bin/env python3
"""Ajaa raportin selaimella ladatusta arvot.json-tiedostosta (kun Python ei pääse verkkoon).

Käyttö:  python raportti_tiedostosta.py arvot.json [--ilman-tekoalya] [--testi 2]
"""
import datetime as dt, json, sys
from pathlib import Path

import raportti as r

if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
    sys.exit("Käyttö: python raportti_tiedostosta.py arvot.json [--ilman-tekoalya] [--testi N]")
data = json.loads(Path(sys.argv.pop(1)).read_text(encoding="utf-8-sig"))


def hae_arvot(fund_id, today):
    text = data.get(str(fund_id))
    if not text:
        raise ValueError("rahaston tietoja ei löydy arvot.json-tiedostosta")
    rows = [(dt.date(int(y), int(m), int(d)), float(v.replace(" ", "").replace(",", ".")))
            for d, m, y, v in r.ROW.findall(text)]
    if not rows:
        raise ValueError("arvoja ei löytynyt tiedostosta")
    return sorted(rows)


r.hae_arvot = hae_arvot
r.main()