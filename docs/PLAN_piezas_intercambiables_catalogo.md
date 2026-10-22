# Plan: piezas intercambiables y catálogo de piezas para una línea

_2026-10-21. Solo plan: no se implementó código. Base: `main` (4c64944) de `fschpp/action_figure_maker_stl`; módulos `hand.py`, `head.py`, `waist.py`, `joints.py`, `limbs.py`, `detent.py`, `pipeline.py`, `project.py`, `params.py`, `profile.py`, `cli.py`. Continúa «mano estándar» (`PROGRESS.md`) y el perfil de línea de `PLAN_herramienta_stl_articulable.md` §8.12 (≈20 personajes: Güemes, Belgrano, Azurduy…)._

## 0. Resumen

Hoy solo la **mano** es una pieza que se comparte entre figuras (STL estándar en marco propio + plano de muñeca + unión). Cabeza, torso, pelvis, brazos y piernas salen **tallados de un único STL por personaje**, y sus cavidades dependen de la forma de la pieza vecina, así que no existen como archivos reutilizables.

Propuesta: generalizar el patrón de la mano.

1. Cada pieza se guarda **cruda** (sin pasadores ni cavidades) con sus **puertos**: marco (centro, eje, referencia de giro), cara de unión, rol (enchufe / toma / soldado) y clase de pasador.
2. Un **catálogo en disco por línea** guarda las piezas, el perfil de la línea (`Params`) y las recetas de cada figura.
3. El motor **extrae** piezas de las figuras ya configuradas (sin clics nuevos), las **coloca alineando puertos** y **genera pasadores, cavidades, clic y cintura al armar**, con los parámetros de la línea (lo que ya hace hoy).
4. La compatibilidad se mide en **dos niveles**: barata (metadatos de los puertos) y completa (`build` + `validate` de la figura, con caché).

Camino incremental (cada fase deja el motor funcionando): extraer sin tocar el motor → ganchos mínimos en `pipeline.build` → reemplazo de piezas en una figura existente (**MVP: llevar las piernas y brazos de Güemes a Belgrano por CLI**) → armado puro desde el catálogo → GUI.

Flujo objetivo del cliente:

1. **Catalogar** las figuras ya configuradas (los planos, siluetas, cuello, cintura y muñeca que ya existen en sus `.proyecto` alcanzan).
2. Marcar qué es **estándar de la línea** (manos, brazos, piernas, pelvis) y qué es **propio del personaje** (cabeza, torso con levita, accesorios).
3. Personaje nuevo: STL propio solo para lo único + **receta** con las piezas estándar.
4. **Armar → validar → exportar** (con lista de piezas).
5. Cambiar una pieza o el perfil de la línea → **regenerar** todas las figuras que la usan.

## 1. Qué hace intercambiable a la mano y por qué las demás piezas no

### 1.1 La receta de la mano (`hand.py`)

1. **Pieza estándar en marco propio**: STL + `<nombre>.mano.json` (`afm-mano-1`); origen en el centroide de la cara del corte, +Z hacia los dedos, +X = referencia de giro (`save_standard_hand` / `load_standard_hand`).
2. **Interfaz explícita**: `WristFrame` (centro, eje, ref) sale de 3 puntos; el punto 1 fija el giro anatómico (lado del pulgar), así que no se ajustan grados a mano.
3. **Sin reescalar** (mm reales): lo que no coincide se informa (`escalon_mm`, tamaño, altura de origen) en vez de corregirse.
4. **Quita lo viejo, pone lo nuevo**: `split_at_wrist` descarta la mano del modelo y `apply_hand` pone la estándar en el mismo marco.
5. **Unión elegida**: `solape` (soldada, se hunde 0,5 mm) o `giro` (pieza aparte con `hand_pin` y cavidad en el brazo; `fit_hand_pin` lo achica hasta que entra).
6. **Se aplica antes de cortar los miembros** (`pipeline.build`, sobre `half`), viaja con el brazo, y `_other_side_half` repite lo mismo en −X si el torso no se espeja.
7. **Vive en el proyecto** (`Project.hand` → `HandSpec`) y se valida (`report["hand"]`: cobertura, escalón, pasador).

