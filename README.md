# Report Captación Ads — automático

Genera cada 3 días un dashboard de los **últimos 30 días** con datos reales de
**META** (gasto) + **GHL** (conteos del pipeline *Embudo Captación Ads*), lo publica
en **GitHub Pages** y envía el **enlace** por correo (sin adjuntar el HTML).

- `generate.py` — descarga META+GHL, calcula el funnel y semáforos, **anonimiza nombres**, escribe `index.html`.
- `send_email.py` — manda el enlace por Gmail SMTP.
- `.github/workflows/report.yml` — orquesta todo en GitHub Actions (cron `0 6 */3 * *`).
- `report_style.html` — estilos del report.

Sin dependencias externas (solo librería estándar de Python).

## Secrets necesarios (Settings → Secrets and variables → Actions)
`GHL_TOKEN`, `GHL_LOCATION_ID`, `META_TOKEN`, `META_AD_ACCOUNT_ID`,
`GMAIL_USER`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`.

## Probar en local
```bash
cp tokens-meta-api-dashboard.env.txt .env   # y añade GMAIL_* / EMAIL_TO
python3 generate.py        # crea index.html
open index.html
```

## Ajustes
- Objetivos de semáforo (CPL/CAC/ROAS…): dict `T` en `generate.py`.
- Frecuencia del envío: `cron` en el workflow.
- Privacidad: el report publicado va **anonimizado** (iniciales + #id). Los nombres
  reales nunca salen del `.env`/CRM.
