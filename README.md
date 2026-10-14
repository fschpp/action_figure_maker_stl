# action_figure_maker_stl

Aplicación de escritorio (Python + GTK4) que toma un STL de una sola pieza (figura de 10 cm)
y devuelve una figura **articulada, sentada y armada en una sola impresión** (print-in-place),
al estilo de las Kenner de los 80: torso en una pieza, brazos y piernas atrapados en cavidades
sobre pasadores tipo hongo.

Documentación de diseño: [`docs/PLAN_herramienta_stl_articulable.md`](docs/PLAN_herramienta_stl_articulable.md).
Proceso manual de referencia (Onshape): [`docs/tutorial_onshape.md`](docs/tutorial_onshape.md).

## Estado

Ver [`docs/PROGRESS.md`](docs/PROGRESS.md).

## Instalación (desarrollo)

```bash
pip install -e ".[dev]"
pytest
```

## Uso (motor, sin interfaz)

```bash
afm demo --out out/demo            # genera una figura sintética y la articula
afm build figura.stl proyecto.json --out out/figura
```

## Convenciones

* Unidades: mm. Figura normalizada a 100 mm de los pies a la coronilla.
* Ejes: Z arriba, X izquierda-derecha, Y atrás-adelante (frente = +Y).
* Se trabaja el lado **izquierdo (+X)** y se espeja; el torso sale soldado en una sola pieza.
