#!/usr/bin/env python3
"""Mandatum / Danske Invest -rahastoraportti.

Arvot (osuuden arvo, päiväkohtainen) haetaan Danske Investin omalta sivulta (danskeinvest.fi).
Prosenttimuutokset lasketaan koodissa 4 / 12 / 36 viikon ajalta. Merkittävin tapahtuma
selvitetään Clauden verkkohaulla (ANTHROPIC_API_KEY).

Aja:  python raportti.py                 koko raportti
      python raportti.py --testi 2       vain 2 ensimmäistä rahastoa
      python raportti.py --ilman-tekoalya  vain luvut (ei tarvitse API-avainta)
"""
import argparse, concurrent.futures as cf, datetime as dt, html, json, os, re, smtplib, sys
import urllib.parse, urllib.request
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Helsinki")
MODEL = os.environ.get("RAPORTTI_MALLI", "claude-sonnet-5-5")
PERIODS = (("4 vk", 28), ("12 vk", 84), ("36 vk", 252))
URL = "https://www.danskeinvest.fi/w/show_hist.hist_data"
ROW = re.compile(r'<td nowrap>(\d\d)\.(\d\d)\.(\d{4})</td>\s*<td class="tright" nowrap>([\d\s.,]+)</td>')


def hae_arvot(fund_id, today):
    """Palauttaa lajitellun listan [(date, arvo), ...] viimeiseltä ~40 viikolta."""
    q = urllib.parse.urlencode({
        "p_nId": 59, "p_nFundgroup": 59, "p_nFund": fund_id,
        "p_vDatoFra": (today - dt.timedelta(days=280)).isoformat(),
        "p_vDatoTil": today.isoformat(), "p_vFormat": "TABEL", "p_vType": "DAG", "p_vFelter": "IV;"})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "Mozilla/5.0 rahastoraportti"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8", "replace")
    rows = [(dt.date(int(y), int(m), int(d)), float(v.replace(" ", "").replace(",", ".")))
            for d, m, y, v in ROW.findall(text)]
    if not rows:
        raise ValueError("Arvoja ei löytynyt vastauksesta")
    return sorted(rows)


def laske(rows, today):
    """Muutokset (nyt vs. viimeisin arvo tavoitepäivänä tai sitä ennen, enintään 10 vrk aiemmin)."""
    nyt_pvm, nyt = rows[-1]
    out = {"nyt": (nyt_pvm, nyt), "muutokset": {}}
    for nimi, days in PERIODS:
        tavoite = nyt_pvm - dt.timedelta(days=days)
        vanhat = [(d, v) for d, v in rows if d <= tavoite and (tavoite - d).days <= 10]
        out["muutokset"][nimi] = None if not vanhat else (vanhat[-1][0], vanhat[-1][1],
                                                           (nyt / vanhat[-1][1] - 1) * 100)
    return out

PROMPT = """Rahasto: "{name}" (Danske Invest, K-osuussarja, saatavilla mm. Mandatumin kautta). \
Tänään on {today}.

Rahasto-osuuden arvon muutos (todelliset arvot Danske Investin sivulta):
{muutokset}

Selvitä verkkohaun avulla MERKITTÄVIN tapahtuma (markkina-, makro-, sektori-, yhtiö- tai \
rahastokohtainen uutinen), joka on vaikuttanut rahaston arvoon viimeisten 36 viikon aikana, ja \
erikseen merkittävin tapahtuma viimeisen 4 viikon ajalta. Käytä rahaston omia kuukausi- ja \
salkunhoitajakatsauksia (danskeinvest.fi), kun niitä löytyy. Selitä, miksi arvo nousi tai laski. \
Älä keksi: jos syytä ei löydy, sano se, ja merkitse oma päätelmäsi päätelmäksi.

Vastaa VAIN yhdellä JSON-objektilla, ei muuta tekstiä:
{{"tapahtuma_36vk": "1-2 lausetta suomeksi", "tapahtuma_4vk": "1-2 lausetta suomeksi", \
"lahteet": ["url", "url"]}}"""


