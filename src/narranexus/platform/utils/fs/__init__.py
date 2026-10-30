"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-10-30
@description: Filesystem-safety group: helpers for touching tenant-controlled trees.

Home for the primitives that decide HOW host code may build, validate, read,
write or delete paths: ``confined_io`` (fd-based, link-free I/O inside a tree a
tenant can rearrange), ``workspace_paths`` (layout + id-segment guard),
``file_safety`` (filename / zip-member / upload-size validation) and
``fs_safety`` (platform-owned dirs made writable). New path-safety helpers
belong here, not at the root of ``utils/``.
"""
