#!/usr/bin/env python3
"""px — terminal de proyectos y agentes de Claude Code, sobre tmux.

Un proyecto = una carpeta bajo PX_ROOT (o registrada en projects.conf).
Un agente   = una subcarpeta con CLAUDE.md (o listada en el .px.conf del proyecto).
Una pestana = una ventana de tmux corriendo `claude` en el cwd del agente.

El estado de cada agente (trabajando / espera input / listo) se deduce
leyendo el pane con `tmux capture-pane` y buscando los marcadores reales de
la TUI de Claude Code (verificados 2026-08-30 contra claude 2.1.251):
  - "esc to interrupt"  en el pie  -> esta trabajando
  - "Esc to cancel"                -> hay un dialogo/menu esperando respuesta
  - ninguno de los dos             -> prompt libre
Un demonio ligero refresca esos estados en opciones de usuario de tmux
(@px_state), para que la barra de estado sea gratis de pintar.
"""

import json
import os
import re
import subprocess
import sys
import time

VERSION = "0.2.0"   # fuente unica: build.sh la inyecta en la app

HOME = os.path.expanduser("~")
ROOT = os.environ.get("PX_ROOT", "/Volumes/PERSONAL/Proyectos")
CONF_DIR = os.environ.get("PX_CONF_DIR", os.path.join(HOME, ".config", "px"))
PROJECTS_CONF = os.path.join(CONF_DIR, "projects.conf")
WS_CONF = os.path.join(CONF_DIR, "workspaces.conf")
ORDER_CONF = os.path.join(CONF_DIR, "order.conf")
COLORS_CONF = os.path.join(CONF_DIR, "colors.conf")
CACHE_DIR = os.path.join(HOME, ".cache", "px")
SESS_PREFIX = "px-"        # sesion del TUI: px-<proyecto>, una ventana por agente
AGENT_SESS_PREFIX = "pxa-" # sesion por agente: pxa-<proyecto>-<agente> (app / px attach)
CLAUDE_BIN = os.environ.get("PX_CLAUDE", "claude")

MAX_DEPTH = 3
SKIP = {".git", ".beads", "node_modules", ".venv", "venv", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".px", "#recycle", ".Trash",
        "@eaDir"}   # @eaDir: metadatos de Synology, aparecia como "proyecto"

# --- marcadores de la TUI de Claude Code (verificados en vivo) -------------
MARK_WAIT = "esc to cancel"       # dialogo de confianza, menus, permisos
MARK_WORK = "esc to interrupt"    # tarea en curso
CLAUDE_CMDS = {"claude", "claude.exe", "node", "bun"}

# estado -> (glifo, etiqueta, color tmux, color ansi)
STATES = {
    "work": ("●", "trabajando",   "#7c5cff", "\033[35m"),
    "wait": ("◐", "ESPERA INPUT", "#ffb020", "\033[33m"),
    "idle": ("○", "listo",        "#8a8a96", "\033[36m"),
    "off":  ("·", "sin agente",   "#4a4a55", "\033[2m"),
}

VIOLET, INK, DIM, FG = "#7c5cff", "#0b0b0f", "#4a4a55", "#c9c9d2"