def hae_tapahtumat(client, name, today, laskettu):
    m = "\n".join(f"- {n}: {'ei tietoa' if v is None else f'{v[2]:+.2f} %'}"
                  for n, v in laskettu["muutokset"].items())
    messages = [{"role": "user", "content": PROMPT.format(name=name, today=today.isoformat(), muutokset=m)}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
    for _ in range(6):  # jatka, jos palvelin keskeyttää pitkän haun (pause_turn)
        resp = client.messages.create(model=MODEL, max_tokens=3000, tools=tools, messages=messages)
        if resp.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": resp.content}]
    text = "".join(b.text for b in resp.content if b.type == "text")
    mt = re.search(r"\{.*\}", text, re.S)
    if not mt:
        raise ValueError("Tapahtumavastauksesta ei löytynyt JSONia")
    return json.loads(mt.group(0))

def fmt_pvm(d):
    return f"{d.day}.{d.month}."


def prosentti(v, base):
    if v is None:
        return '<td class="na">ei tietoa</td>'
    p = v[2]
    cls = "up" if p > 0.005 else "down" if p < -0.005 else "flat"
    arrow = "▲" if cls == "up" else "▼" if cls == "down" else "■"
    return (f'<td class="{cls}">{arrow} {f"{p:+.2f}".replace(".", ",")} %'
            f'<br><small>vs. {fmt_pvm(v[0])}</small></td>')


def render(rows, today, virheet, tekoaly):
    body = []
    for name, r in rows:
        if r is None:
            body.append(f'<tr><td class="n">{html.escape(name)}</td><td colspan="4" class="na">'
                        f'Tietojen haku epäonnistui</td></tr>')
            continue
        ev = r.get("tapahtumat") or {}
        evtxt = ""
        if ev.get("tapahtuma_4vk") and ev["tapahtuma_4vk"] != "null":
            evtxt += f"<b>Viim. 4 vk:</b> {html.escape(ev['tapahtuma_4vk'])}<br>"
        if ev.get("tapahtuma_36vk"):
            evtxt += f"<b>36 vk:</b> {html.escape(ev['tapahtuma_36vk'])}"
        if not evtxt:
            evtxt = '<span class="na">–</span>' if not tekoaly else '<span class="na">Tapahtumien haku epäonnistui</span>'
        src = " ".join(f'<a href="{html.escape(u)}">[{i+1}]</a>' for i, u in enumerate(ev.get("lahteet") or [])
                       if str(u).startswith("http"))
        m = r["laskettu"]["muutokset"]
        nyt_pvm, nyt = r["laskettu"]["nyt"]
        body.append(f'<tr><td class="n">{html.escape(name)}<br><small>{f"{nyt:.5f}".replace(".", ",")} € ({fmt_pvm(nyt_pvm)})</small></td>'
                    + "".join(prosentti(m[n], n) for n, _ in PERIODS)
                    + f'<td class="ev">{evtxt} {src}</td></tr>')
    return f"""<!doctype html><html lang="fi"><meta charset="utf-8"><title>Rahastoraportti {today:%d.%m.%Y}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#1a1a1a}}table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}th{{background:#f2f5f3}}
.n{{font-weight:600;min-width:200px}}.up{{color:#0a7a3b;font-weight:600;white-space:nowrap}}
.down{{color:#c0261c;font-weight:600;white-space:nowrap}}.flat,.na{{color:#777;white-space:nowrap}}.ev{{max-width:520px}}
small{{color:#666;font-weight:400}}</style>
<h1>Danske Invest -rahastot (Mandatum) – viikkoraportti</h1>
<p><small>Luotu {dt.datetime.now(TZ):%d.%m.%Y %H:%M}. Muutos = viimeisin osuuden arvo verrattuna arvoon 4 / 12 / 36 viikkoa aiemmin
(päivämäärä "vs." näyttää vertailuarvon päivän). Arvot: danskeinvest.fi. Tapahtumat on haettu verkosta tekoälyn avulla –
tarkista tärkeät asiat lähteistä. Ei ole sijoitusneuvontaa.</small></p>
<table><tr><th>Rahasto</th><th>4 vk</th><th>12 vk</th><th>36 vk</th><th>Merkittävin tapahtuma</th></tr>
{''.join(body)}</table>
{f'<p><small>Virheet: {html.escape("; ".join(virheet))}</small></p>' if virheet else ''}</html>"""


def laheta(path, subject):
    host = os.environ.get("SMTP_HOST")
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ["SMTP_FROM"], os.environ["SMTP_TO"]
    msg.set_content("Raportti liitteenä (HTML).")
    msg.add_alternative(path.read_text(encoding="utf8"), subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--testi", type=int, help="aja vain N ensimmäistä rahastoa")
    ap.add_argument("--ilman-tekoalya", action="store_true", help="vain luvut, ei tapahtumahakua")
    a = ap.parse_args()
    tekoaly = not a.ilman_tekoalya
    client = None
    if tekoaly:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Aseta ympäristömuuttuja ANTHROPIC_API_KEY (tai käytä --ilman-tekoalya)")
        import anthropic
        client = anthropic.Anthropic()
    today = dt.datetime.now(TZ).date()
    rahastot = json.loads((HERE / "rahastot.json").read_text(encoding="utf8"))[: a.testi]
    results, virheet = {}, []

    def job(f):
        try:
            laskettu = laske(hae_arvot(f["id"], today), today)
        except Exception as e:
            return f["nimi"], None, f"{f['nimi']}: arvojen haku: {e}"
        res, err = {"laskettu": laskettu}, None
        if tekoaly:
            try:
                res["tapahtumat"] = hae_tapahtumat(client, f["nimi"], today, laskettu)
            except Exception as e:  # luvut säilyvät, vaikka tapahtumahaku epäonnistuu
                err = f"{f['nimi']}: tapahtumahaku: {e}"
        return f["nimi"], res, err

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for n, res, err in ex.map(job, rahastot):
            results[n] = res
            if err:
                virheet.append(err)
            print(("OK   " if res and not err else "VIRHE" if res is None else "OSIT."), n, flush=True)

    out = HERE / "raportit"
    out.mkdir(exist_ok=True)
    path = out / f"rahastoraportti_{today.isoformat()}.html"
    path.write_text(render([(f["nimi"], results[f["nimi"]]) for f in rahastot], today, virheet, tekoaly),
                    encoding="utf8")
    print("Raportti:", path)
    if virheet:
        print("Virheet:\n  " + "\n  ".join(virheet))
    if laheta(path, f"Rahastoraportti {today:%d.%m.%Y}"):
        print("Sähköposti lähetetty")


if __name__ == "__main__":
    main()