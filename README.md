# afm-catalogo-indice

Índice de paquetes de [afm](https://github.com/fschpp/action_figure_maker_stl). Solo guarda **metadatos**: nombre, descripción, portada y un
enlace al Release de GitHub donde vive cada `.zip`. Un bot arma `data/index.json` y lo publica en GitHub Pages.

- Sitio: `https://fschpp.github.io/afm-catalogo-indice/`
- En afm: **Explorar → Agregar índice…** con `https://fschpp.github.io/afm-catalogo-indice/data/index.json`.

## Cómo funciona

1. Publicás tu paquete en tu repo (Release con `<id>-<versión>.zip`) con **Publicar un paquete** de afm.
2. `afm catalogo paquete enviar <carpeta>` abre un Pull Request acá con `paquetes/<Autor>@<id>/` (`meta.json`, `descripcion.md`, `portada`).
3. El CI del PR revisa el alta (IX101–IX108) y baja el zip del Release para validarlo como `afm catalogo validar --estricto`.
4. Al fusionar, `build-index.yml` regenera el índice. También corre cada noche para recoger Releases nuevos.

Reglas: solo GitHub Releases, zip de hasta 100 MB, portada de hasta 2 MB, 1–4 categorías de la lista cerrada. Ver `docs/POLITICA_catalogos_online.md`
en el repo de afm y `CONTRIBUTING.md`.

## Retiros y reclamos

Abrí un issue «Reclamo». Se retira primero (`bloqueados.json`) y se discute después. Listar no es verificar.

## Desarrollo

```
pip install -r requirements.txt
pytest
python scripts/construir_indice.py --salida data          # sin red: solo desde los meta.json
```

Licencia del código: GPL-3.0. Cada paquete tiene la suya.