# Paleta de colores de proyecto. El orden esta entrelazado a proposito: cuando
# dos proyectos caen en el mismo hueco, el sondeo avanza al siguiente, y asi
# el vecino es un tono claramente distinto, no el de al lado en la rueda.
# La app lleva una copia (Theme.projectColor) como respaldo sin `px json`.
PROJECT_PALETTE = [
    "#7C5CFF",  # violeta
    "#54C07A",  # verde
    "#FFB020",  # ambar
    "#4FC7E8",  # celeste
    "#E0607E",  # rosa
    "#A6CC3C",  # lima
    "#B06CE8",  # orquidea
    "#2EC4B6",  # turquesa
    "#FF8A4C",  # naranja
    "#5E8DFF",  # azul
    "#E5C838",  # amarillo
    "#E060C8",  # magenta
    "#8A93A8",  # acero
    "#C08552",  # caramelo
]
C = {"b": "\033[1m", "d": "\033[2m", "v": "\033[38;5;99m", "g": "\033[32m",
     "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------
def run(args, timeout=20, cwd=None):
    """Ejecuta y devuelve stdout (o '' si falla). Nunca lanza."""
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
        return p.stdout
    except Exception:
        return ""


def ok(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout).returncode == 0
    except Exception:
        return False


def die(msg, code=1):
    sys.stderr.write("px: %s\n" % msg)
    raise SystemExit(code)


def slug(name):
    """08_ODOO_CRM -> odoo_crm ; brand-identity -> brand-identity"""
    s = re.sub(r"^\d+[\s_\-.]+", "", name).strip()
    s = s.replace(" ", "-").lower()
    return s or name.lower()


# --------------------------------------------------------------------------
# descubrimiento: proyectos y agentes
# --------------------------------------------------------------------------
class Agent(object):
    def __init__(self, name, project, relpath, path):
        self.name = name
        self.project = project
        self.relpath = relpath
        self.path = path

    @property
    def brief(self):
        p = os.path.join(self.project.path, ".px", "briefs", self.name + ".md")
        return p if os.path.isfile(p) else None

    @property
    def spec(self):
        return "%s/%s" % (self.project.name, self.name)


class Project(object):
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self._agents = None

    @property
    def session(self):
        return SESS_PREFIX + self.name

    @property
    def agents(self):
        if self._agents is None:
            self._agents = discover_agents(self)
        return self._agents

    def agent(self, name):
        for a in self.agents:
            if a.name == name:
                return a
        return None

    @property
    def color_override(self):
        """Linea `color=#hex` en el .px.conf (misma sintaxis que workspaces.conf)."""
        for k, v in read_conf(os.path.join(self.path, ".px.conf")):
            if k.startswith("color="):
                c = k[6:] or v
                if c:
                    return c
        return None


def read_conf(path):
    """Lee un fichero '<clave> <valor>' con # de comentario."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                rows.append((parts[0], parts[1].strip() if len(parts) > 1 else ""))
    except IOError:
        pass
    return rows


# --------------------------------------------------------------------------
# entornos de trabajo (empresa vs personal)
# --------------------------------------------------------------------------
def workspaces():
    """[(nombre, color, [proyectos])] en el orden del fichero."""
    out = []
    try:
        with open(WS_CONF, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                name, color, projs = parts[0], None, []
                for tok in parts[1:]:
                    if tok.startswith("color="):
                        color = tok[6:]
                    else:
                        projs.append(tok)
                out.append((name, color or VIOLET, projs))
    except IOError:
        pass
    return out


def ws_state_file():
    return os.path.join(STATE_DIR, "workspace")


def current_ws():
    """Entorno activo. 'all' = sin filtro."""
    env = os.environ.get("PX_WS")
    if env:
        return env
    try:
        with open(ws_state_file(), encoding="utf-8") as fh:
            v = fh.read().strip()
            return v or "all"
    except IOError:
        return "all"


def ws_assigned():
    """Proyectos que aparecen en ALGUN entorno."""
    out = set()
    for _n, _c, projs in workspaces():
        out.update(projs)
    return out


def ws_projects(name=None):
    """Proyectos visibles en un entorno, o None si no filtra.

    REGLA (importante): un proyecto se oculta solo si esta asignado a algun
    entorno y no a este. Lo que no esta asignado se ve SIEMPRE. Asi dar de alta
    un proyecto nuevo nunca lo hace desaparecer sin avisar — el fallo es
    hacia mostrar de mas, no hacia esconder.
    """
    name = name or current_ws()
    if name == "all":
        return None
    for n, _c, projs in workspaces():
        if n == name:
            return set(projs) | ws_unassigned()
    return None


def ws_unassigned():
    asig = ws_assigned()
    return {p.name for p in projects() if p.name not in asig}


def visible(ps, name=None):
    allow = ws_projects(name)
    return ps if allow is None else [p for p in ps if p.name in allow]


def projects():
    """Proyectos registrados a mano + los que cuelgan de PX_ROOT."""
    out, seen = [], set()
    for name, path in read_conf(PROJECTS_CONF):
        path = os.path.expanduser(path)
        if os.path.isdir(path) and name not in seen:
            seen.add(name)
            out.append(Project(name, os.path.realpath(path)))
    # el propio utillaje es un proyecto mas: ahi vive el agente maestro
    tool = os.path.join(ROOT, "_px")
    if "px" not in seen and os.path.isfile(os.path.join(tool, ".px.conf")):
        seen.add("px")
        out.append(Project("px", os.path.realpath(tool)))
    if os.path.isdir(ROOT):
        for entry in sorted(os.listdir(ROOT)):
            if entry.startswith((".", "_", "#", "@")) or entry in SKIP:
                continue
            path = os.path.join(ROOT, entry)
            if not os.path.isdir(path):
                continue
            name = slug(entry)
            if name in seen:
                continue
            seen.add(name)
            out.append(Project(name, os.path.realpath(path)))
    return apply_order(out)


def project_colors(ps):
    """{proyecto: '#hex'}: overrides, memoria persistida, y recien asignados.

    La asignacion es PEGAJOSA. Sondear sobre la paleta entera cada vez parecia
    estable, pero con la paleta llena un alta rebarajaba a TODOS (parte del
    maestro, 2026-09-02: al aparecer el proyecto 15 cambiaron los 14 tonos y
    encima aparecio un duplicado). Por eso lo ya asignado se recuerda en
    colors.conf y un proyecto nuevo solo sondea colores libres: dar de alta
    jamas recolorea a los existentes.

    Prioridad: `color=#hex` del .px.conf del proyecto > colors.conf > hueco
    libre de la paleta (hash djb2 + sondeo). Agotada la paleta se repite tono
    — y `px ls` lo avisa sugiriendo el override, en vez de duplicar en
    silencio. Las entradas de proyectos que ya no existen se conservan (si el
    proyecto vuelve, recupera su tono) pero no bloquean colores.

    OJO consumidores: calcularlo sobre TODOS los proyectos, no los visibles —
    si dependiera del entorno activo, cambiar de entorno recolorearia.
    """
    names = {p.name for p in ps}
    out, taken = {}, set()
    for p in ps:
        c = p.color_override
        if c:
            out[p.name] = c
            taken.add(c.lower())
    saved = dict(read_conf(COLORS_CONF))
    for name, c in saved.items():
        if name in names and name not in out and c:
            out[name] = c
            taken.add(c.lower())
    n = len(PROJECT_PALETTE)
    fresh = {}
    for name in sorted(names - set(out)):
        h = 5381
        for b in name.encode("utf-8"):
            h = (h * 33 + b) & 0xFFFFFFFF   # djb2, el mismo de Theme.swift
        color = PROJECT_PALETTE[h % n]
        for k in range(n):
            c = PROJECT_PALETTE[(h + k) % n]
            if c.lower() not in taken:
                color = c
                break
        out[name] = fresh[name] = color
        taken.add(color.lower())
    if fresh:
        saved.update(fresh)
        lines = ["# Memoria de colores de proyecto: la apunta px al asignar,",
                 "# para que un alta nueva no recoloree a los existentes.",
                 "# Editable; el `color=#hex` del .px.conf de cada proyecto",
                 "# manda sobre esto."]
        lines += ["%-16s %s" % (k, v) for k, v in sorted(saved.items())]
        atomic_write(COLORS_CONF, "\n".join(lines) + "\n")
    return out


def ansi_rgb(hexcolor):
    """'#RRGGBB' -> secuencia truecolor de primer plano ('' si no es valido)."""
    try:
        v = int(str(hexcolor).lstrip("#"), 16)
    except (ValueError, AttributeError):
        return ""
    return "\033[38;2;%d;%d;%dm" % (v >> 16 & 255, v >> 8 & 255, v & 255)


def read_order():
    """Orden manual de proyectos (uno por linea). Lo escribe la app al
    arrastrar; vive en la config y no en la app para que `px ls` y la barra
    lateral no se contradigan."""
    try:
        with open(ORDER_CONF, encoding="utf-8") as fh:
            return [l.strip() for l in fh
                    if l.strip() and not l.startswith("#")]
    except IOError:
        return []


def apply_order(ps):
    """Ordena por la lista manual; lo no listado va detras, en su orden."""
    order = read_order()
    if not order:
        return ps
    idx = {n: i for i, n in enumerate(order)}
    return sorted(ps, key=lambda p: (idx.get(p.name, len(idx)),))


def cmd_order(argv):
    """px order <proyecto> [proyecto...]  — fija el orden. Sin argumentos, lo muestra."""
    if not argv:
        cur = read_order()
        print("\n  %sorden actual%s  %s" % (C["d"], C["x"],
              " ".join(cur) if cur else "(alfabetico)"))
        print("  %s%s%s\n" % (C["d"], ORDER_CONF, C["x"]))
        return 0
    todos = [p.name for p in projects()]          # ya en el orden vigente
    desconocidos = [a for a in argv if a not in set(todos)]
    if desconocidos:
        die("proyecto(s) que no existen: %s" % ", ".join(desconocidos))

    # FUSION, no reemplazo: la app solo ve los proyectos del entorno activo,
    # asi que reescribir la lista entera con lo que ella ve borraria el orden
    # de los demas. Los nombres recibidos ocupan las MISMAS posiciones que ya
    # ocupaban entre ellos, en el nuevo orden relativo; el resto no se mueve.
    nuevo = list(todos)
    dados = set(argv)
    huecos = [i for i, n in enumerate(nuevo) if n in dados]
    for hueco, nombre in zip(huecos, argv):
        nuevo[hueco] = nombre

    atomic_write(ORDER_CONF,
                 "# Orden de los proyectos. Lo escribe la app al arrastrar;\n"
                 "# editable a mano. Lo que no aparezca va detras.\n"
                 + "\n".join(nuevo) + "\n")
    print("  %sorden%s  %s" % (C["g"], C["x"], " ".join(nuevo)))
    return 0


def project(name):
    for p in projects():
        if p.name == name:
            return p
    return None


def discover_agents(proj):
    """.px.conf manda; si no existe, se descubre por CLAUDE.md."""
    conf = os.path.join(proj.path, ".px.conf")
    # `color=` es del proyecto, no un agente. Se filtra ANTES de decidir si el
    # conf "tiene filas": un .px.conf con solo el color no debe anular el
    # descubrimiento por CLAUDE.md.
    rows = [r for r in read_conf(conf) if not r[0].startswith("color=")]
    if rows:
        out = []
        for name, rel in rows:
            rel = rel or "."
            path = proj.path if rel == "." else os.path.join(proj.path, rel)
            if os.path.isdir(path):
                out.append(Agent(name, proj, rel, path))
        return out
    return scan_agents(proj)


def scan_agents(proj):
    """Cualquier carpeta con CLAUDE.md hasta MAX_DEPTH = un agente."""
    found = []
    root = proj.path.rstrip("/")
    base_depth = root.count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.count(os.sep) - base_depth
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
        if "CLAUDE.md" in filenames:
            rel = os.path.relpath(dirpath, root)
            found.append(rel)
    out, used = [], set()
    for rel in sorted(found, key=lambda r: (r != ".", r)):
        if rel == ".":
            name = "raiz"
        else:
            name = slug(os.path.basename(rel))
            if name in used:  # colision: cualifica con la carpeta padre
                parent = slug(os.path.basename(os.path.dirname(rel)))
                name = "%s-%s" % (parent, name)
        i, base = 2, name
        while name in used:
            name = "%s%d" % (base, i)
            i += 1
        used.add(name)
        path = root if rel == "." else os.path.join(root, rel)
        out.append(Agent(name, proj, rel, path))
    return out


def resolve(spec):
    """'coder' | 'gibtelligence/coder' | 'gibtelligence' -> Agent"""
    if "/" in spec:
        pname, aname = spec.split("/", 1)
        p = project(pname)
        if not p:
            die("proyecto desconocido: '%s'" % pname)
        a = p.agent(aname)
        if not a:
            die("'%s' no tiene agente '%s' (prueba: px ls %s)" % (pname, aname, pname))
        return a
    hits = []
    for p in projects():
        if p.name == spec:
            if p.agents:
                return p.agents[0]
            die("el proyecto '%s' no tiene agentes todavia: crea un CLAUDE.md "
                "en la carpeta que quieras abrir (o un .px.conf) y repite" % spec)
        a = p.agent(spec)
        if a:
            hits.append(a)
    if not hits:
        die("agente desconocido: '%s' (prueba: px ls)" % spec)
    if len(hits) > 1:
        die("'%s' esta en varios proyectos: %s" % (spec, ", ".join(h.spec for h in hits)))
    return hits[0]


# --------------------------------------------------------------------------
# tmux
# --------------------------------------------------------------------------
def tmux(*args, **kw):
    return run(("tmux",) + args, timeout=kw.get("timeout", 20))


def tmux_ok(*args):
    return ok(("tmux",) + args)


def has_server():
    return tmux_ok("has-session")


def sessions():
    out = tmux("list-sessions", "-F", "#{session_name}")
    return [s for s in out.split() if s.startswith(SESS_PREFIX)]


def windows(agent_sessions=False):
    """[(sess, idx, name, cmd, path, activity)] de todas las sesiones px-.

    Con `agent_sessions` entran tambien las sesiones por-agente `pxa-*`
    (las que crean la app nativa y `px attach`); por defecto quedan fuera
    para no tocar a los consumidores del modelo del TUI (demonio,
    window_exists)."""
    fmt = "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_current_command}\t#{pane_current_path}\t#{window_activity}"
    rows = []
    for line in tmux("list-windows", "-a", "-F", fmt).splitlines():
        f = line.split("\t")
        if len(f) != 6:
            continue
        if f[0].startswith(SESS_PREFIX) or \
                (agent_sessions and f[0].startswith(AGENT_SESS_PREFIX)):
            rows.append(f)
    return rows


def classify_target(target, cmd):
    """Estado real del agente leyendo ese panel de tmux."""
    if cmd not in CLAUDE_CMDS:
        return "off"
    txt = tmux("capture-pane", "-p", "-t", target, "-S", "-14")
    low = txt.lower()
    if MARK_WAIT in low:
        return "wait"
    if MARK_WORK in low:
        return "work"
    return "idle"


def classify(sess, idx, cmd):
    return classify_target("%s:%s" % (sess, idx), cmd)


def tmux_set_window_opt(target, key, value):
    tmux("set-option", "-w", "-t", target, key, value)


def tmux_get_window_opt(target, key):
    return tmux("show-options", "-w", "-v", "-t", target, key).strip()


# --------------------------------------------------------------------------
# barra de estado
# --------------------------------------------------------------------------
# OJO (verificado 2026-08-30 contra tmux 3.7c): en los bucles #{S:...} y
# #{W:actual,resto}, tmux parte por la PRIMERA coma de nivel 0. Una coma dentro
# de #[fg=x,bold] rompe el formato (salia literalmente "○ coder , ○ coder").
# Regla: en estas cadenas, cada atributo de estilo va en su propio #[...] y las
# unicas comas de nivel 0 son las que separan ramas.
_NAME = "#{s/^" + SESS_PREFIX + "//:session_name}"
_IS_PX = "#{m:" + SESS_PREFIX + "*,#{session_name}}"
_IS_CUR = "#{==:#{session_name},#{client_session}}"
_PROJ_CUR = "#[fg=" + VIOLET + "]#[bold]▌" + _NAME + "#[nobold]#[fg=" + DIM + "]"
_PROJ_OTH = "#[fg=" + DIM + "] " + _NAME

#   S{ ?( es px , ?( es la actual , RESALTADA , NORMAL ) + separacion , nada ) }
PROJ_ROW = (
    "#[align=left]#[bg=" + INK + "]#[fg=" + DIM + "] "
    "#{S:"
      "#{?" + _IS_PX + ","
        "#{?" + _IS_CUR + "," + _PROJ_CUR + "," + _PROJ_OTH + "}   "
      ",}"
    "}"
    "#[align=right]#[fg=" + DIM + "]#{host_short} · px "
)

#   W{ VENTANA-ACTUAL , RESTO }
AGENT_ROW = (
    "#[align=left]#[bg=" + INK + "]"
    "#{W:"
      "#[fg=" + VIOLET + "]#[bold] #{@px_state} #{window_name} #[nobold]"
      ","
      "#[fg=" + FG + "] #{@px_state} #{window_name} "
    "}"
    "#[align=right]#[fg=" + DIM + "]#{@px_sig} "
)


def apply_theme(sess):
    """Estilo + atajos de la sesion (idempotente)."""
    tmux("set-option", "-g", "status", "2")
    tmux("set-option", "-g", "status-interval", "5")
    tmux("set-option", "-g", "mouse", "on")
    tmux("set-option", "-g", "base-index", "1")
    tmux("set-option", "-g", "renumber-windows", "on")
    tmux("set-option", "-g", "history-limit", "50000")
    tmux("set-option", "-g", "status-style", "bg=%s,fg=%s" % (INK, FG))
    tmux("set-option", "-g", "status-format[0]", PROJ_ROW)
    tmux("set-option", "-g", "status-format[1]", AGENT_ROW)
    tmux("set-option", "-g", "status-left", "")
    tmux("set-option", "-g", "status-right", "")
    tmux("set-option", "-g", "pane-active-border-style", "fg=%s" % VIOLET)
    tmux("set-option", "-g", "pane-border-style", "fg=%s" % DIM)
    tmux("set-option", "-g", "allow-rename", "off")
    # navegacion: agentes = M-flecha lateral, proyectos = M-flecha vertical
    tmux("bind-key", "-n", "M-Left", "previous-window")
    tmux("bind-key", "-n", "M-Right", "next-window")
    tmux("bind-key", "-n", "M-Up", "switch-client", "-p")
    tmux("bind-key", "-n", "M-Down", "switch-client", "-n")
    for i in range(1, 10):
        tmux("bind-key", "-n", "M-%d" % i, "select-window", "-t", ":%d" % i)
    px = os.environ.get("PX_BIN", "px")
    tmux("bind-key", "-n", "F1", "display-popup", "-E", "-w", "85%", "-h", "80%",
         "%s brief" % px)
    tmux("bind-key", "-n", "F2", "display-popup", "-E", "-w", "70%", "-h", "70%",
         "%s ls" % px)
    tmux("bind-key", "p", "choose-tree", "-Zs")
    tmux("bind-key", "a", "choose-tree", "-Zw")


# --------------------------------------------------------------------------
# demonio de estado
# --------------------------------------------------------------------------
def daemon(once=False, interval=2.0):
    """Refresca glifos de estado (barra del TUI) y el registro anticorte.

    OJO: el registro NO puede depender de que haya ventanas del TUI. La app
    nativa usa sesiones `pxa-*`, que no llevan el prefijo del TUI; si se sale
    del bucle antes de guardar, el registro se queda viejo justo cuando mas
    falta hace. Por eso el guardado va SIEMPRE, haya o no ventanas.
    """
    last = {}
    sig_at, sigs, state_at = 0.0, {}, 0.0
    while True:
        rows = windows() if has_server() else []

        # glifos de estado para la barra del TUI (solo sesiones px-)
        for sess, idx, name, cmd, path, act in rows:
            target = "%s:%s" % (sess, idx)
            glyph = STATES[classify(sess, idx, cmd)][0]
            if last.get(target) != glyph:
                tmux_set_window_opt(target, "@px_state", glyph)
                last[target] = glyph

        now = time.time()
        if now - state_at > 15 or once:
            state_at = now
            try:
                save_state()
            except Exception:
                pass
        if rows and (now - sig_at > 60):
            sig_at = now
            for p in projects():
                if p.session in [r[0] for r in rows]:
                    txt = project_signal(p)
                    if sigs.get(p.session) != txt:
                        tmux("set-option", "-t", p.session, "@px_sig", txt)
                        sigs[p.session] = txt
        if once:
            return
        time.sleep(interval if has_server() else 5.0)


def ensure_daemon():
    """Arranca el demonio si no corre ya (lo cuelga de la propia sesion tmux)."""
    if tmux("show-options", "-g", "-v", "@px_daemon").strip() == "1":
        pid = tmux("show-options", "-g", "-v", "@px_daemon_pid").strip()
        if pid and os.path.isdir("/proc") is False:
            # macOS: comprobamos con kill -0
            if ok(["kill", "-0", pid]):
                return
        elif pid:
            return
    me = os.path.abspath(__file__)
    try:
        p = subprocess.Popen([sys.executable, me, "daemon"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        tmux("set-option", "-g", "@px_daemon", "1")
        tmux("set-option", "-g", "@px_daemon_pid", str(p.pid))
    except Exception:
        pass


# --------------------------------------------------------------------------
# senales del proyecto (git / beads) — caras, van en bucle lento
# --------------------------------------------------------------------------
def git_dirty(path):
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    out = run(["git", "-C", path, "status", "--porcelain"], timeout=25)
    return len([l for l in out.splitlines() if l.strip()])


def beads_ready(path):
    jsonl = os.path.join(path, ".beads", "issues.jsonl")
    if not os.path.isfile(jsonl):
        return None
    try:
        n = 0
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if o.get("status") in ("open", "in_progress"):
                    n += 1
        return n
    except Exception:
        return None


def project_signal(p):
    bits, roots, total = [], [], 0
    for a in p.agents:
        r = git_root(a.path)
        if r and r not in roots:
            roots.append(r)
            total += git_dirty(r) or 0
    if total:
        bits.append("● %d sin commitear" % total)
    n = beads_ready(p.path)
    if n is None:
        for a in p.agents:
            n = beads_ready(a.path)
            if n is not None:
                break
    if n:
        bits.append("%d issues" % n)
    return "  ".join(bits)


# --------------------------------------------------------------------------
# comandos
# --------------------------------------------------------------------------
def live_states():
    """{clave: (estado, edad_en_seg)} de lo que hay abierto ahora.

    Dos claves segun el front-end: ('px-<proyecto>', ventana) para el TUI y
    el nombre de sesion 'pxa-<proyecto>-<agente>' para la app y `px attach`.
    Busca con `agent_state`, que resuelve ambas."""
    out = {}
    now = time.time()
    for sess, idx, name, cmd, path, act in windows(agent_sessions=True):
        st = classify(sess, idx, cmd)
        try:
            age = max(0, now - int(act))
        except ValueError:
            age = 0
        key = sess if sess.startswith(AGENT_SESS_PREFIX) else (sess, name)
        if key in out and (st == "off" or out[key][0] != "off"):
            continue  # ya hay estado de otra ventana de esa sesion; mejor el vivo
        out[key] = (st, age)
    return out


def agent_state(live, project, agent):
    """(estado, edad) del agente venga del TUI o de la app; None si cerrado."""
    return (live.get((project.session, agent.name))
            or live.get(session_name_for(project.name, agent.name)))


def human_age(sec):
    if sec < 90:
        return "ahora"
    if sec < 5400:
        return "hace %dm" % (sec // 60)
    if sec < 172800:
        return "hace %dh" % (sec // 3600)
    return "hace %dd" % (sec // 86400)


def cmd_ls(argv):
    only = argv[0] if argv else None
    live = live_states() if has_server() else {}
    ps = visible(projects())
    if only:
        sel = [p for p in ps if p.name == only]
        if not sel:
            todos = {p.name for p in projects()}
            if only in todos:
                die("'%s' existe, pero esta fuera del entorno '%s'\n"
                    "    px ws all            para verlo todo\n"
                    "    px ws <entorno>      para cambiar de entorno"
                    % (only, current_ws()))
            die("proyecto desconocido: %s" % only)
        ps = sel
    colors = project_colors(projects())   # todos, no los visibles: estabilidad
    print("")
    for p in ps:
        agents = p.agents
        openn = sum(1 for a in agents if agent_state(live, p, a))
        print("%s%s%s%s %s(%s)%s %s%d agentes%s%s" % (
            C["b"], ansi_rgb(colors.get(p.name)), p.name, C["x"], C["d"], p.path, C["x"],
            C["d"], len(agents), C["x"],
            "  %s%d abiertos%s" % (C["v"], openn, C["x"]) if openn else ""))
        for a in agents:
            hit = agent_state(live, p, a)
            if hit:
                st, age = hit
                g, label, _, ansi = STATES[st]
                extra = "" if st == "work" else "  %s%s%s" % (C["d"], human_age(age), C["x"])
                print("  %s%s%s %-16s %s%-12s%s%s   %s%s%s" % (
                    ansi, g, C["x"], a.name, ansi, label, C["x"], extra,
                    C["d"], a.relpath, C["x"]))
            else:
                brief = " · brief" if a.brief else ""
                print("  %s·%s %-16s %s%-12s%s      %s%s%s%s" % (
                    C["d"], C["x"], a.name, C["d"], "cerrado", C["x"],
                    C["d"], a.relpath, brief, C["x"]))
        print("")
    if not live:
        print("  %sNada abierto. Abre con: px open <agente>%s\n" % (C["d"], C["x"]))
    o = others_summary()
    if o:
        bits = ["%s%s %d %s%s" % (STATES[k][3], STATES[k][0], v, STATES[k][1], C["x"])
                for k, v in o.items()]
        print("  %sfuera del entorno '%s':%s %s\n"
              % (C["y"], current_ws(), C["x"], "  ".join(bits)))
    # paleta agotada: duplicar en silencio seria esconder la perdida de
    # identidad por color — se avisa y se da la salida (el override)
    por_color = {}
    for nm, c in colors.items():
        por_color.setdefault(str(c).lower(), []).append(nm)
    reps = sorted(v for v in por_color.values() if len(v) > 1)
    if reps:
        print("  %scolores repetidos (paleta agotada):%s %s\n"
              "  %sfija uno a mano con `color=#hex` en el .px.conf del proyecto%s\n"
              % (C["y"], C["x"], "; ".join(" y ".join(sorted(g)) for g in reps),
                 C["d"], C["x"]))
    return 0


def window_exists(sess, name):
    for s, idx, n, cmd, path, act in windows():
        if s == sess and n == name:
            return idx
    return None


def cmd_open(argv):
    if not argv:
        return cmd_ls([])
    brief_mode, do_attach, send_brief = True, True, False
    specs = []
    for a in argv:
        if a in ("--no-brief", "-n"):
            brief_mode = False
        elif a in ("--detach", "-d"):
            do_attach = False
        elif a in ("--go", "-g"):
            send_brief = True
        else:
            specs.append(a)
    if not specs:
        return cmd_ls([])
    if not shutil_which("tmux"):
        die("tmux no esta instalado")
    if not shutil_which(CLAUDE_BIN):
        die("'%s' no esta en el PATH" % CLAUDE_BIN)

    last, nuevas, negadas = None, [], 0
    for spec in specs:
        ag = resolve(spec)
        sess = ag.project.session
        sess_exists = tmux_ok("has-session", "-t", "=" + sess)
        if sess_exists and window_exists(sess, ag.name) is not None:
            print("  %ssalta%s    %s ya estaba abierta" % (C["d"], C["x"], ag.spec))
            last = (sess, ag.name)
            continue
        # El mismo candado que px attach: un solo agente por directorio, venga
        # por la ruta que venga (la app crea sesiones pxa-*, esto ventanas px-*).
        other = occupant(ag.path)
        if other:
            print("  %sniego%s    %s: ya hay un agente en esa carpeta "
                  "(sesion '%s', %s)\n           %sengancha o cierra: "
                  "tmux attach -t %s%s"
                  % (C["y"], C["x"], ag.spec, other["session"], other["cmd"],
                     C["d"], other["session"], C["x"]))
            negadas += 1
            continue
        # El pin de `px adopt` manda tambien en esta ruta, no solo en attach
        # (parte del maestro, 2026-09-02: open ignoraba la adopcion y encima
        # pegaba el brief sobre la sesion virgen).
        pinned = take_pin(session_name(ag))
        if not sess_exists:
            tmux("new-session", "-d", "-s", sess, "-n", ag.name, "-c", ag.path)
            apply_theme(sess)
        else:
            tmux("new-window", "-t", "=" + sess, "-n", ag.name, "-c", ag.path)
        launch(sess, ag.name, resume=pinned)
        if pinned:
            # reanuda una conversacion: ya tiene contexto, nada de brief
            print("  %sreanuda%s  %s  %stranscript %s%s" % (
                C["g"], C["x"], ag.spec, C["d"], pinned[:8], C["x"]))
        else:
            nuevas.append(ag)
            print("  %sabierta%s  %s  %s%s%s" % (C["g"], C["x"], ag.spec, C["d"], ag.relpath, C["x"]))
        last = (sess, ag.name)

    # los briefs se pegan cuando la TUI esta lista, y NO se envian:
    # abrir una pestana no puede significar lanzar al agente a trabajar solo.
    if brief_mode:
        for ag in nuevas:
            if not ag.brief:
                continue
            tgt = "%s:%s" % (ag.project.session, ag.name)
            if wait_ready(tgt):
                if paste_brief(tgt, ag.brief, send=send_brief):
                    print("     %sbrief pegado%s en %s%s" % (
                        C["d"], C["x"], ag.name,
                        " y enviado" if send_brief else " (revisalo y pulsa Enter)"))
            else:
                print("     %sbrief NO pegado en %s: la TUI no llego al prompt%s"
                      % (C["y"], ag.name, C["x"]))
    ensure_daemon()
    daemon(once=True)
    if last and do_attach:
        attach(last[0], last[1])
    return 3 if negadas else 0


def launch(sess, wname, resume=None):
    """Arranca claude en la ventana. El brief se pega despues (ver paste_brief)."""
    line = CLAUDE_BIN + ((" --resume " + resume) if resume else "")
    tmux("send-keys", "-t", "=%s:%s" % (sess, wname), line, "C-m")


def wait_ready(target, timeout=180.0, estable=3, intervalo=0.5):
    """Espera a que la TUI de claude este en el prompt libre.

    OJO con dos cosas que costaron un rato:
    - El target es un panel, no "sesion + ventana con el nombre del agente".
      La app usa una sesion por agente y tmux nombra su ventana `claude.exe`,
      asi que buscar una ventana llamada como el agente no encontraba nada.
    - El plazo es largo a proposito: en una carpeta nueva, claude abre el
      dialogo de confianza y ahi se queda hasta que responde una PERSONA. Con
      un plazo corto se perdia el brief justo en el estreno de cada proyecto,
      que es cuando mas falta hace.
    - Se exige idle ESTABLE (`estable` lecturas seguidas) y no una sola: al
      aceptar la confianza la TUI se repinta, y un frame transitorio se
      clasificaba como "listo". El pegado aterrizaba en mitad del repintado y
      se perdia sin dejar rastro.
    """
    t0 = time.time()
    seguidas = 0
    while time.time() - t0 < timeout:
        cmd = tmux("display", "-p", "-t", target, "#{pane_current_command}").strip()
        if not cmd:
            return False                      # el panel ya no existe
        if classify_target(target, cmd) == "idle":
            seguidas += 1
            if seguidas >= estable:
                return True
        else:
            seguidas = 0
        time.sleep(intervalo)
    return False


def paste_brief(target, path, send=False, intentos=2):
    """Deja el brief PEGADO en el prompt, sin enviarlo (Miguel revisa y pulsa Enter).

    Va por buffer de tmux con pegado entre corchetes (-p): asi el texto
    multilinea entra como una pegada y la TUI no lo envia sola. Nada del texto
    pasa por send-keys ni por un shell, o sea que no hay problema de comillas.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
    except IOError:
        return False
    if not text:
        return False

    # Se COMPRUEBA que el brief aterrizo, no se supone: si la TUI estaba
    # repintandose, paste-buffer no da error y el texto se pierde. Devolver
    # exito sin mirar era mentir.
    sonda = "".join(text.split())[:30]

    for intento in range(intentos):
        if intento:
            tmux("send-keys", "-t", target, "C-u")   # limpia un pegado a medias
            time.sleep(0.8)
        tmux("set-buffer", "-b", "pxbrief", text)
        tmux("paste-buffer", "-b", "pxbrief", "-t", target, "-p", "-d")
        time.sleep(0.7)
        pane = "".join(tmux("capture-pane", "-p", "-t", target, "-S", "-25").split())
        if sonda and sonda in pane:
            if send:
                time.sleep(0.4)
                tmux("send-keys", "-t", target, "C-m")
            return True
    return False


def shutil_which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def attach(sess, wname=None):
    if wname:
        tmux("select-window", "-t", "=%s:%s" % (sess, wname))
    if os.environ.get("TMUX"):
        os.execvp("tmux", ["tmux", "switch-client", "-t", "=" + sess])
    os.execvp("tmux", ["tmux", "attach-session", "-t", "=" + sess])


def cmd_tui_attach(argv):
    ss = sessions()
    if not ss:
        die("no hay nada abierto (px open <agente>)")
    want = SESS_PREFIX + argv[0] if argv else None
    attach(want if want in ss else ss[0])


def cmd_close(argv):
    if not argv:
        die("dime que agente cerrar")
    for spec in argv:
        ag = resolve(spec)
        t = "=%s:%s" % (ag.project.session, ag.name)
        if tmux_ok("kill-window", "-t", t):
            print("  %scerrada%s   %s" % (C["y"], C["x"], ag.spec))
        else:
            print("  %ssalta%s     %s no estaba abierta" % (C["d"], C["x"], ag.spec))
    return 0


def cmd_scan(argv):
    """Escribe .px.conf con lo descubierto (revisable a mano)."""
    ps = [project(argv[0])] if argv else projects()
    for p in ps:
        if not p:
            die("proyecto desconocido")
        ags = scan_agents(p)
        if not ags:
            print("  %s: sin CLAUDE.md, nada que registrar" % p.name)
            continue
        dest = os.path.join(p.path, ".px.conf")
        lines = ["# Agentes de %s — <nombre>  <ruta relativa>" % p.name,
                 "# Generado por 'px scan'; editable a mano (el orden manda).", ""]
        for a in ags:
            lines.append("%-16s %s" % (a.name, a.relpath))
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("  %s%s%s  %d agentes -> %s" % (C["g"], p.name, C["x"], len(ags), dest))
    return 0


def cmd_brief(argv):
    since = argv[0] if argv else "24.hours.ago"
    live = live_states() if has_server() else {}
    print("\n%sBriefing%s %s· cambios desde %s%s" % (C["b"], C["x"], C["d"], since, C["x"]))
    for p in visible(projects()):
        agents = p.agents
        gitroots = []
        for a in agents:
            r = git_root(a.path)
            if r and r not in gitroots:
                gitroots.append(r)
        print("\n%s%s%s" % (C["b"], p.name.upper(), C["x"]))
        moved = False
        for r in gitroots:
            n = run(["git", "-C", r, "log", "--since=" + since, "--format=%h|%ar|%s"], 30)
            commits = [l for l in n.splitlines() if l]
            dirty = git_dirty(r) or 0
            label = os.path.basename(r)
            if commits or dirty:
                moved = True
                head = "  %s%-22s%s" % (C["c"], label, C["x"])
                bits = []
                if commits:
                    bits.append("%s%d commit(s)%s" % (C["g"], len(commits), C["x"]))
                if dirty:
                    bits.append("%s%d sin commitear%s" % (C["y"], dirty, C["x"]))
                print("%s %s" % (head, "  ".join(bits)))
                for l in commits[:3]:
                    h, when, subj = l.split("|", 2)
                    print("      %s%s · %s%s  %s" % (C["d"], h, when, C["x"], subj[:62]))
        for a in agents:
            hit = agent_state(live, p, a)
            if hit:
                st, age = hit
                g, lab, _, ansi = STATES[st]
                moved = True
                print("  %s%s %-14s %s%s  %s%s%s" % (ansi, g, a.name, lab, C["x"],
                                                     C["d"], human_age(age), C["x"]))
        n = beads_ready(p.path)
        if n is None:
            for a in agents:
                n = beads_ready(a.path)
                if n is not None:
                    break
        if n:
            print("  %s⏵%s %d issues vivos en beads" % (C["v"], C["x"], n))
            moved = True
        if not moved:
            print("  %ssin movimiento%s" % (C["d"], C["x"]))
    print("")
    return 0


def git_root(path):
    out = run(["git", "-C", path, "rev-parse", "--show-toplevel"], 20).strip()
    return out or None


def others_summary(name=None):
    """Agentes vivos FUERA del entorno activo.

    WHY: filtrar no puede significar perder la senal. Si en el otro entorno hay
    un agente esperandote, tienes que enterarte igual.
    """
    allow = ws_projects(name)
    if allow is None:
        return {}
    live = live_states() if has_server() else {}
    counts = {}
    for p in projects():
        if p.name in allow:
            continue
        for a in p.agents:
            got = agent_state(live, p, a)
            if got:
                st = got[0]
                counts[st] = counts.get(st, 0) + 1
    return counts


def cmd_ws(argv):
    """Entornos de trabajo: ver el activo, o cambiarlo."""
    wss = workspaces()
    if not wss:
        die("no hay entornos definidos: crea %s" % WS_CONF)
    cur = current_ws()
    if argv:
        want = argv[0]
        names = [n for n, _c, _p in wss]
        if want != "all" and want not in names:
            die("entorno desconocido: '%s' (hay: %s, all)" % (want, ", ".join(names)))
        atomic_write(ws_state_file(), want)
        cur = want
        print("  %sentorno%s  %s" % (C["g"], C["x"], want))

    print("")
    for n, _c, projs in wss:
        mark = "%s▌%s" % (C["v"], C["x"]) if n == cur else " "
        vis = [p for p in projects() if p.name in projs]
        ag = sum(len(p.agents) for p in vis)
        print("  %s %-14s %s%d proyectos · %d agentes%s   %s%s%s"
              % (mark, n, C["d"], len(vis), ag, C["x"],
                 C["d"], " ".join(projs), C["x"]))
    mark = "%s▌%s" % (C["v"], C["x"]) if cur == "all" else " "
    print("  %s %-14s %ssin filtro%s" % (mark, "all", C["d"], C["x"]))
    libres = sorted(ws_unassigned())
    if libres:
        print("\n  %ssin asignar (se ven en todos):%s %s"
              % (C["d"], C["x"], " ".join(libres)))

    o = others_summary()
    if o:
        bits = ["%s%s %d %s%s" % (STATES[k][3], STATES[k][0], v, STATES[k][1], C["x"])
                for k, v in o.items()]
        print("\n  %sfuera de '%s':%s %s" % (C["y"], cur, C["x"], "  ".join(bits)))
    print("")
    return 0


def cmd_json(argv):
    """Modelo completo en JSON — lo consume la app nativa (app/)."""
    live = live_states() if has_server() else {}
    cur = current_ws()
    out = {"root": ROOT, "session_prefix": SESS_PREFIX,
           "workspace": cur,
           "workspaces": [{"name": n, "color": c, "projects": pr}
                          for n, c, pr in workspaces()],
           "others": others_summary(),
           "projects": []}
    colors = project_colors(projects())   # todos, no los visibles: estabilidad
    for p in visible(projects()):
        pj = {"name": p.name, "path": p.path, "session": p.session,
              "color": colors.get(p.name), "agents": []}
        for a in p.agents:
            st, age = agent_state(live, p, a) or (None, None)
            pj["agents"].append({
                "name": a.name, "relpath": a.relpath, "path": a.path,
                "brief": bool(a.brief),
                "open": st is not None,
                "state": st or "closed",
                "age": age,
            })
        out["projects"].append(pj)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0



# --------------------------------------------------------------------------
# Salvaguardas ante corte de luz
# --------------------------------------------------------------------------
# tmux NO sobrevive a un reinicio: el servidor muere con la maquina. Lo que SI
# sobrevive es el transcript de cada conversacion en ~/.claude/projects/. Asi
# que la garantia es: (1) dejar por escrito y en disco local que agentes habia
# abiertos y en que carpeta, (2) poder reconstruirlos con `claude --continue`,
# y (3) impedir que arranquen dos agentes en el mismo directorio.
#
# El registro se deriva de "paneles de tmux cuyo cwd es una carpeta de agente",
# no de como se llame la sesion: asi vale igual para la app, para el TUI o para
# un tmux abierto a mano.

STATE_DIR = os.environ.get("PX_STATE_DIR", os.path.join(HOME, ".local", "state", "px"))
STATE_FILE = os.path.join(STATE_DIR, "state.json")
PANE_DIR = os.path.join(STATE_DIR, "panes")
CLAUDE_PROJECTS = os.path.join(HOME, ".claude", "projects")


def atomic_write(path, text):
    """Escritura que sobrevive a un corte: temporal + fsync + rename."""
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def mangle(path):
    """La regla de Claude Code para nombrar la carpeta de un proyecto."""
    return "".join(c if (c.isalnum() and ord(c) < 128) else "-" for c in path)


def transcript_for(cwd):
    """(uuid, mtime) de la conversacion mas reciente de ese directorio."""
    d = os.path.join(CLAUDE_PROJECTS, mangle(cwd))
    best, best_m = None, 0
    try:
        for fn in os.listdir(d):
            if not fn.endswith(".jsonl"):
                continue
            m = os.path.getmtime(os.path.join(d, fn))
            if m > best_m:
                best, best_m = fn[:-6], m
    except OSError:
        return (None, None)
    return (best, best_m)


def real(path):
    """realpath tolerante: en el NAS inaccesible no debe reventar."""
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def agent_index():
    """{ruta_real: (proyecto, agente)} de todos los agentes conocidos."""
    idx = {}
    try:
        for p in projects():
            for a in p.agents:
                idx[real(a.path)] = (p, a)
    except OSError:
        pass
    return idx


def live_agents(deep=None):
    """Agentes con un panel de tmux vivo, sea cual sea el front-end.

    IMPORTANTE (verificado 2026-08-31): el demonio corre bajo launchd, y ahi
    macOS NIEGA el acceso a /Volumes ("Operation not permitted"), ademas de que
    el NAS puede no estar montado aun tras un arranque. Por eso el registro se
    construye SIN tocar el disco de red: el proyecto y el agente salen del
    nombre de la sesion (`pxa-<proyecto>-<agente>`) y el cwd es la cadena que
    da tmux. Solo si `deep` (uso interactivo) se consulta el NAS, para pillar
    tambien sesiones ajenas abiertas a mano.
    """
    if not has_server():
        return []
    if deep is None:
        deep = os.path.isdir(ROOT)
    idx = agent_index() if deep else {}
    fmt = ("#{session_name}\t#{window_index}\t#{pane_current_path}"
           "\t#{pane_current_command}\t#{pane_pid}")
    out = []
    seen = set()
    for line in tmux("list-panes", "-a", "-F", fmt).splitlines():
        f = line.split("\t")
        if len(f) != 5:
            continue
        sess, idxw, path, cmd, pid = f
        key = (sess, idxw)
        if key in seen:
            continue
        proj = agent = None
        if sess.startswith(AGENT_SESS_PREFIX):
            # pxa-<proyecto>-<agente>: el proyecto no lleva guiones (es un slug
            # de carpeta), asi que el primer guion tras el prefijo separa.
            rest = sess[len(AGENT_SESS_PREFIX):]
            if "-" in rest:
                proj, agent = rest.split("-", 1)
        if proj is None and deep:
            hit = idx.get(real(path))
            if hit:
                proj, agent = hit[0].name, hit[1].name
        if proj is None:
            continue
        seen.add(key)
        out.append({"project": proj, "agent": agent, "cwd": path,
                    "session": sess, "window": idxw, "cmd": cmd, "pid": pid,
                    "running_claude": cmd in CLAUDE_CMDS})
    return out


def save_state():
    """Vuelca el registro a disco LOCAL (no al NAS) de forma atomica."""
    rows = live_agents()
    for r in rows:
        uuid, mt = transcript_for(r["cwd"])
        r["transcript"] = uuid
        r["transcript_mtime"] = mt
    doc = {"saved_at": time.time(), "host": os.uname().nodename, "agents": rows}
    atomic_write(STATE_FILE, json.dumps(doc, ensure_ascii=False, indent=1))
    # cola de cada panel: que estaba haciendo cuando se fue la luz
    for r in rows:
        target = "%s:%s" % (r["session"], r["window"])
        txt = tmux("capture-pane", "-p", "-t", target, "-S", "-60")
        if txt.strip():
            atomic_write(os.path.join(PANE_DIR, target.replace(":", "_") + ".txt"), txt)
    return rows


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (IOError, ValueError):
        return None


def occupant(cwd):
    """Quien tiene ya un agente vivo en ese directorio (o None)."""
    want = real(cwd)
    for r in live_agents(deep=True):
        if real(r["cwd"]) == want:
            return r
    return None


PIN_DIR = os.path.join(STATE_DIR, "pins")


def pin_path(session):
    return os.path.join(PIN_DIR, session + ".json")


def cmd_paste_brief(argv):
    """Espera a que la TUI este lista y pega el brief. Uso interno.

    Existe como subcomando porque `px attach` acaba en execvp (se convierte en
    el tmux de la pestana) y por tanto no tiene un "despues" donde esperar. Se
    lanza en segundo plano: asi la pestana se ve al instante y el brief entra
    cuando el prompt esta listo, en vez de dejar el panel en blanco 10s.
    """
    send = "--go" in argv or "-g" in argv
    specs = [a for a in argv if not a.startswith("-")]
    if not specs:
        die("uso: px paste-brief <proyecto>/<agente> [--go]")
    ag = resolve(specs[0])
    if not ag.brief:
        return 0
    # OJO: sin el prefijo "=". Sirve para has-session (evita coincidencia por
    # prefijo), pero como destino de PANEL tmux lo ignora y devuelve vacio:
    # `display -t "=sesion"` no da nada y `paste-buffer` falla en silencio.
    target = session_name(ag)
    if not wait_ready(target):
        sys.stderr.write("px: el panel de %s no llego a un prompt estable\n" % ag.spec)
        return 1
    if not paste_brief(target, ag.brief, send=send):
        sys.stderr.write("px: el brief de %s no llego a pegarse\n" % ag.spec)
        return 1
    return 0


def cmd_adopt(argv):
    """Marca que la PROXIMA apertura de un agente reanude una conversacion dada.

    Sirve para mudar una sesion que nacio fuera de px (p.ej. en Ghostty) sin
    perder el hilo: `--continue` no vale porque coge la mas reciente de esa
    carpeta, que puede ser otra. Aqui se fija el id exacto.

    El transcript tiene que estar YA en esta maquina (la del tmux); para
    traerlo desde otra, `px handoff` lo copia antes.
    """
    uuid = None
    for i, a in enumerate(argv):
        if a in ("--session", "-s") and i + 1 < len(argv):
            uuid = argv[i + 1]
    specs = [a for a in argv if not a.startswith("-") and a != uuid]
    if not specs or not uuid:
        die("uso: px adopt <proyecto>/<agente> --session <uuid>")
    ag = resolve(specs[0])
    tdir = os.path.join(CLAUDE_PROJECTS, mangle(ag.path))
    tfile = os.path.join(tdir, uuid + ".jsonl")
    if not os.path.isfile(tfile):
        die("no encuentro el transcript en esta maquina:\n    %s\n"
            "    (desde el otro Mac: px handoff %s --session %s)"
            % (tfile, ag.spec, uuid))
    session = session_name(ag)
    if tmux_ok("has-session", "-t", "=" + session):
        die("'%s' ya esta abierto: cierralo antes de adoptar otra conversacion\n"
            "    tmux kill-session -t %s" % (ag.spec, session))
    atomic_write(pin_path(session), json.dumps({"session_id": uuid,
                                                "agent": ag.spec,
                                                "at": time.time()}))
    size = os.path.getsize(tfile)
    print("\n  %sadoptada%s  %s" % (C["g"], C["x"], ag.spec))
    print("  %stranscript %s (%.1f MB)%s" % (C["d"], uuid[:8], size / 1e6, C["x"]))
    print("\n  Al abrir esa pestana en PX arrancara con --resume, no de cero.")
    print("  %sCierra antes la sesion de origen: dos claude sobre el mismo"
          "\n  transcript se pisan.%s\n" % (C["y"], C["x"]))
    return 0


def orphan_conversation(ag):
    """Conversacion que estaba viva en el ultimo registro y ya no tiene sesion.

    Tras un corte de luz (o un tmux muerto sin `px close`), state.json conserva
    la fila del agente con su transcript. Si al abrir la pestana se arranca un
    claude de cero, se quema la ventana de recuperacion: la conversacion virgen
    nueva pasa a ser "la mas reciente" y `--continue` / `px restore` cogerian
    esa. Paso con el apagon del 2026-09-01: la app reabrio 5 pestanas y las 5
    arrancaron de cero.

    Devuelve la fila del registro solo si es inequivoco: habia claude corriendo
    ahi, su transcript sigue siendo el mas reciente de la carpeta, y el fichero
    existe. Si alguien ya abrio otra conversacion despues, no opina.
    """
    st = load_state()
    if not st:
        return None
    want = real(ag.path)
    for r in st.get("agents", []):
        if not r.get("running_claude") or real(r.get("cwd", "")) != want:
            continue
        uuid = r.get("transcript")
        if not uuid:
            return None
        newest, _m = transcript_for(ag.path)
        if newest != uuid:
            return None
        if not os.path.isfile(os.path.join(CLAUDE_PROJECTS, mangle(ag.path),
                                           uuid + ".jsonl")):
            return None
        return r
    return None


def archive_pane_queue(session):
    """Aparta la cola de pantalla de la vida ANTERIOR de una sesion.

    El demonio sobreescribe panes/<sesion>_<n>.txt cada 15 s; si la sesion se
    recrea tras un corte, la foto de "que estaba haciendo cada agente" se
    pisaria justo cuando se quiere mirar (paso el 2026-09-02). Se mueve a
    panes/anterior/ con la fecha de la captura, y se poda lo de +30 dias.
    """
    try:
        fns = os.listdir(PANE_DIR)
    except OSError:
        return
    adir = os.path.join(PANE_DIR, "anterior")
    pre = session + "_"
    for fn in fns:
        # <sesion>_<ventana>.txt exacto: una sesion puede ser prefijo de otra
        if not (fn.startswith(pre) and fn.endswith(".txt")
                and fn[len(pre):-4].isdigit()):
            continue
        src = os.path.join(PANE_DIR, fn)
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S",
                                  time.localtime(os.path.getmtime(src)))
            os.makedirs(adir, exist_ok=True)
            os.replace(src, os.path.join(adir, "%s-%s.txt" % (fn[:-4], stamp)))
        except OSError:
            pass
    try:
        cutoff = time.time() - 30 * 86400
        for fn in os.listdir(adir):
            p = os.path.join(adir, fn)
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
    except OSError:
        pass