Lo que le falta para ser un catálogo: un solo lugar fijo (`Project.hand`), el STL se referencia por una ruta suelta, la interfaz es solo «el plano de muñeca de este modelo» y no hay listado, filtros ni versiones.

### 1.2 Interfaces del motor y qué impide cambiar cada pieza

| Interfaz | Enchufe (lleva el pasador) → toma (recibe la cavidad) | Hoy sale de | Qué impide cambiarla |
|---|---|---|---|
| Cuello | cabeza (`neck_pin`) → torso | `HeadSpec.plane`, `split_head`, `build_head` | ya admite cabeza de reemplazo (`replacement_*`, escala por área de base), pero con lógica propia y fuera de un catálogo |
| Hombro | brazo (`shoulder_pin`, clic opcional) → torso | `LimbSpec`: `separate_limb` + `find_contact_face`; torso tallado con el barrido del brazo | el brazo se corta del cuerpo con un prisma; el torso se talla con ese brazo concreto |
| Cadera | pierna (`hip_pin`, clic opcional) → pelvis o torso | ídem; barrido 0…`sit_angle`+`hip_margin` | ídem; además la pierna **incluye un trozo de cadera** (silueta de Güemes: `x > 7` hasta `z = 58`) |
| Cintura | pelvis (`waist_pin`, `pin_on`) → pecho | `WaistSpec.plane`, `build_waist` parte el torso **ya tallado** | el corte se hace sobre el torso entero |
| Codo / rodilla | distal (lengüeta + 2 bulones) → proximal | `JointSpec`, `build_joint` parte un miembro ya separado | depende del miembro previo |
| Muñeca | mano (`hand_pin` o soldada) → antebrazo | `HandSpec` | **ya intercambiable** |

Dos dependencias lo impiden: **(a) el corte** (las piezas no existen como archivo: salen de `half ∩ prisma`, del plano del cuello o del plano de cintura) y **(b) la cavidad** (la toma se talla con `dilate(miembro + pasador)` barrido por los ángulos, así que el torso depende del miembro y al revés).

La (b) **no hay que resolverla guardando cavidades**: ya se recalcula en cada armado. Basta guardar piezas crudas y sus puertos.

## 2. Principios de diseño

- **P1. «Intercambiable» = reutilizar el diseño entre figuras de la línea.** Cada figura se imprime armada (print-in-place), y las piezas con toma (torso, pelvis, antebrazo) cambian de archivo final según los enchufes que reciban. No se promete cambiar piezas *después* de imprimir (pide un conector desmontable): decisión D1.
- **P2. La pieza guardada es cruda**: sin pasadores, cavidades, clic ni lengüetas. Los recortes manuales (`Trim`), la silueta, el 2.º corte, `cap_*`, `burr` y `pin_center` se aplican al extraer y quedan horneados. Cambiar la holgura o los pasadores del perfil regenera todo sin tocar el catálogo.
- **P3. Puertos**: ensamblar = alinear marcos de puertos (mismo centro y referencia, ejes opuestos). Generaliza `WristFrame`.
- **P4. Un solo camino de armado**: la figura es «huésped + reemplazos»; el armado puro es un huésped sintetizado con el torso/pelvis del catálogo. No hay dos pipelines.
- **P5. Mm reales, sin reescalar** (decisión ya tomada con la mano). Cada pieza declara `altura_figura_mm`; si difiere de la de la línea, aviso.
- **P6. Compatibilidad en dos niveles**: barata (metadatos) y completa (la figura real).
- **P7. Las figuras actuales no cambian**: pruebas doradas antes de tocar `pipeline.py`.

## 3. Modelo de datos

### 3.1 Puerto (`ports.py`, nuevo)

`PortFrame` generaliza `WristFrame` (que queda como alias): `center`, `axis` y `ref`. Convención única: **`axis` sale de la pieza hacia su vecino** (como `ContactFace.outward`); dos puertos se acoplan con `axisA = −axisB`. La mano guardada hoy (+Z hacia los dedos) se convierte al cargarla.

