#!/usr/bin/env python3
"""
JD CNC G-code Generator — Main Extension File
Inkscape extension for generating CNC G-code from SVG paths.
"""
import warnings
# Suppress asyncio deprecation warnings from gi bindings on Python 3.14+
warnings.filterwarnings("ignore", category=DeprecationWarning, module="gi")

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import inkex
from config_manager import ConfigManager
from svg_parser import SVGParser
from gcode_logic import GCodeLogic
import threading
import math
import os
import logging
import traceback

SCRIPT_VERSION = "2.0.0"
# Directory this script lives in — used for loading assets like logo.svg
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# CSS — applied once at startup
# ─────────────────────────────────────────────────────────────────────────────
_CSS = b"""
/* Dark header bar feel for the title area */
.cnc-header {
    background-color: #1e1e2e;
    color: #cdd6f4;
    padding: 4px 12px;
}
.cnc-header-title {
    font-size: 15px;
    font-weight: bold;
    color: #cdd6f4;
}
.cnc-header-version {
    font-size: 11px;
    color: #6c7086;
}

/* Info panel section */
.info-panel {
    background-color: #181825;
    border-radius: 6px;
    padding: 6px 10px;
}
.info-label {
    font-size: 11px;
    color: #a6adc8;
}
.info-value {
    font-size: 12px;
    font-weight: bold;
    color: #cdd6f4;
    font-family: monospace;
}
.info-value-good { color: #a6e3a1; }
.info-value-warn { color: #f9e2af; }
.info-value-bad  { color: #f38ba8; }

/* Position entry boxes */
.pos-entry {
    font-family: monospace;
    font-size: 12px;
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 2px 6px;
    min-width: 80px;
}
.pos-entry:focus {
    border-color: #89b4fa;
}

/* Notification toasts */
.toast-info  { background-color: #1e1e2e; color: #cdd6f4; border-radius: 6px; padding: 8px 16px; font-weight: bold; font-size: 13px; }
.toast-error { background-color: #3b0c0c; color: #f38ba8; border-radius: 6px; padding: 8px 16px; font-weight: bold; font-size: 13px; }

/* Buttons */
.btn-generate { background-color: #313244; }
.btn-export   { background-color: #1e6a45; color: #a6e3a1; }
"""


