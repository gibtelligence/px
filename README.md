# px — terminal de proyectos y agentes

Un solo sitio desde el que ver y abrir **todos** los agentes de Claude Code de
**todos** los proyectos, con su estado real a la vista.

```
 ▌gibtelligence    fluge    eez                        zymba-studio · px
 ● coder  ◐ contable  ○ marca                  ● 4 sin commitear  23 issues
```

Fila 1 = proyectos (la actual con `▌`). Fila 2 = agentes del proyecto actual,
cada uno con su estado. A la derecha, señales del proyecto.

| Glifo | Significa |
|-------|-----------|
| `●` violeta | el agente está trabajando |
| `◐` ámbar | **te está esperando** (permiso, menú, pregunta) |
| `○` gris | prompt libre |
| `·` | pestaña cerrada |

## El modelo

- **Proyecto** = una carpeta bajo `PX_ROOT` (`/Volumes/PERSONAL/Proyectos`), o
  registrada a mano en `~/.config/px/projects.conf` mientras dure la migración.
- **Agente** = una subcarpeta con `CLAUDE.md`. Es la convención que ya usas: el
  `CLAUDE.md` raíz da las reglas maestras y el local, la misión.
- **Pestaña** = una ventana de tmux con `claude` corriendo en el cwd del agente.

Cada proyecto puede fijar su reparto en un **`.px.conf`** propio (tus nombres,
tu orden); si no existe, px descubre los agentes solo. Gibtelligence ya lo tiene,
heredado de `~/.config/gib/sessions.conf` y de `00_ORQUESTACION/AGENTES.md`.

## Uso

```bash
px                      # entra a lo último abierto (o lista si no hay nada)
px ls                   # proyectos, agentes y estado
px open coder contable  # abre pestañas y engancha
px open gibtelligence/coder   # desambigua si el nombre se repite
px open -d coder        # abre en segundo plano, sin engancharse
px close coder
px brief 3.days         # qué se ha movido en cada proyecto
px scan fluge           # genera el .px.conf de un proyecto
px doctor               # comprueba el entorno
px theme                # re-aplica estilo/atajos a lo ya abierto
```

Dentro de la sesión:

| Tecla | Acción |
|-------|--------|
| `Alt ←` / `Alt →` | agente anterior / siguiente |
| `Alt ↑` / `Alt ↓` | proyecto anterior / siguiente |
| `Alt 1..9` | ir al agente N |
| `F1` | briefing en ventana flotante |
| `F2` | lista de proyectos/agentes |
| `Ctrl-b p` / `Ctrl-b a` | selector de proyectos / de agentes |

## Briefs: se pegan, no se lanzan

Si existe `<proyecto>/.px/briefs/<agente>.md`, al abrir la pestaña px espera a
que la TUI esté lista y **deja el brief pegado en el prompt sin enviarlo**: tú
lo lees, lo ajustas y pulsas Enter.

Es deliberado. En la primera versión el brief se enviaba solo, y abrir dos
pestañas lanzó a dos agentes a trabajar sin supervisión (uno de ellos, el
contable, sobre contabilidad real). Abrir una pestaña no puede significar eso.
Con `px open -g` sí se envía, cuando tú lo decides.

Los briefs de `coder`, `contable` y `marca` salieron de `AGENTES.md`. Son
ficheros vivos: mantenlos al día, porque un brief viejo manda a un agente a
hacer trabajo ya hecho.

## Cómo sabe el estado de cada agente

Leyendo el pane con `tmux capture-pane` y buscando los marcadores reales de la
TUI de Claude Code (verificados contra `claude 2.1.251` el 2026-08-30):

| Marcador en el pie | Estado |
|--------------------|--------|
| `esc to interrupt` | trabajando |
| `Esc to cancel` | esperando respuesta (diálogo de confianza, menú, permiso) |
| ninguno de los dos | prompt libre |
| `pane_current_command` ≠ claude | no hay agente en esa pestaña |

Un demonio ligero (`px daemon`, lo arranca `px open` solo) refresca eso cada 2 s
y lo guarda en opciones de usuario de tmux (`@px_state`), para que pintar la
barra no cueste nada. Las señales caras (git, beads) van en un bucle de 60 s.

## Si se va la luz

tmux **no** sobrevive a un reinicio: el servidor muere con la máquina. Lo que sí
sobrevive es el transcript de cada conversación en `~/.claude/projects/`. La
garantía se apoya en eso:

```bash
px sessions          # qué hay vivo y qué se restauraría
px restore           # enseña el plan, no toca nada
px restore -y        # recrea las sesiones con `claude --continue`
```

- **Registro**: un demonio (`launchd`, `com.gibtelligence.px-daemon`) escribe cada
  15 s `~/.local/state/px/state.json` — proyecto, agente, cwd, sesión de tmux y
  el id del transcript. Escritura atómica con `fsync`, para que un corte a media
  escritura no lo deje a medias.