| Puerto | Enchufe → toma | `ref` (giro) | Clase de pasador (`Params`) | Movimiento |
|---|---|---|---|---|
| `cuello` | cabeza → torso | +Y (frente) | `neck_pin` | giro libre |
| `hombro` | brazo → torso | +Z | `shoulder_pin` (+ `shoulder_detent`) | 0° o vuelta completa con clic |
| `cadera` | pierna → pelvis o torso | +Z | `hip_pin` (+ `hip_detent`) | 0…`sit_angle`+`hip_margin` |
| `cintura` | pelvis → pecho (`pin_on` configurable) | +Y | `waist_pin` | ±`waist_twist` |
| `muneca` | mano → brazo | punto 1 (lado del pulgar) | `hand_pin` o soldada | giro libre |
| `codo`, `rodilla` | internos a la pieza (`JointSpec` en su marco); entre piezas, más adelante | línea del miembro | `hinge_pin` | `flex_range` |

Cada puerto guarda además la **cara de unión** como polígono en coordenadas (u, v) del puerto, con su área y radio equivalente, y el `barrido_deg` si el miembro lo declara (`LimbSpec.sweep_deg`).

### 3.2 Tipos de pieza

Lado de trabajo +X (izquierda); la derecha sale por espejo, como hoy. Las piezas con diseño asimétrico se marcan `espejable: false` y llevan par izquierdo/derecho propio (D7).

| Tipo | Puertos | Contenido crudo |
|---|---|---|
| `cabeza` | `cuello` (enchufe) | cabeza sin pasador (hoy `HeadSpec.replacement_path`) |
| `torso` | `cuello`, `hombro`×2, `cadera`×2 (tomas) | cuerpo **sin miembros ni cabeza**, **entero** (ambos lados: cubre `torso_mirror = False`) |
| `torso_sup` + `pelvis` (variante con cintura) | `torso_sup`: `cuello`, `hombro`×2, `cintura`; `pelvis`: `cintura`, `cadera`×2 | el torso cortado por el plano de cintura, sin la holgura |
| `brazo` | `hombro` (enchufe), `muneca` (toma), `codo` (interno, opcional) | miembro **sin mano** si la línea usa mano estándar |
| `pierna` | `cadera` (enchufe), `rodilla` (interno, opcional) | miembro con su trozo de cadera |
| `mano` | `muneca` (enchufe) | la mano estándar de hoy |
| _(después)_ `antebrazo`, `pantorrilla`, `bota`, `accesorio` | `codo`, `rodilla`, `muneca`/`agarre` | piezas más finas de un miembro |

### 3.3 Catálogo en disco (`catalog.py`, nuevo)

```
<catalogo>/<linea>/
  linea.json                        # afm-linea-1: nombre, altura_mm, boquilla, params (perfil), versión del motor
  piezas/<tipo>/<id>@<n>.stl            # malla cruda, mm reales, en el marco de la figura de origen (Z arriba, +Y frente, x = 0 simetría); <n> = versión
  piezas/<tipo>/<id>@<n>.pieza.json     # afm-pieza-1 (Apéndice A)
  figuras/<personaje>.receta.json   # afm-receta-1: qué pieza va en cada lugar
  compat.json                       # caché de compatibilidad completa (generado)
  CATALOGO.md                       # índice legible (generado)
```

- **Ruta**: `--catalogo RUTA` o `AFM_CATALOGO`; por defecto `~/.local/share/afm/catalogos/<linea>/` (mismo estilo que `profile.profiles_dir()`). El `.proyecto` guarda el nombre de la línea y el id, no rutas absolutas.
- **ID**: `<tipo>-<descriptor>-<nn>` (p. ej. `pierna-botas-01`). **Inmutables**: cambiar una pieza crea `version` + 1; las recetas fijan `id@version` y el motor avisa si hay una más nueva. Se guarda `sha256` del STL.
- **Estado**: `borrador` → `validada` → `impresa`. **Etiquetas**: `estandar` / `propia`.
- **Perfil de la línea** dentro de `linea.json` (reutiliza `Params.to_dict`): ante una diferencia con los parámetros del proyecto (holgura, pasadores, `ref_height`) gana el de la línea y se avisa.

### 3.4 Proyecto y receta

