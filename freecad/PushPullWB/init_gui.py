# SPDX-License-Identifier: MIT
"""Workbench registration for the PushPull addon.

Importing this module (auto-discovered by FreeCAD's Modern-layout addon
loader from Mod/<addon>/freecad/PushPullWB/) registers the workbench with
Gui.addWorkbench(...). No network access or other expensive work happens
at import/startup time -- the PushPull tool only acts on explicit user
action (activating the command, then clicking/dragging a face).
"""
import os

import FreeCADGui as Gui


class PushPullWorkbench(Gui.Workbench):
    MenuText = "Push/Pull"
    ToolTip = "Direct modeling: click-drag a planar face to Pad/Pocket it"
    Icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "Resources",
        "Icons",
        "pushpull.svg",
    )

    def Initialize(self):
        # Import side effect registers the command with Gui.addCommand.
        from . import commands

        commands.register()
        self.appendToolbar("Push/Pull", ["PushPull_PushPull"])
        self.appendMenu("Push/Pull", ["PushPull_PushPull"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"  # exact string, mandatory, do not change


Gui.addWorkbench(PushPullWorkbench())
