# Cómo agregar un paquete

1. Publicá el paquete en tu repo de GitHub con un Release estable (no borrador ni prerelease) que tenga `<id>-<versión>.zip`.
2. Forkeá este repo y creá `paquetes/<TuUsuario>@<id>/` con:
   - `meta.json` (ver `ejemplos/TuUsuario@paquete-ejemplo/`). `<TuUsuario>` tiene que ser el dueño del repo del paquete.
   - `descripcion.md` (hasta 20 000 caracteres).
   - `portada.png` o `portada.jpg` (opcional, 1280×720, hasta 2 MB).
   Nada más: cualquier otro archivo hace fallar el CI (IX103).
3. Abrí el Pull Request. Con `afm catalogo paquete enviar <carpeta>` se hace solo.

`licencia` y `origen` son obligatorios y son tu declaración: respondés por tener derecho a redistribuir lo que subís, incluidos personajes
de terceros. Las licencias NC se aceptan y se pueden filtrar.

Códigos del CI: IX101 meta/descripción inválidos · IX102 carpeta ≠ meta · IX103 archivos de más · IX104 el zip no coincide con el alta ·
IX105 repo/Release/zip no resuelto · IX106 bloqueado · IX107 portada · IX108 `fuente` no aceptada.