- `Project.parts: dict[str, PartRef]` con `PartRef(linea, id, version, yaw, invert, join, overlap)`. `Project.hand` se migra a `parts["mano"]` al cargar (los `.proyecto` viejos cargan igual); el campo viejo se retira en la Fase 8.
- `figura.receta.json` (armado puro): `lugares` (`cabeza`, `torso`, `pelvis` o `null`, `brazo`, `pierna`, `mano`), `opciones` (`cintura`, clic de hombro y cadera, `torso_mirror`) y `ajustes` por lugar (giro y unión de la mano).

## 4. Cambios en el motor

**Reutiliza** (sin reescribir): `WristFrame`/`split_at_wrist` (corte por plano), `separate_limb`/`find_contact_face`/`_face_shape` (miembros y caras), `split_head`/`neck_plane`, `waist_plane`/`build_waist`, `build_joint`/`JointSpec`, `mushroom_pin` y los barridos, `limbs.pin_fit`/`pin_margin`, `MIN_BASE_IOU` (0,80) y `STEP_WARN_MM` (1,0), `profile.py`, `testfigure.make_doll`, `tools/compare_pieces.py`.

### 4.1 Extraer (`parts.py`, nuevo)

`extract_all(body, project, p)` corre solo la etapa de corte de `build` y devuelve las piezas crudas con sus puertos:

- **brazos y piernas**: `separate_limb` → miembro + `ContactFace` (puerto `hombro`/`cadera`: centro = `face.center`, eje = `face.outward`, cara = `_face_shape`). El brazo sale **sin mano** si el proyecto tiene `hand` (puerto `muneca` desde sus 3 puntos) o se indica `--muneca`; si no, conserva su mano y no admite reemplazo.
- **cabeza**: `split_head(...).head` + puerto `cuello`.
- **torso** = `half` menos los prismas de los miembros y la cabeza, entero (reutiliza `_other_side_half` si `torso_mirror = False`). Con cintura, se parte por `waist_plane` en `torso_sup` y `pelvis`.
- **mano**: `extract_standard_hand` (existe).

El torso crudo más el barrido del miembro puesto en su lugar da el mismo torso de hoy: la toma original vive dentro del prisma del miembro y la holgura queda fuera de él.

### 4.2 Colocar (`assembly.py`, nuevo)

`place_part(part, port, target_frame)` calcula la transformación rígida que lleva el puerto de la pieza al marco objetivo (ejes opuestos, mismo centro y referencia). Sin escala. Se transforman también `ContactFace`, `JointSpec` internos y el `barrido_deg`. El lado derecho sale con `S.mirror_x`, como el resto.

### 4.3 Ganchos mínimos en `pipeline.build` (sin reordenar lo existente)

Parámetro nuevo `parts: dict[str, PlacedPart] | None = None`; con `None` el resultado es idéntico al de hoy.

1. **Huésped sintetizado**: si no hay `body`, `body` = torso crudo (∪ pelvis, soldadas por la cara de cintura y re-partidas por `build_waist` con el `WaistSpec` armado desde el puerto: se reutiliza el código de cintura tal cual).
2. **Miembro de catálogo**: en el `for spec in limbs`, `separate_limb(half_nocav, spec, p.burr)` se reemplaza por el miembro puesto. Antes del bucle, si el huésped tiene su propio miembro, se le quita (`half − limb_prism(spec)`). Desde ahí el flujo es el de hoy: pasador, `DT.build_detent_limb`, barrido y tallado del torso. `LimbSpec` mínimos (nombre, `kind`, `mechanism`, `sweep_deg` y un plano válido para `check_limb_plane`) se generan desde los metadatos.
3. **Mano**: `apply_hand` sobre el brazo ya puesto, con el puerto `muneca` del brazo en lugar de 3 puntos (`HandSpec` admite un marco).
4. **Cabeza**: `HeadSpec.replacement_path` puede ser un id de catálogo (compatibilidad hacia atrás); conserva sus controles (orientación, escala por base, corte).
5. **Articulaciones internas**: los `JointSpec` de la pieza, transformados, se agregan a `joints`.
6. `report["piezas"][lugar]` = `{id, version, sha256, origen, compat}`.

`flat_for_print`, `orient`, `posed_pieces` y `validate` consumen `res.pieces`, `res.motion` y `res.report` con los mismos nombres (`torso`, `pelvis`, `brazo_izq`…), así que no deberían cambiar; se verifica con pruebas.

