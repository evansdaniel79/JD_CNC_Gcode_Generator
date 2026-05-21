import os
import json
import logging


class ConfigManager:
    """
    Loads and saves the extension's configuration as JSON.

    The file lives in the user's Inkscape extensions config directory and
    has the structure:

        {
            "last":        { ...config... },   # most recently used settings
            "default":     { ...config... },   # user-saved defaults
            "last_export": { "dir": ..., "filename": ... }
        }

    This module deliberately does NOT import inkex so it can be imported and
    unit-tested outside of Inkscape.  Errors are logged via the standard
    logging module rather than inkex.errormsg().
    """

    def __init__(self, extension_name="JD_CNC_Gcode_Generator"):
        if os.name == "nt":  # Windows
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                                "inkscape", "extensions", extension_name)
        else:                # Linux / macOS / other POSIX
            base = os.path.join(os.path.expanduser("~"),
                                ".config", "inkscape", "extensions", extension_name)

        self.config_path  = base
        self.config_file  = os.path.join(base, f"{extension_name}_config.json")
        self._default_config = self.get_default_config()

    # ------------------------------------------------------------------ #
    # Loading                                                             #
    # ------------------------------------------------------------------ #

    def load_config(self):
        """Return the last-used config, falling back to defaults for missing keys."""
        data = self.load_full_config()
        config = dict(data.get("last", {}))
        for key, value in self._default_config.items():
            config.setdefault(key, value)
        return config

    def load_default(self):
        """Return the saved default config, or the built-in defaults."""
        data = self.load_full_config()
        config = dict(data.get("default", {}))
        for key, value in self._default_config.items():
            config.setdefault(key, value)
        return config

    def load_full_config(self):
        """
        Load the complete {last, default, last_export} structure.
        Migrates older flat-format files automatically.
        """
        if not os.path.exists(self.config_file):
            return {
                "last":    self._default_config.copy(),
                "default": self._default_config.copy(),
            }
        try:
            with open(self.config_file, "r") as f:
                data = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logging.warning(f"Could not read config, using defaults: {e}")
            return {
                "last":    self._default_config.copy(),
                "default": self._default_config.copy(),
            }

        # Migrate old flat structure → {last, default}
        if not isinstance(data, dict) or "last" not in data or "default" not in data:
            return {
                "last":    dict(data) if isinstance(data, dict) else self._default_config.copy(),
                "default": self._default_config.copy(),
            }
        return data

    # ------------------------------------------------------------------ #
    # Saving                                                              #
    # ------------------------------------------------------------------ #

    def _write(self, data):
        """Write the full config structure to disk."""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, "w") as f:
                json.dump(data, f, indent=4)
        except IOError as e:
            logging.error(f"Could not save configuration: {e}")

    def save_config(self, config_data):
        """Save config_data as the last-used configuration."""
        data = self.load_full_config()
        data["last"] = config_data
        self._write(data)

    def save_default(self, config_data):
        """Save config_data as the default configuration."""
        data = self.load_full_config()
        data["default"] = config_data
        self._write(data)

    def save_full_config(self, data):
        """Write a complete config structure verbatim."""
        self._write(data)

    # ------------------------------------------------------------------ #
    # Last-export memory                                                  #
    # ------------------------------------------------------------------ #

    def get_last_export_info(self):
        data = self.load_full_config()
        return data.get("last_export",
                         {"dir": os.path.expanduser("~"), "filename": "output.gcode"})

    def save_last_export_info(self, dir_path, filename):
        data = self.load_full_config()
        data["last_export"] = {"dir": dir_path, "filename": filename}
        self._write(data)

    # ------------------------------------------------------------------ #
    # Defaults                                                            #
    # ------------------------------------------------------------------ #

    def get_default_config(self):
        """
        Built-in default settings.  Only keys that are actually used by the
        UI and G-code logic are kept here — legacy unused keys (spindle_speed,
        max_velocity_*, jerk, units, plunge_speed, line colors, speed_override)
        have been removed.
        """
        return {
            # Bed & origin
            "bed_width":     "300",
            "bed_height":    "200",
            "origin_point":  "front_left",
            "safety_margin": "5",

            # Servo Z mode
            "servo_score":  "60",
            "servo_cut":    "45",
            "servo_travel": "120",
            "servo_delay":  "200",

            # Stepper Z mode
            "z_stepper_cut_height":    "-2.0",
            "z_stepper_score_height":  "-0.5",
            "z_stepper_travel_height":  "5.0",
            "z_plunge_speed": "20",
            "z_raise_speed":  "30",

            # Tool
            "tool_offset_x": "0",
            "tool_offset_y": "0",
            "tool_diameter": "1",

            # Speeds (mm/s)
            "travel_speed":  "150",
            "cutting_speed": "30",
            "scoring_speed": "35",

            # Z axis mode + G-code templates
            "z_mode": "servo",
            "start_gcode": ("; Start G-code\n"
                            "G21 ; Set units to mm\n"
                            "G90 ; Use absolute coordinates\n"
                            "G28 ; Home all axes"),
            "end_gcode":   ("; End G-code\n"
                            "G28\n"
                            "M2 ; Program end"),
        }