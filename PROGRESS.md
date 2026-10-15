# Progreso del desarrollo

## Current Focus
Fase 2 (visor y planos) completa en código: planos, siluetas dibujables, voladizos y ahora **Generar / Exportar** desde la GUI; falta que el cliente la pruebe en GTK real (nada de la ventana se pudo abrir acá). Después: propuesta automática de siluetas (§8.3 / §8.5) y Fases 3–5.
_Última actualización: 2026-10-15 00:51 (America/Argentina/Buenos_Aires), rama `main` (alineada con `origin/main` tras el PR #5 + los commits de esta tanda). 82 tests pasan, 4 omitidos (sin OpenGL/EGL en el entorno de desarrollo)._

## Todo List
### [ ] Todo
- [ ] Abrir `afm-gui` con GTK4 real y corregir lo que falle (ver Blockers); **probar en especial** el modo «dibujar silueta» (clic, elegir/mover vértice, insertar en borde, Intro/Retroceso/Supr), la casilla de voladizos y **Generar figura** (hilo + `GLib.idle_add`) / **Exportar piezas…** (`Gtk.FileDialog.select_folder`)
- [ ] GUI: voladizos sobre las piezas ya cortadas / en la pose sentada (hoy es una referencia sobre la figura entera)
- [ ] Propuesta automática de siluetas (§8.3 / §8.5)
- [ ] Detectar y quitar automáticamente las láminas finas/rebabas típicas de Hunyuan3D (hoy son `Trim` manuales por figura) — el cliente pidió **no hacerlo por ahora**
- [ ] GUI Fases 3–5 (un lado + espejado en vivo, límite de cadera/sentada/recortes/probetas, empaquetado)
### [/] In Progress
- [/] GUI Fase 2: `controller.py` + `window.py` + `app.py` con tests del controlador; la ventana abre y muestra la figura en el equipo del cliente; falta probar la interacción (clics, planos, siluetas, voladizos, guardar/abrir proyecto)
- [/] Validar el cociente R/3 del pasador de cadera: Güemes y Belgrano no tienen pieza hecha a mano; falta una referencia real
- [/] Belgrano: primera pasada válida; revisar con el cliente el corte de cadera bajo el abrigo (z 38–48) y el del hombro (x = 11)
### [x] Done
- [x] **Generar y Exportar en la GUI (2026-10-15):** `Controller.begin_generate` (devuelve el trabajo pesado, apto para un hilo) / `report_progress` / `finish_generate` / `generate` (síncrono) / `export`, `validation_summary`, bandera `busy` (bloquea clics y la barra lateral); ventana: botón «Generar figura» (hilo + `GLib.idle_add`), casilla «Orientar las piezas…» y «Exportar piezas…». Cualquier edición posterior invalida `Session.outcome` y hay que regenerar. 4 tests nuevos (muñeca de prueba completa: generar → validar → exportar → editar invalida). Tutorial §7.2
- [x] **Editor de siluetas en la GUI (2026-10-15):** `afm.gui.polygon_editor` (`PolygonDraft`, `ray_plane_uv`; lógica pura, 7 tests) + modo `Mode.POLYGON` en `Controller` (`start_polygon`, `close_polygon`, `clear_polygon`, `use_outline_as_base`, `guide_outline`; guardado en el proyecto al instante si el polígono es válido) + ventana (lápiz por miembro en la lista de planos, panel «Silueta del miembro», Intro/Retroceso/Supr). 11 tests del controlador con clics reales proyectados por la cámara + 1 de `sync` con escena falsa. Documentado en `docs/tutorial_gui.md` §7.1
- [x] **Voladizos en la GUI (2026-10-15):** casilla «Mostrar voladizos de impresión (rojo)» y ángulo máximo; `Controller.overhang_faces` usa `best_orientation` sobre la figura entera y pinta las caras voladizas (la malla se parte en `figura` + `voladizos`, sin z-fighting)
- [x] **Corregido `orient.overhang_area` (estaba invertido):** contaba pendientes suaves y no los techos horizontales. Nuevo `orient.overhang_mask`; tests de regresión (T con techo, conos de pared suave/empinada, `max_angle`)
- [x] `afm.gui.controller.Controller` (sin GTK): modos coronilla / plano de simetría / plano de miembro con 3 clics, deshacer y cancelar, lista de planos (mostrar/ocultar/quitar), «un solo lado», frente +Y/−Y, abrir/guardar proyecto y volcado a `SceneRenderer`; 13 tests (`tests/test_gui_controller.py`)
- [x] `afm.gui.window` (GTK4: `Gtk.GLArea` + moderngl, barra lateral, arrastre = orbitar, Mayús/botón medio = desplazar, rueda = zoom, clic corto = elegir punto) y `afm.gui.app` (entrada `afm-gui`, mensaje claro si falta GTK); 2 tests (`tests/test_gui_app.py`)
- [x] `orient` en la exportación por CLI: ya estaba (`afm build --orient --max-angle`); lo pendiente era solo la vista en la GUI
- [x] `docs/tutorial_gui.md`: tutorial de uso de la interfaz (escrito desde el código; la ventana aún no se vio con GTK real)
- [x] `PROGRESS.md` movido a la raíz del repo (antes `docs/PROGRESS.md`); README y comentario de `limbs.py` actualizados
- [x] Azurduy (`examples/azurduy.py`, `tests/test_azurduy.py`): válido, torso 1 componente, holguras 0,475 mm, 10 892 triángulos de entrada (sin voxelizar); 3 recortes de láminas de suelas
- [x] Belgrano (`examples/belgrano.py`, test `tests/test_belgrano.py`): válido, torso 1 componente, holguras 0,477 (brazos) / 0,483 (piernas) mm, sin remallado
- [x] Güemes con `auto=True`: pieza válida, torso 1 componente, holguras 0,474–0,476 mm (manual: 0,476–0,477)

## Latest Blockers/Discoveries
- **Generar corre en un hilo (2026-10-15), sin verificar con GTK real:** `Session.build` usa manifold3d/trimesh en un hilo secundario mientras la UI queda bloqueada (`ctrl.busy`, `sidebar_box.set_sensitive(False)`); la vista GL sigue dibujando `session.work` sin cambios. Si manifold3d no libera el GIL la ventana puede congelarse igual: en ese caso pasar el trabajo a un proceso (`multiprocessing`) en vez de un hilo.
- **Sin cambios del PR #5:** `origin/main` (2b2c475) ya trae las tres tandas anteriores (orient, voladizos, siluetas) con el mismo contenido que mis commits locales; el clon se alineó con `git reset --hard origin/main` y esta tanda va encima.
- **Bug de `orient` corregido (2026-10-15):** `overhang_area` marcaba como voladizo `-n_z <= cos(ángulo)` (pendientes suaves) y dejaba fuera los techos horizontales. Un cono con la pared a 26,6° de la vertical daba ~700 mm² de voladizo y un techo horizontal 0. Ahora es `-n_z > sen(ángulo)` (ángulo de la pared respecto de la vertical, igual que `--max-angle`). **Consecuencia:** `afm build --orient` puede elegir otra orientación que antes y el `voladizo_mm2` del informe cambia; las exportaciones hechas antes del arreglo con `--orient` conviene regenerarlas.
- **Edición de silueta (2026-10-15):** los clics caen en el plano del miembro (intersección rayo-plano), no en la malla; si el plano se ve de canto desde la cámara no se agrega nada y se avisa. Los cambios sobre un polígono cerrado se guardan al instante; un polígono que se cruza o no tiene área **no** se guarda (el proyecto conserva la silueta anterior). Esc descarta solo el borrador abierto, no lo ya guardado.
- **GTK4/OpenGL no verificable acá (2026-10-15):** `window.py` solo pasó `py_compile`, y los tests de `sync` con OpenGL se omiten en este entorno (no hay EGL); la lógica de `sync` de siluetas se prueba con una escena falsa.
- **Push a GitHub no disponible desde este entorno (2026-10-15):** `git push` falla (`could not read Username`): no hay credenciales. Los commits están hechos en el clon local; hay que empujarlos desde una máquina con acceso.
- **Primer intento del cliente en su equipo (2026-10-14 23:47):** `afm-gui` falló con `No module named 'gi'`: su `python3` del PATH es 3.12 y `python3-gi` de Debian 13 es para 3.13. Con `/usr/bin/python3 -m venv --system-site-packages .venv` la ventana abrió y muestra la figura (2026-10-14 23:50).
- **GTK4 no se pudo probar acá (2026-10-14 23:50):** en el entorno de desarrollo `import gi` falla con Python 3.13 (`python3-gi` está compilado para 3.12) y con 3.12 falta el typelib (`Namespace Gtk not available`; no hay `gir1.2-gtk-4.0`). `window.py` solo pasó `py_compile`. Hay `Xvfb` y EGL offscreen funciona (los tests del renderer y de `Controller.sync` dibujan de verdad). Riesgos a revisar al abrirla: `Gtk.GLArea` + `moderngl.create_context()` / `detect_framebuffer()`, `Gtk.FileDialog`/`Gtk.AlertDialog` (GTK ≥ 4.10), `GestureDrag` con botón 0.
- **Decisión de arquitectura:** toda la lógica de la ventana vive en `Controller` (sin GTK) para poder probarla; `window.py` solo traduce eventos y dibuja. Se ve y se elige siempre sobre `session.work` (simetría en x = 0), que es el marco de los planos de miembro.
- **La coronilla solo se puede marcar antes de definir simetría/miembros** (cambia la escala y rompería los planos ya creados); el controlador lo avisa.
- **Cambiar «Frente hacia» borra simetría y miembros** (comportamiento heredado de `Session.set_front`).
- **Cargar un STL grande bloquea la ventana** (se hace en el hilo de la interfaz); aceptable por ahora, mejorar si molesta.
- **Azurduy no necesita voxelizar:** tiene solo 4 aristas abiertas y `fill_holes` alcanza. Fallaba porque `repair` borraba antes 2 triángulos «degenerados» que sostienen la topología
  (la voxelización daba 1,15 M de triángulos). `meshio.repair` ahora prueba (1) con limpieza y (2) sin limpieza, y solo voxeliza si ninguna sirve.
- **Azurduy trae láminas de ~0,4 mm (z 0,6–1,1)** que salen de las suelas hasta x = 14,9 / y = −11,9 (la bota real: x 4,1–10,8, y −8,7..7,0). Se recortan con `Trim` (cajas) en `examples/azurduy.py`. **Confirmado por el cliente:** son rebabas del proceso de generación de la malla (Hunyuan3D, 3d.hunyuan.tencent.com/studio/creation/concept), no una base intencional. Como las figuras salen de ahí, es probable que se repita en otras.
- **Azurduy, contacto de brazo chico:** 75 mm² (Belgrano 104, Güemes 147). El hombro se funde con el torso solo entre z≈68 y 80; si el pasador queda justo, subir el corte (x = 9) o mover la silueta.
- **`crown_z` va en unidades del ARCHIVO, no en mm** (Belgrano mide 1,16 de alto: con `crown_z=100` no se escalaba y todo fallaba en cascada). Documentado en `examples/belgrano.py`.
- **Corregido en `meshio.load_figure`:** (1) se escala a mm antes de reparar (las tolerancias de `repair` son absolutas); (2) `repair` ya no borra triángulos
  «degenerados» si eso abre una malla que era estanca (Belgrano tiene 3 que sostienen la topología; antes se remallaba por voxelización, paso 0,14 mm, y la cavidad no terminaba en 10 min).
- **Belgrano, geometría automática:** pasador de brazo (11,66; −0,87; 72,75), contacto 104 mm²; pasador de pierna (7,66; −1,54; 44,67), contacto 142 mm².
  Siluetas a ojo desde cortes horizontales: brazo suelto del abrigo bajo z≈55 y fundido con el torso entre z 66 y 80 (corte vertical en x = 11); abrigo hasta z≈38 pasa a la pierna.
- **Güemes `auto=True` vs manual (2026-10-14):** brazo casi idéntico (mediana 0,13 mm, p95 0,27, máx 0,51; volumen 2311 vs 2311).
  Pierna distinta a propósito: auto agrega el recorte cilíndrico de cadera (R ≈ 6,8 mm) que el ajuste manual de Güemes no tenía
  (mediana 1,14 mm, p95 2,16, máx 5,43; volumen 4368 vs 4321) y sube el pasador de z 52,5 a 54,3.
- **No hay verdad de referencia para Güemes:** sus STL de `examples/` son salida del propio motor, así que no valida R/3 de forma independiente.
  Hace falta una pierna hecha a mano por el cliente (o Belgrano) para confirmarlo.
- Pendiente de arrastre: aristas no-variedad en piezas (2 por pierna de Mickey, algunas en Güemes); torso de Güemes ≈ 93–174 k triángulos.

---

_Plan de referencia: `docs/PLAN_herramienta_stl_articulable.md`_

## Hecho
- [x] Estructura del repo, documentación del plan y tutorial de Onshape (`docs/`).
- [x] Motor base (`src/afm`): planos por 3 puntos, envoltorio de manifold3d, carga/reparación/normalización (§8.1).
- [x] Pipeline (Fase 0, sobre figura sintética): separación de miembros por prisma de silueta, rebaja de rebaba,
      pasadores hongo, cavidades con holgura (Minkowski), barrido de cadera hasta 95°, pose sentada a 90°,
      espejado, torso soldado en una sola pieza, validación y exportación (§8.3–§8.10).
- [x] CLI: `afm demo`, `afm build STL proyecto.json`. Archivo de proyecto JSON.
- [x] Tests (`pytest`): 9 pasan. Demo: 5 piezas, torso 1 componente, holguras ≈0,48 mm.
- [x] Generador de probetas (§8.11): `afm probe` (grilla holgura x Ø de vástago + tabla de referencia).
- [x] Perfil de línea (§8.12): guardar/cargar parámetros, defaults por boquilla, aplicar resultado de probetas.
- [x] Herramienta de recorte manual (§8.8): polígono/esfera/caja/cilindro -> extruir -> restar/unir; integrada al pipeline y al `.proyecto`.
- [x] Orientación de impresión y voladizos (§8.7): `afm.orient.best_orientation`.
- [x] Tests: 18 pasan.

## Pendiente (próximos pasos)
- [ ] Fase 1: Güemes, Belgrano y Azurduy (Azurduy no es estanca: probar el remallado por voxelización).
- [ ] Propuesta automática de siluetas y centros de pasador (§8.3 / §8.5).
- [ ] Usar `orient` en la exportación (CLI) y mostrar voladizos en la GUI.
- [ ] GUI GTK4 + visor OpenGL (Fases 2–5).

- [x] STL real de Güemes en `examples/`: el test de integración deja de omitirse y pasa (holguras ≈0,477 mm, torso 1 componente).

- [x] Fase 0 con Mickey real (`examples/mickey.py`, `tools/compare_pieces.py`, test de integración). Frente hacia −Y; coronilla en z=60,6 del STL (75 mm pies→sombrero).
      Comparación contra las piezas del cliente (distancia entre mallas, tras ICP de traslación):
      brazo izq.: mediana 0,11 mm, p95 0,45, máx 2,9, volumen 2009 vs 2003 mm³ (**cumple ≤0,3 mm en mediana**);
      pierna izq.: mediana ≈0,27, p95 ≈1,9, máx ≈8,3, volumen 5238 vs 5123 mm³.

- [x] Validación de recorrido libre (§9): la pierna se rota cada 2,5° de 0° a 95° y se verifica que no interfiera con el torso (detecta pasos de barrido demasiado gruesos: con 10° falla, con 5° pasa).
- [x] Decimación cuadrática del torso (`solids.decimate`, dependencia `fast-simplification`) con reducción adaptativa que preserva la holgura:
      Mickey 129 260 → 51 290 triángulos (torso sin aristas no-variedad); Güemes 149 292 → 79 356 (holgura ≥0,476 mm).

## Descubrimientos (2026-10-14)
- Mickey: las caras de contacto salen del borde vertical (x = const) de la silueta: hombro x≈5,1 y cadera x≈5,45 (STL original), deducido de las piezas finales.
- Pierna: la diferencia máxima (≈8 mm) no cambia al variar tope/borde de la silueta (probado 22–26 y x 5,0–6,0): parece venir de la posición del pasador de cadera o de su forma (el cliente usa un cilindro Ø6,6), no de la silueta. Pendiente de investigar.
- Los `guemes_*.stl` subidos por el cliente coinciden en triángulos y dimensiones con la salida del motor: son resultado de la herramienta.
- **Torso de Güemes: 149 292 triángulos** (offset Minkowski + barrido de 19 ángulos): STL pesado.
- (Actualización: la decimación reduce o elimina las aristas no-variedad del torso; quedan 2 en cada pierna de Mickey y algunas en brazos/piernas de Güemes.)
- Las piezas son variedad para manifold3d, pero al soldar vértices por posición quedan aristas con != 2 caras
  (brazo: 38; torso: ~1700). Ahora se reportan como `avisos` en la validación. Pendiente: reducir/reparar
  (remallado por SDF + marching cubes, o decimar el barrido con menos pasos/segmentos).

## Pierna de Mickey: desvío de ~8 mm resuelto (2026-10-14)
Causa: no era la silueta ni el pasador. El motor dejaba el extremo de cadera como un bloque plano; el cliente lo recorta a un
**cilindro R = 7,6 mm** (eje X) con centro 3,75 mm por debajo del pasador (tutorial 027-028). Además, midiendo contra la pieza del cliente
(alineando por el pie, que debe coincidir 1:1; el ICP completo se sesgaba):
- pasador de cadera: 1,6 mm más arriba y 0,6 mm hacia atrás que el centroide de la cara; la punta queda a 6,6 mm de la cara **original**
  (el cliente no rebaja rebaba en la pierna -> `burr = 0` por miembro);
- borde interno del pie: superficie natural del STL (x >= 2,7 mm) hasta z ≈ 12 mm; el corte plano en x = 4,95 mm solo rige en la pelvis;
- la cola de Mickey sobraba pegada a la pierna (≈36 mm³): dos recortes (`TAIL_ROOT`, `TAIL_TIP` en `examples/mickey.py`).
Nuevo en el motor: `LimbSpec.cap_radius/cap_center/cap_dir` (extremo redondeado, `limbs.round_end`) y `LimbSpec.burr` (override por miembro).
Resultado (distancia entre mallas, pierna izq.): mediana 0,27 -> **0,14 mm**, p95 1,9 -> **0,33**, máx 8,3 -> **2,9**, volumen 5238 -> 5170 vs 5123.
Sin aristas no-variedad en la pierna; 22 tests pasan.
Residual (máx ≈ 3 mm, solo ~0,5 % de los puntos): forma del pasador (cilindro del cliente vs. hongo achaflanado) y una lámina de ~0,5 mm en el disco.

## Propuesta automática de pasador y recorte de cadera (2026-10-14)
`LimbSpec(auto=True)` (+ `limbs.propose_geometry`): toma el **mayor círculo inscrito en la cara de contacto** (polo de inaccesibilidad).
Brazo: pasador en el centro del círculo. Pierna: el círculo es el recorte cilíndrico del extremo de cadera y el pasador sube R/3 hacia el torso.
Lo que el usuario fija (`pin_center`, `cap_*`) no se pisa. Contra Mickey: círculo a 0,02 mm del cliente en el eje lateral, R 7,40 vs 7,6;
pasador a 0,02 mm del medido. Pierna con `auto=True`: mediana 0,15 mm, p95 0,62, máx 3,0 (ajuste manual: 0,14 / 0,33 / 2,9).
**Ojo:** el cociente 1/3 sale de una sola figura; hay que validarlo con Güemes y Belgrano antes de darlo por bueno.
Quedan manuales: el borde interno del pie y los recortes de la cola (dependen de cada personaje). 23 tests pasan.

## Limitaciones conocidas
- Un solo plano de silueta por miembro; la cara de contacto debe quedar plana y perpendicular a X.
- La cabeza queda unida al torso (fuera de alcance v1).
- Los brazos no tienen barrido (solo la cadera lleva límite de giro).