### 4.4 Compatibilidad

**Barata** (solo `.pieza.json`, sin cargar mallas; instantánea), para cada par enchufe/toma:

- misma **clase** de puerto (si no, `no`);
- **IoU de las caras** alineadas por centro y `ref` (mismo criterio que `coincidencia_base` de la cabeza; < 0,80 → aviso);
- **escalón** = |radio equivalente A − B| (> 1 mm → aviso, como la mano);
- **margen del pasador** en ambas caras (`pin_fit` ≥ `min_wall`; vástago ≥ `min_pin_d`);
- **altura de figura** igual (±0,5 mm).

Resultado `ok` / `aviso` / `no`. Alimenta los filtros del CLI y de la GUI.

**Completa**: `build` + `validate` de la receta o la combinación; se guarda en `compat.json` con clave = (ids y versiones, hash de `Params`, versión del motor). Valida lo que ya existe (holgura, retención, giro libre en cadera/cintura/cuello, pared del pasador, `_validate_hand`) y suma una sección `piezas` en `validate`. La completa se hace solo sobre las figuras que se imprimen; no sobre todas las combinaciones.

### 4.5 Trazabilidad

`export` suma `<nombre>_lista_de_piezas.json` y `.md` (lugar → id@version, sha256, origen, resultado de compatibilidad). `afm catalogo usos <id>` busca en recetas y proyectos qué figuras la usan, para saber qué regenerar al cambiarla.

## 5. CLI y GUI

**CLI** (`afm`, subcomando nuevo `catalogo`):

```
afm catalogo crear RUTA --linea NOMBRE --altura 130 --boquilla 0.4 [--perfil NOMBRE]
afm catalogo agregar RUTA figura.stl figura.proyecto [--lugares todos|brazo,pierna,…] [--prefijo guemes] [--muneca x1 y1 z1 x2 y2 z2 x3 y3 z3]
afm catalogo agregar-stl RUTA pieza.stl --tipo mano --puerto muneca x1 y1 z1 x2 y2 z2 x3 y3 z3
afm catalogo listar RUTA [--tipo pierna] [--compatible-con torso-guemes-01]
afm catalogo verificar RUTA            # formato, hash, mallas cerradas, puertos, altura
afm catalogo usos RUTA ID
afm catalogo importar-mano RUTA mano.stl   # migra afm-mano-1
afm build fig.stl fig.proyecto --catalogo RUTA --pieza pierna=pierna-botas-01 --pieza mano=mano-01
afm armar RUTA receta.json --out out/fig [--sin-soportes MM]
```

`afm extraer-mano` y `--mano` siguen funcionando (por dentro usan el catálogo).

**GUI** (sin GTK en el entorno de desarrollo: lógica en `Controller`/`Session` con pruebas sobre `FakeScene`, como el resto):

- sección «Piezas de catálogo» junto a «Mano estándar»: ruta del catálogo, botón **Catalogar esta figura…** (usa los planos y siluetas ya definidos), y una fila por lugar (original / pieza) con la etiqueta de compatibilidad (`ok`/`aviso`/`no`, IoU, escalón);
- vista previa con un color por lugar (hoy: mano turquesa, cabeza naranja, extraída verde);
- **Definir puerto (3 puntos)** para importar una pieza suelta (reutiliza el patrón de `Mode.HAND_EXTRACT`).

## 6. Fases

