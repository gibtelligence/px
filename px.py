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

VERSION = "0.1.0"

HOME = os.path.expanduser("~")
ROOT = os.environ.get("PX_ROOT", "/Volumes/PERSONAL/Proyectos")
CONF_DIR = os.environ.get("PX_CONF_DIR", os.path.join(HOME, ".config", "px"))
PROJECTS_CONF = os.path.join(CONF_DIR, "projects.conf")
CACHE_DIR = os.path.join(HOME, ".cache", "px")
SESS_PREFIX = "px-"
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
    return out


def project(name):
    for p in projects():
        if p.name == name:
            return p
    return None


def discover_agents(proj):
    """.px.conf manda; si no existe, se descubre por CLAUDE.md."""
    conf = os.path.join(proj.path, ".px.conf")
    rows = read_conf(conf)
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


def windows():
    """[(sess, idx, name, cmd, path, activity)] de todas las sesiones px-."""
    fmt = "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_current_command}\t#{pane_current_path}\t#{window_activity}"
    rows = []
    for line in tmux("list-windows", "-a", "-F", fmt).splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[0].startswith(SESS_PREFIX):
            rows.append(f)
    return rows


def classify(sess, idx, cmd):
    """Estado real del agente, leyendo el pane."""
    if cmd not in CLAUDE_CMDS:
        return "off"
    txt = tmux("capture-pane", "-p", "-t", "%s:%s" % (sess, idx), "-S", "-14")
    low = txt.lower()
    if MARK_WAIT in low:
        return "wait"
    if MARK_WORK in low:
        return "work"
    return "idle"


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
    """{('sess','name'): (estado, edad_en_seg)} de lo que hay abierto ahora."""
    out = {}
    now = time.time()
    for sess, idx, name, cmd, path, act in windows():
        st = classify(sess, idx, cmd)
        try:
            age = max(0, now - int(act))
        except ValueError:
            age = 0
        out[(sess, name)] = (st, age)
    return out


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
    ps = projects()
    if only:
        ps = [p for p in ps if p.name == only] or die("proyecto desconocido: %s" % only)
    print("")
    for p in ps:
        agents = p.agents
        openn = sum(1 for a in agents if (p.session, a.name) in live)
        print("%s%s%s %s(%s)%s %s%d agentes%s%s" % (
            C["b"], p.name, C["x"], C["d"], p.path, C["x"],
            C["d"], len(agents), C["x"],
            "  %s%d abiertos%s" % (C["v"], openn, C["x"]) if openn else ""))
        for a in agents:
            key = (p.session, a.name)
            if key in live:
                st, age = live[key]
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

    last, nuevas = None, []
    for spec in specs:
        ag = resolve(spec)
        sess = ag.project.session
        if not tmux_ok("has-session", "-t", "=" + sess):
            tmux("new-session", "-d", "-s", sess, "-n", ag.name, "-c", ag.path)
            apply_theme(sess)
            launch(sess, ag.name)
            nuevas.append(ag)
            print("  %sabierta%s  %s  %s%s%s" % (C["g"], C["x"], ag.spec, C["d"], ag.relpath, C["x"]))
        elif window_exists(sess, ag.name) is not None:
            print("  %ssalta%s    %s ya estaba abierta" % (C["d"], C["x"], ag.spec))
        else:
            tmux("new-window", "-t", "=" + sess, "-n", ag.name, "-c", ag.path)
            launch(sess, ag.name)
            nuevas.append(ag)
            print("  %sabierta%s  %s  %s%s%s" % (C["g"], C["x"], ag.spec, C["d"], ag.relpath, C["x"]))
        last = (sess, ag.name)

    # los briefs se pegan cuando la TUI esta lista, y NO se envian:
    # abrir una pestana no puede significar lanzar al agente a trabajar solo.
    if brief_mode:
        for ag in nuevas:
            if not ag.brief:
                continue
            if wait_ready(ag.project.session, ag.name):
                if paste_brief(ag.project.session, ag.name, ag.brief, send=send_brief):
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
    return 0


