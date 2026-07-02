# -*- coding: utf-8 -*-
"""
Genera el report de Captación Ads (últimos 30 días) con datos reales de
META (gasto) + GHL (conteos). Self-contained: lee credenciales del entorno
(o de .env), llama a las APIs, anonimiza nombres y escribe index.html.

Credenciales esperadas (env vars o .env):
  GHL_TOKEN, GHL_LOCATION_ID, META_TOKEN, META_AD_ACCOUNT_ID
GHL_LOCATION_ID puede venir como 'v2/location/XXXX' o 'XXXX'.
"""
import os, sys, json, datetime as dt, urllib.parse, urllib.request
from collections import defaultdict

GRAPH = "https://graph.facebook.com/v21.0"
GHLAPI = "https://services.leadconnectorhq.com"

# ---------- env / .env ----------
def load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
load_env()

def need(k):
    v = os.environ.get(k, "").strip()
    if not v:
        sys.exit("FALTA la variable %s (env o .env)" % k)
    return v

GHL_TOKEN = need("GHL_TOKEN")
LOC = need("GHL_LOCATION_ID").split("/")[-1]          # admite v2/location/XXX
META_TOKEN = need("META_TOKEN")
ACT = need("META_AD_ACCOUNT_ID")
if not ACT.startswith("act_"):
    ACT = "act_" + ACT

# ---------- http ----------
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 report-gen"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise SystemExit("HTTP %s en %s\n  -> %s" % (e.code, url.split("access_token=")[0][:120], body))

def ghl_get(path, params):
    params = dict(params)
    url = GHLAPI + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + GHL_TOKEN,
        "Version": "2021-07-28",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 report-gen",   # GHL WAF bloquea Python-urllib
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

def meta_get(path, params):
    params = dict(params); params["access_token"] = META_TOKEN
    return http_get(GRAPH + "/" + path + "?" + urllib.parse.urlencode(params))

# ---------- dates (ventana dinámica: últimos N días, sin incluir hoy) ----------
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "30") or "30")
DD = str(WINDOW_DAYS)                      # para textos ("7 días", "Total 7d"…)
TODAY = dt.date.today()
WIN_END_D = TODAY                          # exclusivo (cubre hasta ayer)
WIN_START_D = TODAY - dt.timedelta(days=WINDOW_DAYS)
PRV_START_D = TODAY - dt.timedelta(days=2 * WINDOW_DAYS)
def D(d): return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
WIN_A, WIN_B, PRV_A = D(WIN_START_D), D(WIN_END_D), D(PRV_START_D)
def tr(a, b):  # META time_range JSON {since,until} (until inclusivo => b-1); a,b son datetimes
    s = a.date() if isinstance(a, dt.datetime) else a
    u = (b - dt.timedelta(days=1)); u = u.date() if isinstance(u, dt.datetime) else u
    return json.dumps({"since": s.isoformat(), "until": u.isoformat()}, separators=(",", ":"))

# ---------- pipeline / stages ----------
pl = ghl_get("/opportunities/pipelines", {"locationId": LOC})["pipelines"]
pipe = pl[0]
for p in pl:
    if "captaci" in p["name"].lower() or "capta" in p["name"].lower():
        pipe = p; break
stages = sorted(pipe["stages"], key=lambda s: s["position"])
POS = {s["id"]: s["position"] for s in stages}
def idx_of(substr, default):
    for s in stages:
        if substr.lower() in s["name"].lower():
            return s["position"]
    return default
WON_NAME = None
for s in stages:
    if "paga" in s["name"].lower() or "cliente" in s["name"].lower() or "ganado" in s["name"].lower():
        WON_NAME = s["id"]
Q_POS  = idx_of("contact", 2)
AG_POS = idx_of("agendad", 6)
CALL_POS = idx_of("realizada", 8)

def pos(o): return POS.get(o.get("pipelineStageId"), -1)
def is_won(o): return o.get("status") == "won" or o.get("pipelineStageId") == WON_NAME

# ---------- opportunities (paged) ----------
def fetch_opps():
    out, page = [], 1
    while True:
        d = ghl_get("/opportunities/search", {"location_id": LOC, "limit": 100, "page": page})
        batch = d.get("opportunities", [])
        out += batch
        total = (d.get("meta") or {}).get("total")
        if not batch or (total is not None and len(out) >= total) or len(batch) < 100:
            break
        page += 1
        if page > 50: break
    return out
opps = fetch_opps()