def take_pin(session):
    """Lee y CONSUME el pin (de un solo uso)."""
    p = pin_path(session)
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        os.unlink(p)
        return doc.get("session_id")
    except (IOError, ValueError):
        return None


def session_name_for(project_name, agent_name):
    clean = lambda s: s.replace(".", "_").replace(":", "_")
    return AGENT_SESS_PREFIX + "%s-%s" % (clean(project_name), clean(agent_name))


def session_name(ag):
    return session_name_for(ag.project.name, ag.name)


def cmd_attach(argv):
    """Abre (o engancha) el agente en su sesion. Es lo que ejecuta la app.

    Aqui vive el candado de "un solo agente por directorio": `new-session -A`
    ya es idempotente, pero ademas comprobamos que no haya otra sesion distinta
    trabajando en la misma carpeta (p.ej. abierta a mano o desde otro sitio).
    """
    resume = "--continue" in argv or "-c" in argv
    no_brief = "--no-brief" in argv or "-n" in argv
    specs = [a for a in argv if not a.startswith("-")]
    if not specs:
        die("dime que agente enganchar")
    ag = resolve(specs[0])
    session = session_name(ag)

    pinned, nueva = None, False
    if not tmux_ok("has-session", "-t", "=" + session):
        nueva = True
        other = occupant(ag.path)
        if other:
            sys.stderr.write(
                "\npx: YA HAY UN AGENTE en %s\n"
                "    sesion tmux '%s' (%s)\n"
                "    No arranco un segundo: dos agentes en la misma carpeta se\n"
                "    pisan los ficheros. Engancha a esa, o cierrala primero:\n"
                "        tmux attach -t %s\n\n"
                % (ag.relpath, other["session"], other["cmd"], other["session"]))
            return 3
        # El pin es de un solo uso: se consume DESPUES del candado, o un
        # attach abortado con 3 quemaba la adopcion en silencio (parte del
        # maestro, 2026-09-02, alta de fluge).
        pinned = take_pin(session)
    reanuda = bool(pinned) or resume   # arranca sobre una conversacion previa
    if pinned:
        cmd = [CLAUDE_BIN, "--resume", pinned]
    else:
        cmd = [CLAUDE_BIN] + (["--continue"] if resume else [])
        if nueva and not resume:
            r = orphan_conversation(ag)
            if r:
                uuid = r["transcript"]
                edad = human_age(time.time() - (r.get("transcript_mtime")
                                                or r.get("at") or time.time()))
                if sys.stdin.isatty():
                    # Preguntar, no decidir: arrancar de cero quema la ventana
                    # de recuperacion, pero reanudar en silencio tampoco es lo
                    # que se pidio al abrir la pestana. Enter = reanudar, que
                    # es la opcion que no pierde nada.
                    sys.stdout.write(
                        "\n  px: aqui habia una conversacion viva que no se"
                        " cerro con px\n      %s(transcript %s · ultimo cambio"
                        " hace %s)%s\n      Reanudarla? [S/n] "
                        % (C["d"], uuid[:8], edad, C["x"]))
                    sys.stdout.flush()
                    try:
                        resp = input().strip().lower()
                    except EOFError:
                        resp = ""
                    if resp in ("", "s", "si", "y", "yes"):
                        cmd = [CLAUDE_BIN, "--resume", uuid]
                        reanuda = True
                else:
                    sys.stderr.write(
                        "px: OJO: aqui habia una conversacion viva (transcript"
                        " %s).\n    Arranco de cero; para recuperarla despues:\n"
                        "        px adopt %s --session %s\n"
                        % (uuid[:8], ag.spec, uuid))

    # La cola de pantalla de la vida anterior se archiva ANTES de recrear la
    # sesion: si no, el demonio la pisa a los 15 s con la pantalla nueva.
    if nueva:
        archive_pane_queue(session)

    # Brief: solo al CREAR la sesion, y nunca sobre un --resume/--continue (una
    # conversacion reanudada ya tiene su contexto; pegarle el brief encima
    # seria ruido). Va en segundo plano porque abajo hacemos execvp.
    if nueva and not reanuda and not no_brief and ag.brief:
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__),
                              "paste-brief", ag.spec],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass
    os.execvp("tmux", ["tmux", "new-session", "-A", "-s", session,
                       "-c", ag.path] + cmd)