def launch(sess, wname):
    """Arranca claude en la ventana. El brief se pega despues (ver paste_brief)."""
    tmux("send-keys", "-t", "=%s:%s" % (sess, wname), CLAUDE_BIN, "C-m")


def wait_ready(sess, wname, timeout=30.0):
    """Espera a que la TUI de claude este en el prompt (no arrancando, no en dialogo)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        idx = window_exists(sess, wname)
        if idx is None:
            return False
        cmd = tmux("display", "-p", "-t", "=%s:%s" % (sess, wname),
                   "#{pane_current_command}").strip()
        if classify(sess, idx, cmd) == "idle":
            return True
        time.sleep(0.5)
    return False


def paste_brief(sess, wname, path, send=False):
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
    target = "=%s:%s" % (sess, wname)
    tmux("set-buffer", "-b", "pxbrief", text)
    tmux("paste-buffer", "-b", "pxbrief", "-t", target, "-p", "-d")
    if send:
        time.sleep(0.4)
        tmux("send-keys", "-t", target, "C-m")
    return True


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
    for p in projects():
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
            key = (p.session, a.name)
            if key in live:
                st, age = live[key]
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


def cmd_json(argv):
    """Modelo completo en JSON — lo consume la app nativa (app/)."""
    live = live_states() if has_server() else {}
    out = {"root": ROOT, "session_prefix": SESS_PREFIX, "projects": []}
    for p in projects():
        pj = {"name": p.name, "path": p.path, "session": p.session, "agents": []}
        for a in p.agents:
            st, age = live.get((p.session, a.name), (None, None))
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
        if sess.startswith("pxa-"):
            # pxa-<proyecto>-<agente>: el proyecto no lleva guiones (es un slug
            # de carpeta), asi que el primer guion tras el prefijo separa.
            rest = sess[4:]
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


def session_name(ag):
    clean = lambda s: s.replace(".", "_").replace(":", "_")
    return "pxa-%s-%s" % (clean(ag.project.name), clean(ag.name))


def cmd_attach(argv):
    """Abre (o engancha) el agente en su sesion. Es lo que ejecuta la app.

    Aqui vive el candado de "un solo agente por directorio": `new-session -A`
    ya es idempotente, pero ademas comprobamos que no haya otra sesion distinta
    trabajando en la misma carpeta (p.ej. abierta a mano o desde otro sitio).
    """
    resume = "--continue" in argv or "-c" in argv
    specs = [a for a in argv if not a.startswith("-")]
    if not specs:
        die("dime que agente enganchar")
    ag = resolve(specs[0])
    session = session_name(ag)

    pinned = None
    if not tmux_ok("has-session", "-t", "=" + session):
        pinned = take_pin(session)
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
    if pinned:
        cmd = [CLAUDE_BIN, "--resume", pinned]
    else:
        cmd = [CLAUDE_BIN] + (["--continue"] if resume else [])
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
    if os.path.dirname(path) == os.path.realpath(ROOT):
        ok.append("cuelga de PX_ROOT: se descubre solo")
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
    print("\n  %sPX_ROOT%s        %s%s" % (C["d"], C["x"], ROOT,
          "" if os.path.isdir(ROOT) else "  %s(no existe todavia)%s" % (C["y"], C["x"])))
    print("  %sprojects.conf%s  %s%s" % (C["d"], C["x"], PROJECTS_CONF,
          "" if os.path.isfile(PROJECTS_CONF) else "  %s(no existe)%s" % (C["y"], C["x"])))
    ps = projects()
    print("  %sproyectos%s      %d  (%s)" % (C["d"], C["x"], len(ps),
                                             ", ".join(p.name for p in ps) or "ninguno"))
    for p in ps:
        print("      %-16s %d agentes" % (p.name, len(p.agents)))
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
  px sessions             que hay vivo y que se restauraria tras un corte
  px restore [-y]         recrea las sesiones perdidas (claude --continue)
  px theme                re-aplica estilo y atajos a lo que ya este abierto
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
