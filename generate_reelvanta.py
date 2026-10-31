# -*- coding: utf-8 -*-
"""
Dashboard ReelVanta — pipeline propio (Nuevo Lead→Contactado→Cualifica→Agenda→Show→Compra),
cuenta de Meta propia (act_681492424611813), con desglose de leads/agendas/ventas POR ANUNCIO
y por landing A/B (pendiente de trackear variante). Self-contained, reutiliza report_style.html.
"""
import os, sys, json, time, datetime as dt, urllib.parse, urllib.request
from collections import defaultdict

GRAPH = "https://graph.facebook.com/v21.0"
GHLAPI = "https://services.leadconnectorhq.com"
PIPELINE_ID = "Efil5GNmHZF93zfoTw25"            # Embudo Captación ReelVanta
META_ACCT = "act_681492424611813"               # cuenta Meta de ReelVanta
CAMPAIGN_KW = "reelvanta"                        # campañas de ReelVanta (por nombre)
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "30") or "30")

def load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
load_env()
GHL_TOKEN = os.environ["GHL_TOKEN"]; LOC = os.environ["GHL_LOCATION_ID"].split("/")[-1]
META_TOKEN = os.environ["META_TOKEN"]

def _req(url, headers):
    for a in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if a < 3 and (e.code in (429, 500, 502, 503) or '"code":2' in body or '"code":1' in body or "temporarily" in body.lower()):
                time.sleep(5 * (a + 1)); continue
            raise SystemExit("HTTP %s en %s -> %s" % (e.code, url.split("access_token=")[0][:90], body[:300]))
        except urllib.error.URLError:
            if a < 3: time.sleep(5 * (a + 1)); continue
            raise
UA = "Mozilla/5.0 report-gen"
def ghl(path, params): return _req(GHLAPI + path + "?" + urllib.parse.urlencode(params),
    {"Authorization": "Bearer " + GHL_TOKEN, "Version": "2021-07-28", "Accept": "application/json", "User-Agent": UA})
def meta(path, params):
    params = dict(params); params["access_token"] = META_TOKEN
    return _req(GRAPH + "/" + path + "?" + urllib.parse.urlencode(params), {"User-Agent": UA})

