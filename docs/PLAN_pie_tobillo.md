# Plan: el pie y el tobillo «como la mano»

_2026-11-27 · solo plan, sin código del motor. Base: `main` **88f0f39** de `fschpp/action_figure_maker_stl`. Responde a P7 de `PLAN_plugins_articulacion.md` (§4.2, §7): **el cliente dijo que el pie puede cortarse como se hace con la mano.** Leído a mano: `hand.py`, `pipeline.py` (mano: líneas ~146–240, 358, 383, 945, 1003–1060, 1419), `parts.py`, `extract.py`, `catalog.py` (`cut_wrist`), `ports.py`, `interface.py`. Contado con `grep` (sin distinguir mayúsculas)._

Marcas: **[V]** verificado en el código · **[I]** inferencia o estimación a validar · **[P]** pendiente de decidir con el cliente.

## 1. Qué hace hoy la mano [V]

La mano **no es un tipo de articulación**: es una función completa, de punta a punta.

| Capa | Qué hay |
|---|---|
| Corte | `WristFrame` (plano de 3 puntos: centro, eje hacia la mano, referencia de giro); `split_at_wrist` separa «la mano» (el lado de menor volumen, o el otro con `invert`) del resto |
| Pieza estándar | Una mano compartida por la línea (`.stl` + `.mano.json`, `afm-mano-1`), en un marco local con la cara plana en z = 0; **no se reescala** |
| Unión | `HandSpec.join`: `solape` (la mano se hunde `overlap` mm y queda soldada) o `giro` (pieza aparte con un pasador de hongo que `fit_hand_pin` **achica solo** hasta que entra con `min_wall`, y una cavidad en el brazo) |
| Motor | `pipeline.build` pega la mano **antes** de cortar los miembros (`apply_hand`) o, en un brazo de catálogo con puerto `muneca`, dentro del bucle de miembros (`attach_hand`); `BuildResult.hand`; pieza `mano_izq/der`; validación `_validate_hand` (pasador que entra, giro libre de 360°, mano cautiva al tirar 1 mm) |
| Catálogo | Clase de puerto `muneca` (`ports.PORT_CLASSES`), tipo de pieza `mano` (`parts.py:47`), `Catalog.cut_wrist` (parte un brazo en brazo sin mano + mano), contrato duro de talles (`interface.HARD_CLASSES`) |
| CLI y GUI | `afm extraer-mano`, `--mano`, `afm catalogo agregar --muneca`; ventanas de la mano en la GUI |

Cuánto está cableado (referencias a «hand/mano/muneca/wrist» por archivo, `grep -ci`): `gui/controller.py` 138 + 152, `pipeline.py` 92 + 50, `props.py` 84 + 81 (accesorios), `hand.py` 83 + 88, `gui/session.py` 55, `catalog.py` 34 + 35, `cli.py` 31 + 61. Tests propios: `test_hand.py`, `test_hand_place.py`, `test_gui_hand.py`, `test_gui_hand_place.py`, `test_catalog_drill_hand.py`.

**Consecuencia:** copiar eso para el pie es una función entera, no un cambio de registro de tipos.

## 2. Dos caminos

| | **A. Tobillo como articulación de la pierna** | **B. Pie estándar compartido (la mano, repetida)** |
|---|---|---|
| Idea | El plano del tobillo parte la pierna en canilla + pie, con un pasador de giro: es el tipo `swivel` en el lugar `tobillo` | Una pieza `pie` estándar por línea (`.stl` + `.pie.json`), pegada en el plano del tobillo del modelo, con `solape` o `giro` |
| Se apoya en | Fase 3 del plan de plugins (varias articulaciones por miembro: rodilla + tobillo) | Generalizar `hand.py` a «extremidad» y hacer en `pipeline`, catálogo, CLI y GUI lo que hoy hay para la mano |
| El pie | Es parte de la figura de origen (se imprime como viene) | Es intercambiable entre figuras de la línea |
| Esfuerzo [I] | **bajo–medio**: añadir `tobillo` a los `slots` de `swivel`, la clase de puerto, y esperar a la Fase 3 | **alto**: 19–30 días (ver §3) |
| Riesgo | Que el pie no quede plano en la cama (pose de impresión) | Tocar el camino de la mano que hoy funciona (R2 del plan de plugins) |

