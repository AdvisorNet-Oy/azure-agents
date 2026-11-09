"""Lukee Rahastovertailu.xlsx-tiedoston (jos se löytyy) ja tuottaa raporttiin Taustatiedot-sarakkeen."""
import datetime as dt, html, os, re
from pathlib import Path


def _avain(nimi):
    s = str(nimi).lower().replace("danske invest", "").strip()
    return re.sub(r"\s+k$", "", s).strip()


def _pros(x, desimaalit=1, merkki=False):
    if not isinstance(x, (int, float)):
        return None
    return f"{x * 100:{'+' if merkki else ''}.{desimaalit}f}".replace(".", ",") + " %"


def lataa(here):
    """Palauttaa {avain: tiedot} tai None, jos Exceliä ei ole / sitä ei voi lukea."""
    polku = Path(os.environ.get("RAHASTOVERTAILU") or Path(here) / "Rahastovertailu.xlsx")
    if not polku.exists():
        return None
    try:
        import openpyxl
        ws = openpyxl.load_workbook(polku, data_only=True).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        hi = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == "Rahasto")
        otsikot = [str(c or "") for c in rows[hi]]
        col = lambda alku: next((i for i, o in enumerate(otsikot) if o.startswith(alku)), None)
        c = {"tuotto": col("Toteutunut"), "heil": col("Heilunta"), "kulut": col("Kulut"),
             "odo": col("Tuotto-odotus"), "ala": col("Vaihteluväli, alaraja"),
             "yla": col("Vaihteluväli, yläraja"), "nak": col("Danske Bankin")}
        get = lambda r, k: r[c[k]] if c[k] is not None and c[k] < len(r) else None
        out = {"_pvm": dt.date.fromtimestamp(polku.stat().st_mtime)}
        for r in rows[hi + 1:]:
            if not r or not r[0] or r[1] is None:
                continue  # tyhjät rivit ja alaviitteet
            out[_avain(r[0])] = {k: get(r, k) for k in c}
        return out
    except Exception as e:  # raportti syntyy silti ilman Exceliä
        print("Excelin luku epäonnistui (ohitetaan):", e)
        return None


def solu(lisa, nimi):
    t = (lisa or {}).get(_avain(nimi))
    if not t:
        return '<td class="na">–</td>'
    osat = []
    osat.append("1 v: " + (_pros(t["tuotto"], 1, True) or "ei vielä historiaa"))
    if isinstance(t["heil"], (int, float)):
        osat.append(f"heilunta {t['heil']:g} %".replace(".", ","))
    if _pros(t["kulut"], 2):
        osat.append("kulut " + _pros(t["kulut"], 2) + " / v")
    rivi2 = ""
    if _pros(t["odo"]):
        rivi2 = "Tuotto-odotus (arvio) " + _pros(t["odo"])
        if _pros(t["ala"]) and _pros(t["yla"]):
            rivi2 += f" ({_pros(t['ala'], 1, True)} … {_pros(t['yla'], 1, True)})"
    nak = f"Danske Bank: {t['nak']}" if t["nak"] else ""
    teksti = html.escape(" · ".join(osat)) + "".join("<br>" + html.escape(x) for x in (rivi2, nak) if x)
    return f'<td class="ev"><small>{teksti}</small></td>'