# -*- coding: utf-8 -*-
"""
Envía por correo el ENLACE al report (sin adjuntar HTML), vía Gmail SMTP.
Credenciales (env o .env): GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_TO, REPORT_URL
"""
import os, sys, ssl, smtplib, datetime as dt
from email.message import EmailMessage

def load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

def need(k):
    v = os.environ.get(k, "").strip()
    if not v or v.startswith("PENDIENTE"):
        sys.exit("FALTA %s (env o .env)" % k)
    return v

# Si aún no hay App Password, salimos en VERDE (no rompe el workflow): el report
# ya se publicó, solo se omite el aviso por correo hasta que pegues la contraseña.
_pwd = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
if not _pwd or _pwd.startswith("PENDIENTE"):
    print("⏭️  Email omitido: falta GMAIL_APP_PASSWORD. El report SÍ se publicó.")
    sys.exit(0)

USER = need("GMAIL_USER")
PWD  = _pwd
TO   = need("EMAIL_TO")
URL  = need("REPORT_URL")
hoy  = dt.date.today().strftime("%d/%m/%Y")

msg = EmailMessage()
msg["Subject"] = "📊 Report Captación Ads — %s" % hoy
msg["From"] = USER
msg["To"] = TO
msg.set_content(
    "Hola Ian,\n\n"
    "Tu report de los últimos 30 días (META + GHL) está actualizado.\n\n"
    "👉 Ábrelo aquí: %s\n\n"
    "Datos reales, nombres anonimizados. Se regenera automáticamente cada 3 días.\n\n"
    "— Automatización Report Captación Ads\n" % URL
)
# versión HTML con botón (sigue siendo solo el enlace, sin adjuntar el report)
msg.add_alternative(
    '<div style="font-family:-apple-system,Segoe UI,sans-serif;font-size:15px;color:#1a1a1a">'
    '<p>Hola Ian,</p>'
    '<p>Tu <strong>report de los últimos 30 días</strong> (META + GHL) está actualizado.</p>'
    '<p><a href="%s" style="background:#d4af37;color:#0a0a0a;text-decoration:none;'
    'padding:11px 20px;border-radius:8px;font-weight:700;display:inline-block">Abrir report →</a></p>'
    '<p style="color:#666;font-size:13px">O copia el enlace: <a href="%s">%s</a><br>'
    'Datos reales, nombres anonimizados. Se regenera cada 3 días.</p></div>' % (URL, URL, URL),
    subtype="html",
)

ctx = ssl.create_default_context()
with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
    s.login(USER, PWD)
    s.send_message(msg)
print("Email enviado a %s con enlace %s" % (TO, URL))
