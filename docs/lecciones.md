# Lecciones de px — no repetir errores

Los fallos que ya nos costaron un rato. Cada uno con el síntoma, la causa y la
regla que sale de él. Si tocas px, léelo antes.

## L1 · tmux falla EN SILENCIO más de lo que parece

Tres bugs distintos del mismo día, todos por confiar en que tmux se quejaría:

- **`paste-buffer` no da error cuando el pegado se pierde.** Si la TUI destino
  se está repintando, el texto desaparece y el comando sale con 0.
- **El prefijo `=` no vale como destino de PANEL.** Sirve para `has-session`
  (evita coincidencia por prefijo), pero `display -t "=sesion"` devuelve
  **vacío** y `paste-buffer -t "=sesion"` no hace nada, sin quejarse.
- `capture-pane` sobre una ventana que no existe devuelve vacío, que es
  indistinguible de un panel en blanco.

**Regla:** con tmux, comprueba el *resultado*, no el código de retorno. Si
escribes en un panel, léelo después para confirmar que llegó. Un `rc=0` de tmux
es una esperanza, no un hecho.

## L2 · Dos modelos de sesión conviviendo

El TUI usa `px-<proyecto>` con una ventana por agente; la app usa
`pxa-<proyecto>-<agente>` con una sola ventana, que tmux nombra `claude.exe`.
Código escrito para uno falla mudo en el otro: `wait_ready` buscaba una ventana
llamada como el agente y no encontraba nada nunca.

**Regla:** las funciones que hablan con un panel reciben un **target**, no
"sesión + nombre de agente". Si escribes `sess:agente`, estás asumiendo un
modelo.

## L3 · Lo que no tiene prueba, se pierde

El enrutado de la app por `px attach` se hizo, se perdió en una edición
posterior y nadie se enteró hasta que el maestro vio dos agentes en la misma
carpeta. No había ninguna prueba que lo cubriera.

**Regla:** si un cambio existe para que algo *no* pase (un candado, un pin, un
filtro), deja una prueba que lo demuestre. La prueba buena aquí fue abrir la
pestaña con un intruso en la carpeta y ver el mensaje del candado **dentro del
panel**: eso solo puede pasar si la app pasa por px.

## L4 · "Funciona 3 veces" no es una prueba en algo con carreras

El pegado de briefs pasó tres veces seguidas y estaba roto: era suerte de
temporización. Lo que lo demostró fue un **A/B** — la misma prueba con las
guardas desactivadas: 3/3 perdido sin ellas, 3/3 bien con ellas.

**Regla:** ante algo dependiente del tiempo, desactiva tu propio arreglo y
comprueba que el fallo vuelve. Si no vuelve, no sabes qué arreglaste.

## L5 · El espacio de nombres de tmux es único, los roots no

Montando un laboratorio con `PX_ROOT` alternativo y un proyecto llamado como
uno real, `px attach` se enganchó a la sesión **real** del usuario: la sesión
se nombra por proyecto+agente, sin el root.

**Regla:** en pruebas con root alternativo, usa nombres de proyecto que no
existan de verdad.

## L6 · launchd no ve el NAS

Un servicio lanzado por launchd no puede leer `/Volumes` (`Operation not
permitted`), y además el NAS puede no estar montado tras un arranque — que es
justo cuando hace falta.

**Regla:** lo que tenga que sobrevivir a un reinicio vive en disco local
(`~/.local/state/px`, `~/.local/libexec/px`) y no consulta el NAS.
