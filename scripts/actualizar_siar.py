#!/usr/bin/env python3
"""
AgroPanel · Descarga diaria de datos de la red SiAR (Comunitat Valenciana)
SIN REGISTRO: usa la misma "Consulta de datos" pública de la web del SiAR
(https://servicio.mapa.gob.es/siarweb/consultaDatos/inicio).

Qué hace, igual que haría una persona en el navegador:
  1. Abre la página de consulta y recoge el código de seguridad (_csrf) y la sesión.
  2. Busca en esa página el identificador interno de cada estación (p. ej. 46-1).
  3. Pide los datos DIARIOS de cada estación de Alicante, Castellón y Valencia.
  4. Lee la tabla de resultados y guarda datos/{CODIGO}.json, que es lo que lee AgroPanel.

Solo usa la biblioteca estándar de Python. Opcional: SIAR_DIAS (días hacia atrás, por defecto 120).
Si algo cambia en la web del SiAR, guarda páginas de diagnóstico en datos/_debug_*.html.
"""
import datetime as dt
import html as htmlmod
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE = "https://servicio.mapa.gob.es/siarweb/consultaDatos"
INICIO = BASE + "/inicio"
CONSULTA = BASE + "/consultaDatos"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 AgroPanel/1.0"

# Estaciones activas de la Comunitat Valenciana (códigos SiAR)
ESTACIONES = [
    "A02", "A03", "A04", "A05", "A06", "A08", "A09", "A10", "A13", "A15", "A18", "A19", "A20", "A21",
    "A101", "A102", "A1020",
    "CS01", "CS03", "CS05", "CS07", "CS08", "CS09", "CS11", "CS13", "CS101",
    "V01", "V02", "V04", "V05", "V06", "V07", "V10", "V14", "V17", "V18", "V19", "V20", "V21", "V22",
    "V23", "V25", "V26", "V27", "V28", "V29", "V30", "V101", "V102", "V103", "V104", "V106", "V109", "V110",
]
# Variables que pide la web en la consulta diaria (copiadas de una consulta real)
VARIABLES = "10,11,9,12,13,14,15,16,17,18,19,21,22,20,63,24,69,23,66"
DIAS = int(os.environ.get("SIAR_DIAS", "120"))
PAUSA = 3  # segundos entre consultas, para no cargar el servidor público

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def http(url, data=None):
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "es-ES,es;q=0.9"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://servicio.mapa.gob.es", "Referer": INICIO})
    req = urllib.request.Request(url, data=body, headers=headers)
    with opener.open(req, timeout=90) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def debug(name, text):
    os.makedirs("datos", exist_ok=True)
    with open(os.path.join("datos", f"_debug_{name}.html"), "w", encoding="utf-8") as f:
        f.write(text)


def get_csrf(page):
    for pat in (r'<meta[^>]+name="_csrf"[^>]+content="([^"]+)"', r'<meta[^>]+content="([^"]+)"[^>]+name="_csrf"',
                r'name="_csrf"[^>]*value="([^"]+)"', r'value="([^"]+)"[^>]*name="_csrf"'):
        m = re.search(pat, page)
        if m:
            return m.group(1)
    return None


def strip_tags(s):
    return htmlmod.unescape(re.sub(r"<[^>]+>", " ", s))


def station_ids(page):
    """Relaciona el código visible (V14) con el identificador interno de la web (46-xx)."""
    ids = {}
    for m in re.finditer(r'value="(\d{1,2}-\d{1,5})"', page):
        val = m.group(1)
        prov = val.split("-")[0]
        if prov not in ("3", "03", "12", "46"):
            continue
        after = strip_tags(page[m.end(): m.end() + 400])
        before = strip_tags(page[max(0, m.start() - 400): m.start()])
        code = None
        mm = re.search(r"\b((?:A|CS|V)\d{2,5})\s*-\s*\S", after)
        if mm:
            code = mm.group(1)
        else:
            cands = re.findall(r"\b((?:A|CS|V)\d{2,5})\s*-\s*\S", before)
            if cands:
                code = cands[-1]
        if code and code not in ids:
            ids[code] = val
    return ids


class TableParser(HTMLParser):
    """Extrae todas las tablas HTML como listas de filas de texto."""
    def __init__(self):
        super().__init__()
        self.tables, self._t, self._row, self._cell, self._in = [], None, None, None, False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._in = [], True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in:
            self._row.append(" ".join("".join(self._cell).split()))
            self._in = False
        elif tag == "tr" and self._row is not None and self._t is not None:
            if self._row:
                self._t.append(self._row)
            self._row = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t = None

    def handle_data(self, data):
        if self._in:
            self._cell.append(data)


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


