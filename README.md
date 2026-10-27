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

Catálogo de piezas de una línea (plan `docs/PLAN_piezas_intercambiables_catalogo.md`, fases 0–2):

```bash
afm catalogo crear ~/catalogos/proceres-130 --linea proceres-130 --altura 130
afm catalogo importar-mano ~/catalogos/proceres-130 out/mano/mano.stl   # mano estándar de `afm extraer-mano`
afm catalogo agregar-stl RUTA pierna.stl --tipo pierna --puerto cadera x1 y1 z1 x2 y2 z2 x3 y3 z3
afm catalogo listar RUTA [--tipo mano] [--compatible-con brazo-01]
afm catalogo verificar RUTA
# en la GUI: «Armar desde catálogo…» arma una figura nueva con piezas del catálogo, sin abrir un STL (equivale a `afm armar` con una receta).
# «Armar y exportar» (y `afm armar`) escribe además `<figura>.proyecto`: se abre en la ventana principal SIN el STL (el cuerpo base sale del
# catálogo de la línea y la figura se ve por partes); si el catálogo cambió de carpeta, la ventana pide dónde está.
# Las tareas largas (armar, generar, exportar, abrir STL) muestran una barra con porcentaje, tareas pendientes y tiempo estimado.
afm catalogo eliminar RUTA pierna-belgrano-01@1 brazo-belgrano-01@1 [--buscar DIR] [--si]   # borra versiones (id@n) del disco; avisa quién las usa
```

## Convenciones

* Unidades: mm. Figura normalizada a `ref_height` mm de los pies a la coronilla (100 por defecto; se cambia en la GUI,
  «Parámetros → Altura de la figura» + «Aplicar», que reescala también planos, siluetas y recortes; los pasadores,
  la holgura y el clic son medidas absolutas y no se escalan).
* Ejes: Z arriba, X izquierda-derecha, Y atrás-adelante (frente = +Y).
* Se trabaja el lado **izquierdo (+X)** y se espeja; el torso sale soldado en una sola pieza.
  Si el torso tiene un diseño asimétrico (banda cruzada, condecoraciones de un solo lado), poné
  `torso_mirror = 0` («Parámetros → Torso espejado» en la GUI): el torso conserva ambos lados del STL y solo
  los miembros se espejan (la cabeza ya sale entera del STL).

### Ajustes de proporciones y pelvis de catálogo

Podés conservar el torso del proyecto al importar una pelvis, dividir la figura actual y escalar sus piezas originales
por separado antes de regenerar las uniones. Ver [uso en GUI y CLI](docs/ajustes_piezas.md).