def pdt(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
def inwin(o, a, b):
    c = pdt(o.get("createdAt")); return c is not None and a <= c < b

def funnel(sub):
    return dict(
        lead=len(sub),
        q=sum(1 for o in sub if pos(o) >= Q_POS),
        ag=sum(1 for o in sub if pos(o) >= AG_POS),
        call=sum(1 for o in sub if pos(o) >= CALL_POS),
        sale=sum(1 for o in sub if is_won(o)),
        rev=sum(float(o.get("monetaryValue") or 0) for o in sub if is_won(o)),
    )
cur = [o for o in opps if inwin(o, WIN_A, WIN_B)]
prv = [o for o in opps if inwin(o, PRV_A, WIN_A)]
F, P = funnel(cur), funnel(prv)

# ---------- META ----------
acct = meta_get("%s/insights" % ACT, {"time_range": tr(WIN_A, WIN_B), "fields": "spend"})["data"]
SPEND = float(acct[0]["spend"]) if acct else 0.0
acctp = meta_get("%s/insights" % ACT, {"time_range": tr(PRV_A, WIN_A), "fields": "spend"})["data"]
SPEND_PRV = float(acctp[0]["spend"]) if acctp else 0.0

daily = meta_get("%s/insights" % ACT, {"time_range": tr(WIN_A, WIN_B),
                 "time_increment": 1, "fields": "spend,actions", "limit": 100})["data"]
daily.sort(key=lambda d: d["date_start"])
ads = meta_get("%s/insights" % ACT, {"time_range": tr(WIN_A, WIN_B), "level": "ad",
              "fields": "ad_name,adset_name,spend,frequency,actions", "limit": 200})["data"]

LEADT = {"lead", "offsite_conversion.fb_pixel_lead", "onsite_web_lead"}
def leadval(actions):
    return int(sum(float(a["value"]) for a in (actions or []) if a["action_type"] in LEADT))

# ---------- derived ----------
def sd(n, d): return (n / d) if d else None
cpl, cplq, cag = sd(SPEND, F["lead"]), sd(SPEND, F["q"]), sd(SPEND, F["ag"])
ccall, cac, roas = sd(SPEND, F["call"]), sd(SPEND, F["sale"]), sd(F["rev"], SPEND)
cac_prv, roas_prv = sd(SPEND_PRV, P["sale"]), sd(P["rev"], SPEND_PRV)

won_evt = [o for o in opps if is_won(o) and o.get("lastStatusChangeAt")
           and WIN_A <= pdt(o["lastStatusChangeAt"]) < WIN_B]
cash_evt = sum(float(o.get("monetaryValue") or 0) for o in won_evt)
big = max(won_evt, key=lambda o: float(o.get("monetaryValue") or 0)) if won_evt else None

# ---------- targets (editables) ----------
T = dict(cpl=80, cplq=150, cag=200, ccall=250, cac=600, roas=2.5)
def sem(v, obj):
    if v is None: return ""
    r = v / obj; return "g" if r <= 1.1 else ("a" if r <= 2.5 else "r")
def sem_low(v, obj):
    if v is None: return ""
    r = v / obj; return "g" if r <= 1.0 else ("a" if r <= 1.4 else "r")

# ---------- anonimización ----------
_ids = {}
def anon(name):
    name = (name or "").strip()
    if not name: return "Lead"
    if name not in _ids: _ids[name] = len(_ids) + 1
    parts = [p for p in name.replace(".", " ").split() if p]
    ini = ".".join(p[0].upper() for p in parts[:2]) + "." if parts else "L."
    return "%s · #%02d" % (ini, _ids[name])

# ---------- formato ES ----------
def _th(s):
    neg = s.startswith("-"); s = s.lstrip("-"); out = ""
    while len(s) > 3: out = "." + s[-3:] + out; s = s[:-3]
    return ("-" if neg else "") + s + out
def eur(x): return "—" if x is None else _th("%.0f" % x) + "€"
def eur2(x):
    if x is None: return "—"
    i, d = ("%.2f" % x).split("."); return _th(i) + "," + d + "€"
def xx(x): return "—" if x is None else ("%.2fx" % x).replace(".", ",")
def pct(a, b): return "—" if not b else "%d%%" % round(a / b * 100)

# ---------- weekly (4 buckets de 7 días contando hacia atrás, el más viejo absorbe el resto) ----------
def spend_between(a, b):
    s = 0.0
    for d in daily:
        day = dt.date.fromisoformat(d["date_start"])
        if a <= day < b: s += float(d["spend"])
    return s
# tamaño de bucket adaptativo: semanal si la ventana es larga, más fino si es corta
bsize = 7 if WINDOW_DAYS >= 21 else max(1, -(-WINDOW_DAYS // 4))  # ceil(days/4) para ventanas cortas
nb = max(1, -(-WINDOW_DAYS // bsize))                            # nº de buckets
bounds = [WIN_END_D - dt.timedelta(days=bsize * k) for k in range(nb)]  # [end, end-bsize, ...]
segs = [(max(WIN_START_D, bounds[k + 1]) if k + 1 < len(bounds) else WIN_START_D, bounds[k]) for k in range(nb)]
segs = list(reversed(segs))                                     # de más viejo a más nuevo
weeks = []
for a, b in segs:
    A, B = D(a), D(b)
    sub = [o for o in opps if inwin(o, A, B)]
    f = funnel(sub)
    f["spend"] = spend_between(a, b)
    f["lab"] = "%s–%s" % (a.strftime("%d/%m"), (b - dt.timedelta(days=1)).strftime("%d/%m"))
    weeks.append(f)

# ---------- adsets ----------
adset = defaultdict(lambda: {"spend": 0.0, "leads": 0, "ads": 0})
for a in ads:
    nm = a.get("adset_name") or "—"
    adset[nm]["spend"] += float(a["spend"]); adset[nm]["leads"] += leadval(a.get("actions")); adset[nm]["ads"] += 1
adset_rows = sorted(adset.items(), key=lambda x: -x[1]["spend"])
meta_leads_total = sum(v["leads"] for _, v in adset.items())

# ---------- daily aux ----------
leads_by_day = defaultdict(int); won_by_day = defaultdict(lambda: [0, 0.0])
for o in opps:
    c = pdt(o.get("createdAt"))
    if c and WIN_A <= c < WIN_B: leads_by_day[c.date().isoformat()] += 1
    if is_won(o) and o.get("lastStatusChangeAt"):
        w = pdt(o["lastStatusChangeAt"])
        if WIN_A <= w < WIN_B:
            won_by_day[w.date().isoformat()][0] += 1
            won_by_day[w.date().isoformat()][1] += float(o.get("monetaryValue") or 0)

# ================= SVG =================
def svg_chart():
    if not daily: return ""
    x0, x1 = 70, 1040; W = x1 - x0; n = len(daily); ymax = 120; bw = W / n * 0.6
    bars = labs = ""
    for i, d in enumerate(daily):
        cx = x0 + (i + 0.5) * W / n; sp = float(d["spend"]); h = (sp / ymax) * 240
        bars += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>' % (cx - bw / 2, 270 - h, bw, h)
        if i % 2 == 0:
            labs += '<text x="%.1f" y="288">%s</text>' % (cx, dt.date.fromisoformat(d["date_start"]).strftime("%d/%m"))
    pts = [(x0 + (i + 0.5) * W / n, leads_by_day.get(d["date_start"], 0)) for i, d in enumerate(daily)]
    lmax = max(1, max(p[1] for p in pts))
    poly = " ".join("%.1f,%.1f" % (cx, 270 - (lc / lmax) * 230) for cx, lc in pts)
    circ = "".join('<circle cx="%.1f" cy="%.1f" r="2.5" fill="#10b981"/>' % (cx, 270 - (lc / lmax) * 230) for cx, lc in pts if lc)
    mk = "".join('<circle cx="%.1f" cy="40" r="4" fill="#d4af37" stroke="#0a0a0a"/>' % (x0 + (i + 0.5) * W / n)
                 for i, d in enumerate(daily) if won_by_day.get(d["date_start"]))
    grid = '<line x1="70" y1="30" x2="1040" y2="30"/><line x1="70" y1="110" x2="1040" y2="110"/><line x1="70" y1="190" x2="1040" y2="190"/><line x1="70" y1="270" x2="1040" y2="270"/>'
    yax = '<text x="60" y="34">120€</text><text x="60" y="114">80€</text><text x="60" y="194">40€</text><text x="60" y="274">0€</text>'
    return ('<svg viewBox="0 0 1100 320" style="width:100%;height:auto" font-family="-apple-system,Segoe UI,sans-serif">'
            '<g stroke="#262626" stroke-width="1">' + grid + '</g>'
            '<g fill="#8b8b8b" font-size="11" text-anchor="end">' + yax + '</g>'
            '<g fill="rgba(212,175,55,.20)">' + bars + '</g>'
            '<g fill="#8b8b8b" font-size="10" text-anchor="middle">' + labs + '</g>'
            '<polyline fill="none" stroke="#10b981" stroke-width="2.5" points="' + poly + '"/><g>' + circ + '</g><g>' + mk + '</g>'
            '<g font-size="12" fill="#ededed"><rect x="72" y="6" width="14" height="11" fill="rgba(212,175,55,.45)"/><text x="92" y="16">Spend/día</text>'
            '<line x1="180" y1="11" x2="206" y2="11" stroke="#10b981" stroke-width="3"/><text x="212" y="16">Leads GHL/día</text>'
            '<circle cx="330" cy="11" r="4" fill="#d4af37"/><text x="342" y="16">Venta cerrada</text></g></svg>')

# ================= HTML =================
CSS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_style.html")).read()
GEN_TS = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
WLAB = "%s – %s" % (WIN_START_D.strftime("%d %b"), (WIN_END_D - dt.timedelta(days=1)).strftime("%d %b %Y"))
ticket_med = sd(F["rev"], F["sale"])

H = []
H.append('<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">'
         '<title>Report Captación Ads · ' + WLAB + '</title>' + CSS + '</head><body>')
H.append('<div class="nav-bar"><span class="nav-brand">◆ MIM · N4 · AUTO</span><div class="nav-links">'
         '<a href="#mes">KPIs</a><a href="#semana">Semana</a><a href="#corridos">vs prev</a><a href="#accion">★ Acción</a>'
         '<a href="#funnel">Funnel</a><a href="#discrepancia">Meta vs GHL</a><a href="#plan">Plan</a>'
         '<a href="#diaria">Diaria</a><a href="#adsets">Adsets</a><a href="#creativos">Anuncios</a>'
         '<a href="#evolucion">Evolución</a><a href="#scoring">Ventas</a></div></div><div class="container">')

H.append('<div class="banner">🔄 <strong>Report automático</strong> · datos reales META + GHL · ventana <strong>' + WLAB +
         '</strong> (últimos ' + DD + ' días) · generado ' + GEN_TS + ' · nombres anonimizados.</div>')
H.append('<div class="hero"><div class="hero-tag">Report · Captación Amazon Ads · VSL→Llamada</div>'
         '<h1>Embudo Captación Ads</h1><div class="hero-sub">Funnel Lead→Cualif→Agenda→Call→Venta · ' + DD + ' días (' + WLAB +
         ') · Ticket medio cohorte ~' + eur(ticket_med) + ' · Canal: META · Formato: Video</div></div>')
H.append('<div class="legend">Fuente: <span><span class="src src-meta">META</span> gasto</span> '
         '<span><span class="src src-ghl">GHL</span> conteos</span> <span><span class="src src-deriv">DERIV</span> coste=gasto÷conteo</span>'
         ' · Semáforo vs objetivo: <span class="g">🟢 ≤1,1×</span> <span class="a">🟡 1,1–2,5×</span> <span class="r">🔴 &gt;2,5×</span>'
         ' · <em>Objetivos editables: CPL ' + str(T["cpl"]) + '€ · CPL Q ' + str(T["cplq"]) + '€ · C/Agenda ' + str(T["cag"]) +
         '€ · C/Call ' + str(T["ccall"]) + '€ · CAC ≤' + str(T["cac"]) + '€ · ROAS ≥' + xx(T["roas"]) + '</em></div>')

def tile(label, val, sub, extra, src):
    sp = {"META": '<span class="src src-meta">META</span>', "GHL": '<span class="src src-ghl">GHL</span>',
          "DERIV": '<span class="src src-deriv">DERIV</span>', "": ""}[src]
    s = ('<div class="tile-sub">%s</div>' % sub) if sub else ""
    return '<div class="tile %s"><div class="tile-label">%s %s</div><div class="tile-value">%s</div>%s</div>' % (extra, label, sp, val, s)

# 1 KPIs
big_txt = ("El gran trato del periodo: <strong>" + anon(big.get("name")) + " · " + eur2(float(big.get("monetaryValue") or 0)) +
           "</strong>.") if big else ""
H.append('<section id="mes"><h2><span class="num">1</span>KPIs del periodo <span class="toggle">cohorte: leads que entraron en la ventana</span></h2>'
         '<div class="sub">' + WLAB + ' · ventana corrida de ' + DD + ' días</div><div class="scorecard">' +
         tile("Spend", eur2(SPEND), "campaña activa · 3 adsets", "", "META") +
         tile("Leads", str(F["lead"]), 'CPL <span class="%s">%s</span> · obj %d€' % (sem(cpl, T["cpl"]), eur(cpl), T["cpl"]), "green", "GHL") +
         tile("Cualificados", str(F["q"]), 'CPL Q <span class="%s">%s</span> · obj %d€' % (sem(cplq, T["cplq"]), eur(cplq), T["cplq"]), "amber", "GHL") +
         tile("Agendas", str(F["ag"]), 'C/Agenda <span class="%s">%s</span> · obj %d€' % (sem(cag, T["cag"]), eur(cag), T["cag"]), "amber", "GHL") +
         tile("Calls", str(F["call"]), 'C/Call <span class="%s">%s</span> · obj %d€' % (sem(ccall, T["ccall"]), eur(ccall), T["ccall"]), "green", "GHL") +
         tile("Ventas", '<span class="g">%d</span>' % F["sale"], "Cash cohorte " + eur2(F["rev"]), "gold", "GHL") +
         tile("CAC", '<span class="%s">%s</span>' % (sem_low(cac, T["cac"]), eur(cac)), "target ≤ %d€ · cohorte" % T["cac"], "green", "DERIV") +
         tile("ROAS · Cash", '<span class="g">%s</span>' % xx(roas), "target ≥ %s · cash cohorte" % xx(T["roas"]), "green", "DERIV") +
         tile("Cierres evento", '<span class="g">%d</span>' % len(won_evt), eur2(cash_evt) + " cerrados en ventana", "gold", "GHL") +
         '</div><div class="sub" style="margin-top:10px">⚠️ <strong>Dos lecturas de venta:</strong> <strong>cohorte</strong> = de los ' +
         str(F["lead"]) + ' leads que entraron, ' + str(F["sale"]) + ' ya compraron (' + eur2(F["rev"]) +
         ', atribuible a este gasto). <strong>Evento</strong> = ' + str(len(won_evt)) + ' tratos cerrados dentro de la ventana (' +
         eur2(cash_evt) + ', incluye leads de meses previos). ' + big_txt + '</div></section>')

# 2 semana
def cost_or(f, num, obj):
    if f[num] == 0: return '<td class="r">— <span class="cellsub">%s·0</span></td>' % eur(f["spend"])
    c = f["spend"] / f[num]; return '<td><span class="%s">%s</span><span class="cellsub">%s·%d</span></td>' % (sem(c, obj), eur(c), eur(f["spend"]), f[num])
dead = min(weeks, key=lambda w: (w["q"], -w["spend"]))
H.append('<section id="semana"><h2><span class="num">2</span>Desglose por semana</h2>'
         '<div class="sub">Conteo <span class="src src-ghl">GHL</span> (cohorte por entrada del lead) · spend <span class="src src-meta">META</span> · semáforo vs objetivo</div>'
         '<div class="twrap"><table><thead><tr><th class="ml">Métrica</th>' +
         "".join('<th>%s</th>' % w["lab"] for w in weeks) + '<th>Total ' + DD + 'd</th></tr></thead><tbody>')
H.append('<tr><td>Spend <span class="src src-meta">META</span></td>' + "".join('<td>%s</td>' % eur2(w["spend"]) for w in weeks) + '<td><strong>%s</strong></td></tr>' % eur2(SPEND))
H.append('<tr><td>Leads · CPL</td>' + "".join('<td>%d · <span class="%s">%s</span></td>' % (w["lead"], sem(w["spend"]/w["lead"], T["cpl"]) if w["lead"] else "r", eur(w["spend"]/w["lead"]) if w["lead"] else "—") for w in weeks) + '<td>%d · <span class="%s">%s</span></td></tr>' % (F["lead"], sem(cpl, T["cpl"]), eur(cpl)))
H.append('<tr><td>Cualificados · CPL Q</td>' + "".join(cost_or(w, "q", T["cplq"]) for w in weeks) + '<td><span class="%s">%s</span> · %dq</td></tr>' % (sem(cplq, T["cplq"]), eur(cplq), F["q"]))
H.append('<tr><td>Agendas · C/Agenda</td>' + "".join(cost_or(w, "ag", T["cag"]) for w in weeks) + '<td><span class="%s">%s</span> · %da</td></tr>' % (sem(cag, T["cag"]), eur(cag), F["ag"]))
H.append('<tr><td>Calls · C/Call</td>' + "".join(cost_or(w, "call", T["ccall"]) for w in weeks) + '<td><span class="%s">%s</span> · %dc</td></tr>' % (sem(ccall, T["ccall"]), eur(ccall), F["call"]))
H.append('<tr><td>Ventas · CAC</td>' + "".join('<td>%d · <span class="%s">%s</span></td>' % (w["sale"], sem_low(w["spend"]/w["sale"], T["cac"]) if w["sale"] else "r", eur(w["spend"]/w["sale"]) if w["sale"] else "—") for w in weeks) + '<td>%d · <span class="%s">%s</span></td></tr>' % (F["sale"], sem_low(cac, T["cac"]), eur(cac)))
H.append('<tr><td>Cash cohorte</td>' + "".join('<td>%s</td>' % (eur2(w["rev"]) if w["rev"] else "—") for w in weeks) + '<td><strong>%s</strong></td></tr>' % eur2(F["rev"]))
H.append('</tbody></table></div><div class="sub" style="margin-top:8px">🔴 <strong>Semana más floja (' + dead["lab"] + '):</strong> ' +
         eur2(dead["spend"]) + ' gastados, ' + str(dead["lead"]) + ' leads, <strong>' + str(dead["q"]) + ' cualificados</strong>.</div></section>')

# 3 vs prev
def delta(c, p, inv=False):
    if not p: return ("—", "n0")
    d = (c - p) / p * 100; up = d >= 0; good = up if inv else (not up)
    cl = "g" if good else "r"; arrow = "▲" if up else "▼"
    if abs(d) < 0.5: cl, arrow = "n0", "▲"
    return ("%s%.0f%%" % (arrow, abs(d)), cl)
ds, dsc = delta(SPEND, SPEND_PRV, inv=True); dl, dlc = delta(F["lead"], P["lead"])
dv, dvc = delta(F["sale"], P["sale"], inv=True); dcac, dcacc = delta(cac or 0, cac_prv or 0)
dr, drc = delta(roas or 0, roas_prv or 0, inv=True)
H.append('<section id="corridos"><h2><span class="num">3</span>Visión ' + DD + ' días vs los ' + DD + ' previos</h2>'
         '<div class="sub">Actual (' + WLAB + ') vs anterior (' + PRV_START_D.strftime("%d %b") + '–' + (WIN_START_D - dt.timedelta(days=1)).strftime("%d %b") + ') · spend prev ' + eur2(SPEND_PRV) + '</div><div class="scorecard">'
         '<div class="tile"><div class="tile-label">Spend ' + DD + 'd <span class="src src-meta">META</span></div><div class="tile-value">' + eur2(SPEND) + '</div><div class="tile-sub ' + dsc + '">' + ds + ' vs prev</div></div>'
         '<div class="tile red"><div class="tile-label">Leads ' + DD + 'd <span class="src src-ghl">GHL</span></div><div class="tile-value">' + str(F["lead"]) + '</div><div class="tile-sub ' + dlc + '">' + dl + ' · ' + str(P["lead"]) + ' prev</div></div>'
         '<div class="tile amber"><div class="tile-label">Ventas cohorte <span class="src src-ghl">GHL</span></div><div class="tile-value">' + str(F["sale"]) + '</div><div class="tile-sub ' + dvc + '">' + dv + ' · ' + str(P["sale"]) + ' prev</div></div>'
         '<div class="tile"><div class="tile-label">CAC <span class="src src-deriv">DERIV</span></div><div class="tile-value ' + sem_low(cac, T["cac"]) + '">' + eur(cac) + '</div><div class="tile-sub ' + dcacc + '">' + dcac + ' · ' + eur(cac_prv) + ' prev</div></div>'
         '<div class="tile green"><div class="tile-label">ROAS cohorte <span class="src src-deriv">DERIV</span></div><div class="tile-value g">' + xx(roas) + '</div><div class="tile-sub ' + drc + '">' + dr + ' · ' + xx(roas_prv) + ' prev</div></div>'
         '<div class="tile"><div class="tile-label">Lead→Cualif <span class="src src-deriv">DERIV</span></div><div class="tile-value">' + pct(F["q"], F["lead"]) + '</div><div class="tile-sub a">cualificación</div></div>'
         '<div class="tile"><div class="tile-label">Call→Venta <span class="src src-deriv">DERIV</span></div><div class="tile-value">' + pct(F["sale"], F["call"]) + '</div><div class="tile-sub">cierre</div></div>'
         '</div></section>')

# accion
acc_q = pct(F["q"], F["lead"])
H.append('<section id="accion"><div class="principal"><div class="principal-tag">★ La acción si solo hicieras UNA</div>'
         '<div class="principal-title">Arreglar la cualificación: solo ' + acc_q + ' de los leads pasan el filtro</div>'
         '<div class="principal-body">De ' + str(F["lead"]) + ' leads, ' + str(F["q"]) + ' cualifican. El cuello está en la <strong>entrada</strong>, no en el cierre (' +
         pct(F["sale"], F["call"]) + ' Call→Venta). Revisa segmentación/creativo del adset que trae leads que no encajan y endurece la pre-cualificación.</div>'
         '<div class="principal-impact">→ Subir la cualificación mueve toda la cascada con el mismo gasto</div></div></section>')

# funnel
H.append('<section id="funnel"><h2><span class="num">4</span>Funnel · cohorte ' + DD + ' días</h2>'
         '<div class="sub">Conteo <span class="src src-ghl">GHL</span> · coste por fase <span class="src src-deriv">DERIV</span> · el peor salto = cuello</div><div class="funnel">'
         '<div class="fstep"><div class="fstep-label">Lead</div><div class="fstep-n">' + str(F["lead"]) + '</div><div class="fstep-cost">CPL ' + eur(cpl) + '</div></div><div class="farrow">→</div>'
         '<div class="fstep cuello"><div class="fstep-label">Cualificado</div><div class="fstep-n">' + str(F["q"]) + '</div><div class="fstep-cost">CPL Q ' + eur(cplq) + '</div><div class="fstep-conv r">' + pct(F["q"], F["lead"]) + '</div><span class="cuello-tag">CUELLO · cualificación</span></div><div class="farrow">→</div>'
         '<div class="fstep"><div class="fstep-label">Agenda</div><div class="fstep-n">' + str(F["ag"]) + '</div><div class="fstep-cost">C/Agenda ' + eur(cag) + '</div><div class="fstep-conv g">' + pct(F["ag"], F["q"]) + '</div></div><div class="farrow">→</div>'
         '<div class="fstep"><div class="fstep-label">Call</div><div class="fstep-n">' + str(F["call"]) + '</div><div class="fstep-cost">C/Call ' + eur(ccall) + '</div><div class="fstep-conv g">' + pct(F["call"], F["ag"]) + '</div></div><div class="farrow">→</div>'
         '<div class="fstep"><div class="fstep-label">Venta</div><div class="fstep-n">' + str(F["sale"]) + '</div><div class="fstep-cost">CAC ' + eur(cac) + '</div><div class="fstep-conv a">' + pct(F["sale"], F["call"]) + '</div></div></div></section>')

# discrepancia
disc = ""
for nm, v in adset_rows:
    disc += '<tr><td><code>%s</code></td><td><span class="src src-meta">META</span> %d</td><td>%s</td><td class="%s">%s</td></tr>' % (
        nm, v["leads"], eur2(v["spend"]), sem(v["spend"]/v["leads"], T["cpl"]) if v["leads"] else "r", eur(v["spend"]/v["leads"]) if v["leads"] else "—")
factor = (meta_leads_total / F["lead"]) if F["lead"] else 0
H.append('<section id="discrepancia"><h2><span class="num">6</span>Discrepancia META vs GHL · leads</h2>'
         '<div class="sub">Salud del tracking. META cuenta píxel/formulario; GHL cuenta oportunidades reales del pipeline.</div>'
         '<div class="twrap"><table><thead><tr><th>Adset</th><th>Leads META (píxel)</th><th>Spend</th><th>CPL META</th></tr></thead><tbody>' + disc +
         '<tr style="font-weight:700"><td>TOTAL META</td><td>%d</td><td>%s</td><td class="a">%s</td></tr>' % (meta_leads_total, eur2(SPEND), eur(sd(SPEND, meta_leads_total))) +
         '<tr style="font-weight:700"><td>TOTAL GHL (oportunidades)</td><td>%d</td><td>%s</td><td class="a">%s</td></tr>' % (F["lead"], eur2(SPEND), eur(cpl)) +
         '</tbody></table></div><div class="sub" style="margin-top:8px">Factor META/GHL ≈ <strong>%.1f×</strong>. ⚠️ Sin UTM por anuncio en GHL, el coste por etapa <em>por anuncio</em> no es atribuible.</div></section>' % factor)

# plan
exp = max(adset_rows, key=lambda x: (x[1]["spend"]/x[1]["leads"]) if x[1]["leads"] else 9e9)
best = min([a for a in adset_rows if a[1]["leads"]], key=lambda x: x[1]["spend"]/x[1]["leads"]) if any(v["leads"] for _, v in adset_rows) else adset_rows[0]
H.append('<section id="plan"><h2><span class="num">7</span>Plan de acción</h2><div class="sub">Reglas: cualificación &lt;55% → revisar entrada · frecuencia &gt;2,1 → renovar · escalar ±20%/día máx</div>')
H.append('<div class="rec p0"><div class="rec-prio">P0 · URGENTE</div><div class="rec-title">Auditar la cualificación de entrada (' + acc_q + ')</div><div class="rec-body">La mitad del gasto se va en leads que no pasan el filtro. Revisa segmentación y promesa del creativo del adset que trae esos leads.</div></div>')
H.append('<div class="rec p1"><div class="rec-prio">P1 · ESTA SEMANA</div><div class="rec-title">Revisar <code>' + exp[0] + '</code> · CPL píxel ' + (eur(exp[1]["spend"]/exp[1]["leads"]) if exp[1]["leads"] else "—") + '</div><div class="rec-body">' + eur2(exp[1]["spend"]) + ' con ' + str(exp[1]["leads"]) + ' leads de píxel: el adset menos eficiente en captación. Renovar creativo o recortar presupuesto.</div></div>')
H.append('<div class="rec p2"><div class="rec-prio">P2 · ESCALAR</div><div class="rec-title">Escalar <code>' + best[0] + '</code> +20% · CPL píxel ' + (eur(best[1]["spend"]/best[1]["leads"]) if best[1]["leads"] else "—") + '</div><div class="rec-body">El más barato en captación. Sube presupuesto con la regla ±20%/día y vigila que la cualificación aguante.</div></div></section>')

# diaria
drows = ""
for d in daily:
    day = dt.date.fromisoformat(d["date_start"]); sp = float(d["spend"])
    wd = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][day.weekday()]
    lc = leads_by_day.get(d["date_start"], 0); ml = leadval(d.get("actions"))
    wv = won_by_day.get(d["date_start"]); vs = ("%d" % wv[0]) if wv else "0"; cash = eur2(wv[1]) if wv else "—"
    drows += '<tr><td>%s %s</td><td>%s</td><td>%d</td><td class="%s">%s</td><td>%d</td><td>%s</td><td>%s</td></tr>' % (
        day.strftime("%d/%m"), wd, eur2(sp), lc, sem(sp/lc, T["cpl"]) if lc else "", eur(sp/lc) if lc else "—", ml, vs, cash)
H.append('<section id="diaria"><h2><span class="num">8</span>Tendencia diaria · ' + DD + ' días</h2>'
         '<div class="sub">Barras = Spend <span class="src src-meta">META</span> · línea = Leads GHL/día · ◆ = venta cerrada</div>'
         '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 16px 10px;margin-bottom:14px">' + svg_chart() + '</div>'
         '<div class="twrap"><table><thead><tr><th>Día</th><th>Spend <span class="src src-meta">META</span></th><th>Leads <span class="src src-ghl">GHL</span></th><th>CPL</th><th>Leads píxel <span class="src src-meta">META</span></th><th>Ventas</th><th>Cash</th></tr></thead><tbody>' + drows + '</tbody></table></div></section>')

# adsets
arows = ""
for nm, v in adset_rows:
    arows += '<tr><td><strong>%s</strong> <span class="on">● ON</span></td><td>%s</td><td>%d</td><td>%d</td><td class="%s">%s</td></tr>' % (
        nm, eur2(v["spend"]), v["ads"], v["leads"], sem(v["spend"]/v["leads"], T["cpl"]) if v["leads"] else "r", eur(v["spend"]/v["leads"]) if v["leads"] else "—")
H.append('<section id="adsets"><h2><span class="num">10</span>Adsets · spend &amp; captación</h2>'
         '<div class="sub">Spend &amp; leads píxel <span class="src src-meta">META</span> · ordenado por gasto. (Coste post-lead necesita UTM en GHL.)</div>'
         '<div class="twrap"><table><thead><tr><th>Adset</th><th>Spend ' + DD + 'd</th><th># Anuncios</th><th>Leads píxel</th><th>CPL píxel</th></tr></thead><tbody>' + arows + '</tbody></table></div></section>')

# anuncios
crows = ""; maxfreq = 0
for a in sorted(ads, key=lambda x: -float(x["spend"])):
    sp = float(a["spend"]); fr = float(a.get("frequency") or 0); l = leadval(a.get("actions")); maxfreq = max(maxfreq, fr)
    frc = "r" if fr > 2.1 else ("a" if fr > 1.8 else "g")
    crows += '<tr><td><code>%s</code> <span class="on">● ON</span></td><td><span class="pill">%s</span></td><td>%s</td><td>%d</td><td class="%s">%s</td><td class="%s">%s</td></tr>' % (
        a.get("ad_name"), (a.get("adset_name") or "").replace("ADSET ", ""), eur2(sp), l, sem(sp/l, T["cpl"]) if l else "", eur(sp/l) if l else "—", frc, ("%.2f" % fr).replace(".", ","))
fat = "🔴 Hay creativos con frecuencia &gt;2,1 (fatiga)." if maxfreq > 2.1 else "✅ Ninguna frecuencia supera 2,1 (máx %s) → sin fatiga." % ("%.2f" % maxfreq).replace(".", ",")
H.append('<section id="creativos"><h2><span class="num">11</span>Anuncios · spend · captación · frecuencia</h2>'
         '<div class="sub">Inversión + leads píxel + frecuencia <span class="src src-meta">META</span> (semáforo: &gt;2,1 = fatiga).</div>'
         '<div class="twrap"><table><thead><tr><th>Anuncio</th><th>Adset</th><th>Spend</th><th>Leads píxel</th><th>CPL píxel</th><th>Frecuencia</th></tr></thead><tbody>' + crows +
         '</tbody></table></div><div class="sub" style="margin-top:8px">' + fat + '</div></section>')

# evolucion
H.append('<section id="evolucion"><h2><span class="num">12</span>Evolución semanal del funnel</h2><div class="sub">Cohorte por semana de entrada</div>'
         '<div class="twrap"><table class="evol"><thead><tr><th class="ml">Métrica</th>' + "".join('<th>%s</th>' % w["lab"] for w in weeks) + '</tr></thead><tbody>')
H.append('<tr><td class="ml"><strong>Spend</strong></td>' + "".join('<td>%s</td>' % eur2(w["spend"]) for w in weeks) + '</tr>')
H.append('<tr><td class="ml"><strong>Leads</strong><div class="sub2">CPL</div></td>' + "".join('<td><div class="n">%d</div><div class="c">%s</div></td>' % (w["lead"], eur(w["spend"]/w["lead"]) if w["lead"] else "—") for w in weeks) + '</tr>')
H.append('<tr><td class="ml"><strong>Cualificados</strong><div class="sub2">CPL Q</div></td>' + "".join('<td><div class="n">%d</div><div class="c">%s</div></td>' % (w["q"], eur(w["spend"]/w["q"]) if w["q"] else "—") for w in weeks) + '</tr>')
H.append('<tr><td class="ml"><strong>Calls</strong><div class="sub2">C/Call</div></td>' + "".join('<td><div class="n">%d</div><div class="c">%s</div></td>' % (w["call"], eur(w["spend"]/w["call"]) if w["call"] else "—") for w in weeks) + '</tr>')
H.append('<tr><td class="ml"><strong>Ventas</strong><div class="sub2">CAC</div></td>' + "".join('<td><div class="n">%d</div><div class="c">%s</div></td>' % (w["sale"], eur(w["spend"]/w["sale"]) if w["sale"] else "—") for w in weeks) + '</tr>')
H.append('<tr><td class="ml"><strong>Cash</strong></td>' + "".join('<td>%s</td>' % (eur2(w["rev"]) if w["rev"] else "—") for w in weeks) + '</tr></tbody></table></div></section>')

# ventas
won_all = sorted([o for o in opps if is_won(o)], key=lambda o: o.get("lastStatusChangeAt") or "", reverse=True)
vrows = ""
for o in won_all:
    inw = o.get("lastStatusChangeAt") and WIN_A <= pdt(o["lastStatusChangeAt"]) < WIN_B
    badge = '<span class="dec esc">en ventana</span>' if inw else '<span class="dec sd">previo</span>'
    cd = pdt(o.get("createdAt")); wd = pdt(o.get("lastStatusChangeAt"))
    vrows += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        anon(o.get("name")), eur2(float(o.get("monetaryValue") or 0)),
        cd.strftime("%d/%m") if cd else "—", wd.strftime("%d/%m") if wd else "—", badge)
total_won = sum(float(o.get("monetaryValue") or 0) for o in won_all)
H.append('<section id="scoring"><h2><span class="num">13</span>Ventas cerradas (pipeline completo)</h2>'
         '<div class="sub">%d tratos ganados · %s acumulado · nombres anonimizados</div>'
         '<div class="chips"><span class="dec esc">%d en ventana</span><span class="dec sd">%d previos</span><span class="dec obs">ticket medio %s</span></div>'
         '<div class="twrap"><table><thead><tr><th>Cliente</th><th>Valor</th><th>Entró</th><th>Cerró</th><th>Ventana</th></tr></thead><tbody>%s</tbody></table></div></section>' % (
             len(won_all), eur2(total_won), len(won_evt), len(won_all)-len(won_evt), eur(sd(total_won, len(won_all))), vrows))

H.append('<div class="banner" style="margin-top:30px">📌 Report automático. Ajusta los <em>objetivos</em> de CPL/CAC a tu negocio (dict <code>T</code> en generate.py) y añade UTMs por anuncio en GHL para atribuir coste por etapa a cada creativo.</div>')
H.append('</div></body></html>')

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
open(out, "w").write("".join(H))
print("OK index.html  spend=%.2f leads=%d q=%d call=%d sale=%d roas=%s" % (SPEND, F["lead"], F["q"], F["call"], F["sale"], xx(roas)))