# Nombre de columna de la web -> clave que entiende AgroPanel
COLS = [
    ("fecha", r"^(fecha|dia)"),
    ("tmed", r"^(tempmed|temperaturamed|tmed)"),
    ("tmax", r"^(tempmax|temperaturamax|tmax)"),
    ("tmin", r"^(tempmin|temperaturamin|tmin)"),
    ("hr", r"^(hummed|humedadmed|hrmed)"),
    ("hrmax", r"^(hummax|humedadmax|hrmax)"),
    ("hrmin", r"^(hummin|humedadmin|hrmin)"),
    ("viento", r"^(velviento|velocidadviento)(?!max)"),
    ("radiacion", r"^radiacion"),
    ("precip", r"^precip"),
    ("eto", r"^(etpmon|eto|et0|evapotranspiracion|etp)"),
]


def num(s):
    s = (s or "").strip().replace("\xa0", "")
    if not s or s in ("-", "--", "n/d"):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_result(page):
    p = TableParser()
    p.feed(page)
    for t in p.tables:
        hi = next((i for i, r in enumerate(t) if any(norm(c).startswith("fecha") for c in r)), None)
        if hi is None:
            continue
        head = [norm(c) for c in t[hi]]
        idx = {}
        for key, pat in COLS:
            for i, h in enumerate(head):
                if i not in idx.values() and re.search(pat, h):
                    idx[key] = i
                    break
        if "fecha" not in idx:
            continue
        rows = []
        for r in t[hi + 1:]:
            if len(r) <= idx["fecha"]:
                continue
            f = r[idx["fecha"]].strip()
            m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", f) or None
            if m:
                f = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
            elif not re.match(r"\d{4}-\d{2}-\d{2}", f):
                continue
            o = {"fecha": f[:10]}
            for key, i in idx.items():
                if key != "fecha" and i < len(r):
                    o[key] = num(r[i])
            rows.append(o)
        if rows:
            rows.sort(key=lambda x: x["fecha"])
            return rows, t[hi]
    return [], None


def main():
    print("Abriendo la consulta pública del SiAR…")
    page = http(INICIO)
    ids = station_ids(page)
    if not get_csrf(page) or not ids:
        debug("inicio", page)
        sys.exit("No se ha encontrado el código de seguridad o la lista de estaciones (ver datos/_debug_inicio.html).")
    print(f"  {len(ids)} estaciones de la Comunitat Valenciana encontradas en la web.")

    hoy = dt.date.today()
    ini = (hoy - dt.timedelta(days=DIAS)).isoformat()
    fin = hoy.isoformat()
    os.makedirs("datos", exist_ok=True)
    ok, fallos = 0, []

    for code in ESTACIONES:
        if code not in ids:
            fallos.append(f"{code}: no aparece en la web")
            print(f"  ✗ {code}: no aparece en la web")
            continue
        sid = ids[code]
        prov = sid.split("-")[0].lstrip("0") or "0"
        try:
            page = http(INICIO)  # código de seguridad nuevo para cada consulta
            csrf = get_csrf(page)
            form = [
                ("_csrf", csrf), ("variablesSeleccionadas", VARIABLES), ("idEstaciones", sid),
                ("accionHidden", "resultadoConsultaDatos"), ("fechaInicial", ini), ("fechaFinal", fin),
                ("tipoCalculo", "2"), ("tipoFiltroEstaciones", "provincias"), ("consultaPersonalizada", "false"),
                ("peticion.id", ""), ("peticion.idTipoPeticion", ""), ("tipoFiltro", "provincias"),
                ("idCCAA", "VAL"), ("checkCA_VAL", "VAL"), ("idProvs", prov), ("idComRegantes", ""),
                ("estacion.sEstacionCorto", ""), ("tipoCalculo", "2"), ("checkbox", "on"),
            ]
            res = http(CONSULTA, form)
            rows, head = parse_result(res)
            if not rows:
                debug(code, res)
                raise RuntimeError("la respuesta no contiene una tabla de datos (ver datos/_debug_%s.html)" % code)
            with open(os.path.join("datos", f"{code}.json"), "w", encoding="utf-8") as f:
                json.dump({"estacion": code, "idWeb": sid, "columnas": head, "datos": rows}, f, ensure_ascii=False)
            ok += 1
            print(f"  ✓ {code} ({sid}): {len(rows)} días, último {rows[-1]['fecha']}")
        except Exception as e:
            fallos.append(f"{code}: {e}")
            print(f"  ✗ {code}: {e}")
        time.sleep(PAUSA)

    with open(os.path.join("datos", "_actualizado.json"), "w", encoding="utf-8") as f:
        json.dump({"fecha": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": ok, "fallos": fallos}, f, ensure_ascii=False, indent=1)
    print(f"Terminado: {ok} estaciones actualizadas, {len(fallos)} con incidencias.")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