def cmd_sessions(argv):
    """Que hay abierto ahora y que se restauraria tras un corte."""
    rows = save_state()
    st = load_state()
    print("\n%sAgentes vivos ahora%s" % (C["b"], C["x"]))
    if rows:
        for r in rows:
            uuid = r.get("transcript") or "-"
            print("  %s%s/%s%s  %ssesion %s · %s%s\n     %stranscript %s%s" % (
                C["v"], r["project"], r["agent"], C["x"],
                C["d"], r["session"], r["cmd"], C["x"],
                C["d"], uuid[:8], C["x"]))
    else:
        print("  %sninguno%s" % (C["d"], C["x"]))
    if st:
        age = time.time() - st.get("saved_at", 0)
        print("\n%sRegistro en disco%s  %s%s · hace %s · %d agente(s)%s" % (
            C["b"], C["x"], C["d"], STATE_FILE, human_age(age),
            len(st.get("agents", [])), C["x"]))
        print("  %scolas de pantalla en %s%s" % (C["d"], PANE_DIR, C["x"]))
    print("")
    return 0


def cmd_restore(argv):
    """Reconstruye tras un corte: recrea las sesiones con `claude --continue`."""
    st = load_state()
    if not st or not st.get("agents"):
        die("no hay registro que restaurar (%s)" % STATE_FILE)
    yes = "-y" in argv or "--yes" in argv
    alive = {real(r["cwd"]) for r in live_agents(deep=True)}
    plan, skip = [], []
    for r in st["agents"]:
        (skip if real(r["cwd"]) in alive else plan).append(r)

    print("\n%sRestaurar%s %s(registro de hace %s)%s" % (
        C["b"], C["x"], C["d"], human_age(time.time() - st.get("saved_at", 0)), C["x"]))
    for r in skip:
        print("  %sya vivo%s   %s/%s" % (C["d"], C["x"], r["project"], r["agent"]))
    for r in plan:
        uuid = (r.get("transcript") or "")[:8]
        print("  %srecrear%s   %s/%s  %s%s · claude --continue%s" % (
            C["g"], C["x"], r["project"], r["agent"], C["d"],
            r["cwd"], C["x"]) + ("  %s[%s]%s" % (C["d"], uuid, C["x"]) if uuid else ""))
    if not plan:
        print("\n  nada que restaurar: todo sigue vivo\n")
        return 0
    if not yes:
        print("\n  %s%d sesion(es) a recrear. Repite con -y para hacerlo.%s\n"
              % (C["y"], len(plan), C["x"]))
        return 0

    done = 0
    for r in plan:
        if not os.path.isdir(r["cwd"]):
            print("  %ssalta%s     %s/%s: la carpeta ya no existe"
                  % (C["y"], C["x"], r["project"], r["agent"]))
            continue
        if occupant(r["cwd"]):
            print("  %ssalta%s     %s/%s: alguien la ocupa ya"
                  % (C["y"], C["x"], r["project"], r["agent"]))
            continue
        session = "pxa-%s-%s" % (r["project"].replace(".", "_"),
                                 r["agent"].replace(".", "_"))  # noqa
        archive_pane_queue(session)   # la foto del corte, a panes/anterior/
        tmux("new-session", "-d", "-s", session, "-c", r["cwd"],
             CLAUDE_BIN, "--continue")
        print("  %srestaurado%s %s/%s -> %s" % (C["g"], C["x"], r["project"],
                                                r["agent"], session))
        done += 1
    print("\n  %d sesion(es) recreadas. Abre PX y las veras.\n" % done)
    return 0


