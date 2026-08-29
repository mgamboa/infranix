"""Colección builtin: Packer — construye templates clonables desde ISO.

Es el "paquete ejemplo" del modelo de colecciones: el core solo sabe que la
colección tiene capability BUILD. Si Packer falla, el fallo se queda acá
dentro: se desactiva con `infra collection disable packer` y el core sigue.
"""