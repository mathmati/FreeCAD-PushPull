# SPDX-License-Identifier: MIT
"""PushPull workbench package (Modern namespaced layout).

Importing this package has no side effects beyond making the submodules
importable -- workbench/command registration happens in init_gui.py, which
is imported once by FreeCAD's Addon Manager (or a Mod/ install) when the
GUI loads this addon. Nothing here does network access, GUI, or other
expensive work at import time.
"""