def cmd_onboard(argv):
    """Analiza un proyecto para darlo de alta en px, y valida lo ya dado.

    Hace la parte mecanica (que carpetas son candidatas, colisiones de nombre,
    git, agentes sin brief, dos agentes compartiendo carpeta). El criterio
    -que es un agente, como se llama, que dice su brief- lo pone el maestro.
    """
    apply = "--apply" in argv
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        die("uso: px onboard <ruta-o-proyecto> [--apply]")
    target = args[0]
    p = project(target)
    path = p.path if p else os.path.realpath(os.path.expanduser(target))
    if not os.path.isdir(path):
        die("no existe: %s" % path)
    name = p.name if p else slug(os.path.basename(path))

    print("\n%sAlta en px:%s %s%s%s\n  %s%s%s\n" % (
        C["b"], C["x"], C["v"], name, C["x"], C["d"], path, C["x"]))

    ok, warn, bad = [], [], []

    # --- ubicacion ---------------------------------------------------------
    registrado = any(os.path.realpath(os.path.expanduser(rp)) == path
                     for _n, rp in read_conf(PROJECTS_CONF))
    if os.path.dirname(path) == os.path.realpath(ROOT):
        ok.append("cuelga de PX_ROOT: se descubre solo")
    elif registrado:
        ok.append("fuera de PX_ROOT pero ya registrado en %s"
                  % os.path.basename(PROJECTS_CONF))
    else:
        warn.append("esta FUERA de %s -> hay que registrarlo en %s"
                    % (ROOT, PROJECTS_CONF))

    # --- agentes -----------------------------------------------------------
    conf = os.path.join(path, ".px.conf")
    tmp = Project(name, path)
    scanned = scan_agents(tmp)
    # OJO: si hay .px.conf, la verdad son los agentes DECLARADOS (con los
    # nombres de Miguel). Usar los descubiertos daba falsos "sin brief".
    found = discover_agents(tmp) if os.path.isfile(os.path.join(path, ".px.conf")) else scanned
    if os.path.isfile(conf):
        ok.append(".px.conf presente (%d agentes declarados)" % len(read_conf(conf)))
    elif found:
        (ok if apply else warn).append(
            "sin .px.conf; %d carpeta(s) con CLAUDE.md detectada(s)%s"
            % (len(found), " -> se genera" if apply else " -> px onboard %s --apply" % name))
    else:
        bad.append("ninguna carpeta con CLAUDE.md: sin agentes no hay nada que abrir")

    print("  %sAgentes detectados%s" % (C["b"], C["x"]))
    if found:
        for a in found:
            b = " %sbrief%s" % (C["v"], C["x"]) if a.brief else ""
            print("    %-16s %s%s%s%s" % (a.name, C["d"], a.relpath, C["x"], b))
    else:
        print("    %s(ninguno)%s" % (C["d"], C["x"]))

    # --- candidatas sin CLAUDE.md -----------------------------------------
    known = {a.relpath for a in found}
    sin_declarar = [a.relpath for a in scanned if a.relpath not in known]
    if sin_declarar:
        warn.append("con CLAUDE.md pero fuera del .px.conf: %s"
                    % ", ".join(sin_declarar))
    cands = []
    for e in sorted(os.listdir(path)):
        if e.startswith((".", "_", "@", "#")) or e in SKIP:
            continue
        d = os.path.join(path, e)
        if os.path.isdir(d) and e not in known:
            has_git = os.path.exists(os.path.join(d, ".git"))
            if has_git:
                cands.append((e, "repo git sin CLAUDE.md"))
    if cands:
        print("\n  %sCandidatas a agente%s %s(les falta CLAUDE.md)%s"
              % (C["b"], C["x"], C["d"], C["x"]))
        for e, why in cands:
            print("    %-16s %s%s%s" % (e, C["d"], why, C["x"]))
        warn.append("%d repo(s) sin CLAUDE.md: decide si son agentes" % len(cands))

    # --- colisiones y carpetas compartidas --------------------------------
    names, paths = {}, {}
    for a in found:
        names.setdefault(a.name, []).append(a.relpath)
        paths.setdefault(real(a.path), []).append(a.name)
    for n, rs in names.items():
        if len(rs) > 1:
            bad.append("nombre repetido '%s': %s" % (n, ", ".join(rs)))
    for pth, ns in paths.items():
        if len(ns) > 1:
            bad.append("misma carpeta para %s: dos agentes ahi se pisan" % ", ".join(ns))

    # --- git ---------------------------------------------------------------
    roots = []
    for a in found:
        r = git_root(a.path)
        if r and r not in roots:
            roots.append(r)
    safe = run(["git", "config", "--global", "--get-all", "safe.directory"], 15).split("\n")
    for r in roots:
        if r.startswith("/Volumes/") and r not in safe:
            warn.append("git: %s en disco de red sin safe.directory" % os.path.basename(r))
    if roots:
        ok.append("%d repo(s) git: %s" % (len(roots), ", ".join(os.path.basename(r) for r in roots)))

    # --- briefs ------------------------------------------------------------
    sin = [a.name for a in found if not a.brief]
    if sin:
        warn.append("sin brief de arranque: %s  (-> %s/.px/briefs/<agente>.md)"
                    % (", ".join(sin), name))

    # --- aplicar -----------------------------------------------------------
    if apply and found and not os.path.isfile(conf):
        cmd_scan([name] if p else [])
        ok.append("escrito %s" % conf)

    for label, rows, col in (("OK", ok, C["g"]), ("REVISAR", warn, C["y"]),
                             ("BLOQUEA", bad, C["r"])):
        if rows:
            print("\n  %s%s%s" % (col, label, C["x"]))
            for r in rows:
                print("    %s %s" % ("·", r))
    print("")
    return 1 if bad else 0


