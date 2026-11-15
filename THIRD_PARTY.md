# Software de terceros

Licencias verificadas el 2026-11-15 en PyPI / npm (versión indicada). Todas son compatibles con la GPL-3.0-or-later del proyecto.

## Motor (Python)
| Paquete | Versión | Licencia |
|---|---|---|
| manifold3d | 3.5.5 | Apache-2.0 |
| trimesh | 5.2.1 | MIT |
| numpy | 2.5.4 | BSD-3-Clause (y otras permisivas incluidas en su wheel) |
| scipy | 1.18.1 | BSD |
| shapely | 2.2.0 | BSD-3-Clause |
| scikit-image | 0.26.0 | BSD |
| networkx | 3.7 | BSD-3-Clause |
| pillow | 13.0.0 | MIT-CMU |
| python-i18n | 0.3.9 | MIT |

`mapbox_earcut`, `fast-simplification` y `pymeshfix` son opcionales o se reemplazan en la versión web (ver `docs/PLAN_port_pyodide.md` §4); sus licencias no se verificaron.

## Versión web
| Componente | Versión | Licencia |
|---|---|---|
| Pyodide | 314.0.8 | MPL-2.0 |
| micropip | 0.11.1 | MPL-2.0 |
| three.js | 0.186.1 (fijada en `web/index.html`; la última publicada es 0.187.0) | MIT |

El wheel de manifold3d para Pyodide lo compila `.github/workflows/manifold-wheel.yml` a partir del código fuente de manifold3d 3.5.5 con parches propios (ver `PROGRESS.md`); mantiene su licencia Apache-2.0.

## Modelos
Las figuras de personajes de terceros usadas como casos de estudio no se distribuyen con el repo ni con el sitio.
