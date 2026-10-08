#!/usr/bin/env python3
"""Scarica le previsioni di meteo.it e iLMeteo per le città in CITIES e le
salva in data/meteo-<slug>.json già nella forma usata dalla dashboard
(stessa struttura di Open-Meteo: current / hourly / daily), così il
front-end le disegna con le stesse card del meteo principale.

Gira ogni ora da GitHub Actions (.github/workflows/meteo.yml).
Dipendenze: solo la libreria standard.
"""
import json, re, sys, html, math, os, datetime as dt
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

# città seguite: slug file, nome, coordinate, id meteo.it (da api.meteosuper.it/v1/locations?name=), slug iLMeteo
CITIES = [
    {"slug": "calimera", "name": "Calimera", "lat": 40.2495, "lon": 18.2798,
     "meteoit": "calimera-75010", "ilmeteo": "calimera"},
]

# ---------------------------------------------------------------- utilità
def fetch(url):
    r = Request(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    with urlopen(r, timeout=40) as resp:
        return resp.read().decode("utf-8", "ignore")

def tokens(page):
    """HTML -> testo a token separati da ' | ', con le immagini rese come [img:alt]."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    t = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r" [img:\1] ", t)
    t = re.sub(r"<[^>]+>", " | ", t)
    t = html.unescape(re.sub(r"\s+", " ", t))
    return re.sub(r"(\s*\|\s*)+", " | ", t)

MONTHS = {m: i + 1 for i, m in enumerate("gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split())}

COMPASS = {"nord": 0, "nordest": 45, "est": 90, "sudest": 135, "sud": 180, "sudovest": 225, "ovest": 270, "nordovest": 315,
           "n": 0, "nne": 22.5, "ne": 45, "ene": 67.5, "e": 90, "ese": 112.5, "se": 135, "sse": 157.5, "s": 180,
           "sso": 202.5, "so": 225, "oso": 247.5, "o": 270, "ono": 292.5, "no": 315, "nno": 337.5,
           "ssw": 202.5, "sw": 225, "wsw": 247.5, "w": 270, "wnw": 292.5, "nw": 315, "nnw": 337.5}

def wind_dir(label):
    """'Sud sud est intenso' / 'SSE' / 'Sud SE' -> gradi."""
    s = re.sub(r"[^a-zA-Z ]", " ", label).strip().lower()
    for w in ("intenso", "forte", "moderato", "debole", "calmo", "teso"):
        s = s.replace(w, "")
    key = s.replace(" ", "")
    if key in COMPASS: return COMPASS[key]
    # "sud sud est" -> "sse"
    abbr = "".join(w[0] for w in s.split())
    return COMPASS.get(abbr, 180)

def wmo(cond):
    """testo condizione (italiano) -> codice WMO usato dalla dashboard."""
    c = cond.lower()
    if "temporal" in c or "grandin" in c: return 95
    if "neve" in c or "nevic" in c: return 73
    if "nebbia" in c or "foschia" in c: return 45
    if "piogg" in c or "rovesc" in c or "precipitaz" in c or "pioviggin" in c or "acquazz" in c:
        if "forte" in c or "intens" in c or "abbondant" in c: return 65
        if "debole" in c or "legger" in c or "pioviggin" in c: return 61
        if "schiarit" in c or "isolat" in c or "rovesc" in c: return 80
        return 63
    if "coperto" in c or "gran parte nuvoloso" in c or "molto nuvoloso" in c or "cielo nuvoloso" in c: return 3
    if "nuvoloso" in c or "nubi" in c or "variabil" in c or "parzial" in c or "velat" in c: return 2
    if "prevalentemente" in c or "poco nuvoloso" in c: return 1
    return 0

def sun_times(lat, lon, day):
    """alba/tramonto (ora locale, 'HH:MM') con l'algoritmo NOAA semplificato."""
    n = day.timetuple().tm_yday
    def calc(rising):
        lng_h = lon / 15
        t = n + ((6 if rising else 18) - lng_h) / 24
        M = (0.9856 * t) - 3.289
        L = (M + 1.916 * math.sin(math.radians(M)) + 0.020 * math.sin(math.radians(2 * M)) + 282.634) % 360
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
        RA = (RA + (math.floor(L / 90) * 90 - math.floor(RA / 90) * 90)) / 15
        sinDec = 0.39782 * math.sin(math.radians(L)); cosDec = math.cos(math.asin(sinDec))
        cosH = (math.cos(math.radians(90.833)) - sinDec * math.sin(math.radians(lat))) / (cosDec * math.cos(math.radians(lat)))
        if cosH > 1 or cosH < -1: return None
        H = (360 - math.degrees(math.acos(cosH))) if rising else math.degrees(math.acos(cosH))
        T = H / 15 + RA - 0.06571 * t - 6.622
        UT = (T - lng_h) % 24
        base = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc) + dt.timedelta(hours=UT)
        return base.astimezone(TZ)
    sr, ss = calc(True), calc(False)
    return sr, ss