**Recomendación [I]:** A primero. Entra en la Fase 3 sin trabajo extra de diseño, da un tobillo que gira y se imprime, y no bloquea B: el plano de 3 puntos y el corte son los mismos. B se hace después si el cliente quiere pies **intercambiables** entre figuras de la línea (es lo que justifica la pieza estándar).

## 3. Cómo sería B, por etapas [I]

1. **Extremidad (sin cambiar una salida).** `HandSpec` pasa a `EndSpec(kind = "mano" | "pie")`; `HandSpec` queda como alias de `kind="mano"` y `to_dict` solo escribe `kind` si no es «mano» (mismo patrón de «se escribe solo si se usa» de `Params.joints`). `WristFrame`, `split_at_wrist`, `attach_hand` y `fit_hand_pin` reciben el nombre y el pasador base. Criterio: `tools/golden.py comparar todos` idéntico. **4–6 d.**
2. **Pipeline.** `BuildResult.hand` pasa a un diccionario de extremidades (con `hand` como propiedad de lectura); el bucle de miembros pega la mano en el brazo y el pie en la pierna; `_validate_hand` → `_validate_end`; pose de impresión con la planta apoyada. **4–6 d.**
3. **Parámetros y registro.** `Params.foot_pin` (por defecto más robusto que `hand_pin` **[P]**) y el lugar `tobillo` para el tipo `mushroom`, con `fit` automático como la mano. **1–2 d.**
4. **Catálogo.** Clase de puerto `tobillo`, tipo de pieza `pie` (reglas de `parts.py`), `Catalog.cut_ankle` (el equivalente de `cut_wrist`), `extract`, contrato de talles (`HARD_CLASSES`), `capacity`/`ports` para la compatibilidad, claves es/en/pt. **5–8 d.**
5. **CLI.** `afm extraer-pie`, `--pie`, `catalogo agregar --tobillo`. **1–2 d.**
6. **GUI.** Ventanas del pie espejo de las de la mano (la GUI GTK no se puede probar en el entorno de desarrollo, R6). **6–10 d.**

Total B: 21–34 d si se suma todo; 19–30 d con los solapes habituales. Las etapas 1–3 y 5 (motor y CLI, sin catálogo ni GUI) son un primer hito usable: **10–16 d**.

## 4. Lo que se mantiene del plan de plugins

- El corte sigue siendo un plano (`ContactFace`/`PortFrame`): el tobillo es un lugar más.
- Un pie de catálogo con pasador es el tipo `mushroom` con el pasador ajustado (`fit_*_pin`): no hace falta un tipo nuevo, sí el lugar `tobillo` en los `slots`.
- `offer()` y la guía de cortes lo listarán solos una vez que el lugar exista.
- La probeta del tobillo reutiliza la del hongo (`afm probe --tipo hongo`) con un pasador fino.

## 5. Preguntas para el cliente **[P]**

1. **¿A, B o A y después B?** ¿Los pies tienen que ser **intercambiables** entre figuras de la línea (como las manos) o alcanza con que el tobillo gire en cada figura?
2. **¿El pie gira libre (360°) o solo flexiona (planta)?** Un pasador normal al plano gira libre como la mano; si debe balancear adelante/atrás, es una bisagra de rango corto (`hinge`) o una rótula.
3. **¿La planta tiene que quedar apoyada y la figura de pie?** Un tobillo que gira libre deja el pie rotar hacia los lados: ¿se acepta, o hace falta un tope (clic con `detent`)?
4. **Altura de la línea:** ¿los pies estándar se miden a la misma altura de figura que las manos (130 mm, `LINE_HEIGHT_MM`)?

## 6. Qué no se verificó
Ninguna estimación de días [I]; que un pie imprima de pie sobre el pasador (hace falta una probeta); la GUI; los dorados de las figuras reales (necesitan sus STL).
