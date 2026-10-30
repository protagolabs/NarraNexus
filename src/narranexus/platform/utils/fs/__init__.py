"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-10-30
@description: Filesystem-safety group: helpers for touching tenant-controlled trees.

Home for the primitives that decide HOW host code may read, write or delete
inside a tree a tenant can rearrange (``confined_io``: fd-based, link-free).
New path-safety helpers belong here, not at the root of ``utils/``.
"""