def cmd_update(argv):
    """Pone al dia ESTA maquina: repo, instalacion y app si hace falta.

    WHY: el Studio no puede entrar al MacBook (Remote Login apagado, y la
    relacion es a proposito unidireccional). Asi que actualizar el portatil
    tiene que ser un comando corto que se lance alli, no una receta larga.
    """
    repo = os.path.dirname(os.path.abspath(__file__))
    print("\n%spx update%s %s(%s)%s" % (C["b"], C["x"], C["d"], repo, C["x"]))

    # 1. repo
    if os.path.isdir(os.path.join(repo, ".git")):
        before = run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], 30).strip()
        out = run(["git", "-C", repo, "pull", "--ff-only"], 120)
        after = run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], 30).strip()
        if before and after and before != after:
            print("  %sactualizado%s  %s -> %s" % (C["g"], C["x"], before, after))
        else:
            print("  %sal dia%s       %s" % (C["d"], C["x"], after or "(sin git)"))
        if "diverg" in out or "Automatic merge failed" in out:
            print("  %sojo: el repo local diverge; resuelvelo a mano%s" % (C["y"], C["x"]))

    # 2. instalacion (px, demonio si toca)
    inst = os.path.join(repo, "install.sh")
    if os.path.isfile(inst):
        for line in run(["bash", inst], 120).splitlines():
            if line.strip():
                print("  " + line.strip())

    # 3. app: recompilar solo si hay fuentes mas nuevas que el binario
    app = os.path.join(repo, "app")
    binp = os.path.expanduser("~/Applications/PX.app/Contents/MacOS/PX")
    if os.path.isdir(app):
        newest = 0.0
        for d, _, fs in os.walk(os.path.join(app, "Sources")):
            for f in fs:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(d, f)))
                except OSError:
                    pass
        for extra in ("Package.swift", "Resources/Info.plist"):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(app, extra)))
            except OSError:
                pass
        have = os.path.getmtime(binp) if os.path.isfile(binp) else 0.0
        if not shutil_which("swift"):
            print("  %sapp: swift no esta instalado, no se compila%s" % (C["y"], C["x"]))
        elif newest > have:
            print("  %scompilando la app%s %s(fuentes mas nuevas que el binario)%s"
                  % (C["v"], C["x"], C["d"], C["x"]))
            out = run(["bash", os.path.join(app, "build.sh")], 900)
            err = [l for l in out.splitlines() if "error:" in l]
            if err:
                print("  %sFALLO al compilar:%s" % (C["r"], C["x"]))
                for l in err[:5]:
                    print("    " + l.strip())
                return 1
            print("  %sapp compilada%s  %s" % (C["g"], C["x"],
                  "reinicia PX para usarla (los agentes siguen vivos en tmux)"))
        else:
            print("  %sapp al dia%s" % (C["d"], C["x"]))
    print("")
    return 0