# ---------- fechas ----------
TODAY = dt.date.today()
WIN_A = dt.datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=dt.timezone.utc) - dt.timedelta(days=WINDOW_DAYS)
WIN_B = dt.datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=dt.timezone.utc)
def tr(a, b): return json.dumps({"since": a.date().isoformat(), "until": (b - dt.timedelta(days=1)).date().isoformat()}, separators=(",", ":"))
def pdt(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

# ---------- pipeline / stages ReelVanta ----------
pls = ghl("/opportunities/pipelines", {"locationId": LOC})["pipelines"]
pipe = next((p for p in pls if p["id"] == PIPELINE_ID), pls[0])
stages = sorted(pipe["stages"], key=lambda s: s["position"])
POS = {s["id"]: s["position"] for s in stages}
def idx(sub, d):
    return next((s["position"] for s in stages if sub.lower() in s["name"].lower()), d)
CONT_P = idx("contactado", 1); CUAL_P = idx("cualifica", 6); AG_P = idx("agenda", 7)
SHOW_P = idx("show", 8); NOSHOW_P = idx("no show", 9); WON_P = idx("compra", 12)
def pos(o): return POS.get(o.get("pipelineStageId"), -1)
def is_won(o): return pos(o) == WON_P or o.get("status") == "won"

# ---------- opps ReelVanta ----------
def fetch_opps():
    out, page = [], 1
    while True:
        d = ghl("/opportunities/search", {"location_id": LOC, "limit": 100, "page": page})
        b = d.get("opportunities", []); out += b
        if len(b) < 100 or page > 10: break
        page += 1
    return [o for o in out if o.get("pipelineId") == PIPELINE_ID]
opps = fetch_opps()
def inwin(o): c = pdt(o.get("createdAt")); return c and WIN_A <= c < WIN_B
cur = [o for o in opps if inwin(o)]

# atribución (ad + landing) por contacto, solo de la ventana
_cc = {}
def contact(cid):
    if cid not in _cc:
        try: _cc[cid] = ghl("/contacts/" + cid, {}).get("contact", {})
        except SystemExit: _cc[cid] = {}
    return _cc[cid]
def ad_of(o):
    c = contact(o.get("contactId") or "")
    return (c.get("lastAttributionSource") or {}).get("utmContent") or "Sin anuncio"

def F(sub):
    return dict(
        lead=len(sub),
        cont=sum(1 for o in sub if pos(o) >= CONT_P),
        cual=sum(1 for o in sub if pos(o) >= CUAL_P),
        ag=sum(1 for o in sub if pos(o) >= AG_P),
        show=sum(1 for o in sub if pos(o) >= SHOW_P and pos(o) != NOSHOW_P),
        venta=sum(1 for o in sub if is_won(o)),
        cash=sum(float(o.get("monetaryValue") or 0) for o in sub if is_won(o)),
    )
f = F(cur)

# ---------- META ReelVanta ----------
def _mspend(params):
    d = meta("%s/insights" % META_ACCT, params).get("data", [])
    return d
acct = _mspend({"time_range": tr(WIN_A, WIN_B), "fields": "spend"})
SPEND = float(acct[0]["spend"]) if acct else 0.0
ads = _mspend({"time_range": tr(WIN_A, WIN_B), "level": "ad",
               "fields": "ad_name,spend,frequency,actions", "limit": 100})
LEADT = {"lead", "offsite_conversion.fb_pixel_lead", "onsite_web_lead"}
def px(a): return int(sum(float(x["value"]) for x in (a.get("actions") or []) if x["action_type"] in LEADT))
spend_by_ad = {a["ad_name"]: float(a["spend"]) for a in ads}
freq_by_ad = {a["ad_name"]: float(a.get("frequency") or 0) for a in ads}

# ---------- por anuncio (GHL leads/agendas/ventas + META spend) ----------
ad_rows = defaultdict(lambda: {"lead": 0, "ag": 0, "venta": 0, "cash": 0.0})
for o in cur:
    a = ad_of(o); r = ad_rows[a]
    r["lead"] += 1
    if pos(o) >= AG_P: r["ag"] += 1
    if is_won(o): r["venta"] += 1; r["cash"] += float(o.get("monetaryValue") or 0)
# unir con spend (match por nombre); añadir anuncios con gasto y 0 leads GHL
for nm in spend_by_ad:
    ad_rows[nm]  # asegura fila
rows = []
for nm, r in ad_rows.items():
    rows.append((nm, spend_by_ad.get(nm, 0.0), r["lead"], r["ag"], r["venta"], r["cash"], freq_by_ad.get(nm, 0.0)))
rows.sort(key=lambda x: -x[1])

# ---------- derivados ----------
def sd(n, d): return (n / d) if d else None
cpl = sd(SPEND, f["lead"]); cac = sd(SPEND, f["venta"]); roas = sd(f["cash"], SPEND)
cpcual = sd(SPEND, f["cual"]); cpag = sd(SPEND, f["ag"])

# ---------- formato ----------
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
_ids = {}
def anon(n):
    n = (n or "").strip()
    if not n: return "Lead"
    if n not in _ids: _ids[n] = len(_ids) + 1
    p = [x for x in n.replace(".", " ").split() if x]
    return (".".join(x[0].upper() for x in p[:2]) + ".") + " · #%02d" % _ids[n]

# objetivos (editables)
T = dict(cpl=15, cpcual=40, cpag=60, cac=400, roas=2.5)
def sem(v, obj):
    if v is None: return ""
    r = v / obj; return "g" if r <= 1.1 else ("a" if r <= 2.5 else "r")
def semlow(v, obj):
    if v is None: return ""
    return "g" if v / obj <= 1.0 else ("a" if v / obj <= 1.4 else "r")

CSS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_style.html")).read()
WLAB = "%s – %s" % (WIN_A.strftime("%d %b"), (WIN_B - dt.timedelta(days=1)).strftime("%d %b %Y"))
GEN = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
H = []
H.append('<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">'
         '<title>Report ReelVanta · ' + WLAB + '</title>' + CSS + '</head><body>')
H.append('<div class="nav-bar"><span class="nav-brand">◆ ReelVanta</span><div class="nav-links">'
         '<a href="#kpis">KPIs</a><a href="#funnel">Funnel</a><a href="#anuncios">Por anuncio</a>'
         '<a href="#landing">Landing A/B</a><a href="#ventas">Ventas</a></div></div><div class="container">')
H.append('<div class="banner">🔄 <strong>ReelVanta</strong> · datos reales META (act ...611813) + GHL · ventana <strong>' + WLAB +
         '</strong> (' + str(WINDOW_DAYS) + ' días) · generado ' + GEN + ' · nombres anonimizados.</div>')
H.append('<div class="hero"><div class="hero-tag">Report · ReelVanta · Editor de contenido IA</div>'
         '<h1>Embudo Captación ReelVanta</h1><div class="hero-sub">Funnel Lead→Contactado→Cualifica→Agenda→Show→Compra · '
         + str(WINDOW_DAYS) + ' días (' + WLAB + ') · Canal: META</div></div>')

# KPIs
def tile(lab, val, sub, extra, src):
    sp = {"META": '<span class="src src-meta">META</span>', "GHL": '<span class="src src-ghl">GHL</span>',
          "DERIV": '<span class="src src-deriv">DERIV</span>', "": ""}[src]
    s = ('<div class="tile-sub">%s</div>' % sub) if sub else ""
    return '<div class="tile %s"><div class="tile-label">%s %s</div><div class="tile-value">%s</div>%s</div>' % (extra, lab, sp, val, s)
H.append('<section id="kpis"><h2><span class="num">1</span>KPIs del periodo</h2><div class="sub">' + WLAB + '</div><div class="scorecard">' +
         tile("Spend", eur2(SPEND), "campaña Captación ReelVanta", "", "META") +
         tile("Leads", str(f["lead"]), 'CPL <span class="%s">%s</span>' % (sem(cpl, T["cpl"]), eur(cpl)), "", "GHL") +
         tile("Contactados", str(f["cont"]), pct(f["cont"], f["lead"]) + " de leads", "", "GHL") +
         tile("Cualificados", str(f["cual"]), 'CPL Q <span class="%s">%s</span>' % (sem(cpcual, T["cpcual"]), eur(cpcual)), "", "GHL") +
         tile("Agendas", str(f["ag"]), 'C/Agenda <span class="%s">%s</span>' % (sem(cpag, T["cpag"]), eur(cpag)), "", "GHL") +
         tile("Shows", str(f["show"]), pct(f["show"], f["ag"]) + " show rate", "", "GHL") +
         tile("Ventas", '<span class="g">%d</span>' % f["venta"], "Cash " + eur2(f["cash"]), "gold", "GHL") +
         tile("CAC", '<span class="%s">%s</span>' % (semlow(cac, T["cac"]), eur(cac)), "target ≤ %d€" % T["cac"], "", "DERIV") +
         tile("ROAS", '<span class="g">%s</span>' % xx(roas), "cash ÷ spend", "green", "DERIV") +
         '</div></section>')

# Funnel
H.append('<section id="funnel"><h2><span class="num">2</span>Funnel</h2><div class="sub">Conteo <span class="src src-ghl">GHL</span> · coste por fase <span class="src src-deriv">DERIV</span></div><div class="funnel">'
         + '<div class="fstep"><div class="fstep-label">Lead</div><div class="fstep-n">%d</div><div class="fstep-cost">CPL %s</div></div><div class="farrow">→</div>' % (f["lead"], eur(cpl))
         + '<div class="fstep"><div class="fstep-label">Cualificado</div><div class="fstep-n">%d</div><div class="fstep-cost">%s</div><div class="fstep-conv g">%s</div></div><div class="farrow">→</div>' % (f["cual"], eur(cpcual), pct(f["cual"], f["lead"]))
         + '<div class="fstep"><div class="fstep-label">Agenda</div><div class="fstep-n">%d</div><div class="fstep-cost">%s</div><div class="fstep-conv g">%s</div></div><div class="farrow">→</div>' % (f["ag"], eur(cpag), pct(f["ag"], f["cual"]))
         + '<div class="fstep"><div class="fstep-label">Show</div><div class="fstep-n">%d</div><div class="fstep-cost">%s show</div><div class="fstep-conv a">%s</div></div><div class="farrow">→</div>' % (f["show"], pct(f["show"], f["ag"]), pct(f["show"], f["ag"]))
         + '<div class="fstep"><div class="fstep-label">Venta</div><div class="fstep-n">%d</div><div class="fstep-cost">CAC %s</div><div class="fstep-conv a">%s</div></div>' % (f["venta"], eur(cac), pct(f["venta"], f["show"]))
         + '</div></section>')

# Por anuncio
arows = ""
for nm, sp, lead, ag, venta, cash, fr in rows:
    cpla = sd(sp, lead)
    arows += '<tr><td><code>%s</code></td><td>%s</td><td>%d</td><td class="%s">%s</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (
        nm, eur2(sp), lead, sem(cpla, T["cpl"]) if lead else "", eur(cpla) if lead else "—", ag, venta, eur2(cash) if cash else "—")
