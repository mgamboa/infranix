"""Builtin collection: Packer — builds cloneable templates from ISO.

It is the "example package" of the collection model: the core only knows the
collection has the BUILD capability. If Packer fails, the failure stays here:
disable it with `infra collection disable packer` and the core keeps going.
"""