def hhmm(d): return d.strftime("%H:%M") if d else "06:30"

# ---------------------------------------------------------------- meteo.it
def scrape_meteoit(city):
    base = f"https://www.meteo.it/meteo/{city['meteoit']}"
    name, code = city["meteoit"].rsplit("-", 1)
    main = fetch(base)
    today = dt.datetime.now(TZ).date()

    # --- giornaliero: <li><a href=... title="Meteo X Giovedì 8"><img alt="cond"/> ... <span>min°</span><span>max°</span>
    daily = []
    for m in re.finditer(r'<li[^>]*><a href="/meteo/[^"]+" title="Meteo [^"]+?\s(\S+)\s(\d{1,2})">(.*?)</a></li>', main, flags=re.S):
        dnum, body = int(m.group(2)), m.group(3)
        alt = re.search(r'alt="([^"]*)"', body)
        temps = re.findall(r"<span[^>]*>(-?\d+)°</span>", body)
        if not alt or len(temps) < 2: continue
        d = today
        for k in range(0, 20):           # trova la data con quel numero di giorno a partire da oggi
            cand = today + dt.timedelta(days=k)
            if cand.day == dnum: d = cand; break
        pop = 0
        lvl = re.search(r"ic_rain_(low|mid|high)", body)
        if lvl: pop = {"low": 30, "mid": 60, "high": 80}[lvl.group(1)]
        alert = re.search(r'alt="Allerta meteo (GIALLA|ARANCIONE|ROSSA|VERDE)', body)
        daily.append({"date": d.isoformat(), "cond": alt.group(1), "lo": int(temps[0]), "hi": int(temps[1]),
                      "pop": pop, "alert": alert.group(1).capitalize() if alert else None})
    if not daily: raise RuntimeError("meteo.it: nessun giorno trovato")

    # --- orario: pagine oggi / domani / 2-giorni
    hourly, extra = [], {}
    pages = [("oggi", 0), ("domani", 1), ("2-giorni", 2)]
    for suffix, off in pages:
        try: txt = tokens(fetch(f"https://www.meteo.it/meteo/{name}-{suffix}-{code}"))
        except Exception as e:
            print("meteo.it pagina", suffix, "non letta:", e, file=sys.stderr); continue
        day = today + dt.timedelta(days=off)
        if off == 0:
            ss = re.search(r"\| (\d{2}:\d{2}) \| (\d{2}:\d{2}) \| Luna", txt)
            if ss: extra["sunrise"], extra["sunset"] = ss.group(1), ss.group(2)
            al = re.search(r"Allerta Meteo (\w+)!", txt)
            if al: extra["alert"] = al.group(1).capitalize()
        prev_h = -1
        for m in re.finditer(r"(-?\d+)° \| \[img:UV[^\]]*\] (\d+) \| ore \| (\d{2}) \| \[img:([^\]]*)\] \| \[img:Umidit[^\]]*\] \| (\d+)% \| \[img:Raffic[^\]]*\] \| (\d+) \| - \| (\d+) \| Km/h \| \[img:([^\]]*)\] \| ([^|]+) \|", txt):
            h = int(m.group(3))
            if h < prev_h: day = day + dt.timedelta(days=1)   # dopo le 23 arriva lo 00 del giorno dopo
            prev_h = h
            hourly.append({"time": f"{day.isoformat()}T{h:02d}:00", "temp": int(m.group(1)), "uv": int(m.group(2)),
                           "cond": m.group(4), "hum": int(m.group(5)), "wind": int(m.group(6)), "gust": int(m.group(7)),
                           "dir": wind_dir(m.group(8) + " " + m.group(9)), "pop": None, "precip": None, "press": None, "vis": None})
    return build(city, "meteo.it", base, daily, hourly, extra)

