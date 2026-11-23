# Plan: color por roles con sombreado («Conservar sombreado»)

**Versión:** 1.0 — 2026-11-23
**Repo:** `fschpp/action_figure_maker_stl`, sobre `main` `faf90ee`.
**Continúa:** `docs/PLAN_paleta_color_linea.md` (roles, atlas de etiquetas, `afm-color-roles-1`) y `docs/PLAN_orientacion_color_cabeza.md`.
**Marcas:** **[C]** dicho por el cliente · **[V]** verificado en el código · **[I]** inferido, a validar.

## 1. Problema

Una cabeza de catálogo sacada de un busto con FBX llega **discretizada en roles**: cada rol sale como el color plano de su hex en la paleta. Se pierde la luz y la
sombra horneadas de la textura (el plan de la paleta midió ΔE 8 contra 4,3 con 8 tonos en Güemes, y la vista previa se ve «chata»). **[V]**
El modo `rgb` (FBX entero, ~26 MB) conserva el aspecto pero pierde el recoloreo por paleta y no tiene entrada de interfaz. **[V]**

## 2. Idea

El paquete guarda, además del atlas de etiquetas, un **atlas de sombreado** de 8 bits, del mismo tamaño: el brillo de cada píxel respecto del color medio de su rol.
Al armar, el color de cada punto es **el hex del rol × ese factor**. **[C]** (propuesta aceptada)

- `factor = luminancia del píxel / luminancia media del rol` (Rec. 709 sobre sRGB 0–255), acotado a **0,5 – 1,5**; código uint8 con 128 = ×1,0.
- La luminancia media del rol es la de sus clusters **ponderada por el área de superficie que cubren** (`Proposal.share`), no por píxeles del atlas (que incluye
  el fondo sin usar).
- Un rol casi negro (luminancia < 8) y los píxeles «sin rol» quedan en ×1,0: el cociente ahí mide ruido, no luz.
- **Un solo factor para los tres canales** (el matiz del hex no cambia) y, al multiplicar, el factor se limita a lo que deja el canal más alto sin pasar de 255
  (`texmesh.apply_shade`): un tono claro no se quema hacia el blanco ni cambia de matiz. Esto resuelve el riesgo de «piel clara que sale azul» al recolorear:
  el sombreado solo cambia el brillo.
- Cambiar el hex de un rol sigue recoloreando todas las piezas que lo usan, y la luz se conserva.
- Una pincelada de retoque que cambia el rol de una muestra le deja su factor (la luz es del lugar, no del rol). Un punto «sin origen» pintado a mano sale plano (×1,0).

## 3. Formato y compatibilidad

| Caso | Archivo |
|---|---|
| Sin sombreado (por defecto de la API y la CLI) | `afm-color-roles-1`, idéntico byte a byte al de antes |
| Con sombreado | `afm-color-roles-2`: lo mismo + array `shade` (uint8, forma de `labels`) |

- `RolesPackage.load` lee los dos. Un `-2` sin `shade` se rechaza; un `-1` que trae un `shade` de más se lee como `-1` (formato viejo).
- Los paquetes viejos siguen funcionando igual: `shade is None` recorre exactamente el mismo camino de antes.
- La ficha de la pieza anota `color.texturas.sombreado: true`. Peso: unas decenas de KB más por pieza (la prueba exige < 200 KB en un atlas de prueba).

## 4. Dónde toca el motor

| Módulo | Cambio |
|---|---|
| `texmesh.py` | `TexturedMesh.shade`; `sample_shade` (bilineal, es continuo); `sample` modula `lut[etiqueta]`; `apply_shade`, `shade_factor`, `shade_code`, `sample_plane` |
| `colorroles.py` | `RolesPackage.shade`; `build_package(shade=True)` + `shade_atlas`; `save`/`load` formato `-2`; `with_roles`, `prune_small_roles` y `to_textured` lo llevan (el PNG de vista previa sale sombreado) |
| `colorsrc.py` | `ColorSource.sample_shade`; `_compose` (usado por `recolor` y `paint`); `ColorQuery.shade` |
| `colorbake.py` | `BakeResult.shade` (atlas de factor por texel, dilatado igual que las etiquetas) |
| `colorexport.py` | `Colorizer.recolor` rehace `lut[etiquetas] × factor`: sigue siendo igual, píxel por píxel, a volver a hornear |
| `colorpiece.py`, `colorwarp.py` | `_load_roles`, `package_from_fbx` y `warp_package` conservan el atlas |
| `catalog.py` | `color.texturas.sombreado` en la ficha |
| `gui/roles_wizard*.py` | casilla **«Conservar sombreado»** (activada por defecto en la ventana) → `build_package(shade=…)`; la vista «roles» del asistente muestra el sombreado; la prueba en una pieza se invalida si se cambia la casilla |
| `gui/palette_editor.py` | la vista previa del editor de paleta aplica el sombreado |
| `catalog_cli.py` | `afm catalogo color … --sombreado` |
| `locales/*.json` | `roles_wizard.conservar_sombreado*`, `color_cli.ayuda_sombreado` (es/en/pt) |

## 5. Pruebas

`tests/test_color_shade.py` (16): atlas del mismo tamaño y acotado; sigue la luz y promedia ~1 por rol; un rol plano no se sombrea; sin `shade` el paquete es el
de antes; roles casi negros y «sin rol» en ×1,0; ida y vuelta del `-2`; compatibilidad con un `-1` escrito por la versión anterior; rechazo de `-2` sin `shade`;
`with_roles`/`prune_small_roles`; `apply_shade` conserva el matiz; recoloreo por paleta conserva la luz; `ColorSource.recolor` = fuente nueva;
horneado + `recolor` = volver a hornear.

## 6. Pendiente (no hecho)

- **[P]** Probar con el busto de Bolívar y la cabeza de Güemes **reales** y comparar contra la textura original (ΔE medio): en este entorno no están los FBX, solo
  se probó con texturas sintéticas. El criterio de aceptación es bajar el ΔE medio de Güemes de ~8 hacia ~4 o menos con el sombreado activado.
- **[P]** Probar a mano la casilla en la ventana GTK (sin GTK en este entorno): que cambiarla recalcule la vista «roles» y descarte la prueba en pieza.
- **[I]** Suavizar el atlas de sombreado (hoy es por píxel, sin filtro) si en una cabeza real se ve granulado; el rango acotado ya evita los extremos.
- **[I]** Si el clamp 0,5–1,5 se queda corto en pelo muy oscuro con brillos, subir `SHADE_MAX` o hacerlo por rol.
