# Reclamos y retiros de paquetes

Esta guía explica qué hacer si un paquete del índice usa algo que no debería (una obra tuya sin permiso, una licencia que no corresponde, contenido dañino)
y qué pasa después, tanto en el índice como en la app. Es la parte de *Seguridad y moderación* del plan de catálogos en línea (§5.8, D24).

> **Listar no es verificar.** El índice no revisa a mano el contenido de cada paquete: la licencia y el origen los declara quien lo publicó. Por eso existe
> este proceso: lo que no se vio al listar se puede retirar después.

## 1. Cómo reclamar

1. Abrí un *issue* en el repo del índice con la plantilla «Reclamo». Poné:
   - **qué paquete**: la carpeta `<Autor>@<id>` y, si lo sabés, la versión;
   - **qué es tuyo o qué está mal**: una pieza concreta, la licencia declarada, el origen;
   - **cómo se comprueba**: un enlace a la obra original o a su licencia (no hace falta subir nada privado);
   - **qué pedís**: retirar el paquete, retirar una versión o una pieza, o corregir la ficha.
2. Si el reclamo es por algo que **no se puede redistribuir** (una obra de terceros), tené en cuenta que el camino correcto para el autor del paquete es
   publicar solo planos y pedir el archivo por referencia (§5.11); el reclamo puede resolverse así sin retirar nada.
3. Si el contenido es peligroso (software malicioso, datos personales), avisá igual por el *issue* y marcá la urgencia: se retira primero y se revisa después.

Los plazos de respuesta los fija quien mantiene el índice y se escriben acá cuando existan **[a definir con D00]**; mientras tanto, no hay un plazo prometido.

## 2. Qué hace quien mantiene el índice

Retirar es agregar **una línea** a `bloqueados.json`, con el motivo y la fecha. Cada línea bloquea por **uno solo** de estos criterios:

| Criterio | Qué alcanza | Cuándo usarlo |
|---|---|---|
| `id` | el paquete entero, todas sus versiones | el paquete completo está mal |
| `autor` | todos los paquetes de ese autor | reincidencia o cuenta que publica contenido dañino |
| `sha256` | esa versión (el hash de su `.zip`) o esa **pieza** (el hash de su STL) | una versión puntual, o una pieza que se reclama aunque reaparezca en otro paquete o con otro nombre |

```json
[
  {"id": "heroe-x", "motivo": "reclamo del autor de la obra original (issue #12)", "fecha": "2026-11-20"},
  {"sha256": "<64 caracteres en minúscula>", "motivo": "pieza sin permiso (issue #15)", "fecha": "2026-11-21"}
]
```

El `motivo` es obligatorio y se muestra a quien tenga el paquete instalado, así que tiene que explicar sin exponer datos personales de nadie.

Con eso, el CI del índice hace dos cosas (`afm catalogo bloqueados …`):

- **Rechaza las altas** que coincidan (código **IX106**): un paquete bloqueado no entra, ni volviéndolo a subir con otro nombre si el contenido es el mismo.
- **Retira lo ya listado** (`retirar`): saca del `index.json` la entrada (o la versión) y deja la lista `bloqueados` para que las apps avisen. Si lo bloqueado es la
  **última** versión de un paquete, la entrada entera sale hasta que sus responsables publiquen otra versión que no esté bloqueada.

Se puede probar antes de publicar:

```
afm catalogo bloqueados validar bloqueados.json
afm catalogo bloqueados comprobar bloqueados.json --indice index.json      # sale 1 si algo listado está bloqueado
afm catalogo bloqueados retirar bloqueados.json --indice index.json --salida index.filtrado.json
```

## 3. Qué pasa en la app

- El paquete **desaparece de «Explorar»** y no se puede instalar (ni él ni una versión bloqueada por `sha256`).
- Si ya lo tenías instalado, **«Paquetes instalados» y `afm catalogo remoto auditar` lo marcan como BLOQUEADO** y muestran el motivo y la fecha.
- La app compara **en tu equipo** el `sha256` de cada pieza instalada con la lista: avisa aunque hayas importado el paquete a mano o con otro nombre. No se envía nada
  a ningún servidor.
- **No se borra nada.** Tus figuras y recetas siguen funcionando; el aviso te deja decidir si quitás el paquete («Paquetes instalados → Quitar…») o lo reemplazás.
  Una pieza bloqueada que no vino de ningún paquete aparece como «sin paquete» en el aviso de la línea de comandos.

## 4. Si pensás que el bloqueo es un error

Abrí un *issue* con la referencia al bloqueo (la línea de `bloqueados.json`) y el motivo por el que no corresponde: permiso del titular, licencia que sí lo permite,
una confusión de nombre. Quien mantiene el índice puede **quitar la línea**; el siguiente ciclo del CI vuelve a listar el paquete. Mientras no se quite, las
apps siguen avisando.

## 5. Lo que este proceso no hace

- No verifica la identidad de quien reclama ni resuelve disputas de derechos: retira por precaución y deja el rastro (motivo y fecha) en el repo.
- No impide que alguien instale un `.zip` bloqueado a mano; solo avisa. Tampoco puede borrar copias que ya estén en otros equipos.
- No es asesoramiento legal. Ante un conflicto de derechos de autor, el contacto es el titular de la obra.
