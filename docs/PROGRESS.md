# Progreso del desarrollo

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

## Descubrimientos (2026-10-14)
- Mickey: las caras de contacto salen del borde vertical (x = const) de la silueta: hombro x≈5,1 y cadera x≈5,45 (STL original), deducido de las piezas finales.
- Pierna: la diferencia máxima (≈8 mm) no cambia al variar tope/borde de la silueta (probado 22–26 y x 5,0–6,0): parece venir de la posición del pasador de cadera o de su forma (el cliente usa un cilindro Ø6,6), no de la silueta. Pendiente de investigar.
- Los `guemes_*.stl` subidos por el cliente coinciden en triángulos y dimensiones con la salida del motor: son resultado de la herramienta.
- **Torso de Güemes: 149 292 triángulos** (offset Minkowski + barrido de 19 ángulos): STL pesado.
- Las piezas son variedad para manifold3d, pero al soldar vértices por posición quedan aristas con != 2 caras
  (brazo: 38; torso: ~1700). Ahora se reportan como `avisos` en la validación. Pendiente: reducir/reparar
  (remallado por SDF + marching cubes, o decimar el barrido con menos pasos/segmentos).

## Limitaciones conocidas
- Un solo plano de silueta por miembro; la cara de contacto debe quedar plana y perpendicular a X.
- La cabeza queda unida al torso (fuera de alcance v1).
- Los brazos no tienen barrido (solo la cadera lleva límite de giro).
