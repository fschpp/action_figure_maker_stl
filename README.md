# action_figure_maker_stl

Aplicación de escritorio (Python + GTK4) que toma un STL de una sola pieza (figura de 10 cm)
y devuelve una figura **articulada, sentada y armada en una sola impresión** (print-in-place),
al estilo de las Kenner de los 80: torso en una pieza, brazos y piernas atrapados en cavidades
sobre pasadores tipo hongo.

Documentación de diseño: [`docs/PLAN_herramienta_stl_articulable.md`](docs/PLAN_herramienta_stl_articulable.md).
Proceso manual de referencia (Onshape): [`docs/tutorial_onshape.md`](docs/tutorial_onshape.md).

## Estado

Ver [`PROGRESS.md`](PROGRESS.md).

## Instalación (uso)

En Debian / Ubuntu y derivados, con GTK4 de apt:

```bash
sudo apt install python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 libgl1 libegl1
./packaging/instalar.sh
```

Deja el programa en el menú y `afm-gui` / `afm` en `~/.local/bin`. Resumen de una página: [`docs/manual_corto.md`](docs/manual_corto.md).

## Instalación (desarrollo)

```bash
pip install -e ".[dev]"
pytest
```

## Interfaz gráfica

```bash
afm-gui [figura.stl | proyecto.proyecto]
```

Guía de uso: [`docs/tutorial_gui.md`](docs/tutorial_gui.md).

## Uso (motor, sin interfaz)

```bash
afm demo --out out/demo            # genera una figura sintética y la articula
afm build figura.stl proyecto.json --out out/figura
```

## Convenciones

* Unidades: mm. Figura normalizada a `ref_height` mm de los pies a la coronilla (100 por defecto; se cambia en la GUI,
  «Parámetros → Altura de la figura» + «Aplicar», que reescala también planos, siluetas y recortes; los pasadores,
  la holgura y el clic son medidas absolutas y no se escalan).
* Ejes: Z arriba, X izquierda-derecha, Y atrás-adelante (frente = +Y).
* Se trabaja el lado **izquierdo (+X)** y se espeja; el torso sale soldado en una sola pieza.
