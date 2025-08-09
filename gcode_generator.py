#!/usr/bin/env python3
"""
JD CNC G-code Generator - Main Extension File
Creates a comprehensive CNC cutter control dialog for Inkscape.
"""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk
from gi.repository import GLib
import inkex
from config_manager import ConfigManager
from svg_parser import SVGParser
from gcode_logic import GCodeLogic
import threading
import os
import logging

# Define a script version for easier debugging
SCRIPT_VERSION = "1.0.9" # Incremented version for zoom and pan with middle click

class CNCDialog(Gtk.Dialog):
    """Main CNC Cutter Control Dialog class."""

    def __init__(self, effect):
        # CORRECTED: Use modern keyword arguments instead of deprecated 'flags'
        super().__init__(title="JD CNC G-code Generator", modal=True, destroy_with_parent=True)
        self.effect = effect
        
        # Initialize backend components
        self.config_manager = ConfigManager("JD_CNC_Gcode_Generator")
        self.svg_parser = SVGParser(effect.svg)
        self.gcode_logic = GCodeLogic()
        
        # Load last-used configuration
        self.config = self.config_manager.load_config()
        self.default_config = self.config_manager.load_default()
        
        self.set_default_size(1200, 800)
        self.set_resizable(True)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_border_width(10)
        # Notebook for tabbed interface
        self.notebook = Gtk.Notebook()
        self.notebook.set_hexpand(True)
        # Overlay for notebook and hamburger menu
        overlay = Gtk.Overlay()
        overlay.add(self.notebook)
        menu_button = self._create_hamburger_menu()
        overlay.add_overlay(menu_button)
        menu_button.set_halign(Gtk.Align.END)
        menu_button.set_valign(Gtk.Align.START)
        menu_button.set_margin_top(2)
        menu_button.set_margin_end(2)
        main_box.pack_start(overlay, True, True, 0)
        self.get_content_area().add(main_box)

        # Create and add all the tabs from your outline
        self.create_home_tab()
        self.create_bed_config_tab()
        self.create_tool_options_tab()
        self.create_speeds_and_limits_tab()
        self.create_gcode_templates_tab()
        # Create bottom button panel
        button_box = self.create_button_panel()
        button_box.show_all()
        main_box.pack_start(button_box, False, False, 0)
        # Populate UI with loaded config
        self.load_config_to_ui()
        self.connect("destroy", self.on_dialog_close)
        self.show_all()
        self.queue_resize()
        self.queue_draw()
        self.notebook.set_current_page(0)
        self.auto_save_enabled = True
        self.connect_auto_save()

        # Initialize generated paths and G-code state
        self.generated_cut_paths = None
        self.generated_score_paths = None
        self.gcode_generated = False

        # Dragging state for the object itself
        self.is_object_dragging = False
        self.drag_last_mouse_x = 0
        self.drag_last_mouse_y = 0

        # Preview state for zoom and pan
        self.gcode_preview_zoom = 1.0 # Initial zoom level
        self.gcode_preview_offset = [0, 0] # Initial pan offset
        self.gcode_preview_drag = False # For view panning (middle click)
        self.gcode_preview_last = (0, 0) # For view panning

        # Set up logging to the log panel
        self._setup_logging()

        # Initial setup: auto-center and then generate G-code
        GLib.idle_add(self._initial_setup)


    def _setup_logging(self):
        """
        Sets up the logging system to direct messages to the Gtk.TextView log panel.
        Creates Gtk.TextTag objects for coloring messages.
        """
        class GtkLogHandler(logging.Handler):
            def __init__(self, dialog):
                super().__init__()
                self.dialog = dialog

            def emit(self, record):
                msg = self.format(record)
                # Ensure log_message is called on the main GTK thread
                GLib.idle_add(self.dialog.log_message, msg, record.levelname.lower())

        # Configure root logger to use our custom handler
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        # Clear existing handlers to prevent duplicate messages if run multiple times
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        
        handler = GtkLogHandler(self)
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        root_logger.addHandler(handler)

        # Redirect warnings to our logging system
        import warnings
        def warning_to_log(message, category, filename, lineno, file=None, line=None):
            logging.warning(f'{category.__name__}: {message} ({filename}:{lineno})')
        warnings.showwarning = warning_to_log

        # Setup tags for colored messages and store them as attributes
        # These tags are created directly on the buffer and then reused.
        self.info_tag = self.log_buffer.create_tag("info", foreground="white") # White for info and warnings
        self.error_tag = self.log_buffer.create_tag("error", foreground="#FF0000", weight=600) # Nice red and bold for errors


    def connect_auto_save(self):
        """Connects all relevant UI fields to auto-save handler."""
        def auto_save(*args):
            if self.auto_save_enabled:
                current_config = self.get_config_from_ui()
                self.config_manager.save_config(current_config)
        # Entry fields
        def entry_commit_handler(entry, *args):
            pass  # No undo/redo, so nothing needed here
        for entry in [self.bed_width_entry, self.bed_height_entry,
                      self.servo_score_entry, self.servo_cut_entry, self.servo_travel_entry,
                      self.servo_delay_entry, self.tool_offset_x_entry, self.tool_offset_y_entry,
                      self.tool_diameter_entry, self.travel_speed_entry, self.z_plunge_speed_entry, self.z_raise_speed_entry, self.cutting_speed_entry, self.scoring_speed_entry,
                      self.max_velocity_xy_entry, self.max_velocity_z_entry, self.max_acceleration_entry, self.jerk_entry, self.speed_override_entry, self.safety_margin_entry, self.spindle_speed_entry,
                      self.z_stepper_cut_entry, self.z_stepper_score_entry, self.z_stepper_travel_entry]:
            entry.connect("changed", auto_save)
            entry.connect("focus-out-event", entry_commit_handler)
            entry.connect("activate", entry_commit_handler)
        # Radio buttons (origin)
        for btn in [self.origin_front_left, self.origin_front_right, self.origin_center, self.origin_back_left, self.origin_back_right]:
            btn.connect("toggled", lambda *a: None)
        # Text buffers
        self.start_gcode_buffer.connect("changed", auto_save)
        self.end_gcode_buffer.connect("changed", auto_save)

    def create_frame(self, label, margin=10):
        """Helper to create a styled frame without a visible label/title."""
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        grid = Gtk.Grid(row_spacing=8, column_spacing=10, margin=margin)
        frame.add(grid)
        return frame, grid

    def create_bed_config_tab(self):
        """Creates the Bed & Origin tab with a single grid and section headers, no extra frames."""
        frame, grid = self.create_frame("")
        row = 0
        # Bed Size Section Header
        bed_label = Gtk.Label()
        bed_label.set_markup('<b>Bed Size</b>')
        bed_label.set_halign(Gtk.Align.START)
        grid.attach(bed_label, 0, row, 3, 1)
        row += 1
        grid.attach(Gtk.Label(label="Bed Width (X)"), 0, row, 1, 1)
        self.bed_width_entry = Gtk.Entry()
        self.bed_width_entry.set_width_chars(8)
        grid.attach(self.bed_width_entry, 1, row, 1, 1)
        mm_label1 = Gtk.Label(label="mm")
        mm_label1.set_halign(Gtk.Align.START)
        mm_label1.set_margin_start(4)
        grid.attach(mm_label1, 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Bed Height (Y)"), 0, row, 1, 1)
        self.bed_height_entry = Gtk.Entry()
        self.bed_height_entry.set_width_chars(8)
        grid.attach(self.bed_height_entry, 1, row, 1, 1)
        mm_label2 = Gtk.Label(label="mm")
        mm_label2.set_halign(Gtk.Align.START)
        mm_label2.set_margin_start(4)
        grid.attach(mm_label2, 2, row, 1, 1)
        row += 1
        # Origin Section Header
        origin_label = Gtk.Label()
        origin_label.set_markup('<b>Origin Point</b>')
        origin_label.set_halign(Gtk.Align.START)
        grid.attach(origin_label, 0, row, 3, 1)
        row += 1
        # Origin radio buttons
        self.origin_front_left = Gtk.RadioButton.new_with_label(None, "Front Left")
        self.origin_front_right = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left, "Front Right")
        self.origin_center = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left, "Center")
        self.origin_back_left = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left, "Back Left")
        self.origin_back_right = Gtk.RadioButton.new_with_label_from_widget(self.origin_front_left, "Back Right")
        grid.attach(self.origin_front_left, 0, row, 1, 1)
        grid.attach(self.origin_front_right, 1, row, 1, 1)
        row += 1
        grid.attach(self.origin_center, 0, row, 1, 1)
        grid.attach(self.origin_back_left, 1, row, 1, 1)
        grid.attach(self.origin_back_right, 2, row, 1, 1)
        row += 1
        # Bed preview drawing area
        self.bed_preview = Gtk.DrawingArea()
        self.bed_preview.set_size_request(300, 200)
        self.bed_preview.set_hexpand(True)
        self.bed_preview.set_vexpand(True)
        self.bed_preview.connect("draw", self.on_bed_preview_draw)
        self.bed_width_entry.connect("changed", lambda w: self.bed_preview.queue_draw())
        self.bed_height_entry.connect("changed", lambda w: self.bed_preview.queue_draw())
        self.origin_front_left.connect("toggled", lambda w: self.bed_preview.queue_draw())
        self.origin_front_right.connect("toggled", lambda w: self.bed_preview.queue_draw())
        self.origin_center.connect("toggled", lambda w: self.bed_preview.queue_draw())
        self.origin_back_left.connect("toggled", lambda w: self.bed_preview.queue_draw())
        self.origin_back_right.connect("toggled", lambda w: self.bed_preview.queue_draw())
        # Add grid and preview to a vbox
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_vbox.pack_start(frame, False, False, 0)
        main_vbox.pack_start(self.bed_preview, True, True, 0)
        self.notebook.append_page(main_vbox, Gtk.Label(label="Bed & Origin"))

    def on_bed_preview_draw(self, widget, cr):
        """Draws a fast, responsive 2D preview of the bed, origin, and cutter head with responsive X/Y arrows."""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        # Get bed size from entries, fallback to defaults
        try:
            bed_w = float(self.bed_width_entry.get_text())
        except ValueError:
            bed_w = 300
        try:
            bed_h = float(self.bed_height_entry.get_text())
        except ValueError:
            bed_h = 200
        # Calculate scale to fit preview area
        scale = min(width / bed_w, height / bed_h) * 0.9
        offset_x = (width - bed_w * scale) / 2
        offset_y = (height - bed_h * scale) / 2
        # Draw bed rectangle
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.rectangle(offset_x, offset_y, bed_w * scale, bed_h * scale)
        cr.fill_preserve()
        cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.set_line_width(2)
        cr.stroke()
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.set_line_width(1)
        step = 50
        for x in range(step, int(bed_w), step):
            cr.move_to(offset_x + x * scale, offset_y)
            cr.line_to(offset_x + x * scale, offset_y + bed_h * scale)
        for y in range(step, int(bed_h), step):
            cr.move_to(offset_x, offset_y + y * scale)
            cr.line_to(offset_x + bed_w * scale, offset_y + y * scale)
        cr.stroke()
        # Draw origin/cutter head marker (blue)
        cr.set_source_rgb(0.1, 0.4, 1.0)
        cr.set_line_width(3)
        # Map UI to preview: front = bottom, back = top
        if self.origin_front_left.get_active():
            ox, oy = offset_x, offset_y + bed_h * scale
            x_dir, y_dir = 1, -1
        elif self.origin_front_right.get_active():
            ox, oy = offset_x + bed_w * scale, offset_y + bed_h * scale
            x_dir, y_dir = -1, -1
        elif self.origin_center.get_active():
            ox, oy = offset_x + (bed_w * scale) / 2, offset_y + (bed_h * scale) / 2
            x_dir, y_dir = 1, -1
        elif self.origin_back_left.get_active():
            ox, oy = offset_x, offset_y
            x_dir, y_dir = 1, 1
        elif self.origin_back_right.get_active():
            ox, oy = offset_x + bed_w * scale, offset_y
            x_dir, y_dir = -1, 1
        else:
            ox, oy = offset_x, offset_y + bed_h * scale
            x_dir, y_dir = 1, -1
        # Draw cutter head circle (blue)
        cr.arc(ox, oy, 8, 0, 2 * 3.1416)
        cr.fill_preserve()
        cr.set_source_rgb(0, 0, 0)
        cr.set_line_width(1)
        cr.stroke()
        # Crosshairs
        cr.set_source_rgb(0, 0, 0)
        cr.move_to(ox - 12, oy)
        cr.line_to(ox + 12, oy)
        cr.move_to(ox, oy - 12)
        cr.line_to(ox, oy + 12)
        cr.stroke()
        # Draw X (red) and Y (green) arrows responsively
        arrow_len = 40
        # X arrow (red)
        cr.set_source_rgb(1, 0, 0)
        cr.set_line_width(3)
        cr.move_to(ox, oy)
        cr.line_to(ox + arrow_len * x_dir, oy)
        cr.stroke()
        cr.move_to(ox + arrow_len * x_dir, oy)
        cr.line_to(ox + (arrow_len - 10) * x_dir, oy - 7)
        cr.move_to(ox + arrow_len * x_dir, oy)
        cr.line_to(ox + (arrow_len - 10) * x_dir, oy + 7)
        cr.stroke()
        # Y arrow (green)
        cr.set_source_rgb(0, 0.7, 0)
        cr.set_line_width(3)
        cr.move_to(ox, oy)
        cr.line_to(ox, oy + arrow_len * y_dir)
        cr.stroke()
        cr.move_to(ox, oy + arrow_len * y_dir)
        cr.line_to(ox - 7, oy + (arrow_len - 10) * y_dir)
        cr.move_to(ox, oy + arrow_len * y_dir)
        cr.line_to(ox + 7, oy + (arrow_len - 10) * y_dir)
        cr.stroke()

    def create_tool_options_tab(self):
        """Creates a combined Tool Options tab with Z Axis, Spindle, and Tool Offset sections, using a single grid layout with section headers and unit labels."""
        frame, grid = self.create_frame("")
        row = 0
        # Z Axis Section Header
        z_label = Gtk.Label()
        z_label.set_markup('<b>Z Axis</b>')
        z_label.set_halign(Gtk.Align.START)
        grid.attach(z_label, 0, row, 3, 1)
        row += 1
        # Z Axis Type Selector
        z_type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        z_type_label = Gtk.Label(label="Z Axis Type:")
        z_type_label.set_halign(Gtk.Align.START)
        self.z_mode_combo = Gtk.ComboBoxText()
        self.z_mode_combo.append_text("Servo")
        self.z_mode_combo.append_text("Stepper")
        z_type_box.pack_start(z_type_label, False, False, 0)
        z_type_box.pack_start(self.z_mode_combo, False, False, 0)
        grid.attach(z_type_box, 0, row, 3, 1)
        row += 1
        # Container for dynamic fields
        self.z_fields_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        grid.attach(self.z_fields_stack, 0, row, 3, 1)
        row += 1
        # Servo fields group
        self.servo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # Servo Score Position
        servo_score_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        servo_score_row.pack_start(Gtk.Label(label="Servo Score Position"), False, False, 0)
        self.servo_score_entry = Gtk.Entry()
        servo_score_row.pack_start(self.servo_score_entry, True, True, 0)
        servo_score_row.pack_start(Gtk.Label(label="°"), False, False, 0)
        self.servo_box.pack_start(servo_score_row, False, False, 0)
        # Servo Cut Position
        servo_cut_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        servo_cut_row.pack_start(Gtk.Label(label="Servo Cut Position"), False, False, 0)
        self.servo_cut_entry = Gtk.Entry()
        servo_cut_row.pack_start(self.servo_cut_entry, True, True, 0)
        servo_cut_row.pack_start(Gtk.Label(label="°"), False, False, 0)
        self.servo_box.pack_start(servo_cut_row, False, False, 0)
        # Servo Travel Position
        servo_travel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        servo_travel_row.pack_start(Gtk.Label(label="Servo Travel Position"), False, False, 0)
        self.servo_travel_entry = Gtk.Entry()
        servo_travel_row.pack_start(self.servo_travel_entry, True, True, 0)
        servo_travel_row.pack_start(Gtk.Label(label="°"), False, False, 0)
        self.servo_box.pack_start(servo_travel_row, False, False, 0)
        # Servo Delay
        self.servo_delay_entry = Gtk.Entry()
        servo_delay_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        servo_delay_row.pack_start(Gtk.Label(label="Servo Delay (ms)"), False, False, 0)
        servo_delay_row.pack_start(self.servo_delay_entry, True, True, 0)
        servo_delay_row.pack_start(Gtk.Label(label="ms"), False, False, 0)
        self.servo_box.pack_start(servo_delay_row, False, False, 0)
        # Stepper fields group
        self.stepper_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # Stepper Cut Height
        stepper_cut_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        stepper_cut_row.pack_start(Gtk.Label(label="Stepper Cut Height"), False, False, 0)
        self.z_stepper_cut_entry = Gtk.Entry()
        stepper_cut_row.pack_start(self.z_stepper_cut_entry, True, True, 0)
        stepper_cut_row.pack_start(Gtk.Label(label="mm"), False, False, 0)
        self.stepper_box.pack_start(stepper_cut_row, False, False, 0)
        # Stepper Score Height
        self.z_stepper_score_entry = Gtk.Entry()
        stepper_score_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        stepper_score_row.pack_start(Gtk.Label(label="Stepper Score Height"), False, False, 0)
        stepper_score_row.pack_start(self.z_stepper_score_entry, True, True, 0)
        stepper_score_row.pack_start(Gtk.Label(label="mm"), False, False, 0)
        self.stepper_box.pack_start(stepper_score_row, False, False, 0)
        # Stepper Travel Height
        stepper_travel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        stepper_travel_row.pack_start(Gtk.Label(label="Stepper Travel Height"), False, False, 0)
        self.z_stepper_travel_entry = Gtk.Entry()
        stepper_travel_row.pack_start(self.z_stepper_travel_entry, True, True, 0)
        stepper_travel_row.pack_start(Gtk.Label(label="mm"), False, False, 0)
        self.stepper_box.pack_start(stepper_travel_row, False, False, 0)
        # Show/hide logic
        def update_z_fields_visibility(combo):
            for child in self.z_fields_stack.get_children():
                self.z_fields_stack.remove(child)
            mode_text = self.z_mode_combo.get_active_text()
            mode = mode_text.lower() if mode_text else "servo"
            if mode == "servo":
                self.z_fields_stack.pack_start(self.servo_box, False, False, 0)
            else:
                self.z_fields_stack.pack_start(self.stepper_box, False, False, 0)
            self.z_fields_stack.show_all()
        self.z_mode_combo.connect("changed", update_z_fields_visibility)
        update_z_fields_visibility(self.z_mode_combo)
        # Tool Offset Section Header
        tool_label = Gtk.Label()
        tool_label.set_markup('<b>Tool Offset</b>')
        tool_label.set_halign(Gtk.Align.START)
        grid.attach(tool_label, 0, row, 3, 1)
        row += 1
        # Tool Offset fields
        grid.attach(Gtk.Label(label="X-axis Offset"), 0, row, 1, 1)
        self.tool_offset_x_entry = Gtk.Entry()
        grid.attach(self.tool_offset_x_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm"), 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Y-axis Offset"), 0, row, 1, 1)
        self.tool_offset_y_entry = Gtk.Entry()
        grid.attach(self.tool_offset_y_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm"), 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Tool Diameter"), 0, row, 1, 1)
        self.tool_diameter_entry = Gtk.Entry()
        grid.attach(self.tool_diameter_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm"), 2, row, 1, 1)
        row += 1
        # Spindle Section Header
        spindle_label = Gtk.Label()
        spindle_label.set_markup('<b>Spindle</b>')
        spindle_label.set_halign(Gtk.Align.START)
        grid.attach(spindle_label, 0, row, 3, 1)
        row += 1
        # Spindle Speed
        grid.attach(Gtk.Label(label="Spindle Speed"), 0, row, 1, 1)
        self.spindle_speed_entry = Gtk.Entry()
        grid.attach(self.spindle_speed_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="RPM"), 2, row, 1, 1)
        row += 1

        self.notebook.append_page(frame, Gtk.Label(label="Tool Options"))

    def create_speeds_and_limits_tab(self):
        """Creates a combined Speeds & Machine Limits tab with section headers and unit labels in mm/s and mm/s²."""
        frame, grid = self.create_frame("")
        row = 1
        # Speeds Section Header
        speeds_label = Gtk.Label()
        speeds_label.set_markup('<b>Speeds</b>')
        speeds_label.set_halign(Gtk.Align.START)
        grid.attach(speeds_label, 0, 0, 3, 1) # changed parameter 3 from row to 0
        # Speed Override
        grid.attach(Gtk.Label(label="Speed Override"), 0, row, 1, 1)
        self.speed_override_entry = Gtk.Entry()
        grid.attach(self.speed_override_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="%"), 2, row, 1, 1)
        row += 1
        # Speeds (all in mm/s)
        grid.attach(Gtk.Label(label="Travel Speed (Cutter Up)"), 0, row, 1, 1)
        self.travel_speed_entry = Gtk.Entry()
        grid.attach(self.travel_speed_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Cutting Speed (Black Lines)"), 0, row, 1, 1)
        self.cutting_speed_entry = Gtk.Entry()
        grid.attach(self.cutting_speed_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Scoring Speed (Red Lines)"), 0, row, 1, 1)
        self.scoring_speed_entry = Gtk.Entry()
        grid.attach(self.scoring_speed_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Z Plunge Speed (Down)"), 0, row, 1, 1)
        self.z_plunge_speed_entry = Gtk.Entry()
        grid.attach(self.z_plunge_speed_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        row += 1
        grid.attach(Gtk.Label(label="Z Raise Speed (Up)"), 0, row, 1, 1)
        self.z_raise_speed_entry = Gtk.Entry()
        grid.attach(self.z_raise_speed_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        row += 1
        # Limits Section Header
        limits_label = Gtk.Label()
        limits_label.set_markup('<b>Machine Limits</b>')
        limits_label.set_halign(Gtk.Align.START)
        grid.attach(limits_label, 0, row, 3, 1)
        row += 1
        # Max Velocity X/Y
        grid.attach(Gtk.Label(label="Max Velocity X/Y"), 0, row, 1, 1)
        self.max_velocity_xy_entry = Gtk.Entry()
        grid.attach(self.max_velocity_xy_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        row += 1
        # Max Velocity Z (only for stepper)
        grid.attach(Gtk.Label(label="Max Velocity Z"), 0, row, 1, 1)
        self.max_velocity_z_entry = Gtk.Entry()
        grid.attach(self.max_velocity_z_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s"), 2, row, 1, 1)
        self.max_velocity_z_row = row
        row += 1
        # Max Acceleration
        grid.attach(Gtk.Label(label="Max Acceleration"), 0, row, 1, 1)
        self.max_acceleration_entry = Gtk.Entry()
        grid.attach(self.max_acceleration_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s²"), 2, row, 1, 1)
        row += 1
        # Jerk
        grid.attach(Gtk.Label(label="Jerk"), 0, row, 1, 1)
        self.jerk_entry = Gtk.Entry()
        grid.attach(self.jerk_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm/s³"), 2, row, 1, 1)
        row += 1
        # Safety Margin
        grid.attach(Gtk.Label(label="Safety Margin"), 0, row, 1, 1)
        self.safety_margin_entry = Gtk.Entry()
        grid.attach(self.safety_margin_entry, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="mm"), 2, row, 1, 1)
        self.notebook.append_page(frame, Gtk.Label(label="Speeds & Limits"))

        # Show/hide Z velocity field based on Z mode
        def update_z_velocity_visibility():
            mode_text = self.z_mode_combo.get_active_text()
            mode = mode_text.lower() if mode_text else "servo"
            visible = (mode == "stepper")
            self.max_velocity_z_entry.set_visible(visible)
            # Also hide/show the label next to it
            for child in grid.get_children():
                if isinstance(child, Gtk.Label) and child.get_text() == "Max Velocity Z":
                    child.set_visible(visible)
        self.z_mode_combo.connect("changed", lambda combo: update_z_velocity_visibility())
        update_z_velocity_visibility()

    def create_gcode_templates_tab(self):
        """Creates the G-code Templates tab."""
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        # NOTE: Syntax highlighting is not available in Gtk.TextView by default.
        # For future improvement, consider using GtkSourceView for G-code syntax highlighting.
        # Start G-code
        frame_start, grid_start = self.create_frame("Start G-code", margin=5)
        self.start_gcode_buffer = Gtk.TextBuffer()
        self.start_gcode_view = Gtk.TextView(buffer=self.start_gcode_buffer, monospace=True)
        scroll_start = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll_start.add(self.start_gcode_view)
        grid_start.attach(scroll_start, 0, 0, 1, 1)
        
        # End G-code
        frame_end, grid_end = self.create_frame("End G-code", margin=5)
        self.end_gcode_buffer = Gtk.TextBuffer()
        self.end_gcode_view = Gtk.TextView(buffer=self.end_gcode_buffer, monospace=True)
        scroll_end = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll_end.add(self.end_gcode_view)
        grid_end.attach(scroll_end, 0, 0, 1, 1)

        main_vbox.pack_start(frame_start, True, True, 0)
        main_vbox.pack_start(frame_end, True, True, 0)
        self.notebook.append_page(main_vbox, Gtk.Label(label="G-code Templates"))

    def create_home_tab(self):
        """Creates the Home tab with a 2D G-code preview, generated G-code panel, and log panel."""
        # Main vertical container
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Horizontal container for preview and right panel
        top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_hbox.set_hexpand(True)
        top_hbox.set_vexpand(True)

        # --- 2D G-code preview (left) ---
        preview_frame = Gtk.Frame()
        preview_frame.set_shadow_type(Gtk.ShadowType.IN)
        preview_frame.set_hexpand(True)
        preview_frame.set_vexpand(True)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        self.gcode_preview = Gtk.DrawingArea()
        self.gcode_preview.set_size_request(500, 400)
        self.gcode_preview.set_hexpand(True)
        self.gcode_preview.set_vexpand(True)
        self.gcode_preview.connect("draw", self.on_gcode_preview_draw)
        # The preview area should always be shown, even if empty
        self.gcode_preview.show() 
        self.gcode_preview.add_events(
            Gdk.EventMask.SCROLL_MASK | 
            Gdk.EventMask.BUTTON_PRESS_MASK | 
            Gdk.EventMask.BUTTON_RELEASE_MASK | 
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.gcode_preview.connect("scroll-event", self.on_gcode_preview_scroll)
        self.gcode_preview.connect("button-press-event", self.on_gcode_preview_button_press)
        self.gcode_preview.connect("button-release-event", self.on_gcode_preview_button_release)
        self.gcode_preview.connect("motion-notify-event", self.on_gcode_preview_motion)
        preview_box.pack_start(self.gcode_preview, True, True, 0)
        preview_frame.add(preview_box)
        top_hbox.pack_start(preview_frame, True, True, 0)

        # --- Right panel: G-code and log ---
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_vbox.set_hexpand(True)

        # G-code panel
        gcode_frame = Gtk.Frame()
        gcode_frame.set_shadow_type(Gtk.ShadowType.IN)
        gcode_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.gcode_text_buffer = Gtk.TextBuffer()
        self.gcode_text_view = Gtk.TextView(buffer=self.gcode_text_buffer)
        self.gcode_text_view.set_editable(True)
        self.gcode_text_view.set_cursor_visible(True)
        self.gcode_text_view.set_monospace(True)
        gcode_scroll = Gtk.ScrolledWindow()
        gcode_scroll.set_hexpand(True)
        gcode_scroll.set_vexpand(True)
        gcode_scroll.set_min_content_height(500)
        gcode_scroll.add(self.gcode_text_view)
        gcode_vbox.pack_start(gcode_scroll, True, True, 0)
        gcode_frame.add(gcode_vbox)
        right_vbox.pack_start(gcode_frame, True, True, 0)

        # Log panel
        log_frame = Gtk.Frame()
        log_frame.set_shadow_type(Gtk.ShadowType.IN)
        log_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD) # Added this line for text wrapping
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_hexpand(True)
        log_scroll.set_vexpand(True)
        log_scroll.set_min_content_height(80)
        log_scroll.add(self.log_view)
        log_vbox.pack_start(log_scroll, True, True, 0)
        log_frame.add(log_vbox)
        right_vbox.pack_start(log_frame, True, True, 0)

        top_hbox.pack_start(right_vbox, True, True, 0)
        main_vbox.pack_start(top_hbox, True, True, 0)

        self.notebook.insert_page(main_vbox, Gtk.Label(label="Home"), 0)

    def create_button_panel(self):
        """Creates the bottom row with Auto Center, Generate and Export G-code buttons."""
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END) # Align buttons to the end (right)

        # --- Export G-code button ---
        export_button = Gtk.Button(label="Export G-code")
        export_button.get_style_context().add_class("suggested-action")
        export_button.connect("clicked", self.on_export_clicked)
        # Pack this last so it appears on the rightmost
        button_box.pack_end(export_button, False, False, 0)

        # --- Generate G-code button with overlay ---
        self.generate_button = Gtk.Button(label="Generate G-code")
        self.generate_button.connect("clicked", self.on_generate_clicked)
        self.generate_button_overlay = Gtk.Overlay()
        self.generate_button_overlay.set_size_request(160, 36) # Consistent size
        self.generate_button_overlay.add(self.generate_button)
        # Progress haze overlay
        self.progress_haze = Gtk.DrawingArea()
        self.progress_haze.set_size_request(160, 36)
        self.progress_haze.set_no_show_all(True)
        self.progress_haze.set_opacity(0.5)
        self.progress_haze.connect("draw", self.on_progress_haze_draw)
        self.generate_button_overlay.add_overlay(self.progress_haze)
        self.progress_fraction = 0.0
        self.progress_animating = False
        self.progress_haze.hide()  # Ensure haze is hidden at startup
        # Pack this before export button
        button_box.pack_end(self.generate_button_overlay, False, False, 0)

        # --- Auto Center button ---
        self.auto_center_button = Gtk.Button(label="Auto Center")
        self.auto_center_button.connect("clicked", self.on_auto_center_clicked)
        # Set size request to match other buttons for consistency
        self.auto_center_button.set_size_request(160, 36) 
        # Pack this first so it appears on the leftmost of the group
        button_box.pack_end(self.auto_center_button, False, False, 0)

        return button_box

    def on_progress_haze_draw(self, widget, cr):
        alloc = widget.get_allocation()
        width = alloc.width
        height = alloc.height
        # Draw blue haze from left to progress_fraction
        cr.set_source_rgba(0.2, 0.5, 1.0, 0.7)
        cr.rectangle(0, 0, width * self.progress_fraction, height)
        cr.fill()

    def start_progress(self):
        self.progress_fraction = 0.0
        self.progress_animating = True
        self.progress_haze.show()
        self.generate_button.set_sensitive(False)
        # Disable auto center button during generation
        self.auto_center_button.set_sensitive(False) 
        self._progress_tick()

    def stop_progress(self):
        self.progress_animating = False
        self.progress_haze.hide()
        self.generate_button.set_sensitive(True)
        # Re-enable auto center button after generation
        self.auto_center_button.set_sensitive(True) 
        self.progress_fraction = 0.0
        self.progress_haze.queue_draw()

    def _progress_tick(self):
        if not self.progress_animating:
            return False
        self.progress_fraction += 0.04
        if self.progress_fraction > 1.0:
            self.progress_fraction = 1.0
        self.progress_haze.queue_draw()
        if self.progress_fraction < 1.0:
            # Continue animation
            GLib.timeout_add(30, self._progress_tick)
        return False

    def on_generate_clicked(self, widget):
        """Handler for the 'Generate G-code' button."""
        self._generate_gcode_from_current_paths()

    def on_auto_center_clicked(self, widget):
        """Handler for the 'Auto Center' button."""
        self._auto_center_paths()

    def _initial_setup(self):
        """Performs initial auto-centering and G-code generation on startup."""
        self.log_message(f"JD CNC G-code Generator v{SCRIPT_VERSION} loaded.", "info")
        self._auto_center_paths() # First, center the paths
        self._generate_gcode_from_current_paths() # Then, generate G-code from the centered paths

    def _auto_center_paths(self):
        """
        Centers the currently selected SVG paths on the bed and updates the preview.
        Does NOT generate G-code.
        """
        # No progress indicator for just centering, as it's usually very fast.
        try:
            current_config = self.get_config_from_ui()
            cut_paths, score_paths = self.svg_parser.get_paths_by_color() 

            if not cut_paths and not score_paths:
                msg = "No selectable objects found in SVG to center."
                logging.warning(msg) # Log as warning, will be displayed as info
                return

            bed_w = float(current_config.get("bed_width", 300))
            bed_h = float(current_config.get("bed_height", 200))
            margin = float(current_config.get("safety_margin", 5))

            # Perform centering
            centered_cut_paths, centered_score_paths = self.center_paths_on_bed(cut_paths, score_paths, bed_w, bed_h, margin)

            # Update internal state with centered paths
            self.generated_cut_paths = centered_cut_paths
            self.generated_score_paths = centered_score_paths
            self.gcode_generated = True # Mark that paths are ready for G-code generation

            GLib.idle_add(self.gcode_preview.queue_draw) # Redraw preview with centered paths
            logging.info("Objects centered.")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logging.error(f"Error during auto-centering: {e}") # Log as error
            logging.error(tb) # Log traceback

    def _generate_gcode_from_current_paths(self):
        """
        Generates G-code from the current internal paths (self.generated_cut_paths, self.generated_score_paths).
        Triggers progress indicator.
        """
        self.start_progress() # Show progress indicator

        def generate_gcode_bg():
            try:
                current_config = self.get_config_from_ui()

                if not self.generated_cut_paths and not self.generated_score_paths:
                    GLib.idle_add(self.stop_progress)
                    msg = "No paths available to generate G-code. Please select objects and/or auto-center first."
                    logging.warning(msg) # Log as warning, will be displayed as info
                    return

                bed_w = float(current_config.get("bed_width", 300))
                bed_h = float(current_config.get("bed_height", 200))
                margin = float(current_config.get("safety_margin", 5))

                # Perform boundary check
                all_points_for_check = []
                for path_list in [self.generated_cut_paths, self.generated_score_paths]:
                    if path_list: # Ensure path_list is not None or empty
                        for path in path_list:
                            for subpath in path:
                                all_points_for_check.extend(subpath)

                out_of_bounds = False
                if all_points_for_check:
                    for x, y in all_points_for_check:
                        # Check if any point is outside the safe area
                        if not (margin <= x <= bed_w - margin and margin <= y <= bed_h - margin):
                            out_of_bounds = True
                            break
                
                if out_of_bounds:
                    GLib.idle_add(self.stop_progress)
                    msg = "Object detected outside of cutter area. Please adjust or re-center."
                    logging.error(msg) # Log as error
                    return # Abort G-code generation

                # Generate G-code using the already prepared (e.g., centered) paths
                gcode, stats = self.gcode_logic.generate(current_config, self.generated_cut_paths, self.generated_score_paths)

                GLib.idle_add(self.set_gcode_text, gcode, stats, self.generated_cut_paths, self.generated_score_paths)
                GLib.idle_add(self.stop_progress)
                logging.info("G-code generated.")

            except Exception as e:
                GLib.idle_add(self.stop_progress)
                import traceback
                tb = traceback.format_exc()
                logging.error(f"Error during G-code generation: {e}") # Log as error
                logging.error(tb) # Log traceback

        threading.Thread(target=generate_gcode_bg, daemon=True).start()


    def log_message(self, msg, level="info"):
        """
        Inserts a message into the log Gtk.TextView with appropriate coloring.
        """
        end_iter = self.log_buffer.get_end_iter()
        if level == "error":
            self.log_buffer.insert_with_tags(end_iter, msg + '\n', self.error_tag)
        else: # Default to info (including warnings as per new request)
            self.log_buffer.insert_with_tags(end_iter, msg + '\n', self.info_tag)
        # Optionally, scroll to the end
        self.log_view.scroll_to_iter(self.log_buffer.get_end_iter(), 0.0, False, 0, 0)

    def on_export_clicked(self, widget):
        """Handler for the 'Export G-code' button."""
        # 1. Get current config from UI
        current_config = self.get_config_from_ui()
        
        gcode_to_export = ""
        if hasattr(self, 'gcode_text_buffer'):
            gcode_to_export = self.gcode_text_buffer.get_text(
                self.gcode_text_buffer.get_start_iter(),
                self.gcode_text_buffer.get_end_iter(),
                False
            )
        
        if not gcode_to_export.strip():
            logging.error("No G-code to export.") # Log as error
            return

        self.save_last_export_info()
        self.show_export_dialog(gcode_to_export, None) # Stats are not directly used in show_export_dialog

    def show_export_dialog(self, gcode, stats): # stats parameter is kept for compatibility but not used
        """Show a file save dialog and write the G-code to the selected file."""
        # Use config manager to remember last export location and filename
        last_export = self.config_manager.get_last_export_info()
        dialog = Gtk.FileChooserDialog(
            title="Export G-code",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        # Corrected: add_buttons expects pairs of (button_text, response_id)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_current_name(last_export.get('filename', 'output.gcode'))
        dialog.set_current_folder(last_export.get('dir', os.path.expanduser('~')))
        dialog.set_do_overwrite_confirmation(True)
        response = dialog.run()
        dialog.destroy() # Destroy the dialog immediately after getting the response
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            self.config_manager.save_last_export_info(os.path.dirname(filename), os.path.basename(filename))
            with open(filename, 'w') as f:
                f.write(gcode)
            self.log_message(f"G-code exported to {filename}")
        

    def save_last_export_info(self):
        """Saves the last used export directory and filename to the config."""
        if not hasattr(self, 'last_export_dir'):
            self.last_export_dir = os.path.expanduser('~')
        if not hasattr(self, 'last_export_filename') or not self.last_export_filename:
            self.last_export_filename = 'output.gcode'
        # No dialog here! Only update config if needed
        # (All dialog logic is handled in show_export_dialog)

    def on_save_default_clicked(self, widget):
        """Ask for confirmation, then save current config as the new default."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Save current settings as default?",
        )
        dialog.format_secondary_text("This will overwrite the default configuration for all future sessions.")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            current_config = self.get_config_from_ui()
            self.config_manager.save_default(current_config)
            self.config_manager.save_config(current_config)  # Also update last used
            self.default_config = current_config.copy()
            confirm = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Defaults Saved",
            )
            confirm.format_secondary_text("Your current settings have been saved as the new defaults.")
            confirm.run()
            confirm.destroy()

    def on_reset_defaults_clicked(self, widget):
        """Ask for confirmation, then reset UI to default config."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Reset all settings to default?",
        )
        dialog.format_secondary_text("This will revert all fields to the default configuration.")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self.auto_save_enabled = False
            self.config = self.config_manager.load_default()
            self.load_config_to_ui()
            self.auto_save_enabled = True
            # Optionally, save the reset config immediately
            current_config = self.get_config_from_ui()
            self.config_manager.save_config(current_config)
            confirm = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Defaults Restored",
            )
            confirm.format_secondary_text("All fields have been reset to the default configuration.")
            confirm.run()
            confirm.destroy()

    def on_dialog_close(self, widget):
        """Saves configuration when the dialog is closed."""
        current_config = self.get_config_from_ui()
        self.config_manager.save_config(current_config)
        Gtk.main_quit()

    def load_config_to_ui(self):
        """Populates the UI fields from the loaded config dictionary."""
        c = self.config
        # Bed & Origin
        self.bed_width_entry.set_text(c.get("bed_width", "300"))
        self.bed_height_entry.set_text(c.get("bed_height", "200"))
        origin = c.get("origin_point", "front_left")
        if origin == "front_left": self.origin_front_left.set_active(True)
        elif origin == "front_right": self.origin_front_right.set_active(True)
        elif origin == "center": self.origin_center.set_active(True)
        elif origin == "back_left": self.origin_back_left.set_active(True)
        elif origin == "back_right": self.origin_back_right.set_active(True)
        else: self.origin_front_left.set_active(True)
        # Servo
        self.servo_score_entry.set_text(c.get("servo_score", "60"))
        self.servo_cut_entry.set_text(c.get("servo_cut", "45"))
        self.servo_travel_entry.set_text(c.get("servo_travel", "120"))
        # Tool
        self.tool_offset_x_entry.set_text(c.get("tool_offset_x", "0"))
        self.tool_offset_y_entry.set_text(c.get("tool_offset_y", "0"))
        self.tool_diameter_entry.set_text(c.get("tool_diameter", "1"))
        # Speeds (set as mm/s)
        self.travel_speed_entry.set_text(c.get("travel_speed", "3000"))
        self.z_plunge_speed_entry.set_text(c.get("z_plunge_speed", "20"))
        self.z_raise_speed_entry.set_text(c.get("z_raise_speed", "20"))
        self.cutting_speed_entry.set_text(c.get("cutting_speed", "1500"))
        self.scoring_speed_entry.set_text(c.get("scoring_speed", "800"))
        # Machine Limits
        self.max_velocity_xy_entry.set_text(c.get("max_velocity_xy", "5000"))
        self.max_velocity_z_entry.set_text(c.get("max_velocity_z", "5000"))
        self.max_acceleration_entry.set_text(c.get("max_acceleration", "100"))
        self.jerk_entry.set_text(c.get("jerk", "10"))
        # Speed Override
        self.speed_override_entry.set_text(c.get("speed_override", "100"))
        # Safety Margin
        self.safety_margin_entry.set_text(c.get("safety_margin", "5"))
        # G-code Templates
        self.start_gcode_buffer.set_text(c.get("start_gcode", ""))
        self.end_gcode_buffer.set_text(c.get("end_gcode", ""))
        # Z Axis Mode
        z_mode = c.get("z_mode", "servo")
        self.z_mode_combo.set_active(0 if z_mode == "servo" else 1)
        # Ensure correct visibility after setting z_mode_combo
        if hasattr(self, 'update_z_fields_visibility'):
            self.update_z_fields_visibility()
        else:
            # fallback: call the function directly if defined locally
            try:
                update_z_fields_visibility = self.__class__.__dict__.get('update_z_fields_visibility')
                if update_z_fields_visibility:
                    update_z_fields_visibility(self)
            except Exception:
                pass
        if hasattr(self, "z_stepper_cut_entry"): self.z_stepper_cut_entry.set_text(c.get("z_stepper_cut_height", "-2.0"))
        if hasattr(self, "z_stepper_score_entry"): self.z_stepper_score_entry.set_text(c.get("z_stepper_score_height", "10"))
        if hasattr(self, "z_stepper_travel_entry"): self.z_stepper_travel_entry.set_text(c.get("z_stepper_travel_height", "5.0"))
        # Spindle Speed
        self.spindle_speed_entry.set_text(c.get("spindle_speed", "10000"))
        # Units and additional settings
        if hasattr(self, "units_combo"): self.units_combo.set_active(int(c.get("units", 0)))
        if hasattr(self, "plunge_speed_entry"): self.plunge_speed_entry.set_text(c.get("plunge_speed", "500"))
        if hasattr(self, "score_line_color"): self.score_line_color = c.get("score_line_color", "#00FF00")
        if hasattr(self, "cut_line_color"): self.cut_line_color = c.get("cut_line_color", "#FF0000")
        

    def get_config_from_ui(self):
        """Gathers all values from the UI and returns a config dictionary. Stores speeds in mm/s (no conversion)."""
        config = {}
        # Bed & Origin
        config["bed_width"] = self.bed_width_entry.get_text()
        config["bed_height"] = self.bed_height_entry.get_text()
        if self.origin_front_left.get_active():
            config["origin_point"] = "front_left"
        elif self.origin_front_right.get_active():
            config["origin_point"] = "front_right"
        elif self.origin_center.get_active():
            config["origin_point"] = "center"
        elif self.origin_back_left.get_active():
            config["origin_point"] = "back_left"
        elif self.origin_back_right.get_active():
            config["origin_point"] = "back_right"
        else:
            config["origin_point"] = "front_left"
        # Servo (only new fields)
        config["servo_delay"] = self.servo_delay_entry.get_text()
        config["servo_score"] = self.servo_score_entry.get_text()
        config["servo_cut"] = self.servo_cut_entry.get_text()
        config["servo_travel"] = self.servo_travel_entry.get_text()
        # Tool
        config["tool_offset_x"] = self.tool_offset_x_entry.get_text()
        config["tool_offset_y"] = self.tool_offset_y_entry.get_text()
        config["tool_diameter"] = self.tool_diameter_entry.get_text()
        # Speeds (store as mm/s, no conversion)
        config["travel_speed"] = self.travel_speed_entry.get_text()
        config["z_plunge_speed"] = self.z_plunge_speed_entry.get_text()
        config["z_raise_speed"] = self.z_raise_speed_entry.get_text()
        config["cutting_speed"] = self.cutting_speed_entry.get_text()
        config["scoring_speed"] = self.scoring_speed_entry.get_text()
        # Machine Limits
        config["max_velocity_xy"] = self.max_velocity_xy_entry.get_text()
        config["max_velocity_z"] = self.max_velocity_z_entry.get_text()
        config["max_acceleration"] = self.max_acceleration_entry.get_text()  # Already mm/s²
        config["jerk"] = self.jerk_entry.get_text()  # Already mm/s³
        # Speed Override
        config["speed_override"] = self.speed_override_entry.get_text()
        # Safety Margin
        config["safety_margin"] = self.safety_margin_entry.get_text()
        # G-code Templates
        config["start_gcode"] = self.start_gcode_buffer.get_text(self.start_gcode_buffer.get_start_iter(), self.start_gcode_buffer.get_end_iter(), False)
        config["end_gcode"] = self.end_gcode_buffer.get_text(self.end_gcode_buffer.get_start_iter(), self.end_gcode_buffer.get_end_iter(), False)
        # Z Axis Mode
        config["z_mode"] = self.z_mode_combo.get_active_text().lower()
        # Stepper Z values
        config["z_stepper_cut_height"] = self.z_stepper_cut_entry.get_text() if hasattr(self, "z_stepper_cut_entry") else "-2.0"
        config["z_stepper_travel_height"] = self.z_stepper_travel_entry.get_text() if hasattr(self, "z_stepper_travel_entry") else "5.0"
        config["z_stepper_score_height"] = self.z_stepper_score_entry.get_text() if hasattr(self, "z_stepper_score_entry") else "10"
        # Spindle Speed
        config["spindle_speed"] = self.spindle_speed_entry.get_text()
        # Additional settings
        config["units"] = getattr(self, "units_combo", None).get_active() if hasattr(self, "units_combo") else 0
        config["plunge_speed"] = getattr(self, "plunge_speed_entry", None).get_text() if hasattr(self, "plunge_speed_entry") else "500"
        config["score_line_color"] = getattr(self, "score_line_color", None) if hasattr(self, "score_line_color") else "#00FF00"
        config["cut_line_color"] = getattr(self, "cut_line_color", None) if hasattr(self, "cut_line_color") else "#FF0000"
        return config

    def _create_hamburger_menu(self):
        menu_button = Gtk.MenuButton()
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        menu_button.set_always_show_image(True)
        menu_button.set_relief(Gtk.ReliefStyle.NONE)
        menu_button.set_tooltip_text("More options")
        menu = Gtk.Menu()
        save_default_item = Gtk.MenuItem(label="Save as Default")
        save_default_item.connect("activate", self.on_save_default_clicked)
        reset_defaults_item = Gtk.MenuItem(label="Reset to Defaults")
        reset_defaults_item.connect("activate", self.on_reset_defaults_clicked)
        menu.append(save_default_item)
        menu.append(reset_defaults_item)
        menu.show_all()
        menu_button.set_popup(menu)
        return menu_button

    def on_gcode_preview_scroll(self, widget, event):
        # This handles zooming the view
        if event.direction == Gdk.ScrollDirection.UP:
            factor = 1.1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            factor = 1/1.1
        else:
            return True
        
        # Get mouse position in widget coordinates
        mx, my = event.x, event.y
        w, h = widget.get_allocated_width(), widget.get_allocated_height()

        # Get current transformation parameters from on_gcode_preview_draw logic
        config = self.get_config_from_ui()
        bed_w = float(config.get("bed_width", 300))
        bed_h = float(config.get("bed_height", 200))

        scale_base = min(w / bed_w, h / bed_h) * 0.9
        offset_x_base = (w - bed_w * scale_base) / 2
        offset_y_base = (h - bed_h * scale_base) / 2

        # Calculate coordinates relative to the bed's origin, considering current pan and zoom
        # First, un-apply the base offset and current pan offset to get screen-relative to bed's top-left
        screen_x_relative_to_bed_top_left = mx - offset_x_base - self.gcode_preview_offset[0]
        screen_y_relative_to_bed_top_left = my - offset_y_base - self.gcode_preview_offset[1]

        # Then, convert screen-relative to bed units, considering current zoom and base scale
        bed_x_at_mouse = screen_x_relative_to_bed_top_left / (scale_base * self.gcode_preview_zoom)
        bed_y_at_mouse = screen_y_relative_to_bed_top_left / (scale_base * self.gcode_preview_zoom)

        # Apply new zoom
        new_zoom = self.gcode_preview_zoom * factor
        # Limit zoom to a reasonable range
        self.gcode_preview_zoom = max(0.1, min(new_zoom, 10.0)) # Min 0.1x, Max 10x

        # Recalculate new screen position of the point that was under the mouse
        new_screen_x_relative_to_bed_top_left = bed_x_at_mouse * (scale_base * self.gcode_preview_zoom)
        new_screen_y_relative_to_bed_top_left = bed_y_at_mouse * (scale_base * self.gcode_preview_zoom)

        # Adjust pan offset so that the point under the mouse stays under the mouse
        self.gcode_preview_offset[0] += (screen_x_relative_to_bed_top_left - new_screen_x_relative_to_bed_top_left)
        self.gcode_preview_offset[1] += (screen_y_relative_to_bed_top_left - new_screen_y_relative_to_bed_top_left)

        self.gcode_preview.queue_draw()
        return True

    def _get_current_paths_screen_bounds(self, widget_width, widget_height):
        """
        Calculates the bounding box of the currently generated paths in screen pixel coordinates.
        Returns (min_x_screen, min_y_screen, max_x_screen, max_y_screen) or None if no paths.
        """
        all_points = []
        if self.generated_cut_paths:
            for path in self.generated_cut_paths:
                for subpath in path:
                    all_points.extend(subpath)
        if self.generated_score_paths:
            for path in self.generated_score_paths:
                for subpath in path:
                    all_points.extend(subpath)

        if not all_points:
            return None

        min_x_bed = min(pt[0] for pt in all_points)
        max_x_bed = max(pt[0] for pt in all_points)
        min_y_bed = min(pt[1] for pt in all_points)
        max_y_bed = max(pt[1] for pt in all_points)

        # Get current transformation parameters from on_gcode_preview_draw logic
        config = self.get_config_from_ui()
        bed_w = float(config.get("bed_width", 300))
        bed_h = float(config.get("bed_height", 200))

        scale_base = min(widget_width / bed_w, widget_height / bed_h) * 0.9
        offset_x_base = (widget_width - bed_w * scale_base) / 2
        offset_y_base = (widget_height - bed_h * scale_base) / 2

        current_scale = scale_base * self.gcode_preview_zoom
        current_offset_x = offset_x_base + self.gcode_preview_offset[0]
        current_offset_y = offset_y_base + self.gcode_preview_offset[1]

        # Transform bed coordinates to screen coordinates
        min_x_screen = min_x_bed * current_scale + current_offset_x
        max_x_screen = max_x_bed * current_scale + current_offset_x
        min_y_screen = min_y_bed * current_scale + current_offset_y
        max_y_screen = max_y_bed * current_scale + current_offset_y

        return (min_x_screen, min_y_screen, max_x_screen, max_y_screen)


    def on_gcode_preview_button_press(self, widget, event):
        # This handles panning the view (middle click) and dragging the object (left click)
        if event.button == 2:  # Middle mouse button for view pan
            self.gcode_preview_drag = True
            self.gcode_preview_last = (event.x, event.y)
        elif event.button == 1: # Left mouse button for object drag
            if self.generated_cut_paths or self.generated_score_paths:
                widget_width = widget.get_allocated_width()
                widget_height = widget.get_allocated_height()
                bounds = self._get_current_paths_screen_bounds(widget_width, widget_height)
                
                if bounds:
                    min_x_s, min_y_s, max_x_s, max_y_s = bounds
                    # Check if the click is within the object's screen-space bounding box
                    if min_x_s <= event.x <= max_x_s and min_y_s <= event.y <= max_y_s:
                        self.is_object_dragging = True
                        self.drag_last_mouse_x = event.x
                        self.drag_last_mouse_y = event.y
        return True

    def on_gcode_preview_button_release(self, widget, event):
        # This stops view panning or object dragging
        if event.button == 2: # Middle mouse button for view pan
            self.gcode_preview_drag = False
        elif event.button == 1:
            if self.is_object_dragging:
                self.is_object_dragging = False
                # After dragging, regenerate G-code to reflect new position
                self._generate_gcode_from_current_paths()
        return True

    def on_gcode_preview_motion(self, widget, event):
        # This handles motion for both view panning and object dragging
        if self.gcode_preview_drag: # View panning (middle click)
            dx = event.x - self.gcode_preview_last[0]
            dy = event.y - self.gcode_preview_last[1]
            self.gcode_preview_offset[0] += dx
            self.gcode_preview_offset[1] += dy
            self.gcode_preview_last = (event.x, event.y)
            self.gcode_preview.queue_draw()
        elif self.is_object_dragging: # Object dragging (left click)
            if self.generated_cut_paths or self.generated_score_paths:
                # Get current bed dimensions and scale factor for conversion
                width = widget.get_allocated_width()
                height = widget.get_allocated_height()
                config = self.get_config_from_ui()
                bed_w = float(config.get("bed_width", 300))
                bed_h = float(config.get("bed_height", 200))
                scale_base = min(width / bed_w, height / bed_h) * 0.9 # Base scale from bed units to preview pixels
                zoom = self.gcode_preview_zoom # Current zoom level of the view

                dx_screen = event.x - self.drag_last_mouse_x
                dy_screen = event.y - self.drag_last_mouse_y

                # Convert screen pixel delta to bed units delta, accounting for current zoom and base scale
                dx_bed_units = dx_screen / (scale_base * zoom)
                dy_bed_units = dy_screen / (scale_base * zoom)

                # Apply translation directly to the stored path data
                self.generated_cut_paths = self._translate_paths(self.generated_cut_paths, dx_bed_units, dy_bed_units)
                self.generated_score_paths = self._translate_paths(self.generated_score_paths, dx_bed_units, dy_bed_units)

                self.drag_last_mouse_x = event.x
                self.drag_last_mouse_y = event.y
                self.gcode_preview.queue_draw()
        return True

    def _translate_paths(self, paths, dx, dy):
        """Helper to translate all points in a list of paths by (dx, dy)."""
        if not paths:
            return []
        new_paths = []
        for path in paths:
            new_subpaths = []
            for subpath in path:
                new_points = []
                for x, y in subpath:
                    new_points.append((x + dx, y + dy))
                new_subpaths.append(new_points)
            new_paths.append(new_subpaths)
        return new_paths

    def set_gcode_text(self, gcode, stats=None, cut_paths=None, score_paths=None):
        self.gcode_text_buffer.set_text(gcode)
        self.gcode_text_view.scroll_to_iter(self.gcode_text_buffer.get_start_iter(), 0.0, False, 0, 0)
        if cut_paths is not None and score_paths is not None:
            self.generated_cut_paths = cut_paths
            self.generated_score_paths = score_paths
            self.gcode_generated = True
        else:
            self.generated_cut_paths = None
            self.generated_score_paths = None
            self.gcode_generated = False
        self.gcode_preview.queue_draw()

    def on_generate_gcode_clicked(self, button):
        # This method is now redundant, as on_generate_clicked calls _generate_gcode_from_current_paths
        # Keeping it for now to avoid breaking existing references if any outside this file.
        # It will be removed in future cleanups.
        config = self.get_config_from_ui()
        cut_color = config.get('cut_line_color', '#000000')
        score_color = config.get('score_line_color', '#FF0000')
        cut_paths, score_paths = self.svg_parser.get_paths_by_color(cut_color, score_color)
        bed_w = float(config.get("bed_width", 300))
        bed_h = float(config.get("bed_height", 200))
        margin = float(config.get("safety_margin", 5))
        cut_paths, score_paths = self.center_paths_on_bed(cut_paths, score_paths, bed_w, bed_h, margin)
        # If no paths after centering, abort
        if not cut_paths and not score_paths:
            logging.warning("Unable to generate G-code: could not center paths on bed.")
            return
        # Boundary check
        def out_of_bounds(path):
            for pt in path:
                x, y = pt
                if not (margin <= x <= bed_w - margin and margin <= y <= bed_h - margin):
                    return True
            return False
        any_oob = any(out_of_bounds(p) for p in cut_paths + score_paths)
        if any_oob:
            logging.warning("Some paths are outside the bed area or too close to the edge!")
        # SVG fit check
        all_points = [pt for path in cut_paths + score_paths for sub in path for pt in sub]
        if all_points:
            min_x = min(pt[0] for pt in all_points)
            max_x = max(pt[0] for pt in all_points)
            min_y = min(pt[1] for pt in all_points)
            max_y = max(pt[1] for pt in all_points)
            if min_x < margin or max_x > bed_w - margin or min_y < margin or max_y > bed_h - margin:
                logging.warning("SVG drawing does not fit within the configured bed area (with margin)!")
        gcode, stats = self.gcode_logic.generate(config, cut_paths, score_paths)
        self.set_gcode_text(gcode, stats, cut_paths, score_paths)
        logging.info("G-code generated. Stats: " + str(stats))

    def center_paths_on_bed(self, cut_paths, score_paths, bed_w, bed_h, margin=0):
        """Centers all paths as a group on the bed, returns new cut_paths and score_paths lists. Handles subpaths."""
        all_paths = cut_paths + score_paths
        all_points = [pt for path in cut_paths + score_paths for sub in path for pt in sub]
        if not all_points:
            return cut_paths, score_paths
        min_x = min(pt[0] for pt in all_points)
        max_x = max(pt[0] for pt in all_points)
        min_y = min(pt[1] for pt in all_points)
        max_y = max(pt[1] for pt in all_points)
        svg_w = max_x - min_x
        svg_h = max_y - min_y
        # Offset to center the group as a whole
        offset_x = (bed_w - svg_w) / 2 - min_x
        offset_y = (bed_h - svg_h) / 2 - min_y
        def shift_path(path):
            return [[(x + offset_x, y + offset_y) for (x, y) in sub] for sub in path]
        cut_paths = [shift_path(p) for p in cut_paths]
        score_paths = [shift_path(p) for p in score_paths]
        return cut_paths, score_paths

    def on_gcode_preview_draw(self, widget, cr):
        """Draws a 2D preview of the generated G-code toolpaths, including the cutter bed, grid, and origin with red/green arrows."""
        # Fill the entire drawing area with white to ensure no transparency
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.save()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.restore()
        config = self.get_config_from_ui()
        bed_w = float(config.get("bed_width", 300))
        bed_h = float(config.get("bed_height", 200))
        margin = float(config.get("safety_margin", 5))
        # Calculate scale and offset to fit bed in preview
        scale = min(w / bed_w, h / bed_h) * 0.9
        offset_x = (w - bed_w * scale) / 2
        offset_y = (h - bed_h * scale) / 2
        # Draw white bed rectangle
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(offset_x, offset_y, bed_w * scale, bed_h * scale)
        cr.fill_preserve()
        cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.set_line_width(2)
        cr.stroke()
        # Draw grid lines (#676767ff)
        cr.set_source_rgba(0.403, 0.403, 0.403, 1.0)
        cr.set_line_width(1)
        step = 50
        for x in range(step, int(bed_w), step):
            cr.move_to(offset_x + x * scale, offset_y)
            cr.line_to(offset_x + x * scale, offset_y + bed_h * scale)
        for y in range(step, int(bed_h), step):
            cr.move_to(offset_x, offset_y + y * scale)
            cr.line_to(offset_x + bed_w * scale, offset_y + y * scale)
        cr.stroke()
        # Draw origin marker (blue) and red/green arrows
        origin = config.get("origin_point", "front_left")
        if origin == "front_left":
            ox, oy = offset_x, offset_y + bed_h * scale
            x_dir, y_dir = 1, -1
        elif origin == "front_right":
            ox, oy = offset_x + bed_w * scale, offset_y + bed_h * scale
            x_dir, y_dir = -1, -1
        elif origin == "center":
            ox, oy = offset_x + (bed_w * scale) / 2, offset_y + (bed_h * scale) / 2
            x_dir, y_dir = 1, -1
        elif origin == "back_left":
            ox, oy = offset_x, offset_y
            x_dir, y_dir = 1, 1
        elif origin == "back_right":
            ox, oy = offset_x + bed_w * scale, offset_y
            x_dir, y_dir = -1, 1
        else:
            ox, oy = offset_x, offset_y + bed_h * scale
            x_dir, y_dir = 1, -1
        # Draw origin circle
        cr.set_source_rgb(0.1, 0.4, 1.0)
        cr.arc(ox, oy, 10, 0, 2 * 3.1416)
        cr.fill_preserve()
        cr.set_source_rgb(0, 0, 0)
        cr.set_line_width(1)
        cr.stroke()
        # Draw X (red) and Y (green) arrows (match bed tab)
        arrow_len = 40
        # X arrow (red)
        cr.set_source_rgb(1, 0, 0)
        cr.set_line_width(3)
        cr.move_to(ox, oy)
        cr.line_to(ox + arrow_len * x_dir, oy)
        cr.stroke()
        cr.move_to(ox + arrow_len * x_dir, oy)
        cr.line_to(ox + (arrow_len - 10) * x_dir, oy - 7)
        cr.move_to(ox + arrow_len * x_dir, oy)
        cr.line_to(ox + (arrow_len - 10) * x_dir, oy + 7)
        cr.stroke()
        # Y arrow (green)
        cr.set_source_rgb(0, 0.7, 0)
        cr.set_line_width(3)
        cr.move_to(ox, oy)
        cr.line_to(ox, oy + arrow_len * y_dir)
        cr.stroke()
        cr.move_to(ox, oy + arrow_len * y_dir)
        cr.line_to(ox - 7, oy + (arrow_len - 10) * y_dir)
        cr.move_to(ox, oy + arrow_len * y_dir)
        cr.line_to(ox + 7, oy + (arrow_len - 10) * y_dir)
        cr.stroke()
        # Draw G-code toolpaths
        cr.save()
        # Apply view panning offset and zoom
        cr.translate(offset_x + self.gcode_preview_offset[0], offset_y + self.gcode_preview_offset[1])
        cr.scale(scale * self.gcode_preview_zoom, scale * self.gcode_preview_zoom)
        
        # Only draw toolpaths if G-code has been generated
        if not self.gcode_generated or self.generated_cut_paths is None:
            cr.restore()
            return
        
        def draw_paths(paths, color):
            cr.set_source_rgb(*color)
            cr.set_line_width(1.5 / (scale * self.gcode_preview_zoom)) # Adjust line width for zoom
            for path in paths:
                for sub in path:
                    if not sub: continue
                    cr.move_to(sub[0][0], sub[0][1])
                    for pt in sub[1:]:
                        cr.line_to(pt[0], pt[1])
                    cr.stroke()
        draw_paths(self.generated_cut_paths, (0, 0, 0))  # Black for cut
        draw_paths(self.generated_score_paths, (1, 0, 0))  # Red for score
        cr.restore()

    def on_global_key_press(self, widget, event):
        ctrl = (event.state & Gdk.ModifierType.CONTROL_MASK)
        keyval = event.keyval
        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.undo()
            return True
        if ctrl and keyval in (Gdk.KEY_y, Gdk.KEY_Y):
            self.redo()
            return True
        return False

# Lower GTK transition/animation time for snappier UI
settings = Gtk.Settings.get_default()
if settings:
    settings.set_property("gtk-enable-animations", True)
    # gtk-transition-duration is not available in GTK3, so we skip it

class JDCncGcodeGenerator(inkex.Effect):
    """Inkscape Effect class - the entry point for the extension."""
    def __init__(self):
        super(JDCncGcodeGenerator, self).__init__()

    def effect(self):
        """This is called by Inkscape when the user runs the extension."""
        if not self.svg.selection:
            import logging
            logging.error("Please select objects before running the CNC G-code Generator.")
            return
        dialog = CNCDialog(self)
        dialog.run()
        # When the dialog closes (via destroy), Gtk.main_quit() is called,
        # and the script will exit gracefully.

if __name__ == '__main__':
    JDCncGcodeGenerator().run()
