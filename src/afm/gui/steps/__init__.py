"""Una pestaña de la barra lateral por módulo; `STEPS` fija el orden en que se agregan."""
from . import catalogo, figura, generar, miembros, parametros, planos, recortes

STEPS = (figura, planos, catalogo, miembros, recortes, parametros, generar)