> **Estado (2026-10-21):** Fase 0 (herramientas `tools/golden.py` y `tools/medir_interfaces.py`, dorados en `tests/golden/`), Fase 1 (`ports.py`, `parts.py`) y Fase 2 (`catalog.py`, `afm catalogo crear/listar/verificar/usos/importar-mano/agregar-stl/estado`) implementadas en la rama `piezas-intercambiables`. Desvíos del plan: los archivos de pieza llevan la versión en el nombre (`<id>@<n>.*`) para que las versiones sean inmutables y convivan; las caras se guardan como `poligonos` (contorno + agujeros, en (u, v) del puerto); `WristFrame` no se reemplazó por un alias (`PortFrame.from_wrist` lo convierte) para no tocar el motor antes de los dorados. `catalogo/ejemplo` (muñeco sintético) ya está en el repo.
>
> **Estado (2026-10-22):** Fase 3 implementada (`src/afm/extract.py`, `afm catalogo agregar`). Desvíos: (a) el torso crudo es `half − miembro` (el miembro tal cual salió de `separate_limb`, con rebaba y `cap_*`), no `half − prisma`: restándole luego el barrido da el mismo torso que `pipeline.build`; (b) donde el miembro roza una arista del torso (la cadera de Güemes) la malla soldada tiene una arista de 4 caras; el motor solo lo avisa, el catálogo exige mallas estancas, así que se cura con pymeshfix (Δvolumen < 0,05 %) y, si no alcanza, se resta el miembro ensanchado 2 µm / 30 µm / 0,1 mm; (c) el puerto `cuello` del torso es opcional (figuras sin plano del cuello llevan la cabeza integrada); (d) el centro del puerto es el centro del pasador (`pin_center`), no el centroide de la cara: por eso el IoU entre figuras (≈0,5–0,7) es menor que el de `MEDIDAS_interfaces.md` (centroides) y conviene mirar las dos cosas antes de decidir D3; (e) `compatible_with` ignora los puertos `_der` de piezas espejables (son el `_izq` espejado). Extraídas y verificadas con las figuras reales: Güemes, Belgrano y Azurduy (brazo, pierna y torso; sin cabeza ni mano porque sus proyectos no definen cuello ni muñeca).

**Fase 0: línea base y medición (sin cambiar comportamiento).**
- Congelar **resultados dorados** de `make_doll` y de Güemes, Belgrano, Azurduy y Mickey (volumen por pieza, holguras, claves de `report`, `validate.ok`) en `tests/golden/` con una herramienta `tools/golden.py`.
- `tools/medir_interfaces.py`: por figura y puerto, centro, eje, área y radio de la cara, margen del pasador y alcance del trozo de cadera; y la comparación entre figuras (IoU, escalón). Salida: `docs/MEDIDAS_interfaces.md` y `.json`.
- Con eso se deciden D3 y D4 y los umbrales de §4.4.

**Fase 1: puertos y piezas.** `ports.py` (`PortFrame`, `Port`, `port_compat`), `parts.py` (lectura y escritura de `afm-pieza-1`), lector del `afm-mano-1` viejo. Prueba: ida y vuelta con el muñeco; la mano vieja y la nueva dan el mismo marco.

**Fase 2: catálogo en disco y CLI básico.** `catalog.py`, `afm catalogo crear/listar/verificar/usos/importar-mano`, `linea.json` con el perfil, `CATALOGO.md`. Prueba: inmutabilidad de versiones, hash, `catalogo/ejemplo` sintético (muñeco) en el repo.

**Fase 3: extraer.** `extract_all`, `afm catalogo agregar` y `agregar-stl`. Prueba: catálogo del muñeco y de Güemes (con `examples/referencia/guemes/guemes.proyecto`); cada pieza es una malla cerrada; recolocada en sus propios puertos reproduce la figura de origen.

**Fase 4: ganchos en `pipeline.build`.** §4.3 completo, sin `Project.parts` todavía. Prueba: con `parts=None`, dorados idénticos; con una pierna del muñeco puesta en su propio puerto, igual a la directa.

**Fase 5: reemplazo en una figura existente (MVP).** `Project.parts`, migración de `Project.hand`, `afm build --catalogo --pieza`, sección `piezas` en `validate`, informe de compatibilidad. Prueba: muñeco con pierna y brazo cambiados; Belgrano con las piernas y los brazos de Güemes (prueba lenta, marcada).

**Fase 6: armado puro.** Huésped sintetizado, cintura por soldadura y repartición, `JointSpec` transformados, recetas, `afm armar`, `torso_mirror = False`. Prueba: figura armada solo con piezas de catálogo ≈ la directa.

**Fase 7: compatibilidad completa y trazabilidad.** `compat.json`, lista de piezas, `usos`, informe de matriz (`docs/compatibilidad.md` generado).

**Fase 8: GUI.** §5. Retira `Project.hand`.

