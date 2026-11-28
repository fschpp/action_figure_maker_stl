"""`afm catalogo referencia listar|importar|quitar` (plan de catálogos en línea, §5.11 y D26): los archivos que aporta la persona a un paquete
«por referencia». La lógica está en `referencias.py`; acá solo se leen argumentos y se imprime.

Códigos de salida: 0 ok · 2 error (archivo que no es el pedido, que pesa de más, que no existe…) — lo maneja `catalog_cli.run_catalogo`."""
from __future__ import annotations

from . import referencias as R
from .catalog import Catalog


def add_parsers(cs, root) -> None:
    """`cs`: los subparsers de `afm catalogo`; `root(p)`: agrega el argumento opcional `ruta` del catálogo."""
    r = cs.add_parser("referencia", help="archivos requeridos de los paquetes «por referencia» (D26): listar lo que falta, importar el STL propio "
                                         "(se verifica por sha256) y quitarlo; sin el archivo la pieza queda incompleta y no se arma")
    rs = r.add_subparsers(dest="refcmd", required=True)
    a = rs.add_parser("listar", help="qué archivos piden los paquetes instalados, cuáles ya están y qué piezas completan")
    root(a)
    a = rs.add_parser("importar", help="verificar un archivo (tamaño y sha256) y guardarlo como archivo requerido; nada se guarda si no es el pedido")
    root(a)
    a.add_argument("archivo", metavar="ARCHIVO.stl")
    a.add_argument("--id", default="", metavar="ID", help="exigir que sea este archivo requerido (el de `referencia listar`); sin esto se busca entre todos")
    a = rs.add_parser("quitar", help="borrar un archivo importado (las piezas que completaba vuelven a quedar incompletas)")
    root(a)
    a.add_argument("id", metavar="ID")


def run_referencia(cat: Catalog, args) -> int:
    cmd = args.refcmd
    if cmd == "listar":
        for line in R.lines(cat):
            print(line)
        return 0
    if cmd == "importar":
        res = R.importar(cat, args.archivo, args.id)
        print(f"{'Ya estaba importado' if res.ya_estaba else 'Importado'}: «{res.requerido.nombre}» ({res.requerido.id}), sha256 {res.sha256[:12]}…")
        if res.completadas:
            print("Piezas que ahora están completas: " + ", ".join(res.completadas))
        for w in res.avisos:
            print(f"AVISO: {w}")
        return 0
    if cmd == "quitar":
        pend = R.quitar(cat, args.id)
        print(f"Quitado «{args.id}». Quedan incompletas: " + (", ".join(pend) or "ninguna pieza"))
        return 0
    return 2
