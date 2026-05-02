# futnot

Bot que te avisa por WhatsApp **30 minutos antes** y al **comienzo** de los partidos de los equipos que elijas. Corre 100% gratis en GitHub Actions.

Por default está configurado con **Boca Juniors** y **FC Barcelona** en todas sus competencias oficiales (liga, copas nacionales e internacionales).

---

## 1. Conseguir las credenciales (5 minutos)

### a) API-Football (datos de partidos)

1. Registrate gratis en https://dashboard.api-football.com/register
2. Confirmá el email y entrá al dashboard
3. Copiá tu **API key** (sección "My Access")
4. Plan gratuito: 100 requests/día. Este bot usa ~24/día con 2 equipos, sobra.

### b) CallMeBot (envío de WhatsApp)

1. Agendá el contacto **+34 644 51 95 23** en tu teléfono
2. Desde **tu WhatsApp** mandale el mensaje exacto:
   ```
   I allow callmebot to send me messages
   ```
3. En unos minutos te responde con tu **apikey personal**
4. Anotá tu número en formato internacional sin `+` (ej. Argentina con celular `11 1234-5678` → `5491112345678`)

> Nota: CallMeBot sólo manda mensajes a **tu propio número** (el que activó). No sirve para mandar a contactos.

---

## 2. Subir el proyecto a GitHub

1. Crear un repo nuevo en https://github.com/new
   - **Importante**: marcá **Public** para tener minutos ilimitados de GitHub Actions (en privado son 2000/mes y este cron los consume).
   - Los secrets quedan encriptados igual, no se exponen.
2. Desde esta carpeta (`futnot`):
   ```bash
   git init
   git add .
   git commit -m "initial commit"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/futnot.git
   git push -u origin main
   ```

---

## 3. Configurar los secrets en GitHub

En el repo: **Settings → Secrets and variables → Actions → New repository secret**

Crear los tres secrets:

| Nombre              | Valor                                          |
|---------------------|------------------------------------------------|
| `API_FOOTBALL_KEY`  | la API key de api-football.com                 |
| `WHATSAPP_PHONE`    | tu teléfono en formato internacional, sin `+`  |
| `WHATSAPP_APIKEY`   | el apikey que te dio CallMeBot                 |

---

## 4. Probar que funciona

1. En el repo: **Actions → notify → Run workflow** (botón a la derecha)
2. Esperá ~30 segundos a que termine
3. Si hay un partido próximo dentro de los próximos 30 min de Boca o Barcelona, te llega WhatsApp
4. Si no, revisá los logs del workflow para confirmar que la API responde bien

A partir de ahí el cron corre solo cada 5 minutos.

---

## 5. Cambiar/agregar equipos

Editá `config/teams.json`. Necesitás el ID de API-Football del equipo:

```bash
curl -H "x-apisports-key: TU_API_KEY" \
  "https://v3.football.api-sports.io/teams?search=river"
```

Copiá el `id` del resultado y agregalo:

```json
{
  "teams": [
    { "id": 451, "name": "Boca Juniors", "emoji": "🔵🟡" },
    { "id": 529, "name": "FC Barcelona", "emoji": "🔵🔴" },
    { "id": 435, "name": "River Plate", "emoji": "⚪🔴" }
  ]
}
```

Hacé `git push` y listo.

---

## 6. Probar localmente (opcional)

```bash
cp .env.example .env
# editá .env con tus credenciales
pip install -r requirements.txt

# carga .env y ejecuta
python -c "from pathlib import Path; [__import__('os').environ.update([l.strip().split('=',1)]) for l in Path('.env').read_text().splitlines() if l.strip() and not l.startswith('#')]; exec(open('src/main.py').read())"
```

---

## Cómo funciona

- GitHub Actions ejecuta `src/main.py` cada 5 minutos.
- El script consulta los próximos 5 partidos de cada equipo en API-Football.
- Si un partido arranca dentro de los próximos 30 min y aún no se notificó, manda WhatsApp.
- Si un partido ya empezó (hasta 60 min de gracia) y aún no se notificó, manda WhatsApp.
- El estado de las notificaciones enviadas se guarda en `state/sent.json` (committeado por el bot) para evitar duplicados.

## Limitaciones

- **CallMeBot** es no-oficial y depende de WhatsApp Web. Funciona, pero puede romperse si WhatsApp cambia algo. Si pasa, alternativa robusta: cambiar a Telegram Bot API (oficial, gratis, ilimitada). El cambio sería sólo la función `send_whatsapp` en `src/main.py`.
- **API-Football free tier**: 100 requests/día. Con 2 equipos y cron cada 5 min, usás ~24 al día. Si agregás muchos equipos puede no alcanzar.
- El cron de GitHub Actions tiene una latencia de hasta 5–15 minutos en horas pico — los avisos pueden llegar uno o dos minutos antes/después de la marca exacta.