**Fase 9: impresión, calibre y documentación (con el cliente).** Imprimir 2–3 combinaciones cruzadas con el perfil de la línea; **calibre de puerto** (par de probetas por clase de pasador, extensión de `probes.py`); actualizar `docs/tutorial_gui.md`, `manual_corto.md`, `README.md` y `PROGRESS.md`.

**Fase 10 (opcional): piezas más finas** (antebrazo, pantorrilla, bota) y **accesorios** sobre un puerto `agarre`.

Orden recomendado: 0 → 5 (MVP por CLI, valida la idea con las figuras que ya hay) → 6 → 7 → 8. Las Fases 1–3 pueden avanzar en paralelo con la 4.

## 7. Criterios de aceptación

1. **Regresión**: con `parts=None`, Güemes, Belgrano, Azurduy, Mickey y el muñeco coinciden con los dorados (volumen por pieza ±0,01 %, mismas claves de `report`, mismo `validate.ok`). Los tests de mano y de GUI de mano siguen en verde.
2. **Ida y vuelta**: catalogar Güemes y armarlo solo con piezas de catálogo da cada pieza a < 0,05 mm de la directa (mediana, `tools/compare_pieces.py`), mismo `validate.ok` y holgura mínima ≥ 0,9·`clearance`.
3. **Cruce**: una figura con piernas de A, brazos de B y torso de C (las tres de ejemplo) no tiene `problems` en `validate`; los avisos (escalón, IoU) quedan listados en el informe; giro libre en cadera (0…`sit_angle`+`hip_margin`), cintura (±`waist_twist`) y cuello; la pieza sigue retenida al desplazarla ≥ 1 mm.
4. **Mano**: la del catálogo equivale a la del `.mano.json` viejo.
5. **Simetría**: la derecha sale por espejo; con `torso_mirror = False` el torso del catálogo conserva su asimetría.
6. **Catálogo**: `afm catalogo verificar` limpio (hash, mallas cerradas, ≥ 1 puerto por pieza, polígonos de cara válidos); no se puede reescribir una versión existente; los `.proyecto` viejos cargan.
7. **Compatibilidad barata**: no da `ok` a un par que la completa rechaza con `problems` de pasador o pared (probar con los pares de las tres figuras de ejemplo).
8. **Tiempo**: armar una figura de catálogo ≤ 1,2 × el `build` actual de la misma figura.
9. **Impresión** (cliente): 2 combinaciones cruzadas impresas giran libres y no se rompen.

## 8. Riesgos y decisiones abiertas

### Decisiones (con recomendación)

| # | Decisión | Recomendación |
|---|---|---|
| D1 | ¿Intercambio **en el diseño** (cada figura se imprime armada) o **físico** (cambiar piezas ya impresas)? | En el diseño. Lo físico pide un conector desmontable (clip, rosca, imán): plan aparte. |
| D2 | Granularidad inicial | Cabeza, torso, pelvis, brazo entero, pierna entera y mano. Antebrazo, pantorrilla y bota después (Fase 10). |
| D3 | Cortes de cintura y cadera: ¿por figura (hoy) o **estándar de la línea**? | Por figura con métricas; decidir tras la Fase 0, según cuánto difieran las caras de Güemes, Belgrano y Azurduy. |
| D4 | Altura y proporciones | Una línea = una altura (130 mm en la mano estándar), mm reales, sin reescalado automático. ¿Hay variantes de talle? |
| D5 | ¿Dónde vive el catálogo? | Fuera del repo: los STL generados no se commitean (la salida de Güemes pesa ≈16 MB, ver `examples/referencia/guemes/README.md`). En el repo, solo `catalogo/ejemplo` sintético. |
| D6 | Escala de la cabeza | Mantener la de hoy (por área de base); opción `escala: ninguna` si el cuello pasa a ser estándar. |
| D7 | Piezas asimétricas (brazo con espada, torso con banda) | `espejable: false` + par izquierdo/derecho; el torso se guarda entero. |
| D8 | ¿Qué es estándar y qué es propio? | Propuesta inicial: estándar = mano, brazos, piernas, pelvis; propio = cabeza, torso, accesorios. A confirmar. |
| D9 | Nombres de pieza y estados | Convención de §3.3; confirmar los nombres que usa el cliente. |

### Riesgos

