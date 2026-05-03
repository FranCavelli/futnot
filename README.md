# futnot

Bot que te avisa por WhatsApp **antes** (60/45/30/15 min) y al **comienzo** de los partidos de los equipos que elijas. Corre 100% gratis en GitHub Actions.

Por default está configurado con **Boca**, **River**, **Racing**, **Barcelona**, **Manchester City**, **Manchester United**, **Liverpool** y **Chelsea** en todas sus competencias oficiales, más todos los partidos de **octavos, cuartos, semis y finales** de las copas grandes de Europa, Sudamérica y Argentina (aunque no juegue ninguno de esos equipos).

---

## 1. Conseguir las credenciales (5 minutos)

### Datos de partidos

Los datos se obtienen de la API pública de ESPN — gratis, sin API key, sin registro. No hace falta hacer nada.

### CallMeBot (envío de WhatsApp)

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

Crear los dos secrets:

| Nombre              | Valor                                          |
|---------------------|------------------------------------------------|
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

Editá `config/teams.json`. El archivo tiene dos secciones: `teams` (equipos a seguir en sus competencias) y `knockouts` (partidos de fase final de copas importantes, sin importar quién juegue).

### `teams`

Cada equipo tiene:

- `name`: nombre para mostrar
- `match`: substring para identificar al equipo en los partidos (case-insensitive). Ej: `"Boca Juniors"` matchea "Boca Juniors", "Boca" matchea cualquier equipo con "Boca" en el nombre.
- `emoji`: emoji opcional para los mensajes
- `leagues`: lista de slugs de competiciones de ESPN donde sigue al equipo

### `knockouts`

Notifica todos los partidos de **octavos, cuartos, semis y finales** en las copas listadas, aunque no juegue un equipo de `teams`. Si un partido también está cubierto por un equipo seguido, se manda una sola vez.

- `enabled`: `true`/`false` para activar o desactivar la sección
- `emoji`: emoji para los mensajes de partidos sin equipo asociado
- `leagues`: lista de slugs de competiciones a vigilar en fase final

Slugs comunes de ESPN:

| Competición                | Slug                       |
|----------------------------|----------------------------|
| Liga Profesional Argentina | `arg.1`                    |
| Copa Argentina             | `arg.copa`                 |
| Copa Libertadores          | `conmebol.libertadores`    |
| Copa Sudamericana          | `conmebol.sudamericana`    |
| La Liga (España)           | `esp.1`                    |
| Copa del Rey               | `esp.copa_del_rey`         |
| Supercopa de España        | `esp.super_cup`            |
| Champions League           | `uefa.champions`           |
| Europa League              | `uefa.europa`              |
| Conference League          | `uefa.europa.conf`         |
| UEFA Super Cup             | `uefa.super_cup`           |
| Premier League             | `eng.1`                    |
| FA Cup                     | `eng.fa`                   |
| EFL Cup (Carabao)          | `eng.league_cup`           |
| Serie A (Italia)           | `ita.1`                    |
| Coppa Italia               | `ita.coppa_italia`         |
| Bundesliga                 | `ger.1`                    |
| DFB-Pokal                  | `ger.dfb_pokal`            |
| Ligue 1                    | `fra.1`                    |
| Coupe de France            | `fra.coupe_de_france`      |
| Brasileirão Série A        | `bra.1`                    |
| Mundial de Clubes          | `fifa.cwc`                 |
| Mundial (selecciones)      | `fifa.world`               |

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
- El script consulta los próximos partidos de cada equipo en SofaScore.
- Para no consumir muchas requests, los fixtures se cachean 6 horas en `state/fixtures_cache.json`.
- Si un partido arranca dentro de los próximos 30 min y aún no se notificó, manda WhatsApp.
- Si un partido ya empezó (hasta 60 min de gracia) y aún no se notificó, manda WhatsApp.
- El estado de las notificaciones enviadas se guarda en `state/sent.json` (committeado por el bot) para evitar duplicados.

## Limitaciones

- **CallMeBot** es no-oficial y depende de WhatsApp Web. Funciona, pero puede romperse si WhatsApp cambia algo. Si pasa, alternativa robusta: cambiar a Telegram Bot API (oficial, gratis, ilimitada). El cambio sería sólo la función `send_whatsapp` en `src/main.py`.
- **ESPN** expone una API pública pero no documentada. Es muy estable para uso personal pero podrían cambiar los slugs de competiciones o bloquearla. Si pasa, alternativas: football-data.org (cubre Barcelona pero no Boca) o web scraping.
- El cron de GitHub Actions tiene una latencia de hasta 5–15 minutos en horas pico — los avisos pueden llegar uno o dos minutos antes/después de la marca exacta.