H.append('<section id="anuncios"><h2><span class="num">3</span>Leads · Agendas · Ventas por anuncio</h2>'
         '<div class="sub">Inversión <span class="src src-meta">META</span> · leads/agendas/ventas <span class="src src-ghl">GHL</span> (vía utmContent) · CPL <span class="src src-deriv">DERIV</span></div>'
         '<div class="twrap"><table><thead><tr><th>Anuncio</th><th>Inversión</th><th>Leads</th><th>CPL</th><th>Agendas</th><th>Ventas</th><th>Cash</th></tr></thead><tbody>'
         + arows +
         '<tr style="font-weight:700"><td>TOTAL</td><td>%s</td><td>%d</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (eur2(SPEND), f["lead"], eur(cpl), f["ag"], f["venta"], eur2(f["cash"])) +
         '</tbody></table></div>'
         '<div class="sub" style="margin-top:8px">Si un lead entró sin <code>utmContent</code>, aparece como <em>Sin anuncio</em> (atribución perdida en el formulario).</div></section>')

# Landing A/B (pendiente)
H.append('<section id="landing"><h2><span class="num">4</span>Landing A/B</h2>'
         '<div class="twrap"><table><thead><tr><th>Landing</th><th>Inversión</th><th>Leads</th><th>Agendas</th><th>Ventas</th></tr></thead><tbody>'
         '<tr><td><strong>A · CONTROL</strong></td><td colspan="4" class="a">⏳ pendiente de trackear variante</td></tr>'
         '<tr><td><strong>B · VARIATION</strong></td><td colspan="4" class="a">⏳ pendiente de trackear variante</td></tr>'
         '</tbody></table></div>'
         '<div class="sub" style="margin-top:8px">⚠️ El A/B es un split test sobre la <strong>misma URL</strong> (<code>lp-captacin-meta-reelvanta-6164</code>), así que GHL no distingue la variante. Para separarlo: campo oculto <code>variante=A/B</code> en cada variante → tag en GHL. Entonces esta tabla se rellena sola.</div></section>')

