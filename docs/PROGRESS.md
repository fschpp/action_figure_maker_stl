# Progreso del desarrollo

## Current Focus
Fase 1 (repetibilidad): validar la propuesta automática de pasador/recorte (`auto=True`) con Güemes y seguir con Belgrano y Azurduy.
_Última actualización: 2026-10-14 21:10 (America/Argentina/Buenos_Aires). Repo en `e43db74`, 23 tests pasan._

## Todo List
### [ ] Todo
- [ ] Belgrano: STL propio, siluetas y prueba con `auto=True` (sin STL en `examples/` todavía)
- [ ] Azurduy (no estanca): probar remallado por voxelización
- [ ] Propuesta automática de siluetas (§8.3 / §8.5)
- [ ] Usar `orient` en la exportación (CLI) y mostrar voladizos en la GUI
- [ ] GUI GTK4 + visor OpenGL (Fases 2–5)
### [/] In Progress
- [/] Validar el cociente R/3 del pasador de cadera con una segunda figura (Güemes hecho, falta una referencia real)
### [x] Done
- [x] Güemes con `auto=True`: pieza válida, torso 1 componente, holguras 0,474–0,476 mm (manual: 0,476–0,477)

## Latest Blockers/Discoveries
- **Güemes `auto=True` vs manual (2026-10-14):** brazo casi idéntico (mediana 0,13 mm, p95 0,27, máx 0,51; volumen 2311 vs 2311).
  Pierna distinta a propósito: auto agrega el recorte cilíndrico de cadera (R ≈ 6,8 mm) que el ajuste manual de Güemes no tenía
  (mediana 1,14 mm, p95 2,16, máx 5,43; volumen 4368 vs 4321) y sube el pasador de z 52,5 a 54,3.
- **No hay verdad de referencia para Güemes:** sus STL de `examples/` son salida del propio motor, así que no valida R/3 de forma independiente.
  Hace falta una pierna hecha a mano por el cliente (o Belgrano) para confirmarlo.
- Pendiente de arrastre: aristas no-variedad en piezas (2 por pierna de Mickey, algunas en Güemes); torso de Güemes ≈ 93–174 k triángulos.

---

_Plan de referencia: `PLAN_herramienta_stl_articulable.md`_

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
