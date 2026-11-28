## Current Focus — Repo del índice listo para subir (2026-11-28)
**Objetivo.** Entregar `afm-catalogo-indice` completo (scripts, workflows, sitio, docs, pruebas) para subirlo a `fschpp/afm-catalogo-indice`.

### Todo List
- [x] Done: scripts (validar_alta, comprobar_enlaces, puerta_releases, comprobar_bloqueados, construir_indice), esquemas, 61 pruebas
- [x] Done: validate-pr.yml, build-index.yml, sitio, README, CONTRIBUTING, plantillas, ejemplo
- [ ] Todo: subir, activar Pages y permisos, primer alta real
- [ ] Todo: fijar `afm` a un tag en requirements.txt cuando exista

### Latest Blockers/Discoveries
- Workflows no probados en GitHub real (solo la lógica de scripts, con un servidor local).
- Con la puerta caída o un zip roto se conserva la entrada previa (`update_check`); fatal (exit 2) no despliega.
