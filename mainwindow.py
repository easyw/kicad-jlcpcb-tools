import datetime
import logging
import os
import re
import sys

import wx
import wx.dataview
from pcbnew import GetBoard, GetBuildVersion

from .events import (
    EVT_ASSIGN_PART_EVENT,
    EVT_MESSAGE_EVENT,
    EVT_POPULATE_FOOTPRINT_LIST_EVENT,
    EVT_RESET_GAUGE_EVENT,
    EVT_UPDATE_GAUGE_EVENT,
)
from .fabrication import Fabrication
from .helpers import (
    PLUGIN_PATH,
    GetScaleFactor,
    HighResWxSize,
    get_footprint_by_ref,
    getVersion,
    loadBitmapScaled,
    toggle_exclude_from_bom,
    toggle_exclude_from_pos,
)
from .library import Library, LibraryState
from .partdetails import PartDetailsDialog
from .partselector import PartSelectorDialog
from .rotations import RotationManagerDialog
from .store import Store

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


class JLCPCBTools(wx.Dialog):
    def __init__(self, parent):
        wx.Dialog.__init__(
            self,
            parent,
            id=wx.ID_ANY,
            title=f"JLCPCB Tools [ {getVersion()} ]",
            pos=wx.DefaultPosition,
            size=wx.Size(1300, 800),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.KicadBuildVersion = GetBuildVersion()
        self.window = wx.GetTopLevelParent(self)
        self.SetSize(HighResWxSize(self.window, wx.Size(1300, 800)))
        self.scale_factor = GetScaleFactor(self.window)
        self.project_path = os.path.split(GetBoard().GetFileName())[0]
        self.hide_bom_parts = False
        self.hide_pos_parts = False
        self.manufacturers = []
        self.packages = []
        self.library = None
        self.store = None
        self.Bind(wx.EVT_CLOSE, self.quit_dialog)

        # ---------------------------------------------------------------------
        # ---------------------------- Hotkeys --------------------------------
        # ---------------------------------------------------------------------
        quitid = wx.NewId()
        self.Bind(wx.EVT_MENU, self.quit_dialog, id=quitid)

        entries = [wx.AcceleratorEntry(), wx.AcceleratorEntry(), wx.AcceleratorEntry()]
        entries[0].Set(wx.ACCEL_CTRL, ord("W"), quitid)
        entries[1].Set(wx.ACCEL_CTRL, ord("Q"), quitid)
        entries[2].Set(wx.ACCEL_SHIFT, wx.WXK_ESCAPE, quitid)
        accel = wx.AcceleratorTable(entries)
        self.SetAcceleratorTable(accel)

        # ---------------------------------------------------------------------
        # -------------------- Horizontal top buttons -------------------------
        # ---------------------------------------------------------------------

        top_button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.generate_button = wx.Button(
            self,
            wx.ID_ANY,
            "Generate fabrication files",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(200, 38)),
            0,
        )

        layer_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.layer_icon = wx.StaticBitmap(
            self,
            wx.ID_ANY,
            loadBitmapScaled(
                os.path.join(PLUGIN_PATH, "icons", "mdi-layers-triple-outline.png"),
                self.scale_factor,
            ),
            size=HighResWxSize(self.window, wx.Size(24, 36)),
        )
        self.layer_selection = wx.Choice(
            self,
            wx.ID_ANY,
            wx.DefaultPosition,
            wx.DefaultSize,
            ["Auto", "1 Layer", "2 Layers", "4 Layers", "6 Layers"],
            0,
        )
        self.layer_selection.SetSelection(0)
        # self.library_description = wx.StaticText(
        #     self,
        #     wx.ID_ANY,
        #     "",
        #     wx.DefaultPosition,
        #     wx.DefaultSize,
        #     wx.ALIGN_LEFT,
        #     "library_desc",
        # )
        self.rotation_button = wx.Button(
            self,
            wx.ID_ANY,
            "Manage rotations",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )

        self.download_button = wx.Button(
            self,
            wx.ID_ANY,
            "Update library",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )

        layer_sizer.Add(
            self.layer_icon, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_RIGHT | wx.RIGHT, 5
        )
        layer_sizer.Add(
            self.layer_selection,
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_LEFT,
            5,
        )

        top_button_sizer.Add(self.generate_button, 0, wx.ALL, 5)
        top_button_sizer.Add(layer_sizer, 0, wx.ALL | wx.EXPAND, 5)
        # top_button_sizer.Add(self.library_description, 1, wx.TOP | wx.EXPAND, 10)
        # Add a spacer to push download button to the right
        top_button_sizer.Add((0, 0), 1, wx.EXPAND, 5)
        top_button_sizer.Add(
            self.rotation_button,
            0,
            wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            5,
        )
        top_button_sizer.Add(
            self.download_button,
            0,
            wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            5,
        )

        self.generate_button.Bind(wx.EVT_BUTTON, self.generate_fabrication_data)
        self.rotation_button.Bind(wx.EVT_BUTTON, self.manage_rotations)
        self.download_button.Bind(wx.EVT_BUTTON, self.update_library)

        self.generate_button.SetBitmap(self._load_icon("fabrication.png"))
        self.generate_button.SetBitmapMargins((2, 0))

        self.rotation_button.SetBitmap(self._load_icon("mdi-format-rotate-90.png"))
        self.rotation_button.SetBitmapMargins((2, 0))

        self.download_button.SetBitmap(
            self._load_icon("mdi-cloud-download-outline.png")
        )
        self.download_button.SetBitmapMargins((2, 0))

        # ---------------------------------------------------------------------
        # ----------------------- Footprint List ------------------------------
        # ---------------------------------------------------------------------
        table_sizer = wx.BoxSizer(wx.HORIZONTAL)
        table_sizer.SetMinSize(HighResWxSize(self.window, wx.Size(-1, 600)))
        self.footprint_list = wx.dataview.DataViewListCtrl(
            self,
            wx.ID_ANY,
            wx.DefaultPosition,
            wx.DefaultSize,
            style=wx.dataview.DV_MULTIPLE,
        )
        self.footprint_list.SetMinSize(HighResWxSize(self.window, wx.Size(750, 400)))
        self.reference = self.footprint_list.AppendTextColumn(
            "Reference",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 100),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.value = self.footprint_list.AppendTextColumn(
            "Value",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 200),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.footprint = self.footprint_list.AppendTextColumn(
            "Footprint",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 300),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.lcsc = self.footprint_list.AppendTextColumn(
            "LCSC",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 100),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.lcsc = self.footprint_list.AppendTextColumn(
            "Type",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 100),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.stock = self.footprint_list.AppendTextColumn(
            "Stock",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 100),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.bom = self.footprint_list.AppendIconTextColumn(
            "BOM",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 40),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.pos = self.footprint_list.AppendIconTextColumn(
            "POS",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 40),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.rot = self.footprint_list.AppendTextColumn(
            "Rot Off",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 40),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        self.side = self.footprint_list.AppendTextColumn(
            "Side",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            width=int(self.scale_factor * 40),
            align=wx.ALIGN_CENTER,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        dummy = self.footprint_list.AppendTextColumn(
            "",
            mode=wx.dataview.DATAVIEW_CELL_INERT,
            align=wx.ALIGN_CENTER,
            width=1,
            flags=wx.dataview.DATAVIEW_COL_RESIZABLE,
        )
        table_sizer.Add(self.footprint_list, 20, wx.ALL | wx.EXPAND, 5)

        self.footprint_list.Bind(
            wx.dataview.EVT_DATAVIEW_COLUMN_HEADER_CLICK, self.OnSortFootprintList
        )

        self.footprint_list.Bind(
            wx.dataview.EVT_DATAVIEW_SELECTION_CHANGED, self.OnFootprintSelected
        )

        self.footprint_list.Bind(
            wx.dataview.EVT_DATAVIEW_ITEM_CONTEXT_MENU, self.OnRightDown
        )

        # ---------------------------------------------------------------------
        # ----------------------- Vertical Toolbar ----------------------------
        # ---------------------------------------------------------------------
        toolbar_sizer = wx.BoxSizer(wx.VERTICAL)
        self.select_part_button = wx.Button(
            self,
            wx.ID_ANY,
            "Select part",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.remove_part_button = wx.Button(
            self,
            wx.ID_ANY,
            "Remove part",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.select_alike_button = wx.Button(
            self,
            wx.ID_ANY,
            "Select alike",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.toggle_bom_pos_button = wx.Button(
            self,
            wx.ID_ANY,
            "Toggle BOM/POS",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.toggle_bom_button = wx.Button(
            self,
            wx.ID_ANY,
            "Toggle BOM",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.toggle_pos_button = wx.Button(
            self,
            wx.ID_ANY,
            "Toggle POS",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.part_details_button = wx.Button(
            self,
            wx.ID_ANY,
            "Show part details",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        # self.part_costs_button = wx.Button(
        #     self, wx.ID_ANY, "Calculate part costs", wx.DefaultPosition, (175, 38), 0
        # )
        self.hide_bom_button = wx.Button(
            self,
            wx.ID_ANY,
            "Hide excluded BOM",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )
        self.hide_pos_button = wx.Button(
            self,
            wx.ID_ANY,
            "Hide excluded POS",
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(175, 38)),
            0,
        )

        self.select_part_button.Bind(wx.EVT_BUTTON, self.select_part)
        self.remove_part_button.Bind(wx.EVT_BUTTON, self.remove_part)
        self.select_alike_button.Bind(wx.EVT_BUTTON, self.select_alike)
        self.toggle_bom_pos_button.Bind(wx.EVT_BUTTON, self.toggle_bom_pos)
        self.toggle_bom_button.Bind(wx.EVT_BUTTON, self.toggle_bom)
        self.toggle_pos_button.Bind(wx.EVT_BUTTON, self.toggle_pos)
        self.part_details_button.Bind(wx.EVT_BUTTON, self.get_part_details)
        # self.part_costs_button.Bind(wx.EVT_BUTTON, self.calculate_costs)
        self.hide_bom_button.Bind(wx.EVT_BUTTON, self.OnBomHide)
        self.hide_pos_button.Bind(wx.EVT_BUTTON, self.OnPosHide)

        toolbar_sizer.Add(self.select_part_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.remove_part_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.select_alike_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.toggle_bom_pos_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.toggle_bom_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.toggle_pos_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.part_details_button, 0, wx.ALL, 5)
        # toolbar_sizer.Add(self.part_costs_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.hide_bom_button, 0, wx.ALL, 5)
        toolbar_sizer.Add(self.hide_pos_button, 0, wx.ALL, 5)

        self.select_part_button.SetBitmap(
            self._load_icon("mdi-database-search-outline.png")
        )
        self.select_part_button.SetBitmapMargins((2, 0))

        self.remove_part_button.SetBitmap(self._load_icon("mdi-close-box-outline.png"))
        self.remove_part_button.SetBitmapMargins((2, 0))

        self.select_alike_button.SetBitmap(
            self._load_icon("mdi-checkbox-multiple-marked.png")
        )
        self.select_alike_button.SetBitmapMargins((2, 0))

        self.toggle_bom_pos_button.SetBitmap(self._load_icon("bom-pos.png"))
        self.toggle_bom_pos_button.SetBitmapMargins((2, 0))

        self.toggle_bom_button.SetBitmap(
            self._load_icon("mdi-format-list-bulleted.png")
        )
        self.toggle_bom_button.SetBitmapMargins((2, 0))

        self.toggle_pos_button.SetBitmap(self._load_icon("mdi-crosshairs-gps.png"))
        self.toggle_pos_button.SetBitmapMargins((2, 0))

        self.part_details_button.SetBitmap(
            self._load_icon("mdi-text-box-search-outline.png")
        )
        self.part_details_button.SetBitmapMargins((2, 0))

        # self.part_costs_button.SetBitmap(self._load_icon("mdi-cash.png"))
        # self.part_costs_button.SetBitmapMargins((2, 0))

        # self.hide_icon = loadBitmapScaled(
        #     os.path.join(PLUGIN_PATH, "icons", "mdi-eye-off-outline.png"),
        #     self.scale_factor,
        # )
        # self.show_icon = loadBitmapScaled(
        #     os.path.join(PLUGIN_PATH, "icons", "mdi-eye-outline.png"), self.scale_factor
        # )
        self.hide_bom_button.SetBitmap(self._load_icon("mdi-eye-off-outline.png"))
        self.hide_bom_button.SetBitmapMargins((2, 0))
        self.hide_pos_button.SetBitmap(self._load_icon("mdi-eye-off-outline.png"))
        self.hide_pos_button.SetBitmapMargins((2, 0))

        table_sizer.Add(toolbar_sizer, 1, wx.EXPAND, 5)

        # ---------------------------------------------------------------------
        # --------------------- Bottom Logbox and Gauge -----------------------
        # ---------------------------------------------------------------------
        self.logbox = wx.TextCtrl(
            self,
            wx.ID_ANY,
            wx.EmptyString,
            wx.DefaultPosition,
            wx.DefaultSize,
            wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.logbox.SetMinSize(HighResWxSize(self.window, wx.Size(-1, 150)))
        self.gauge = wx.Gauge(
            self,
            wx.ID_ANY,
            100,
            wx.DefaultPosition,
            HighResWxSize(self.window, wx.Size(100, -1)),
            wx.GA_HORIZONTAL,
        )
        self.gauge.SetValue(0)
        self.gauge.SetMinSize(HighResWxSize(self.window, wx.Size(-1, 5)))

        # ---------------------------------------------------------------------
        # ---------------------- Main Layout Sizer ----------------------------
        # ---------------------------------------------------------------------

        self.SetSizeHints(HighResWxSize(self.window, wx.Size(1000, -1)), wx.DefaultSize)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(top_button_sizer, 0, wx.ALL | wx.EXPAND, 5)
        layout.Add(table_sizer, 20, wx.ALL | wx.EXPAND, 5)
        layout.Add(self.logbox, 0, wx.ALL | wx.EXPAND, 5)
        layout.Add(self.gauge, 0, wx.ALL | wx.EXPAND, 5)

        self.SetSizer(layout)
        self.Layout()
        self.Centre(wx.BOTH)

        # ---------------------------------------------------------------------
        # ------------------------ Custom Events ------------------------------
        # ---------------------------------------------------------------------

        self.Bind(EVT_RESET_GAUGE_EVENT, self.reset_gauge)
        self.Bind(EVT_UPDATE_GAUGE_EVENT, self.update_gauge)
        self.Bind(EVT_MESSAGE_EVENT, self.display_message)
        self.Bind(EVT_ASSIGN_PART_EVENT, self.assign_part)
        self.Bind(EVT_POPULATE_FOOTPRINT_LIST_EVENT, self.populate_footprint_list)

        self.enable_toolbar_buttons(False)

        self.init_logger()
        self.init_library()
        self.init_fabrication()
        if self.library.state == LibraryState.UPDATE_NEEDED:
            self.library.update()
        else:
            self.init_store()
        self.logger.info(f"kicad-jlcpcb-tools version {getVersion()}")

    def quit_dialog(self, e):
        """Destroy dialog on close"""
        self.Destroy()
        self.EndModal(0)

    def init_library(self):
        """Initialize the parts library"""
        self.library = Library(self)

    def init_store(self):
        """Initialize the store of part assignments"""
        self.store = Store(self.project_path)
        if self.library.state == LibraryState.INITIALIZED:
            self.populate_footprint_list()

    def init_fabrication(self):
        """Initialize the fabrication"""
        self.fabrication = Fabrication(self)

    def reset_gauge(self, e):
        """Initialize the gauge."""
        self.gauge.SetRange(100)
        self.gauge.SetValue(0)

    def update_gauge(self, e):
        """Update the gauge"""
        self.gauge.SetValue(int(e.value))

    def assign_part(self, e):
        """Assign a selected LCSC number to a part"""
        self.store.set_lcsc(e.reference, e.lcsc)
        self.store.set_stock(e.reference, e.stock)
        self.populate_footprint_list()

    def display_message(self, e):
        """Dispaly a message with the data from the event"""
        styles = {
            "info": wx.ICON_INFORMATION,
            "warning": wx.ICON_WARNING,
            "error": wx.ICON_ERROR,
        }
        wx.MessageBox(e.text, e.title, style=styles.get(e.style, wx.ICON_INFORMATION))

    def populate_footprint_list(self, e=None):
        """Populate/Refresh list of footprints."""
        if not self.store:
            self.init_store()
        self.footprint_list.DeleteAllItems()
        if "6.99" in GetBuildVersion():
            icons = {
                0: wx.dataview.DataViewIconText(
                    "", self._load_icon("mdi-check-color.png")
                ),
                1: wx.dataview.DataViewIconText(
                    "", self._load_icon("mdi-close-color.png")
                ),
            }
        else:
            icons = {
                0: wx.dataview.DataViewIconText(
                    "", wx.Icon(self._load_icon("mdi-check-color.png"))
                ),
                1: wx.dataview.DataViewIconText(
                    "", wx.Icon(self._load_icon("mdi-close-color.png"))
                ),
            }
        numbers = []
        parts = []
        for part in self.store.read_all():
            fp = get_footprint_by_ref(GetBoard(), part[0])[0]
            if part[3] and part[3] not in numbers:
                numbers.append(part[3])
            part.insert(4, "")
            part[5] = str(part[5])
            # don't show the part if hide BOM is set
            if self.hide_bom_parts and part[6]:
                continue
            # don't show the part if hide POS is set
            if self.hide_pos_parts and part[7]:
                continue
            # decide which icon to use
            part[6] = icons.get(part[6], icons.get(0))
            part[7] = icons.get(part[7], icons.get(0))
            part.insert(8, "")
            side = "Top" if fp.GetLayer() == 0 else "Bot"
            part.insert(9, side)
            part.insert(10, "")
            parts.append(part)
        details = self.library.get_part_details(numbers)
        corrections = self.library.get_all_correction_data()

        for part in parts:
            if detail := list(filter(lambda x: x[0] == part[3], details)):
                part[4] = detail[0][2]
                part[5] = detail[0][1]
            for regex, correction in corrections:
                if re.search(regex, str(part[2])):
                    part[8] = correction
                    continue

            self.footprint_list.AppendItem(part)

    def OnSortFootprintList(self, e):
        """Set order_by to the clicked column and trigger list refresh."""
        self.store.set_order_by(e.GetColumn())
        self.populate_footprint_list()

    def OnBomHide(self, e):
        """Hide all parts from the list that have 'in BOM' set to No."""
        self.hide_bom_parts = not self.hide_bom_parts
        if self.hide_bom_parts:
            self.hide_bom_button.SetBitmap(self._load_icon(wx.Bitmap()))
            self.hide_bom_button.SetBitmap(self._load_icon(self.show_icon))
            self.hide_bom_button.SetBitmapMargins((2, 0))
            self.hide_bom_button.SetLabel("Show excluded BOM")
        else:
            self.hide_bom_button.SetBitmap(self._load_icon(wx.Bitmap()))
            self.hide_bom_button.SetBitmap(self._load_icon(self.hide_icon))
            self.hide_bom_button.SetBitmapMargins((2, 0))
            self.hide_bom_button.SetLabel("Hide excluded BOM")
        self.populate_footprint_list()

    def OnPosHide(self, e):
        """Hide all parts from the list that have 'in pos' set to No."""
        self.hide_pos_parts = not self.hide_pos_parts
        if self.hide_pos_parts:
            self.hide_pos_button.SetBitmap(self._load_icon(wx.Bitmap()))
            self.hide_pos_button.SetBitmap(self._load_icon(self.show_icon))
            self.hide_pos_button.SetBitmapMargins((2, 0))
            self.hide_pos_button.SetLabel("Show excluded POS")
        else:
            self.hide_pos_button.SetBitmap(self._load_icon(wx.Bitmap()))
            self.hide_pos_button.SetBitmap(self._load_icon(self.hide_icon))
            self.hide_pos_button.SetBitmapMargins((2, 0))
            self.hide_pos_button.SetLabel("Hide excluded POS")
        self.populate_footprint_list()

    def OnFootprintSelected(self, e):
        """Enable the toolbar buttons when a selection was made."""
        self.enable_toolbar_buttons(self.footprint_list.GetSelectedItemsCount() > 0)

    def enable_all_buttons(self, state):
        """Control state of all the buttons"""
        self.enable_top_buttons(state)
        self.enable_toolbar_buttons(state)

    def enable_top_buttons(self, state):
        """Control the state of all the buttons in the top section"""
        for b in [
            self.generate_button,
            self.download_button,
            self.layer_selection,
        ]:
            b.Enable(bool(state))

    def enable_toolbar_buttons(self, state):
        """Control the state of all the buttons in toolbar on the right side"""
        for b in [
            self.select_part_button,
            self.remove_part_button,
            self.select_alike_button,
            self.toggle_bom_pos_button,
            self.toggle_bom_button,
            self.toggle_pos_button,
            self.part_details_button,
        ]:
            b.Enable(bool(state))

    def toggle_bom_pos(self, e):
        """Toggle the exclude from BOM/POS attribute of a footprint."""
        selected_rows = []
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            selected_rows.append(row)
            ref = self.footprint_list.GetTextValue(row, 0)
            fp = get_footprint_by_ref(GetBoard(), ref)[0]
            bom = toggle_exclude_from_bom(fp)
            pos = toggle_exclude_from_pos(fp)
            self.store.set_bom(ref, bom)
            self.store.set_pos(ref, pos)
        self.populate_footprint_list()
        for row in selected_rows:
            self.footprint_list.SelectRow(row)

    def toggle_bom(self, e):
        """Toggle the exclude from BOM attribute of a footprint."""
        selected_rows = []
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            selected_rows.append(row)
            ref = self.footprint_list.GetTextValue(row, 0)
            fp = get_footprint_by_ref(GetBoard(), ref)[0]
            bom = toggle_exclude_from_bom(fp)
            self.store.set_bom(ref, bom)
        self.populate_footprint_list()
        for row in selected_rows:
            self.footprint_list.SelectRow(row)

    def toggle_pos(self, e):
        selected_rows = []
        """Toggle the exclude from POS attribute of a footprint."""
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            selected_rows.append(row)
            ref = self.footprint_list.GetTextValue(row, 0)
            fp = get_footprint_by_ref(GetBoard(), ref)[0]
            pos = toggle_exclude_from_pos(fp)
            self.store.set_pos(ref, pos)
        self.populate_footprint_list()
        for row in selected_rows:
            self.footprint_list.SelectRow(row)

    def remove_part(self, e):
        """Remove an assigned a LCSC Part number to a footprint."""
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            ref = self.footprint_list.GetTextValue(row, 0)
            fp = get_footprint_by_ref(GetBoard(), ref)[0]
            self.store.set_lcsc(ref, "")
        self.populate_footprint_list()

    def select_alike(self, e):
        """Select all parts that have the same value and footprint."""
        num_sel = (
            self.footprint_list.GetSelectedItemsCount()
        )  # could have selected more than 1 item (by mistake?)
        if num_sel == 1:
            item = self.footprint_list.GetSelection()
        else:
            self.logger.warning(f"Select only one component, please.")
            return
        row = self.footprint_list.ItemToRow(item)
        ref = self.footprint_list.GetValue(row, 0)
        part = self.store.get_part(ref)
        for r in range(self.footprint_list.GetItemCount()):
            value = self.footprint_list.GetValue(r, 1)
            fp = self.footprint_list.GetValue(r, 2)
            if part[1] == value and part[2] == fp:
                self.footprint_list.SelectRow(r)

    def get_part_details(self, e):
        """Fetch part details from LCSC and show them in a modal."""
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            if row == -1:
                return
            part = self.footprint_list.GetTextValue(row, 3)
            if part != "":
                self.busy_cursor = wx.BusyCursor()
                dialog = PartDetailsDialog(self, part)
                del self.busy_cursor
                dialog.Show()

    def update_library(self, e=None):
        """Update the library from the JLCPCB CSV file."""
        self.library.update()

    def manage_rotations(self, e=None):
        """Manage rotation corrections."""
        RotationManagerDialog(self, "").ShowModal()

    def calculate_costs(self, e):
        """Hopefully we will be able to calculate the part costs in the future."""
        pass

    def select_part(self, e):
        """Select a part from the library and assign it to the selected footprint(s)."""
        selection = {}
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            reference = self.footprint_list.GetTextValue(row, 0)
            lcsc = self.footprint_list.GetTextValue(row, 3)
            selection[reference] = lcsc
        PartSelectorDialog(self, selection).ShowModal()

    def generate_fabrication_data(self, e):
        """Generate fabrication data."""
        self.fabrication.fill_zones()
        layer_selection = self.layer_selection.GetSelection()
        if layer_selection != 0:
            layer_count = int(self.layer_selection.GetString(layer_selection)[:1])
        else:
            layer_count = None
        self.fabrication.generate_geber(layer_count)
        self.fabrication.generate_excellon()
        self.fabrication.zip_gerber_excellon()
        self.fabrication.generate_pos()
        self.fabrication.generate_bom()

    def copy_part_lcsc(self, e):
        """Fetch part details from LCSC and show them in a modal."""
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            if row == -1:
                return
            part = self.footprint_list.GetTextValue(row, 3)
            if part != "":
                if wx.TheClipboard.Open():
                    wx.TheClipboard.SetData(wx.TextDataObject(part))
                    wx.TheClipboard.Close()

    def paste_part_lcsc(self, e):
        text_data = wx.TextDataObject()
        if wx.TheClipboard.Open():
            success = wx.TheClipboard.GetData(text_data)
            wx.TheClipboard.Close()
        if success:
            lcsc = text_data.GetText()
            for item in self.footprint_list.GetSelections():
                row = self.footprint_list.ItemToRow(item)
                reference = self.footprint_list.GetTextValue(row, 0)
                self.store.set_lcsc(reference, lcsc)
            self.populate_footprint_list()

    def add_part_rot(self, e):
        for item in self.footprint_list.GetSelections():
            row = self.footprint_list.ItemToRow(item)
            if row == -1:
                return
            footp = self.footprint_list.GetTextValue(row, 2)
            if footp != "":
                RotationManagerDialog(self, "^" + footp).ShowModal()

    def OnRightDown(self, e):
        conMenu = wx.Menu()
        cpmi = wx.MenuItem(conMenu, wx.NewId(), "Copy LCSC")
        conMenu.Append(cpmi)
        conMenu.Bind(wx.EVT_MENU, self.copy_part_lcsc, cpmi)

        ptmi = wx.MenuItem(conMenu, wx.NewId(), "Paste LCSC")
        conMenu.Append(ptmi)
        conMenu.Bind(wx.EVT_MENU, self.paste_part_lcsc, ptmi)

        crmi = wx.MenuItem(conMenu, wx.NewId(), "Add Rotation")
        conMenu.Append(crmi)
        conMenu.Bind(wx.EVT_MENU, self.add_part_rot, crmi)

        self.footprint_list.PopupMenu(conMenu)
        conMenu.Destroy()  # destroy to avoid memory leak

    def init_logger(self):
        """Initialize logger to log into textbox"""
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        # Log to stderr
        handler1 = logging.StreamHandler(sys.stderr)
        handler1.setLevel(logging.DEBUG)
        # and to our GUI
        handler2 = LogBoxHandler(self.logbox)
        handler2.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(funcName)s -  %(message)s",
            datefmt="%Y.%m.%d %H:%M:%S",
        )
        handler1.setFormatter(formatter)
        handler2.setFormatter(formatter)
        root.addHandler(handler1)
        root.addHandler(handler2)
        self.logger = logging.getLogger(__name__)

    def _load_icon(self, filename):
        """Load an icon from a png file, handle wx difference between 6.0 and 6.99"""
        icon = loadBitmapScaled(
            os.path.join(PLUGIN_PATH, "icons", filename),
            self.scale_factor,
        )
        if "6.99" in self.KicadBuildVersion:
            icon = wx.BitmapBundle(icon)
        return icon

    def __del__(self):
        pass


class LogBoxHandler(logging.StreamHandler):
    def __init__(self, textctrl):
        logging.StreamHandler.__init__(self)
        self.textctrl = textctrl

    def emit(self, record):
        """Pokemon exception that hopefully helps getting this working with threads."""
        try:
            msg = self.format(record)
            self.textctrl.WriteText(msg + "\n")
            self.flush()
        except:
            pass
