# -*- coding: ISO-8859-1 -*-
#
# generated by wxGlade 0.9.3 on Thu Jun 27 21:45:40 2019
#

import wx

from Kernel import Module
from icons import icons8_administrative_tools_50

_ = wx.GetTranslation


# begin wxGlade: dependencies
# end wxGlade

class Settings(wx.Frame, Module):
    def __init__(self, parent, *args, **kwds):
        # begin wxGlade: Settings.__init__
        wx.Frame.__init__(self, parent, -1, "",
                          style=wx.DEFAULT_FRAME_STYLE | wx.FRAME_FLOAT_ON_PARENT | wx.TAB_TRAVERSAL)
        Module.__init__(self)
        self.SetSize((490, 280))
        self.radio_units = wx.RadioBox(self, wx.ID_ANY, _("Units"), choices=["mm", "cm", "inch", "mils"],
                                       majorDimension=1,
                                       style=wx.RA_SPECIFY_ROWS)
        self.combo_svg_ppi = wx.ComboBox(self, wx.ID_ANY,
                                         choices=[_("96 px/in Inkscape"),
                                                  _("72 px/in Illustrator"),
                                                  _("90 px/in Old Inkscape"),
                                                  _("Custom")], style=wx.CB_DROPDOWN)
        # self.text_svg_ppi = wx.TextCtrl(self, wx.ID_ANY, "", style=wx.TE_READONLY)
        self.text_svg_ppi = wx.TextCtrl(self, wx.ID_ANY, "")
        self.choices = [(_("Print Shutdown"), "print_shutdown", False),
                        (_("SVG Uniform Save"), "uniform_svg", False),
                        (_("Image DPI Scaling"), 'image_dpi', True),
                        (_("DXF Centering"), 'dxf_center', True),
                        (_("Show Negative Guide"), "show_negative_guide", True),
                        (_("Launch Spooler JobStart"), "auto_spooler", True),
                        (_("MouseWheel Pan"), "mouse_wheel_pan", False),
                        (_("Invert MouseWheel Pan"), 'mouse_pan_invert', False),
                        (_("Invert MouseWheel Zoom"), 'mouse_zoom_invert', False),
                        (_("Default Operations"), "default_operations", False),
                        ]
        self.checklist_options = wx.CheckListBox(self, wx.ID_ANY, choices=[c[0] for c in self.choices])

        from wxMeerK40t import supported_languages
        choices = [language_name for language_code, language_name, language_index in supported_languages]
        self.combo_language = wx.ComboBox(self, wx.ID_ANY, choices=choices, style=wx.CB_DROPDOWN)

        self.__set_properties()
        self.__do_layout()

        self.Bind(wx.EVT_RADIOBOX, self.on_radio_units, self.radio_units)
        self.Bind(wx.EVT_COMBOBOX, self.on_combo_language, self.combo_language)
        self.Bind(wx.EVT_COMBOBOX, self.on_combo_svg_ppi, self.combo_svg_ppi)
        self.Bind(wx.EVT_TEXT_ENTER, self.on_text_svg_ppi, self.text_svg_ppi)
        self.Bind(wx.EVT_TEXT, self.on_text_svg_ppi, self.text_svg_ppi)
        self.Bind(wx.EVT_CHECKLISTBOX, self.on_checklist_settings, self.checklist_options)

        # end wxGlade
        self.Bind(wx.EVT_CLOSE, self.on_close, self)

        # OSX Window close
        if parent is not None:
            parent.accelerator_table(self)

    def on_close(self, event):
        if self.state == 5:
            event.Veto()
        else:
            self.state = 5
            self.device.close('window', self.name)
            event.Skip()  # Call destroy as regular.

    def initialize(self, channel=None):
        self.device.close('window', self.name)
        self.Show()

        self.device.device_root.setting(float, 'svg_ppi', 96.0)
        self.text_svg_ppi.SetValue(str(self.device.device_root.svg_ppi))

        for name, choice, default in self.choices:
            self.device.setting(bool, choice, default)

        self.device.setting(int, "language", 0)
        self.device.setting(str, "units_name", 'mm')
        self.device.setting(int, "units_marks", 10)
        self.device.setting(int, "units_index", 0)

        for i, c in enumerate(self.choices):
            name, choice, default = c
            if getattr(self.device, choice):
                self.checklist_options.Check(i, True)
        self.radio_units.SetSelection(self.device.units_index)
        self.combo_language.SetSelection(self.device.language)

    def finalize(self, channel=None):
        try:
            self.Close()
        except RuntimeError:
            pass

    def shutdown(self, channel=None):
        try:
            self.Close()
        except RuntimeError:
            pass

    def __set_properties(self):
        _icon = wx.NullIcon
        _icon.CopyFromBitmap(icons8_administrative_tools_50.GetBitmap())
        self.SetIcon(_icon)
        # begin wxGlade: Settings.__set_properties
        self.SetTitle(_("Settings"))
        self.radio_units.SetBackgroundColour(wx.Colour(192, 192, 192))
        self.radio_units.SetToolTip(_("Set default units for guides"))
        self.radio_units.SetSelection(0)
        self.combo_language.SetToolTip(_("Select the desired language to use."))
        self.combo_svg_ppi.SetToolTip(_("Select the Pixels Per Inch to use when loading an SVG file"))
        self.text_svg_ppi.SetMinSize((60, 23))
        self.text_svg_ppi.SetToolTip(_("Custom Pixels Per Inch to use when loading an SVG file"))
        # end wxGlade

    def __do_layout(self):
        # begin wxGlade: Settings.__do_layout
        sizer_settings = wx.BoxSizer(wx.HORIZONTAL)
        sizer_gui_options = wx.BoxSizer(wx.VERTICAL)
        sizer_3 = wx.StaticBoxSizer(wx.StaticBox(self, wx.ID_ANY, _("SVG Pixel Per Inch")), wx.HORIZONTAL)
        sizer_2 = wx.StaticBoxSizer(wx.StaticBox(self, wx.ID_ANY, _("Language")), wx.HORIZONTAL)
        sizer_gui_options.Add(self.radio_units, 0, wx.EXPAND, 0)
        sizer_2.Add(self.combo_language, 0, 0, 0)
        sizer_gui_options.Add(sizer_2, 0, wx.EXPAND, 0)
        sizer_3.Add(self.combo_svg_ppi, 0, 0, 0)
        sizer_3.Add((20, 20), 0, 0, 0)
        sizer_3.Add(self.text_svg_ppi, 1, 0, 0)
        sizer_gui_options.Add(sizer_3, 0, wx.EXPAND, 0)
        sizer_settings.Add(sizer_gui_options, 0, wx.EXPAND, 0)
        sizer_settings.Add(self.checklist_options, 1, wx.EXPAND, 0)
        self.SetSizer(sizer_settings)
        self.Layout()
        # end wxGlade

    def on_combo_svg_ppi(self, event):  # wxGlade: Settings.<event_handler>
        ppi = self.combo_svg_ppi.GetSelection()
        if ppi == 0:
            self.device.device_root.setting(float, 'svg_ppi', 96.0)
            self.device.device_root.svg_ppi = 96.0
        elif ppi == 1:
            self.device.device_root.setting(float, 'svg_ppi', 72.0)
            self.device.device_root.svg_ppi = 72.0
        elif ppi == 2:
            self.device.device_root.setting(float, 'svg_ppi', 90.0)
            self.device.device_root.svg_ppi = 90.0
        else:
            self.device.device_root.setting(float, 'svg_ppi', 96.0)
            self.device.device_root.svg_ppi = 96.0
        self.text_svg_ppi.SetValue(str(self.device.device_root.svg_ppi))

    def on_text_svg_ppi(self, event):  # wxGlade: Settings.<event_handler>
        try:
            svg_ppi = float(self.text_svg_ppi.GetValue())
        except ValueError:
            return
        if svg_ppi == 96:
            if self.combo_svg_ppi.GetSelection() != 0:
                self.combo_svg_ppi.SetSelection(0)
        elif svg_ppi == 72:
            if self.combo_svg_ppi.GetSelection() != 1:
                self.combo_svg_ppi.SetSelection(1)
        elif svg_ppi == 90:
            if self.combo_svg_ppi.GetSelection() != 2:
                self.combo_svg_ppi.SetSelection(2)
        else:
            if self.combo_svg_ppi.GetSelection() != 3:
                self.combo_svg_ppi.SetSelection(3)
        self.device.device_root.svg_ppi = svg_ppi

    def on_checklist_settings(self, event):  # wxGlade: Settings.<event_handler>
        for i, c in enumerate(self.choices):
            name, choice, default = c
            setattr(self.device, choice, self.checklist_options.IsChecked(i))

    def on_combo_language(self, event):  # wxGlade: Preferences.<event_handler>
        lang = self.combo_language.GetSelection()
        if lang != -1 and self.device.app is not None:
            self.device.app.update_language(lang)

    def on_radio_units(self, event):  # wxGlade: Preferences.<event_handler>
        if event.Int == 0:
            self.set_mm()
        elif event.Int == 1:
            self.set_cm()
        elif event.Int == 2:
            self.set_inch()
        elif event.Int == 3:
            self.set_mil()

    def set_inch(self):
        p = self.device.device_root
        p.units_convert, p.units_name, p.units_marks, p.units_index = (1000.0, "inch", 1, 2)
        p.signal('units')

    def set_mil(self):
        p = self.device.device_root
        p.units_convert, p.units_name, p.units_marks, p.units_index = (1.0, "mil", 1000, 3)
        p.signal('units')

    def set_cm(self):
        p = self.device.device_root
        p.units_convert, p.units_name, p.units_marks, p.units_index = (393.7, "cm", 1, 1)
        p.signal('units')

    def set_mm(self):
        p = self.device.device_root
        p.units_convert, p.units_name, p.units_marks, p.units_index = (39.37, "mm", 10, 0)
        p.signal('units')
