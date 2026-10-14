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

## Pendiente (próximos pasos)
- [ ] Probar con el STL real de Mickey y comparar con las piezas del cliente (criterio Fase 0: ≤0,3 mm).
- [ ] Fase 1: Güemes, Belgrano y Azurduy (Azurduy no es estanca: probar el remallado por voxelización).
- [ ] Propuesta automática de siluetas y centros de pasador (§8.3 / §8.5).
- [ ] Orientación de impresión y voladizos (§8.7).
- [ ] Generador de probetas (§8.11) y perfil de línea (§8.12).
- [ ] Herramienta de recorte manual (§8.8).
- [ ] GUI GTK4 + visor OpenGL (Fases 2–5).

## Limitaciones conocidas
- Un solo plano de silueta por miembro; la cara de contacto debe quedar plana y perpendicular a X.
- La cabeza queda unida al torso (fuera de alcance v1).
- Los brazos no tienen barrido (solo la cadera lleva límite de giro).