def cmd_theme(argv):
    """Re-aplica estilo y atajos (util tras tocar los formatos)."""
    ss = sessions()
    if not ss:
        die("no hay ninguna sesion px abierta")
    apply_theme(ss[0])
    print("  tema re-aplicado (%s)" % ", ".join(ss))
    return 0


def cmd_doctor(argv):
    print("\n%spx %s%s\n" % (C["b"], VERSION, C["x"]))
    rows = [("tmux", shutil_which("tmux")),
            (CLAUDE_BIN, shutil_which(CLAUDE_BIN)),
            ("git", shutil_which("git")),
            ("python3", sys.executable)]
    for name, path in rows:
        mark = "%s✔%s" % (C["g"], C["x"]) if path else "%s✘%s" % (C["r"], C["x"])
        print("  %s %-10s %s" % (mark, name, path or "NO ENCONTRADO"))
    print("\n  %sentorno%s        %s%s" % (C["d"], C["x"], current_ws(),
          "" if workspaces() else "  %s(sin %s)%s" % (C["y"], WS_CONF, C["x"])))
    print("  %sPX_ROOT%s        %s%s" % (C["d"], C["x"], ROOT,
          "" if os.path.isdir(ROOT) else "  %s(no existe todavia)%s" % (C["y"], C["x"])))
    print("  %sprojects.conf%s  %s%s" % (C["d"], C["x"], PROJECTS_CONF,
          "" if os.path.isfile(PROJECTS_CONF) else "  %s(no existe)%s" % (C["y"], C["x"])))
    ps = projects()
    con = [p for p in ps if p.agents]
    print("  %sproyectos%s      %d  (%d con agentes, que son los que pinta la app)"
          % (C["d"], C["x"], len(ps), len(con)))
    for p in ps:
        if p.agents:
            print("      %-16s %d agentes" % (p.name, len(p.agents)))
        else:
            print("      %-16s %ssin agentes (ningun CLAUDE.md) -> px onboard %s%s"
                  % (p.name, C["y"], p.name, C["x"]))
    print("  %stmux server%s    %s" % (C["d"], C["x"],
          "vivo, sesiones: %s" % ", ".join(sessions()) if has_server() else "parado"))
    print("")
    return 0


