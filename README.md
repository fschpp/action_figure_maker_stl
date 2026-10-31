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

Accesorios para la mano estándar (espadas, fusiles… con empuñadura dentada que encaja en el puño sin pegamento; `src/afm/props.py`):

```bash
afm agarre out/mano/mano.stl --out out/agarre      # taladra el puño y genera una placa de espadas de prueba con 3 dentados
# opciones: --taladro 2.4 --vastago 2.0 --ranura 0.5 --largo-lengueta 8 --dentados 2.7,2.9,3.1
# escribe <mano>_agarre.stl (+ .mano.json con el eje y el Ø del taladro), <mano>_espadas_prueba.stl (acostadas, fondo plano) e informe .json
afm agarre out/mano/mano.stl --catalogo RUTA --id-mano mano-guemes-agarre-01 --id-accesorio espada-prueba-01   # además lo guarda en el catálogo
afm catalogo importar-accesorio RUTA espada.stl --largo-canal 10.1 --taladro 2.4 --dentado 2.9               # un arma propia con su empuñadura
afm catalogo listar RUTA --compatible-con mano-guemes-agarre-01    # qué accesorios se sujetan en esa mano (y al revés)
```

Armas propias (cualquier STL → arma de la línea, con la empuñadura que ya sabemos que encaja; `src/afm/weapon.py`):

```bash
# 1) escalar (--largo MM o --escala F; por defecto 0,40 × la altura de la línea) y 2) cortar el mango original por un plano de 3 puntos
#    (coordenadas del STL original; con el signo igual para que un primer número negativo no se tome por opción) y ponerle la empuñadura
afm catalogo importar-arma RUTA excalibur.stl --corte=44,0,0;44,1,0;44,0,1 --largo 51 --id excalibur-01
# 3) queda como pieza «accesorio» de la línea (con la mano con agarre más reciente del catálogo; --mano REF para otra)
afm catalogo listar RUTA --tipo accesorio
# 4) viaja con la figura: receta con "accesorios": ["excalibur-01"] (afm armar) o el proyecto de la ventana principal
```

En la ventana principal: sección «Arma (accesorio)» (importar el STL, largo/escala, plano de corte con 3 clics, invertir lado, giro, guardar en el
catálogo abierto) y sección «Accesorios que viajan con la figura» (casillas con los accesorios del catálogo). Al exportar, el arma sale como STL
aparte en `piezas/<figura>_<id>.stl` (acostada, lista para imprimir) y TAMBIÉN dentro de `<figura>_ensamblada.stl`, a la derecha de la figura y a
la altura de su cama, con 10 mm de separación; no va en el puño. Los `.proyecto` y las recetas la guardan como referencia al catálogo (ruta + id@versión + sha256).

En el catálogo, la mano con agarre es una pieza `mano` y el arma una pieza `accesorio`; el agarre (Ø del taladro, canal, vástago, dentado…) va en
`mecanismo.agarre` de cada una y `afm catalogo verificar` lo comprueba. La compatibilidad es barata (solo metadatos): Ø del taladro, vástago,
dentado que retiene, deformación de la lengüeta, hombro del dentado contra el largo del canal y contorno de apoyo.

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
# Accesorios que acompañan a la figura: la receta admite «accesorios»: ["espada-prueba-01@1"] (piezas `accesorio` del catálogo; en la GUI,
# sección «Accesorios que acompañan a la figura» de «Armar desde catálogo»). `afm armar` los escribe aparte en piezas/<figura>_<id>.stl (apoyados en la
# cama, mm reales), los suma a <figura>_lista_de_piezas.{json,md} y al informe con su compatibilidad con la mano de la figura; NO van en el
# ensamblado ni en el puño. Un accesorio que «no se sujeta» en esa mano (sin taladro, o Ø distinto) igual la acompaña, con aviso.
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
