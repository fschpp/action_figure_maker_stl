# Action Figure Maker — manual corto

Convierte el STL de una figura de una sola pieza en una figura **articulada, sentada y lista para imprimir armada**
(torso en una pieza; brazos y piernas atrapados en cavidades sobre pasadores tipo hongo). Guía completa de la
ventana: [`tutorial_gui.md`](tutorial_gui.md).

## Instalar (Debian / Ubuntu)

```bash
sudo apt install python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 libgl1 libegl1
git clone https://github.com/fschpp/action_figure_maker_stl && cd action_figure_maker_stl
./packaging/instalar.sh
```

Queda el programa **Action Figure Maker** en el menú y los comandos `afm-gui` y `afm` en `~/.local/bin`.
`./packaging/instalar.sh --dry-run` muestra lo que haría sin tocar nada. Para actualizar: `git pull` y volver a correr el script.
Para desarrollo: `pip install -e ".[dev]"` y `pytest`.

## Una figura nueva, paso a paso

Abrí el programa (`afm-gui figura.stl`) y seguí la barra lateral de arriba hacia abajo:

1. **Figura:** *Frente hacia* (que se vea de cara) y, si lleva sombrero u orejas, *Marcar coronilla*.
2. **Planos:** *Simetría automática* (o 3 puntos sobre la línea central) y un plano de brazo y uno de pierna (3 puntos cada uno). Para la **cabeza articulada**: *Agregar plano del cuello / cabeza* (3 puntos: 2 en la nuca, a cada lado, y 1 bajo el mentón). Lo de arriba del plano es la cabeza: gira libre sobre un pasador tipo hongo en el cuello (mismos parámetros que el hombro: `neck_pin`). Para recortar la cabeza usá *Recortes manuales* con la pieza «cabeza».
3. **Silueta de cada miembro** (lápiz en la lista de planos): *Proponer silueta automática* y corregir vértices. En las piernas, **2.º** para el corte de frente (columna del muslo + escalón de la cadera).
4. **Pasador** (marcador en la lista): revisar dónde cae sobre la cara de corte; flechas = 0,1 mm.
5. **Parámetros / perfil de línea:** cargar el perfil de la línea de figuras (holgura, pasadores, ángulo sentada). La primera vez, imprimir las **probetas** para elegir holgura y Ø de vástago.
6. **Recortes manuales** si la figura los pide (una cola, la «esfera panza», láminas de la malla).
7. **Generar figura** (tarda minutos) → mirar el resumen del Estado. Probar **Ver la figura generada en la pose de cadera** para revisar colisiones.
8. **Exportar piezas…** → un STL por pieza, el ensamblado y el informe, ya orientados para imprimir.

Guardá el proyecto (`.proyecto`) antes de generar: así se retoma una figura sin repetir nada.

## Atajos

| Acción | Cómo |
|---|---|
| Orbitar / desplazar / zoom | arrastrar / Mayús + arrastrar (o botón medio) / rueda |
| Elegir un punto | clic corto |
| Deshacer el último punto o vértice | Retroceso |
| Cerrar el polígono | Intro, o clic en el primer vértice |
| Terminar la edición en curso | Esc |
| Mover el pasador | clic sobre la cara, o flechas (Mayús = 1 mm) |

## Problemas frecuentes

| Síntoma | Qué hacer |
|---|---|
| Dice «No module named 'gi'» | Reinstalar con `./packaging/instalar.sh`: usa el Python del sistema |
| Dice «Namespace Gtk not available» | `sudo apt install gir1.2-gtk-4.0` |
| El Estado marca interpenetración o holgura corta | Revisar siluetas y pasador (tutorial §7); probar otra holgura con las probetas |
| «Cruza con el del otro lado» al colocar el pasador | Acortar el pasador (`shaft_len`, `head_h`) o poner la cara de corte más hacia afuera |
| Un recorte no hace nada | Tiene que llegar a la superficie de la pieza |

## Línea de comandos (sin ventana)

```bash
afm build figura.stl figura.proyecto --out out/figura --orient   # igual que Generar + Exportar
afm probe --out out/probetas                                      # placa de probetas de articulación
afm demo                                                          # figura sintética de ejemplo
afm --version
```