1. **Las caras no coinciden entre figuras** (escalón, IoU bajo, el trozo de cadera de una pierna no calza con otra pelvis): se mide en la Fase 0, se avisa en `validate` y, si hace falta, se adopta un corte estándar (D3).
2. **Regresiones por tocar `pipeline.build`** (1300 líneas, `validate`, `flat_for_print`): ganchos mínimos con `parts=None` idéntico, dorados antes de cada fase y sin reordenar el flujo.
3. **Tiempo de cómputo**: `build` + `validate` tardan minutos; la matriz completa es inviable → compatibilidad barata por metadatos, completa solo para figuras reales, caché y muñeco sintético en los tests.
4. **Tomas que cambian por enchufe**: el torso o la pelvis finales dependen de las piernas y brazos puestos. Se versiona por **figura + receta**, no solo por pieza; la lista de piezas lo documenta.
5. **Silueta externa**: cambiar una pierna puede cambiar la cadera visible; revisar en la vista previa (color por lugar).
6. **Mano sobre un brazo de catálogo** exige el puerto `muneca`; si falta, hay que dar 3 puntos como hoy (`--muneca`).
7. **Clic, cintura y lengüetas** (`detent.add_fingers`, `protrusion_mm3(dl, body, …)`) usan el `body` original: en armado puro es el huésped sintetizado; verificar en la Fase 6.
8. **Perfil de línea vs. proyecto**: si difieren, gana la línea y se avisa; `ref_height` distinto es error.
9. **Peso de los STL en git**: ver D5; si se quiere versionar, Git LFS o repo aparte.

### Fuera de este plan

Tornillos y tuercas, conectores desmontables, reescalado automático de piezas, rediseño del clic y nuevas probetas (salvo el calibre de puerto de la Fase 9).

## Apéndice A: formatos

`afm-pieza-1` (`<id>.pieza.json`), ejemplo con los números de la cadera de Güemes de `examples/referencia/guemes/README.md`:

```json
{
  "formato": "afm-pieza-1",
  "id": "pierna-guemes-01", "version": 1, "tipo": "pierna",
  "descripcion": "Pierna de Güemes con bota; incluye trozo de cadera",
  "origen": {"figura": "guemes_segmentado_lq_fixed_resized.stl", "proyecto": "guemes.proyecto",
             "motor": "<versión>", "fecha": "2026-10-21"},
  "altura_figura_mm": 130.0,
  "lado": "izq", "espejable": true,
  "stl": "pierna-guemes-01@1.stl", "sha256": "<hash>", "volumen_mm3": 0.0, "bbox": [[0, 0, 0], [0, 0, 0]],
  "puertos": {
    "cadera": {
      "clase": "cadera", "rol": "enchufe",
      "centro": [7.66, -1.27, 52.48], "eje": [-1, 0, 0], "ref": [0, 0, 1],
      "cara": {"area_mm2": 146.3, "radio_eq_mm": 6.82, "poligonos": [{"exterior": [[0.0, 0.0]], "interiors": []}]},
      "barrido_deg": [0, 95]
    }
  },
  "articulaciones": [],
  "mecanismo": null,
  "estado": "borrador", "etiquetas": ["estandar"]
}
```

`afm-linea-1` (`linea.json`):

```json
{"formato": "afm-linea-1", "nombre": "proceres-130", "altura_mm": 130.0, "boquilla": 0.4,
 "params": {"clearance": 0.5, "ref_height": 130.0},
 "lugares": ["cabeza", "torso", "pelvis", "brazo", "pierna", "mano"], "motor": "<versión>"}
```

`afm-receta-1` (`<personaje>.receta.json`):

```json
{"formato": "afm-receta-1", "personaje": "guemes", "linea": "proceres-130",
 "lugares": {"cabeza": "cabeza-guemes-01@1", "torso": "torso-guemes-01@1", "pelvis": null,
             "brazo": "brazo-estandar-01@2", "pierna": "pierna-estandar-01@1", "mano": "mano-estandar-01@1"},
 "opciones": {"cintura": false, "clic_hombro": false, "clic_cadera": false, "torso_mirror": true},
 "ajustes": {"mano": {"yaw": 0, "union": "giro"}}}
```