# ---------------------------------------------------------------- iLMeteo
def scrape_ilmeteo(city):
    base = f"https://www.ilmeteo.it/meteo/{city['ilmeteo']}"
    today = dt.datetime.now(TZ).date()
    main_txt = tokens(fetch(base))

    daily = []
    seen = set()
    for m in re.finditer(r"\| (Lun|Mar|Mer|Gio|Ven|Sab|Dom) \| (\d{1,2}) \| (-?\d+)° \| (-?\d+)°(?: \| (\d+)%)?(?= \|)", main_txt):
        dnum = int(m.group(2))
        if dnum in seen: continue
        seen.add(dnum)
        d = today
        for k in range(0, 20):
            cand = today + dt.timedelta(days=k)
            if cand.day == dnum: d = cand; break
        daily.append({"date": d.isoformat(), "cond": None, "lo": int(m.group(3)), "hi": int(m.group(4)),
                      "pop": int(m.group(5) or 0), "alert": None})
    if not daily: raise RuntimeError("iLMeteo: nessun giorno trovato")

    hourly = []
    pages = ["", "/domani", "/dopodomani", "/3", "/4", "/5", "/6"]
    seen_t = set()
    for p in pages:
        try: txt = main_txt if p == "" else tokens(fetch(base + p))
        except Exception as e:
            print("iLMeteo pagina", p, "non letta:", e, file=sys.stderr); continue
        # blocchi di dettaglio: "Calimera | Giovedì 8 Ottobre 2026 | Ore 13:00 | pioggia e schiarite | Temperatura | 25.2 °C ..."
        for m in re.finditer(r"\| \S+ (\d{1,2}) (\w+) (\d{4}) \| Ore (\d{2}):00 \| ([^|]+) \| Temperatura \| (-?[\d.,]+) °C \|(.*?)(?=\| \S+ \d{1,2} \w+ \d{4} \| Ore |\Z)", txt, flags=re.S):
            dnum, mon, year, h = int(m.group(1)), MONTHS.get(m.group(2).lower()), int(m.group(3)), int(m.group(4))
            if not mon: continue
            t = f"{year:04d}-{mon:02d}-{dnum:02d}T{h:02d}:00"
            if t in seen_t: continue
            seen_t.add(t)
            rest = m.group(7)
            g = lambda rx, cast=float, default=None: (lambda r: cast(r.group(1).replace(",", ".")) if r else default)(re.search(rx, rest))
            precip = re.search(r"Precipitazioni \| ([^|]+) \|", rest)
            pm = re.search(r"([\d.,]+) mm", precip.group(1)) if precip else None
            wind = re.search(r"Vento \| ([A-Z]+) (\d+)/(\d+) km/h", rest)
            vis = re.search(r"Visibilità \| ([<>]?)(\d+)\s*km", rest)
            hourly.append({"time": t, "temp": float(m.group(6).replace(",", ".")), "cond": m.group(5).strip(),
                           "hum": g(r"Umidità rel\. \| (\d+)%", int), "press": g(r"Pressione \| (\d+) mb", int),
                           "precip": float(pm.group(1).replace(",", ".")) if pm else 0.0,
                           "pop": g(r"Probabilità di precipitazione \| (\d+)%", int, 0),
                           "uv": g(r"Indice UV \| ([\d.,]+)", float),
                           "wind": int(wind.group(2)) if wind else None, "gust": int(wind.group(3)) if wind else None,
                           "dir": wind_dir(wind.group(1)) if wind else None,
                           "vis": (int(vis.group(2)) * 1000 * (1.2 if vis.group(1) == ">" else 0.8 if vis.group(1) == "<" else 1)) if vis else None})
    hourly.sort(key=lambda x: x["time"])
    upd = re.search(r"aggiornamento del \| (\d{1,2})/(\d{1,2}) ore (\d{2}:\d{2})", main_txt)
    extra = {}
    if upd: extra["updated_src"] = f"{upd.group(1)}/{upd.group(2)} {upd.group(3)}"
    return build(city, "iLMeteo", base, daily, hourly, extra)