# Ventas
wons = sorted([o for o in opps if is_won(o)], key=lambda o: o.get("lastStatusChangeAt") or "", reverse=True)
vrows = ""
for o in wons:
    cd = pdt(o.get("createdAt")); wd = pdt(o.get("lastStatusChangeAt"))
    vrows += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        anon(o.get("name")), eur2(float(o.get("monetaryValue") or 0)),
        cd.strftime("%d/%m") if cd else "—", wd.strftime("%d/%m") if wd else "—")
H.append('<section id="ventas"><h2><span class="num">5</span>Ventas (Compra)</h2>'
         '<div class="sub">%d venta(s) · %s · nombres anonimizados</div>'
         '<div class="twrap"><table><thead><tr><th>Cliente</th><th>Valor</th><th>Entró</th><th>Compró</th></tr></thead><tbody>%s</tbody></table></div></section>'
         % (len(wons), eur2(sum(float(o.get("monetaryValue") or 0) for o in wons)), vrows or '<tr><td colspan="4">—</td></tr>'))

H.append('<div class="banner" style="margin-top:30px">📌 Report ReelVanta. Pendiente: trackear la variante A/B (campo oculto) y afinar objetivos de CPL/CAC. Datos de la cuenta Meta act_...611813.</div>')
H.append('</div></body></html>')
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reelvanta.html")
open(out, "w").write("".join(H))
print("OK reelvanta.html  spend=%.2f leads=%d cual=%d ag=%d show=%d venta=%d cash=%.0f" % (SPEND, f["lead"], f["cual"], f["ag"], f["show"], f["venta"], f["cash"]))