class CNCDialog(Gtk.Dialog):

    # ─── Init ────────────────────────────────────────────────────────────────

    def __init__(self, parent=None, effect=None):
        super().__init__(
            title=f"JD CNC G-code Generator  v{SCRIPT_VERSION}",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.effect = effect
        self.config_manager = ConfigManager()
        self.gcode_logic    = GCodeLogic()

        try:
            self.svg_parser = SVGParser(effect.svg) if effect is not None else None
        except Exception:
            self.svg_parser = None

        # Apply global CSS
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Runtime state — set BEFORE build_ui so handlers never see AttributeError
        self.generated_cut_paths   = None
        self.generated_score_paths = None
        self.gcode_generated       = False
        self._last_stats           = None          # dict from gcode_logic.generate()
        self._prev_out_of_bounds   = False
        self._cached_config        = None          # throttled during drag/scroll

        # Preview state — single clean transform: zoom=1 means fit-to-viewport centered
        self._view_zoom  = 1.0          # 1.0 = fit; >1 = zoomed in
        self._view_pan_x = 0.0          # screen-pixel pan, only non-zero when zoomed
        self._view_pan_y = 0.0
        self._view_drag  = False        # middle-click pan in progress
        self._view_drag_last = (0.0, 0.0)

        # Object drag state
        self.is_object_dragging  = False
        self.drag_last_mouse_x   = 0
        self.drag_last_mouse_y   = 0

        # Notification widget refs
        self.notification_label      = None
        self.notification_timeout_id = None

        # Progress animation
        self.progress_fraction  = 0.0
        self.progress_animating = False

        self._build_ui()

    # ─── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        self.config         = self.config_manager.load_config()
        self.default_config = self.config_manager.load_default()

        self.set_default_size(1280, 820)
        self.set_resizable(True)
        self.set_position(Gtk.WindowPosition.CENTER)

        # App identity — helps KDE/Wayland show the right taskbar icon & name
        GLib.set_prgname("jd-cnc-gcode-generator")
        GLib.set_application_name("JD CNC G-code Generator")

        # Load window icon from logo.svg next to this script
        try:
            icon_path = os.path.join(_SCRIPT_DIR, "logo.svg")
            if os.path.exists(icon_path):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 128, 128)
                self.set_icon(pixbuf)
        except Exception:
            pass  # icon is cosmetic, never crash over it

        # Strip all default padding from the dialog content area
        ca = self.get_content_area()
        ca.set_border_width(0)
        ca.set_spacing(0)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_border_width(0)
        ca.add(root)

        # Notebook + hamburger overlay
        self.notebook = Gtk.Notebook()
        self.notebook.set_hexpand(True)
        nb_overlay = Gtk.Overlay()
        nb_overlay.add(self.notebook)
        menu_btn = self._create_hamburger_menu()
        nb_overlay.add_overlay(menu_btn)
        menu_btn.set_halign(Gtk.Align.END)
        menu_btn.set_valign(Gtk.Align.START)
        menu_btn.set_margin_top(2)
        menu_btn.set_margin_end(2)
        root.pack_start(nb_overlay, True, True, 0)

        # Tabs
        self._create_home_tab()
        self._create_bed_config_tab()
        self._create_tool_options_tab()
        self._create_speeds_tab()
        self._create_gcode_templates_tab()

        # Bottom button bar
        root.pack_start(self._create_button_panel(), False, False, 0)

        self.load_config_to_ui()
        self.connect("destroy", self._on_dialog_close)
        self.show_all()
        self.notebook.set_current_page(0)

        self.auto_save_enabled = True
        self._connect_auto_save()
        self._setup_logging()

        GLib.idle_add(self._initial_setup)

    # ── Home tab ──────────────────────────────────────────────────────────────

    def _create_home_tab(self):
        """
        Home tab layout:
          Left  — 2D bed preview (zoomable/pannable/draggable)
          Right — G-code text view  +  info panel
        """
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        top_hbox  = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top_hbox.set_hexpand(True)
        top_hbox.set_vexpand(True)

        # ── 2-D preview — no frame, fills all available space ──
        self.gcode_preview = Gtk.DrawingArea()
        self.gcode_preview.set_hexpand(True)
        self.gcode_preview.set_vexpand(True)
        self.gcode_preview.connect("draw", self._on_preview_draw)
        self.gcode_preview.add_events(
            Gdk.EventMask.SCROLL_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.gcode_preview.connect("scroll-event",         self._on_preview_scroll)
        self.gcode_preview.connect("button-press-event",   self._on_preview_button_press)
        self.gcode_preview.connect("button-release-event", self._on_preview_button_release)
        self.gcode_preview.connect("motion-notify-event",  self._on_preview_motion)
        top_hbox.pack_start(self.gcode_preview, True, True, 0)

        # ── Right panel ──
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right_vbox.set_hexpand(False)
        right_vbox.set_size_request(370, -1)

        # G-code text view
        gcode_frame = Gtk.Frame()
        gcode_frame.set_shadow_type(Gtk.ShadowType.IN)
        gcode_frame.set_vexpand(True)
        self.gcode_text_buffer = Gtk.TextBuffer()
        self.gcode_text_view   = Gtk.TextView(buffer=self.gcode_text_buffer)
        self.gcode_text_view.set_editable(True)
        self.gcode_text_view.set_cursor_visible(True)
        self.gcode_text_view.set_monospace(True)
        gcode_scroll = Gtk.ScrolledWindow()
        gcode_scroll.set_hexpand(True)
        gcode_scroll.set_vexpand(True)
        gcode_scroll.set_min_content_height(420)
        gcode_scroll.add(self.gcode_text_view)
        gcode_frame.add(gcode_scroll)
        right_vbox.pack_start(gcode_frame, True, True, 0)

        # Info panel
        right_vbox.pack_start(self._create_info_panel(), False, False, 0)

        top_hbox.pack_start(right_vbox, False, False, 0)
        main_vbox.pack_start(top_hbox, True, True, 0)

        # Notification overlay wraps the whole home content
        self.notification_overlay = Gtk.Overlay()
        self.notification_overlay.add(main_vbox)

        self.notebook.insert_page(
            self.notification_overlay,
            Gtk.Label(label="Home"),
            0,
        )

    def _create_info_panel(self):
        """
        Info panel below the G-code view.
        Shows: object width/height, editable X/Y position (bed coords),
        cut distance, estimated time, and path counts.
        """
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)

        grid = Gtk.Grid(row_spacing=5, column_spacing=10)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        grid.set_margin_start(10)
        grid.set_margin_end(10)

        def _lbl(text, css_class="info-label"):
            l = Gtk.Label(label=text, halign=Gtk.Align.START)
            l.get_style_context().add_class(css_class)
            return l

        def _val(initial="—", css_class="info-value"):
            l = Gtk.Label(label=initial, halign=Gtk.Align.START)
            l.get_style_context().add_class(css_class)
            return l

        r = 0
        # ── Size row ──
        grid.attach(_lbl("Width:"),  0, r, 1, 1)
        self._info_width = _val()
        grid.attach(self._info_width, 1, r, 1, 1)
        grid.attach(_lbl("Height:"), 2, r, 1, 1)
        self._info_height = _val()
        grid.attach(self._info_height, 3, r, 1, 1)
        r += 1

        # ── Position row — editable X / Y entries ──
        grid.attach(_lbl("X pos (mm):"), 0, r, 1, 1)
        self._pos_x_entry = Gtk.Entry()
        self._pos_x_entry.set_width_chars(9)
        self._pos_x_entry.set_placeholder_text("X")
        self._pos_x_entry.get_style_context().add_class("pos-entry")
        self._pos_x_entry.connect("activate",        self._on_position_entry_commit)
        self._pos_x_entry.connect("focus-out-event", self._on_position_entry_commit)
        grid.attach(self._pos_x_entry, 1, r, 1, 1)

        grid.attach(_lbl("Y pos (mm):"), 2, r, 1, 1)
        self._pos_y_entry = Gtk.Entry()
        self._pos_y_entry.set_width_chars(9)
        self._pos_y_entry.set_placeholder_text("Y")
        self._pos_y_entry.get_style_context().add_class("pos-entry")
        self._pos_y_entry.connect("activate",        self._on_position_entry_commit)
        self._pos_y_entry.connect("focus-out-event", self._on_position_entry_commit)
        grid.attach(self._pos_y_entry, 3, r, 1, 1)
        r += 1

        # ── Cut distance / time ──
        grid.attach(_lbl("Cut distance:"), 0, r, 1, 1)
        self._info_cut_dist = _val()
        grid.attach(self._info_cut_dist, 1, r, 1, 1)
        grid.attach(_lbl("Est. time:"), 2, r, 1, 1)
        self._info_time = _val()
        grid.attach(self._info_time, 3, r, 1, 1)
        r += 1

        # ── Path counts ──
        grid.attach(_lbl("Cut paths:"), 0, r, 1, 1)
        self._info_cut_paths = _val()
        grid.attach(self._info_cut_paths, 1, r, 1, 1)
        grid.attach(_lbl("Score paths:"), 2, r, 1, 1)
        self._info_score_paths = _val()
        grid.attach(self._info_score_paths, 3, r, 1, 1)

        frame.add(grid)
        return frame

    # ── Bed & Origin tab ──────────────────────────────────────────────────────

    def _create_bed_config_tab(self):
        frame, grid = self._make_frame()
        r = 0

        grid.attach(Gtk.Label(label="Bed Width",  halign=Gtk.Align.START), 0, r, 1, 1)
        self.bed_width_entry = Gtk.Entry()
        grid.attach(self.bed_width_entry,  1, r, 1, 1)
        grid.attach(Gtk.Label(label="mm"), 2, r, 1, 1)
        r += 1

        grid.attach(Gtk.Label(label="Bed Height", halign=Gtk.Align.START), 0, r, 1, 1)
        self.bed_height_entry = Gtk.Entry()
        grid.attach(self.bed_height_entry, 1, r, 1, 1)
        grid.attach(Gtk.Label(label="mm"), 2, r, 1, 1)
        r += 1

        sep = Gtk.Label()
        sep.set_markup("<b>Machine Origin</b>")
        sep.set_halign(Gtk.Align.START)
        sep.set_margin_top(8)
        grid.attach(sep, 0, r, 3, 1)
        r += 1

        self.origin_front_left  = Gtk.RadioButton.new_with_label_from_widget(None,                    "Front Left")
        self.origin_front_right = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left,  "Front Right")
        self.origin_center      = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left,  "Center")
        self.origin_back_left   = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left,  "Back Left")
        self.origin_back_right  = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left,  "Back Right")

        grid.attach(self.origin_front_left,  0, r, 1, 1)
        grid.attach(self.origin_front_right, 1, r, 1, 1)
        grid.attach(self.origin_center,      2, r, 1, 1)
        r += 1
        grid.attach(self.origin_back_left,  0, r, 1, 1)
        grid.attach(self.origin_back_right, 1, r, 1, 1)

        self.notebook.append_page(frame, Gtk.Label(label="Bed & Origin"))

    # ── Tool Options tab ──────────────────────────────────────────────────────

    def _create_tool_options_tab(self):
        frame, grid = self._make_frame()
        r = 0

        # Z Axis header
        z_hdr = Gtk.Label()
        z_hdr.set_markup("<b>Z Axis</b>")
        z_hdr.set_halign(Gtk.Align.START)
        grid.attach(z_hdr, 0, r, 3, 1)
        r += 1

        # Mode combo
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_box.pack_start(Gtk.Label(label="Z Axis Type:", halign=Gtk.Align.START), False, False, 0)
        self.z_mode_combo = Gtk.ComboBoxText()
        self.z_mode_combo.append_text("Servo")
        self.z_mode_combo.append_text("Stepper")
        mode_box.pack_start(self.z_mode_combo, False, False, 0)
        grid.attach(mode_box, 0, r, 3, 1)
        r += 1

        # Dynamic z fields stack
        self.z_fields_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grid.attach(self.z_fields_stack, 0, r, 3, 1)
        r += 1

        # Servo fields
        self.servo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for attr, label in [
            ("servo_score_entry",  "Servo Score Position (°)"),
            ("servo_cut_entry",    "Servo Cut Position (°)"),
            ("servo_travel_entry", "Servo Travel Position (°)"),
            ("servo_delay_entry",  "Servo Delay (ms)"),
        ]:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row_box.pack_start(Gtk.Label(label=label, halign=Gtk.Align.START, width_chars=28), False, False, 0)
            entry = Gtk.Entry()
            setattr(self, attr, entry)
            row_box.pack_start(entry, True, True, 0)
            self.servo_box.pack_start(row_box, False, False, 0)

        # Stepper fields
        self.stepper_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for attr, label in [
            ("z_stepper_cut_entry",    "Cut Height (mm)"),
            ("z_stepper_score_entry",  "Score Height (mm)"),
            ("z_stepper_travel_entry", "Travel Height (mm)"),
        ]:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row_box.pack_start(Gtk.Label(label=label, halign=Gtk.Align.START, width_chars=28), False, False, 0)
            entry = Gtk.Entry()
            setattr(self, attr, entry)
            row_box.pack_start(entry, True, True, 0)
            self.stepper_box.pack_start(row_box, False, False, 0)

        def _update_z_visibility(combo):
            for child in self.z_fields_stack.get_children():
                self.z_fields_stack.remove(child)
            mode = (self.z_mode_combo.get_active_text() or "servo").lower()
            box = self.servo_box if mode == "servo" else self.stepper_box
            self.z_fields_stack.pack_start(box, False, False, 0)
            self.z_fields_stack.show_all()

        self.z_mode_combo.connect("changed", _update_z_visibility)
        _update_z_visibility(self.z_mode_combo)
        self.update_z_fields_visibility = lambda: _update_z_visibility(self.z_mode_combo)

        # Tool offset header
        offset_hdr = Gtk.Label()
        offset_hdr.set_markup("<b>Tool Offset</b>")
        offset_hdr.set_halign(Gtk.Align.START)
        offset_hdr.set_margin_top(10)
        grid.attach(offset_hdr, 0, r, 3, 1)
        r += 1

        for attr, label in [
            ("tool_offset_x_entry", "X-axis Offset"),
            ("tool_offset_y_entry", "Y-axis Offset"),
            ("tool_diameter_entry", "Tool Diameter"),
        ]:
            grid.attach(Gtk.Label(label=label, halign=Gtk.Align.START), 0, r, 1, 1)
            entry = Gtk.Entry()
            setattr(self, attr, entry)
            grid.attach(entry, 1, r, 1, 1)
            grid.attach(Gtk.Label(label="mm"), 2, r, 1, 1)
            r += 1

        self.notebook.append_page(frame, Gtk.Label(label="Tool Options"))

    # ── Speeds tab ────────────────────────────────────────────────────────────

    def _create_speeds_tab(self):
        frame, grid = self._make_frame()
        r = 0

        hdr = Gtk.Label()
        hdr.set_markup("<b>Speeds</b>")
        hdr.set_halign(Gtk.Align.START)
        grid.attach(hdr, 0, r, 3, 1)
        r += 1

        speed_fields = [
            ("speed_override_entry",  "Speed Override",            "%"),
            ("travel_speed_entry",    "Travel Speed (cutter up)",  "mm/s"),
            ("cutting_speed_entry",   "Cutting Speed (black)",     "mm/s"),
            ("scoring_speed_entry",   "Scoring Speed (red)",       "mm/s"),
            ("z_plunge_speed_entry",  "Z Plunge Speed (down)",     "mm/s"),
            ("z_raise_speed_entry",   "Z Raise Speed (up)",        "mm/s"),
            ("safety_margin_entry",   "Safety Margin",             "mm"),
        ]
        for attr, label, unit in speed_fields:
            grid.attach(Gtk.Label(label=label, halign=Gtk.Align.START), 0, r, 1, 1)
            entry = Gtk.Entry()
            setattr(self, attr, entry)
            grid.attach(entry, 1, r, 1, 1)
            grid.attach(Gtk.Label(label=unit), 2, r, 1, 1)
            r += 1

        self.notebook.append_page(frame, Gtk.Label(label="Speeds & Limits"))

    # ── G-code Templates tab ──────────────────────────────────────────────────

    def _create_gcode_templates_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(8)

        for attr, title in [
            ("start_gcode_buffer", "Start G-code"),
            ("end_gcode_buffer",   "End G-code"),
        ]:
            lbl = Gtk.Label()
            lbl.set_markup(f"<b>{title}</b>")
            lbl.set_halign(Gtk.Align.START)
            vbox.pack_start(lbl, False, False, 0)

            buf = Gtk.TextBuffer()
            setattr(self, attr, buf)
            tv = Gtk.TextView(buffer=buf, monospace=True)
            tv.set_wrap_mode(Gtk.WrapMode.NONE)
            sw = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
            sw.set_min_content_height(160)
            sw.add(tv)
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.IN)
            frame.add(sw)
            vbox.pack_start(frame, True, True, 0)

        self.notebook.append_page(vbox, Gtk.Label(label="G-code Templates"))

    # ── Button panel ─────────────────────────────────────────────────────────

    def _create_button_panel(self):
        # Horizontal bar: status text on the left, action buttons on the right.
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(10)
        box.set_margin_end(10)

        # Left-aligned status label so the bar isn't empty dead space
        self._status_label = Gtk.Label(label=f"JD CNC G-code Generator  v{SCRIPT_VERSION}")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.get_style_context().add_class("info-label")
        box.pack_start(self._status_label, True, True, 0)

        # Auto Center
        self.auto_center_button = Gtk.Button(label="Auto Center")
        self.auto_center_button.set_size_request(140, 34)
        self.auto_center_button.connect("clicked", self._on_auto_center_clicked)

        # Combined Generate / Export button with progress overlay
        self.generate_export_button = Gtk.Button(label="Generate G-code")
        self.generate_export_button.set_size_request(180, 34)
        self.generate_export_button.connect("clicked", self._on_generate_export_clicked)

        btn_overlay = Gtk.Overlay()
        btn_overlay.set_size_request(180, 34)
        btn_overlay.add(self.generate_export_button)

        self.progress_haze = Gtk.DrawingArea()
        self.progress_haze.set_size_request(180, 34)
        self.progress_haze.set_no_show_all(True)
        self.progress_haze.set_opacity(0.5)
        self.progress_haze.connect("draw", self._on_progress_haze_draw)
        btn_overlay.add_overlay(self.progress_haze)
        self.progress_haze.hide()

        # Pack right-side buttons (order: Auto Center, then Generate/Export)
        box.pack_start(self.auto_center_button, False, False, 0)
        box.pack_start(btn_overlay,             False, False, 0)
        return box

    # ── Hamburger menu ────────────────────────────────────────────────────────

    def _create_hamburger_menu(self):
        btn = Gtk.MenuButton()
        btn.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        btn.set_always_show_image(True)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text("More options")

        menu = Gtk.Menu()
        for label, handler in [
            ("Save as Default",   self._on_save_default_clicked),
            ("Reset to Defaults", self._on_reset_defaults_clicked),
        ]:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", handler)
            menu.append(item)
        menu.show_all()
        btn.set_popup(menu)
        return btn

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _make_frame(self, margin=10):
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        grid = Gtk.Grid(row_spacing=8, column_spacing=10, margin=margin)
        frame.add(grid)
        return frame, grid

    def _setup_logging(self):
        """Route Python logging to the toast notification system."""
        dialog_ref = self

        class _GtkHandler(logging.Handler):
            def emit(self, record):
                msg   = self.format(record)
                level = record.levelname.lower()
                GLib.idle_add(dialog_ref.log_message, msg, level)

        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        for h in list(logger.handlers):
            logger.removeHandler(h)
        h = _GtkHandler()
        h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(h)

    def _connect_auto_save(self):
        """Connect all config-related widgets to auto-save and stale-marking."""
        def _auto_save(*_):
            if self.auto_save_enabled:
                self.config_manager.save_config(self.get_config_from_ui())

        def _mark_stale(*_):
            self.gcode_generated = False
            GLib.idle_add(self._update_generate_export_button)

        entry_widgets = [
            self.bed_width_entry, self.bed_height_entry,
            self.servo_score_entry, self.servo_cut_entry,
            self.servo_travel_entry, self.servo_delay_entry,
            self.tool_offset_x_entry, self.tool_offset_y_entry,
            self.tool_diameter_entry,
            self.travel_speed_entry, self.z_plunge_speed_entry,
            self.z_raise_speed_entry, self.cutting_speed_entry,
            self.scoring_speed_entry, self.speed_override_entry,
            self.safety_margin_entry,
            self.z_stepper_cut_entry, self.z_stepper_score_entry,
            self.z_stepper_travel_entry,
        ]
        for w in entry_widgets:
            w.connect("changed", _auto_save)
            w.connect("changed", _mark_stale)

        for btn in [
            self.origin_front_left, self.origin_front_right,
            self.origin_center, self.origin_back_left, self.origin_back_right,
        ]:
            btn.connect("toggled", _mark_stale)

        for buf in [self.start_gcode_buffer, self.end_gcode_buffer]:
            buf.connect("changed", _auto_save)
            buf.connect("changed", _mark_stale)

    # ─── Initial setup ────────────────────────────────────────────────────────

    def _initial_setup(self):
        """Called once after the UI is shown — parse selection and auto-center."""
        try:
            if self.svg_parser is not None:
                cut, score = self.svg_parser.get_paths_by_color()
                if cut or score:
                    cfg = self.get_config_from_ui()
                    bed_w  = float(cfg.get("bed_width",    300))
                    bed_h  = float(cfg.get("bed_height",   200))
                    margin = float(cfg.get("safety_margin", 5))
                    c, s = self._center_paths_on_bed(cut, score, bed_w, bed_h, margin)
                    self.generated_cut_paths   = c
                    self.generated_score_paths = s
                    # gcode_generated stays False until G-code is actually built
                    self._generate_gcode_from_current_paths()
        except Exception:
            logging.exception("Initial setup failed")

        if hasattr(self, "gcode_preview"):
            self.gcode_preview.queue_draw()
        return False  # one-shot GLib.idle_add

    # ─── G-code generation ────────────────────────────────────────────────────

    def _generate_gcode_from_current_paths(self):
        """
        Build G-code from the current stored paths in a background thread.
        OOB check uses gcode_logic.check_paths_in_bounds() which operates on
        post-transform coordinates — fixes the longstanding false-alarm bug.
        """
        self._start_progress()

        def _bg():
            try:
                cfg = self.get_config_from_ui()

                if not self.generated_cut_paths and not self.generated_score_paths:
                    GLib.idle_add(self._stop_progress)
                    logging.warning("No paths to generate G-code from. Use Auto Center first.")
                    return

                # ── Out-of-bounds check (post-transform, authoritative) ──
                in_bounds, msg = self.gcode_logic.check_paths_in_bounds(
                    cfg,
                    self.generated_cut_paths   or [],
                    self.generated_score_paths or [],
                )
                if not in_bounds:
                    GLib.idle_add(self._stop_progress)
                    logging.error(f"Object outside cutter area: {msg}")
                    self._prev_out_of_bounds = True
                    GLib.idle_add(self._update_generate_export_button)
                    return

                if self._prev_out_of_bounds:
                    GLib.idle_add(self.fade_out_error_notification)
                self._prev_out_of_bounds = False

                # ── Fit-to-bed warning ──
                cfg_w  = float(cfg.get("bed_width",    300))
                cfg_h  = float(cfg.get("bed_height",   200))
                margin = float(cfg.get("safety_margin", 5))
                pts = [
                    pt
                    for pl in [self.generated_cut_paths or [], self.generated_score_paths or []]
                    for path in pl for sub in path for pt in sub
                ]
                if pts:
                    design_w = max(p[0] for p in pts) - min(p[0] for p in pts)
                    design_h = max(p[1] for p in pts) - min(p[1] for p in pts)
                    usable_w = cfg_w - 2 * margin
                    usable_h = cfg_h - 2 * margin
                    if design_w > usable_w or design_h > usable_h:
                        logging.warning(
                            f"Design ({design_w:.1f}×{design_h:.1f} mm) is larger than "
                            f"usable bed area ({usable_w:.1f}×{usable_h:.1f} mm)."
                        )

                gcode, stats = self.gcode_logic.generate(
                    cfg,
                    self.generated_cut_paths,
                    self.generated_score_paths,
                )
                GLib.idle_add(
                    self._set_gcode_text, gcode, stats,
                    self.generated_cut_paths,
                    self.generated_score_paths,
                )
                GLib.idle_add(self._stop_progress)
                logging.info("G-code generated successfully.")

            except Exception as e:
                GLib.idle_add(self._stop_progress)
                logging.error(f"G-code generation error: {e}")
                logging.error(traceback.format_exc())

        threading.Thread(target=_bg, daemon=True).start()

    def _set_gcode_text(self, gcode, stats, cut_paths, score_paths):
        """Update the G-code buffer and info panel. Only called on the GTK thread."""
        self.gcode_text_buffer.set_text(gcode)
        self.gcode_text_view.scroll_to_iter(
            self.gcode_text_buffer.get_start_iter(), 0.0, False, 0, 0
        )
        if cut_paths is not None and score_paths is not None:
            self.generated_cut_paths   = cut_paths
            self.generated_score_paths = score_paths
            self.gcode_generated       = True
            self._last_stats           = stats
        else:
            self.generated_cut_paths   = None
            self.generated_score_paths = None
            self.gcode_generated       = False
            self._last_stats           = None

        self.gcode_preview.queue_draw()
        GLib.idle_add(self._update_generate_export_button)
        GLib.idle_add(self._update_info_panel)

    # ─── Button handlers ──────────────────────────────────────────────────────

    def _on_generate_export_clicked(self, widget):
        """Generate if stale, export if ready."""
        gcode_text = ""
        try:
            gcode_text = self.gcode_text_buffer.get_text(
                self.gcode_text_buffer.get_start_iter(),
                self.gcode_text_buffer.get_end_iter(),
                False,
            )
        except Exception:
            pass

        if self.gcode_generated and gcode_text.strip():
            self._show_export_dialog(gcode_text)
        else:
            self._generate_gcode_from_current_paths()

    def _on_auto_center_clicked(self, widget):
        """Re-parse selection, center on bed, regenerate G-code."""
        try:
            if self.svg_parser is None:
                logging.warning("SVG parser not available — run from Inkscape with a selection.")
                return

            cfg    = self.get_config_from_ui()
            bed_w  = float(cfg.get("bed_width",    300))
            bed_h  = float(cfg.get("bed_height",   200))
            margin = float(cfg.get("safety_margin", 5))

            cut, score = self.svg_parser.get_paths_by_color()
            if not cut and not score:
                logging.warning("No paths found in selection.")
                return

            c, s = self._center_paths_on_bed(cut, score, bed_w, bed_h, margin)
            self.generated_cut_paths   = c
            self.generated_score_paths = s
            self._view_zoom  = 1.0
            self._view_pan_x = 0.0
            self._view_pan_y = 0.0
            self.gcode_preview.queue_draw()

            n_cut   = sum(len(p) for p in c) if c else 0
            n_score = sum(len(p) for p in s) if s else 0
            logging.info(f"Centered — {n_cut} cut subpath(s), {n_score} score subpath(s).")

            # Kick off generation immediately so button flips to Export
            self._generate_gcode_from_current_paths()

        except Exception as e:
            logging.error(f"Auto-center error: {e}")
            logging.error(traceback.format_exc())

    def _on_position_entry_commit(self, widget, *_):
        """
        Called when the user presses Enter or defocuses one of the X/Y position
        entries.  Translates the stored paths so the design's top-left corner
        sits at the entered (X, Y) and regenerates.
        """
        if not self.generated_cut_paths and not self.generated_score_paths:
            return

        # Collect all points to find current bounding box top-left
        pts = [
            pt
            for pl in [self.generated_cut_paths or [], self.generated_score_paths or []]
            for path in pl for sub in path for pt in sub
        ]
        if not pts:
            return

        cur_min_x = min(p[0] for p in pts)
        cur_min_y = min(p[1] for p in pts)

        try:
            new_x = float(self._pos_x_entry.get_text())
        except ValueError:
            new_x = cur_min_x

        try:
            new_y = float(self._pos_y_entry.get_text())
        except ValueError:
            new_y = cur_min_y

        dx = new_x - cur_min_x
        dy = new_y - cur_min_y

        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return  # no change

        self.generated_cut_paths   = self._translate_paths(self.generated_cut_paths,   dx, dy)
        self.generated_score_paths = self._translate_paths(self.generated_score_paths, dx, dy)
        self.gcode_preview.queue_draw()
        self._generate_gcode_from_current_paths()

    # ─── Info panel update ────────────────────────────────────────────────────

    def _update_info_panel(self):
        """Refresh all labels and position entries in the info panel."""
        cut   = self.generated_cut_paths   or []
        score = self.generated_score_paths or []
        stats = self._last_stats

        all_pts = [pt for pl in cut + score for sub in pl for pt in sub]

        if not all_pts:
            for lbl in [
                self._info_width, self._info_height,
                self._info_cut_dist, self._info_time,
                self._info_cut_paths, self._info_score_paths,
            ]:
                lbl.set_text("—")
            self._pos_x_entry.set_text("")
            self._pos_y_entry.set_text("")
            return

        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max_x - min_x
        h = max_y - min_y

        self._info_width.set_text(f"{w:.1f} mm")
        self._info_height.set_text(f"{h:.1f} mm")

        # Position entries — show top-left in bed space
        # Block the signal temporarily so editing doesn't trigger re-generation
        self._pos_x_entry.set_text(f"{min_x:.2f}")
        self._pos_y_entry.set_text(f"{min_y:.2f}")

        # Cut distance from stats (cutting moves only, no travel)
        if stats:
            dist_mm  = stats.get("distance", 0.0)
            time_min = stats.get("time",     0.0)
            self._info_cut_dist.set_text(f"{dist_mm:.0f} mm")
            if time_min < 1.0:
                self._info_time.set_text(f"{time_min*60:.0f} sec")
            else:
                self._info_time.set_text(f"{time_min:.1f} min")
        else:
            self._info_cut_dist.set_text("—")
            self._info_time.set_text("—")

        # Path counts
        n_cut   = sum(len(p) for p in cut)
        n_score = sum(len(p) for p in score)
        self._info_cut_paths.set_text(str(n_cut))
        self._info_score_paths.set_text(str(n_score))

        # Status bar — show a quick summary if G-code is ready
        if stats and hasattr(self, "_status_label"):
            self._status_label.set_text(
                f"Ready  ·  {n_cut + n_score} path(s)  ·  "
                f"{stats.get('distance', 0.0):.0f} mm cut"
            )

    def _update_generate_export_button(self):
        """Flip button label and style between Generate and Export states."""
        try:
            btn = self.generate_export_button
            ctx = btn.get_style_context()
            try:
                gcode_text = self.gcode_text_buffer.get_text(
                    self.gcode_text_buffer.get_start_iter(),
                    self.gcode_text_buffer.get_end_iter(),
                    False,
                )
            except Exception:
                gcode_text = ""
            ready = bool(self.gcode_generated and gcode_text.strip())
            if ready:
                ctx.add_class("suggested-action")
                btn.set_label("Export G-code")
            else:
                ctx.remove_class("suggested-action")
                btn.set_label("Generate G-code")
        except Exception:
            pass

    # ─── Config load / save ───────────────────────────────────────────────────

    def load_config_to_ui(self):
        c = self.config

        self.bed_width_entry.set_text( c.get("bed_width",  "300"))
        self.bed_height_entry.set_text(c.get("bed_height", "200"))

        origin = c.get("origin_point", "front_left")
        mapping = {
            "front_left":  self.origin_front_left,
            "front_right": self.origin_front_right,
            "center":      self.origin_center,
            "back_left":   self.origin_back_left,
            "back_right":  self.origin_back_right,
        }
        mapping.get(origin, self.origin_front_left).set_active(True)

        self.servo_score_entry.set_text( c.get("servo_score",  "60"))
        self.servo_cut_entry.set_text(   c.get("servo_cut",    "45"))
        self.servo_travel_entry.set_text(c.get("servo_travel", "120"))
        self.servo_delay_entry.set_text( c.get("servo_delay",  "200"))

        self.tool_offset_x_entry.set_text(c.get("tool_offset_x", "0"))
        self.tool_offset_y_entry.set_text(c.get("tool_offset_y", "0"))
        self.tool_diameter_entry.set_text(c.get("tool_diameter", "1"))

        self.travel_speed_entry.set_text(  c.get("travel_speed",   "250"))
        self.cutting_speed_entry.set_text( c.get("cutting_speed",  "10"))
        self.scoring_speed_entry.set_text( c.get("scoring_speed",  "12"))
        self.z_plunge_speed_entry.set_text(c.get("z_plunge_speed", "12"))
        self.z_raise_speed_entry.set_text( c.get("z_raise_speed",  "12"))
        self.speed_override_entry.set_text(c.get("speed_override", "100"))
        self.safety_margin_entry.set_text( c.get("safety_margin",  "5"))

        self.start_gcode_buffer.set_text(c.get("start_gcode", ""))
        self.end_gcode_buffer.set_text(  c.get("end_gcode",   ""))

        z_mode = c.get("z_mode", "servo")
        self.z_mode_combo.set_active(0 if z_mode == "servo" else 1)
        if hasattr(self, "update_z_fields_visibility"):
            self.update_z_fields_visibility()

        self.z_stepper_cut_entry.set_text(   c.get("z_stepper_cut_height",    "-2.0"))
        self.z_stepper_score_entry.set_text( c.get("z_stepper_score_height",  "-0.5"))
        self.z_stepper_travel_entry.set_text(c.get("z_stepper_travel_height",  "5.0"))

    def get_config_from_ui(self):
        cfg = {}
        cfg["bed_width"]  = self.bed_width_entry.get_text()
        cfg["bed_height"] = self.bed_height_entry.get_text()

        for key, btn in [
            ("front_left",  self.origin_front_left),
            ("front_right", self.origin_front_right),
            ("center",      self.origin_center),
            ("back_left",   self.origin_back_left),
            ("back_right",  self.origin_back_right),
        ]:
            if btn.get_active():
                cfg["origin_point"] = key
                break
        else:
            cfg["origin_point"] = "front_left"

        cfg["servo_score"]  = self.servo_score_entry.get_text()
        cfg["servo_cut"]    = self.servo_cut_entry.get_text()
        cfg["servo_travel"] = self.servo_travel_entry.get_text()
        cfg["servo_delay"]  = self.servo_delay_entry.get_text()

        cfg["tool_offset_x"] = self.tool_offset_x_entry.get_text()
        cfg["tool_offset_y"] = self.tool_offset_y_entry.get_text()
        cfg["tool_diameter"] = self.tool_diameter_entry.get_text()

        cfg["travel_speed"]    = self.travel_speed_entry.get_text()
        cfg["cutting_speed"]   = self.cutting_speed_entry.get_text()
        cfg["scoring_speed"]   = self.scoring_speed_entry.get_text()
        cfg["z_plunge_speed"]  = self.z_plunge_speed_entry.get_text()
        cfg["z_raise_speed"]   = self.z_raise_speed_entry.get_text()
        cfg["speed_override"]  = self.speed_override_entry.get_text()
        cfg["safety_margin"]   = self.safety_margin_entry.get_text()

        cfg["start_gcode"] = self.start_gcode_buffer.get_text(
            self.start_gcode_buffer.get_start_iter(),
            self.start_gcode_buffer.get_end_iter(), False)
        cfg["end_gcode"] = self.end_gcode_buffer.get_text(
            self.end_gcode_buffer.get_start_iter(),
            self.end_gcode_buffer.get_end_iter(), False)

        mode_text = self.z_mode_combo.get_active_text() or "Servo"
        cfg["z_mode"] = mode_text.lower()

        cfg["z_stepper_cut_height"]    = self.z_stepper_cut_entry.get_text()
        cfg["z_stepper_score_height"]  = self.z_stepper_score_entry.get_text()
        cfg["z_stepper_travel_height"] = self.z_stepper_travel_entry.get_text()

        # Carry over fields not in the UI so templates can still reference them
        cfg.setdefault("spindle_speed", "10000")
        cfg.setdefault("units", 0)
        cfg.setdefault("plunge_speed", "500")
        cfg.setdefault("score_line_color", "#00FF00")
        cfg.setdefault("cut_line_color",   "#FF0000")
        cfg.setdefault("max_velocity_xy",  "5000")
        cfg.setdefault("max_velocity_z",   "5000")
        cfg.setdefault("max_acceleration", "100")
        cfg.setdefault("jerk", "10")

        return cfg

    # ─── Export ───────────────────────────────────────────────────────────────

    def _show_export_dialog(self, gcode):
        last = self.config_manager.get_last_export_info()
        dlg  = Gtk.FileChooserDialog(
            title="Export G-code",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,   Gtk.ResponseType.OK,
        )
        dlg.set_current_name(last.get("filename", "output.gcode"))
        dlg.set_current_folder(last.get("dir", os.path.expanduser("~")))
        dlg.set_do_overwrite_confirmation(True)

        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            if path:
                self.config_manager.save_last_export_info(
                    os.path.dirname(path), os.path.basename(path)
                )
                try:
                    with open(path, "w") as f:
                        f.write(gcode)
                    logging.info(f"G-code exported to {path}")
                except OSError as e:
                    logging.error(f"Export failed: {e}")
                    err = Gtk.MessageDialog(
                        transient_for=self, flags=0,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Export failed",
                    )
                    err.format_secondary_text(str(e))
                    err.run()
                    err.destroy()
        dlg.destroy()

    # ─── Defaults / reset ─────────────────────────────────────────────────────

    def _on_save_default_clicked(self, _widget):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Save current settings as default?",
        )
        dlg.format_secondary_text("Overwrites the default configuration for all future sessions.")
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            cfg = self.get_config_from_ui()
            self.config_manager.save_default(cfg)
            self.config_manager.save_config(cfg)
            self.default_config = cfg.copy()
            logging.info("Settings saved as default.")

    def _on_reset_defaults_clicked(self, _widget):
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Reset all settings to default?",
        )
        dlg.format_secondary_text("Reverts all fields to the saved default configuration.")
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            self.auto_save_enabled = False
            self.config = self.config_manager.load_default()
            self.load_config_to_ui()
            self.auto_save_enabled = True
            self.config_manager.save_config(self.get_config_from_ui())
            logging.info("Settings reset to defaults.")

    def _on_dialog_close(self, _widget):
        self.config_manager.save_config(self.get_config_from_ui())
        Gtk.main_quit()

    # ─── Toast notifications ──────────────────────────────────────────────────

    def log_message(self, msg, level="info"):
        """Show a floating toast in the preview area. Errors persist; info auto-hides after 3 s."""
        if self.notification_label is not None:
            try:
                self.notification_overlay.remove(self.notification_label)
            except Exception:
                pass
            self.notification_label = None
        if self.notification_timeout_id is not None:
            GLib.source_remove(self.notification_timeout_id)
            self.notification_timeout_id = None

        lbl = Gtk.Label(label=msg)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_valign(Gtk.Align.END)
        lbl.set_margin_start(16)
        lbl.set_margin_bottom(16)
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(50)
        css_class = "toast-error" if level in ("error", "critical") else "toast-info"
        lbl.get_style_context().add_class(css_class)
        self.notification_overlay.add_overlay(lbl)
        self.notification_label = lbl
        lbl.show()

        def _fade():
            steps, interval = 6, 50
            label_ref = self.notification_label

            def _step(i=0):
                if label_ref is None or label_ref is not self.notification_label:
                    return False
                label_ref.set_opacity(max(0.0, 1.0 - (i + 1) / steps))
                if i + 1 < steps:
                    GLib.timeout_add(interval, _step, i + 1)
                else:
                    try:
                        self.notification_overlay.remove(label_ref)
                    except Exception:
                        pass
                    if self.notification_label is label_ref:
                        self.notification_label = None
                return False

            _step()
            self.notification_timeout_id = None
            return False

        self._fade_out_error_notification = _fade

        if level not in ("error", "critical"):
            self.notification_timeout_id = GLib.timeout_add(3000, _fade)

    def fade_out_error_notification(self):
        if hasattr(self, "_fade_out_error_notification") and self.notification_label:
            self._fade_out_error_notification()
            self._fade_out_error_notification = None

    # ─── Progress indicator ───────────────────────────────────────────────────

    def _on_progress_haze_draw(self, widget, cr):
        alloc = widget.get_allocation()
        cr.set_source_rgba(0.2, 0.5, 1.0, 0.7)
        cr.rectangle(0, 0, alloc.width * self.progress_fraction, alloc.height)
        cr.fill()

    def _start_progress(self):
        self.progress_animating = True
        self.progress_fraction  = 0.0
        self.progress_haze.show()
        try:
            self.generate_export_button.set_sensitive(False)
            self.auto_center_button.set_sensitive(False)
        except Exception:
            pass
        self._progress_tick()

    def _stop_progress(self):
        self.progress_animating = False
        self.progress_haze.hide()
        self.progress_fraction = 0.0
        try:
            self.generate_export_button.set_sensitive(True)
            self.auto_center_button.set_sensitive(True)
        except Exception:
            pass
        self.progress_haze.queue_draw()

    def _progress_tick(self):
        if not self.progress_animating:
            return False
        self.progress_fraction = min(self.progress_fraction + 0.04, 0.95)
        self.progress_haze.queue_draw()
        if self.progress_animating:
            GLib.timeout_add(30, self._progress_tick)
        return False

    # ─── 2-D Preview ─────────────────────────────────────────────────────────

    def _view_transform(self, widget_w, widget_h, bed_w, bed_h):
        """
        Returns (scale, ox, oy).
        Any bed coordinate (bx, by) maps to screen (bx*scale + ox, by*scale + oy).
        At zoom=1, pan=0 the bed is perfectly centred in the viewport with 4% padding.
        """
        fit_scale = min(widget_w / bed_w, widget_h / bed_h) * 0.96
        scale = fit_scale * self._view_zoom
        # Centre position at current zoom (pan=0)
        cx = (widget_w  - bed_w * scale) / 2
        cy = (widget_h - bed_h * scale) / 2
        return scale, cx + self._view_pan_x, cy + self._view_pan_y

    def _clamp_pan(self, widget_w, widget_h, bed_w, bed_h):
        """
        If zoom=1: force pan to zero (bed is centred, no scrolling needed).
        If zoomed in: clamp so at least MARGIN_PX pixels of bed stay visible
        on every edge.
        """
        if self._view_zoom <= 1.0:
            self._view_pan_x = 0.0
            self._view_pan_y = 0.0
            return

        MARGIN_PX = 60
        fit_scale = min(widget_w / bed_w, widget_h / bed_h) * 0.96
        scale = fit_scale * self._view_zoom
        cx = (widget_w  - bed_w * scale) / 2   # centre offset at pan=0
        cy = (widget_h - bed_h * scale) / 2

        # bed occupies screen x: [cx+pan_x , cx+pan_x+bed_w*scale]
        # keep right edge:  cx + pan_x + bed_w*scale  >= MARGIN_PX
        # keep left edge:   cx + pan_x                <= widget_w - MARGIN_PX
        self._view_pan_x = max(MARGIN_PX - cx - bed_w * scale,
                               min(self._view_pan_x, widget_w - MARGIN_PX - cx))
        self._view_pan_y = max(MARGIN_PX - cy - bed_h * scale,
                               min(self._view_pan_y, widget_h - MARGIN_PX - cy))

    def _on_preview_draw(self, widget, cr):
        vw = widget.get_allocated_width()
        vh = widget.get_allocated_height()

        # Dark background fills the whole widget
        cr.set_source_rgb(0.13, 0.13, 0.18)
        cr.rectangle(0, 0, vw, vh)
        cr.fill()

        cfg    = self._cached_config or self.get_config_from_ui()
        bed_w  = float(cfg.get("bed_width",    300))
        bed_h  = float(cfg.get("bed_height",   200))
        margin = float(cfg.get("safety_margin", 5))

        scale, ox, oy = self._view_transform(vw, vh, bed_w, bed_h)

        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)

        # ── Bed ──
        cr.set_source_rgb(0.96, 0.96, 0.98)
        cr.rectangle(0, 0, bed_w, bed_h)
        cr.fill_preserve()
        cr.set_source_rgb(0.30, 0.30, 0.35)
        cr.set_line_width(1.5 / scale)
        cr.stroke()

        # ── Grid ──
        thin = 0.4 / scale
        bold = 0.7 / scale

        cr.set_source_rgba(0.72, 0.72, 0.77, 0.45)
        cr.set_line_width(thin)
        for x in range(10, int(bed_w), 10):
            cr.move_to(x, 0); cr.line_to(x, bed_h)
        for y in range(10, int(bed_h), 10):
            cr.move_to(0, y); cr.line_to(bed_w, y)
        cr.stroke()

        cr.set_source_rgba(0.50, 0.50, 0.56, 0.75)
        cr.set_line_width(bold)
        for x in range(50, int(bed_w), 50):
            cr.move_to(x, 0); cr.line_to(x, bed_h)
        for y in range(50, int(bed_h), 50):
            cr.move_to(0, y); cr.line_to(bed_w, y)
        cr.stroke()

        # ── Safety margin (dashed orange) ──
        dash = 5.0 / scale
        cr.set_source_rgba(1.0, 0.60, 0.10, 0.50)
        cr.set_line_width(0.8 / scale)
        cr.set_dash([dash, dash * 0.6], 0)
        cr.rectangle(margin, margin, bed_w - 2 * margin, bed_h - 2 * margin)
        cr.stroke()
        cr.set_dash([], 0)

        # ── Origin marker + axis arrows ──
        origin = cfg.get("origin_point", "front_left")
        _oc = {
            "front_left":  (0,       bed_h,   1,  -1),
            "front_right": (bed_w,   bed_h,  -1,  -1),
            "back_left":   (0,       0,        1,   1),
            "back_right":  (bed_w,   0,       -1,   1),
            "center":      (bed_w/2, bed_h/2,  1,  -1),
        }
        ox_m, oy_m, xd, yd = _oc.get(origin, (0, bed_h, 1, -1))
        arrow = 35.0

        cr.set_source_rgb(0.25, 0.55, 1.0)
        cr.arc(ox_m, oy_m, 6.0 / scale, 0, 2 * math.pi)
        cr.fill()

        for color, adx, ady in [
            ((0.95, 0.25, 0.25), arrow * xd, 0),
            ((0.20, 0.80, 0.30), 0, arrow * yd),
        ]:
            cr.set_source_rgb(*color)
            cr.set_line_width(2.2 / scale)
            cr.move_to(ox_m, oy_m)
            cr.line_to(ox_m + adx, oy_m + ady)
            cr.stroke()
            # Filled arrowhead
            tip_x = ox_m + adx
            tip_y = oy_m + ady
            length = math.hypot(adx, ady)
            if length > 0:
                ux, uy   = adx / length, ady / length       # unit forward
                px, py   = -uy * 5 / scale, ux * 5 / scale  # perpendicular
                back     = 11.0 / scale
                cr.move_to(tip_x, tip_y)
                cr.line_to(tip_x - ux * back + px, tip_y - uy * back + py)
                cr.line_to(tip_x - ux * back - px, tip_y - uy * back - py)
                cr.close_path()
                cr.fill()

        # ── Tool paths ──
        has_cut   = bool(self.generated_cut_paths)
        has_score = bool(self.generated_score_paths)

        if has_cut or has_score:
            lw = 1.2 / scale

            if has_cut:
                cr.set_source_rgb(0.05, 0.05, 0.08)
                cr.set_line_width(lw)
                for path in self.generated_cut_paths:
                    for sub in path:
                        if len(sub) < 2:
                            continue
                        cr.move_to(sub[0][0], sub[0][1])
                        for pt in sub[1:]:
                            cr.line_to(pt[0], pt[1])
                        cr.stroke()

            if has_score:
                cr.set_source_rgb(0.85, 0.15, 0.15)
                cr.set_line_width(lw)
                for path in self.generated_score_paths:
                    for sub in path:
                        if len(sub) < 2:
                            continue
                        cr.move_to(sub[0][0], sub[0][1])
                        for pt in sub[1:]:
                            cr.line_to(pt[0], pt[1])
                        cr.stroke()

        cr.restore()

    # ─── Preview interaction (zoom / pan / drag) ──────────────────────────────

    def _on_preview_scroll(self, widget, event):
        if event.direction == Gdk.ScrollDirection.UP:
            factor = 1.15
        elif event.direction == Gdk.ScrollDirection.DOWN:
            factor = 1.0 / 1.15
        else:
            return True

        vw = widget.get_allocated_width()
        vh = widget.get_allocated_height()
        cfg   = self.get_config_from_ui()
        bed_w = float(cfg.get("bed_width",  300))
        bed_h = float(cfg.get("bed_height", 200))

        # Bed point currently under the cursor — must stay fixed after zoom
        scale, ox, oy = self._view_transform(vw, vh, bed_w, bed_h)
        bx_under = (event.x - ox) / scale
        by_under = (event.y - oy) / scale

        new_zoom = max(1.0, min(self._view_zoom * factor, 14.0))
        self._view_zoom = new_zoom

        # Recalculate new centre offset at new zoom (pan still at old value)
        new_fit   = min(vw / bed_w, vh / bed_h) * 0.96
        new_scale = new_fit * new_zoom
        new_cx    = (vw - bed_w * new_scale) / 2
        new_cy    = (vh - bed_h * new_scale) / 2

        # Solve for pan so that (bx_under, by_under) stays at (event.x, event.y)
        # event.x = bx_under * new_scale + new_cx + new_pan_x
        self._view_pan_x = event.x - bx_under * new_scale - new_cx
        self._view_pan_y = event.y - by_under * new_scale - new_cy

        self._clamp_pan(vw, vh, bed_w, bed_h)
        self._cached_config = cfg       # reuse, don't invalidate
        self.gcode_preview.queue_draw()
        return True

    def _on_preview_button_press(self, widget, event):
        if event.button == 2:
            self._view_drag      = True
            self._view_drag_last = (event.x, event.y)
        elif event.button == 1:
            if self.generated_cut_paths or self.generated_score_paths:
                bounds = self._get_paths_screen_bounds(widget)
                if bounds:
                    mx, my, Mx, My = bounds
                    if mx <= event.x <= Mx and my <= event.y <= My:
                        self.is_object_dragging = True
                        self.drag_last_mouse_x  = event.x
                        self.drag_last_mouse_y  = event.y
        return True

    def _on_preview_button_release(self, widget, event):
        if event.button == 2:
            self._view_drag = False
        elif event.button == 1 and self.is_object_dragging:
            self.is_object_dragging = False
            self._generate_gcode_from_current_paths()
        return True

    def _on_preview_motion(self, widget, event):
        if self._view_drag:
            dx = event.x - self._view_drag_last[0]
            dy = event.y - self._view_drag_last[1]
            self._view_pan_x += dx
            self._view_pan_y += dy
            self._view_drag_last = (event.x, event.y)

            cfg   = self._cached_config or self.get_config_from_ui()
            bed_w = float(cfg.get("bed_width",  300))
            bed_h = float(cfg.get("bed_height", 200))
            self._clamp_pan(
                widget.get_allocated_width(),
                widget.get_allocated_height(),
                bed_w, bed_h,
            )
            self.gcode_preview.queue_draw()

        elif self.is_object_dragging:
            cfg   = self.get_config_from_ui()
            bed_w = float(cfg.get("bed_width",  300))
            bed_h = float(cfg.get("bed_height", 200))
            vw = widget.get_allocated_width()
            vh = widget.get_allocated_height()

            scale, _, _ = self._view_transform(vw, vh, bed_w, bed_h)
            dx_bed = (event.x - self.drag_last_mouse_x) / scale
            dy_bed = (event.y - self.drag_last_mouse_y) / scale

            # Translate paths
            new_cut   = self._translate_paths(self.generated_cut_paths,   dx_bed, dy_bed)
            new_score = self._translate_paths(self.generated_score_paths, dx_bed, dy_bed)

            # Clamp: don't let the design's bounding box move more than
            # half a bed-length outside the bed on any edge.
            all_pts = [
                pt
                for pl in new_cut + new_score
                for sub in pl for pt in sub
            ]
            if all_pts:
                min_x = min(p[0] for p in all_pts)
                max_x = max(p[0] for p in all_pts)
                min_y = min(p[1] for p in all_pts)
                max_y = max(p[1] for p in all_pts)
                limit = 0.5   # fraction of bed allowed outside
                if (min_x > -bed_w * limit and max_x < bed_w * (1 + limit) and
                        min_y > -bed_h * limit and max_y < bed_h * (1 + limit)):
                    self.generated_cut_paths   = new_cut
                    self.generated_score_paths = new_score
                    self.drag_last_mouse_x = event.x
                    self.drag_last_mouse_y = event.y
            else:
                self.generated_cut_paths   = new_cut
                self.generated_score_paths = new_score
                self.drag_last_mouse_x = event.x
                self.drag_last_mouse_y = event.y

            GLib.idle_add(self._update_info_panel)
            self.gcode_preview.queue_draw()
        return True

    def _get_paths_screen_bounds(self, widget):
        """Return (min_x, min_y, max_x, max_y) in screen pixels for the stored paths."""
        pts = [
            pt
            for pl in [self.generated_cut_paths or [], self.generated_score_paths or []]
            for path in pl for sub in path for pt in sub
        ]
        if not pts:
            return None

        cfg   = self.get_config_from_ui()
        bed_w = float(cfg.get("bed_width",  300))
        bed_h = float(cfg.get("bed_height", 200))
        scale, ox, oy = self._view_transform(
            widget.get_allocated_width(),
            widget.get_allocated_height(),
            bed_w, bed_h,
        )
        xs = [p[0] * scale + ox for p in pts]
        ys = [p[1] * scale + oy for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    # ─── Path utilities ───────────────────────────────────────────────────────

    def _center_paths_on_bed(self, cut_paths, score_paths, bed_w, bed_h, margin):
        """
        Translate cut + score paths together so the design is centred within
        the usable area (bed minus safety margin on all four sides).
        """
        all_pts = [
            pt
            for pl in cut_paths + score_paths
            for sub in pl for pt in sub
        ]
        if not all_pts:
            return cut_paths, score_paths

        min_x = min(p[0] for p in all_pts)
        max_x = max(p[0] for p in all_pts)
        min_y = min(p[1] for p in all_pts)
        max_y = max(p[1] for p in all_pts)
        design_w = max_x - min_x
        design_h = max_y - min_y

        usable_w = bed_w - 2 * margin
        usable_h = bed_h - 2 * margin

        # Centre within the usable area; if the design is too big, anchor to margin
        cx = margin + max(0.0, (usable_w - design_w) / 2)
        cy = margin + max(0.0, (usable_h - design_h) / 2)

        dx = cx - min_x
        dy = cy - min_y

        def _shift(paths):
            return [
                [[(x + dx, y + dy) for x, y in sub] for sub in path]
                for path in paths
            ]
        return _shift(cut_paths), _shift(score_paths)

    @staticmethod
    def _translate_paths(paths, dx, dy):
        if not paths:
            return []
        return [
            [[(x + dx, y + dy) for x, y in sub] for sub in path]
            for path in paths
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Inkscape Effect entry point
# ─────────────────────────────────────────────────────────────────────────────

# Snappier GTK feel
_gtk_settings = Gtk.Settings.get_default()
if _gtk_settings:
    _gtk_settings.set_property("gtk-enable-animations", True)


class JDCncGcodeGenerator(inkex.Effect):
    """Inkscape Effect — the single entry point for the extension."""

    def effect(self):
        if not self.svg.selection:
            dlg = Gtk.MessageDialog(
                transient_for=None,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="No objects selected",
            )
            dlg.format_secondary_text(
                "Select one or more paths in Inkscape before running "
                "the CNC G-code Generator."
            )
            dlg.run()
            dlg.destroy()
            return

        dialog = CNCDialog(None, effect=self)
        dialog.run()


if __name__ == "__main__":
    JDCncGcodeGenerator().run()