USAGE = """
px — terminal de proyectos y agentes

  px                      entra a lo ultimo abierto (o lista si no hay nada)
  px ls [proyecto]        proyectos, agentes y su estado
  px open <agente>...     abre pestana(s) y engancha   (px open coder contable)
  px open <proy>/<ag>     desambigua si el nombre se repite
  px open -n <agente>     abre sin inyectar el brief
  px open -d <agente>     abre en segundo plano (sin engancharse)
  px open -g <agente>     ademas ENVIA el brief (por defecto solo lo deja pegado)
  px close <agente>...    cierra la pestana
  px brief [desde]        que se ha movido en cada proyecto  (px brief 3.days)
  px scan [proyecto]      (re)genera el .px.conf del proyecto
  px onboard <ruta>       analiza y valida un proyecto para darlo de alta
  px adopt <ag> -s <id>   la proxima apertura reanuda ESA conversacion
  px handoff <ag> [-s id] (solo MacBook) copia el transcript al Studio y adopta
  px sessions             que hay vivo y que se restauraria tras un corte
  px restore [-y]         recrea las sesiones perdidas (claude --continue)
  px theme                re-aplica estilo y atajos a lo que ya este abierto
  px update               pone al dia esta maquina (repo + px + app)
  px ws [nombre|all]      entorno de trabajo activo (empresa vs personal)
  px order [p1 p2 ...]    orden de los proyectos (la app lo escribe al arrastrar)
  px doctor               comprueba el entorno
  px daemon               refresca estados (lo arranca 'px open' solo)

  Dentro:  M-<-/->  agente   M-arriba/abajo  proyecto   M-1..9  ir a agente
           F1 briefing   F2 lista   prefix+p proyectos   prefix+a agentes
"""


def main(argv):
    if not argv:
        return cmd_tui_attach([]) if sessions() else cmd_ls([])
    cmd, rest = argv[0], argv[1:]
    table = {"ls": cmd_ls, "list": cmd_ls, "open": cmd_open, "start": cmd_open,
             "close": cmd_close, "stop": cmd_close, "go": cmd_tui_attach,
             "a": cmd_tui_attach, "brief": cmd_brief, "b": cmd_brief,
             "scan": cmd_scan, "doctor": cmd_doctor, "theme": cmd_theme,
             "json": cmd_json, "attach": cmd_attach, "sessions": cmd_sessions,
             "restore": cmd_restore, "adopt": cmd_adopt, "onboard": cmd_onboard,
             "paste-brief": cmd_paste_brief,
             "update": cmd_update, "ws": cmd_ws, "entorno": cmd_ws,
             "order": cmd_order, "orden": cmd_order,
             "daemon": lambda a: daemon(once="--once" in a)}
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd in ("-V", "--version"):
        print("px %s" % VERSION)
        return 0
    fn = table.get(cmd)
    if not fn:
        # 'px coder' = 'px open coder'
        return cmd_open(argv)
    return fn(rest) or 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