# ---------------------------------------------------------------- forma Open-Meteo
def build(city, source, url, daily, hourly, extra):
    hourly = [h for h in hourly if h.get("temp") is not None]
    by_day = {}
    for h in hourly: by_day.setdefault(h["time"][:10], []).append(h)

    # alba/tramonto: da meteo.it se li ha, altrimenti calcolati
    def sun(dstr):
        d = dt.date.fromisoformat(dstr)
        sr, ss = sun_times(city["lat"], city["lon"], d)
        if dstr == dt.datetime.now(TZ).date().isoformat() and extra.get("sunrise"):
            return extra["sunrise"], extra["sunset"], sr, ss
        return hhmm(sr), hhmm(ss), sr, ss

    H = {k: [] for k in ("time", "temperature_2m", "apparent_temperature", "weather_code", "precipitation_probability", "precipitation",
                          "relative_humidity_2m", "cloud_cover", "visibility", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
                          "surface_pressure", "is_day", "uv_index")}
    sun_cache = {}
    for h in hourly:
        d = h["time"][:10]
        if d not in sun_cache: sun_cache[d] = sun(d)
        srs, sss, _, _ = sun_cache[d]
        hm = h["time"][11:16]
        is_day = 1 if srs <= hm < sss else 0
        code = wmo(h["cond"])
        H["time"].append(h["time"]); H["temperature_2m"].append(h["temp"])
        H["apparent_temperature"].append(h.get("feels") if h.get("feels") is not None else h["temp"])
        H["weather_code"].append(code)
        H["precipitation_probability"].append(h.get("pop") if h.get("pop") is not None else 0)
        H["precipitation"].append(h.get("precip") if h.get("precip") is not None else 0)
        H["relative_humidity_2m"].append(h.get("hum") if h.get("hum") is not None else 0)
        H["cloud_cover"].append({0: 5, 1: 25, 2: 50, 3: 95}.get(code, 85))
        H["visibility"].append(h.get("vis"))
        H["wind_speed_10m"].append(h.get("wind") if h.get("wind") is not None else 0)
        H["wind_gusts_10m"].append(h.get("gust") if h.get("gust") is not None else (h.get("wind") or 0))
        H["wind_direction_10m"].append(h.get("dir") if h.get("dir") is not None else 0)
        H["surface_pressure"].append(h.get("press") if h.get("press") is not None else 1013)
        H["is_day"].append(is_day); H["uv_index"].append(h.get("uv") if h.get("uv") is not None else 0)

    D = {k: [] for k in ("time", "weather_code", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max", "apparent_temperature_min",
                          "sunrise", "sunset", "daylight_duration", "sunshine_duration", "uv_index_max", "precipitation_sum", "precipitation_hours",
                          "precipitation_probability_max", "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant", "alert")}
    daily = sorted(daily, key=lambda x: x["date"])[:8]
    for dd in daily:
        d = dd["date"]
        hs = by_day.get(d, [])
        if d not in sun_cache: sun_cache[d] = sun(d)
        srs, sss, sr, ss = sun_cache[d]
        # condizione del giorno: dichiarata, altrimenti quella delle ore centrali
        cond = dd.get("cond")
        if not cond and hs:
            mid = [h for h in hs if 11 <= int(h["time"][11:13]) <= 16] or hs
            codes = [wmo(h["cond"]) for h in mid]
            code = max(codes) if any(c >= 51 for c in codes) else round(sum(codes) / len(codes))
        else:
            code = wmo(cond or "")
        D["time"].append(d); D["weather_code"].append(code)
        D["temperature_2m_max"].append(dd["hi"]); D["temperature_2m_min"].append(dd["lo"])
        D["apparent_temperature_max"].append(dd["hi"]); D["apparent_temperature_min"].append(dd["lo"])
        D["sunrise"].append(f"{d}T{srs}"); D["sunset"].append(f"{d}T{sss}")
        D["daylight_duration"].append((ss - sr).total_seconds() if sr and ss else 0)
        D["sunshine_duration"].append(0)
        D["uv_index_max"].append(max([h.get("uv") or 0 for h in hs], default=0))
        D["precipitation_sum"].append(round(sum(h.get("precip") or 0 for h in hs), 1))
        D["precipitation_hours"].append(sum(1 for h in hs if (h.get("precip") or 0) > 0))
        D["precipitation_probability_max"].append(max([dd.get("pop") or 0] + [h.get("pop") or 0 for h in hs]))
        D["wind_speed_10m_max"].append(max([h.get("wind") or 0 for h in hs], default=0))
        D["wind_gusts_10m_max"].append(max([h.get("gust") or h.get("wind") or 0 for h in hs], default=0))
        dirs = [h.get("dir") for h in hs if h.get("dir") is not None]
        D["wind_direction_10m_dominant"].append(round(sum(dirs) / len(dirs)) if dirs else 0)
        D["alert"].append(dd.get("alert") or (extra.get("alert") if d == daily[0]["date"] else None))

    return {"source": source, "url": url, "city": city["name"], "latitude": city["lat"], "longitude": city["lon"],
            "updated": dt.datetime.now(TZ).isoformat(timespec="minutes"), "updated_src": extra.get("updated_src"),
            "hourly": H, "daily": D}

# ---------------------------------------------------------------- main
def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    ok = True
    for city in CITIES:
        path = os.path.join(out_dir, f"meteo-{city['slug']}.json")
        try: prev = json.load(open(path, encoding="utf-8"))
        except Exception: prev = {}
        result = {"city": city["name"], "updated": dt.datetime.now(TZ).isoformat(timespec="minutes")}
        for key, fn in (("meteoit", scrape_meteoit), ("ilmeteo", scrape_ilmeteo)):
            try:
                result[key] = fn(city)
                print(f"{city['name']} {key}: {len(result[key]['hourly']['time'])} ore, {len(result[key]['daily']['time'])} giorni")
            except Exception as e:
                ok = False
                print(f"ERRORE {city['name']} {key}: {e}", file=sys.stderr)
                if prev.get(key): result[key] = prev[key]   # tengo l'ultimo dato buono
        json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