- **Cola de pantalla**: guarda además las últimas 60 líneas de cada panel en
  `~/.local/state/px/panes/`. Tras un apagón puedes ver qué estaba haciendo cada
  agente en ese momento.
- **`px restore`** distingue lo que sigue vivo de lo que se perdió, no duplica
  nada, y salta las carpetas que ya no existen.

Dos cosas que condicionan el diseño, y que se descubrieron probando:

1. **El demonio no puede vivir en el NAS.** Bajo `launchd`, macOS deniega el
   acceso a `/Volumes` (`Operation not permitted`), y además el NAS puede no
   estar montado justo después de arrancar — que es cuando hace falta. Por eso
   `install.sh` copia `px.py` a `~/.local/libexec/px/` (disco local) y el
   servicio usa esa copia.
2. **El registro no consulta el NAS.** El proyecto y el agente se deducen del
   nombre de la sesión (`pxa-<proyecto>-<agente>`) y el cwd es la cadena que da
   tmux. Nada de `stat` sobre disco de red.

## Entornos de trabajo (empresa vs personal)

Un conmutador global decide qué se ve. Vive en `px`, no en la app, así que la
CLI y la GUI siempre coinciden.

```bash
px ws                  # entornos, cuál está activo y qué queda sin asignar
px ws personal         # cambiar
px ws all              # sin filtro
```

El reparto está en `~/.config/px/workspaces.conf`:

```
gibtelligence  color=#7C5CFF  px gibtelligence eez fluge sanitas sarastudio ftth
personal       color=#54C07A  px tfg piano bitloom eso homelab logi
```

Tres decisiones que importan:

1. **Lo no asignado se ve en todos los entornos.** Un proyecto se oculta solo si
   está asignado a algún entorno y no a éste. Así dar de alta un proyecto nuevo
   nunca lo hace desaparecer sin que te enteres: el fallo es hacia mostrar de
   más, no hacia esconder.
2. **Filtrar no es perder la señal.** Si en el otro entorno hay agentes vivos,
   `px ls` y la barra lateral lo dicen (`fuera del entorno: ◐ 1`). Que un agente
   te esté esperando no puede depender de en qué pestaña mental estés.
3. **El color del entorno tiñe el título** de la app. Saber si estás en trabajo
   de empresa o personal tiene que ser preatento, no algo que se lee.

Cambiar de entorno **no toca las sesiones**: los agentes del otro entorno siguen
vivos en tmux, simplemente no se pintan. `px restore` tampoco filtra — la
recuperación tras un corte es global a propósito.

## Un solo agente por directorio

Dos agentes en la misma carpeta se pisan los ficheros. `px attach` — que es lo
que ejecuta cada pestaña de la app — se niega a arrancar un segundo:

```
px: YA HAY UN AGENTE en 08_ODOO_CRM/accountant
    sesion tmux 'intruso' (claude.exe)
    No arranco un segundo: dos agentes en la misma carpeta se
    pisan los ficheros. Engancha a esa, o cierrala primero:
        tmux attach -t intruso
```

Detecta también sesiones abiertas **fuera de px** (a mano, o desde otra app),
porque compara el `cwd` real de los paneles de tmux, no los nombres. Nota: no
puede usar `lsof` sobre el proceso, porque en el NAS devuelve *Stale NFS file
handle*.

## Instalación

```bash
bash /Volumes/PERSONAL/Proyectos/_px/install.sh
```

Detecta la máquina: en el **Studio** (donde están `claude` y tmux) instala px
entero; en el **MacBook** instala el reenviador por ssh, igual que hace `gib`.
El trabajo vive siempre en el Studio, así que las sesiones sobreviven a cerrar
el portátil.

## Relación con lo que ya había

- **`gib` sigue funcionando igual**, sin tocarlo. px es su generalización a
  multi-proyecto; cuando te fíes de px, `gib` puede jubilarse.
- **`claude-swarm` es otra cosa y sigue siendo útil**: worktrees aislados para
  atacar en paralelo un mismo repo. px es lo contrario — un checkout, varios
  agentes con ámbitos distintos, coordinados por documentos (el modelo de
  `AGENTES.md`).

## Migración a `Proyectos/` — hecha (2026-08-30)

Los cinco proyectos viven ya en `/Volumes/PERSONAL/Proyectos/` en minúsculas
(`gibtelligence`, `fluge`, `eez`, `tfg`, `piano`), así que `projects.conf` está
vacío y px los descubre solos. En la raíz del NAS quedan **symlinks puente**
(`GIBTELLIGENCE -> Proyectos/gibtelligence`, etc.) para no romper las sesiones
de cmux que estaban abiertas sobre las rutas viejas. Cuando las reinicies:

```bash
cd /Volumes/PERSONAL && rm GIBTELLIGENCE FLUGE EEZ TFG Piano
```

Copias de seguridad previas en `_px/.migracion-backup/`.

## Pendiente

- Que la fila de proyectos se pueda clicar (hoy la navegación es por teclado).
