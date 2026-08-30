#!/usr/bin/env bash
# Instala px en ~/.local/bin.
#
# Dos papeles:
#   - MAQUINA DE TRABAJO (el Studio): tmux + claude corren aqui -> px completo.
#   - CLIENTE (el MacBook): reenvia por ssh al Studio -> px-remote.
#
# Se decide por hostname (el MacBook tambien tiene claude instalado, asi que
# "tengo claude" NO sirve para distinguir). Forzable:
#   PX_FORCE=local  o  PX_FORCE=remote   bash install.sh
#   PX_WORK_HOST=zymba-studio            (hostname de la maquina de trabajo)
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
WORK_HOST="${PX_WORK_HOST:-zymba-studio}"
ME="$(hostname -s 2>/dev/null || hostname)"
mkdir -p "$BIN" "$HOME/.config/px"

case "${PX_FORCE:-auto}" in
  local)  ROLE=local ;;
  remote) ROLE=remote ;;
  *)      case "$ME" in "$WORK_HOST"*) ROLE=local ;; *) ROLE=remote ;; esac ;;
esac

if [ "$ROLE" = local ]; then
  ln -sf "$HERE/px" "$BIN/px"
  echo "  instalado  px completo   -> $HERE/px          [esta maquina: $ME]"
  for c in tmux claude; do
    command -v "$c" >/dev/null 2>&1 || echo "  ojo: '$c' no esta en el PATH de $ME"
  done
else
  ln -sf "$HERE/px-remote" "$BIN/px"
  echo "  instalado  px reenviador -> $HERE/px-remote   [$ME -> ${PX_HOST:-studio}]"
fi

# El demonio del registro anticorte solo tiene sentido donde corre tmux.
if [ "$ROLE" = local ]; then
  # COPIA LOCAL a proposito: launchd no puede leer /Volumes (TCC) y el NAS
  # puede no estar montado tras un arranque, que es justo cuando hace falta.
  LIB="$HOME/.local/libexec/px"
  mkdir -p "$LIB"
  cp "$HERE/px.py" "$HERE/px" "$LIB/" && chmod +x "$LIB/px"
  echo "  copiado   demonio a disco local -> $LIB"
  PL="$HOME/Library/LaunchAgents/com.gibtelligence.px-daemon.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s|__PXBIN__|$LIB/px|" "$HERE/com.gibtelligence.px-daemon.plist" > "$PL"
  launchctl bootout "gui/$(id -u)/com.gibtelligence.px-daemon" 2>/dev/null || true
  if launchctl bootstrap "gui/$(id -u)" "$PL" 2>/dev/null; then
    echo "  arrancado  demonio del registro (sobrevive a reinicios)"
  else
    echo "  ojo: no se pudo cargar el demonio ($PL)"
  fi
fi

if [ ! -f "$HOME/.config/px/projects.conf" ]; then
  cp "$HERE/projects.conf.example" "$HOME/.config/px/projects.conf"
  echo "  creado     ~/.config/px/projects.conf (revisalo)"
fi
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  ojo: $BIN no esta en tu PATH" ;; esac
echo "  prueba:    px doctor"
