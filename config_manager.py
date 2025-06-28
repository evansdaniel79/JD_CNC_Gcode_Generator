import os
import json
import inkex

class ConfigManager:
    def __init__(self, extension_name="JD_CNC_Gcode_Generator"):
        # Get the path to the user's extension configuration directory
        # CORRECTED: Use Python's standard 'os' module for OS detection
        if os.name == 'nt':  # 'nt' is the name for the Windows OS
            self.config_path = os.path.join(os.environ['APPDATA'], 'inkscape', 'extensions', extension_name)
        else: # For Linux, macOS, and other POSIX systems
            self.config_path = os.path.join(os.path.expanduser('~'), '.config', 'inkscape', 'extensions', extension_name)

        self.config_file = os.path.join(self.config_path, f"{extension_name}_config.json")
        self._default_config = self.get_default_config()

    def load_config(self):
        """Loads configuration from a JSON file. Returns last used or defaults if not found."""
        if not os.path.exists(self.config_file):
            return self._default_config.copy()
        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
                # If structure is new, use 'last' and 'default' keys
                if isinstance(data, dict) and 'last' in data and 'default' in data:
                    # Ensure all keys from default are present
                    config = data.get('last', {}).copy()
                    for key, value in self._default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
                # If structure is old, treat as flat config and migrate
                else:
                    config = data.copy()
                    for key, value in self._default_config.items():
                        if key not in config:
                            config[key] = value
                    # Migrate to new structure
                    self.save_full_config({'last': config, 'default': self._default_config.copy()})
                    return config
        except (IOError, json.JSONDecodeError):
            return self._default_config.copy()

    def save_config(self, config_data):
        """Saves the given configuration data as the last used config."""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            data = self.load_full_config()
            data['last'] = config_data
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=4)
        except IOError as e:
            inkex.errormsg(f"Could not save configuration: {e}")

    def save_default(self, config_data):
        """Saves the given configuration data as the default config."""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            data = self.load_full_config()
            data['default'] = config_data
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=4)
        except IOError as e:
            inkex.errormsg(f"Could not save default configuration: {e}")

    def load_default(self):
        """Loads the default config from the config file, or returns built-in defaults."""
        data = self.load_full_config()
        return data.get('default', self._default_config.copy())

    def load_full_config(self):
        """Loads the full config file structure, or creates it if missing."""
        if not os.path.exists(self.config_file):
            return {'last': self._default_config.copy(), 'default': self._default_config.copy()}
        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
                if 'last' not in data or 'default' not in data:
                    # Migrate old structure
                    return {'last': data.copy(), 'default': self._default_config.copy()}
                return data
        except (IOError, json.JSONDecodeError):
            return {'last': self._default_config.copy(), 'default': self._default_config.copy()}

    def save_full_config(self, data):
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=4)
        except IOError as e:
            inkex.errormsg(f"Could not save configuration: {e}")

    def get_default_config(self):
        """Returns a dictionary of default settings."""
        return {
            "bed_width": "300",
            "bed_height": "200",
            "units": 0, # 0 for mm, 1 for inches
            "origin_point": "bottom_left",
            "safety_margin": "5",
            "servo_score": "60",
            "servo_cut": "45",
            "servo_travel": "120",
            "servo_delay": "200",
            "tool_offset_x": "0",
            "tool_offset_y": "0",
            "tool_diameter": "1",
            "travel_speed": "3000",
            "cutting_speed": "1500",
            "scoring_speed": "800",
            "plunge_speed": "500",
            "speed_override": "100",
            "max_velocity_xy": "5000",
            "max_velocity_z": "5000",
            "max_acceleration": "100",
            "jerk": "10",
            "start_gcode": "; Start G-code\nG21 ; Set units to mm\nG90 ; Use absolute coordinates\nG28 ; Home all axes\nM3 S{servo_travel}\nG4 P{servo_delay}",
            "end_gcode": "; End G-code\nM3 S{servo_travel}\nG4 P{servo_delay}\nG0 X0 Y0\nM2 ; Program end",
            "z_mode": "servo",  # 'servo' or 'stepper'
            "z_stepper_cut_height": "-2.0",
            "z_stepper_score_height": "-0.5",
            "z_stepper_travel_height": "5.0",
            "spindle_speed": "10000",
        }

    def get_last_export_info(self):
        data = self.load_full_config()
        return data.get('last_export', {'dir': os.path.expanduser('~'), 'filename': 'output.gcode'})

    def save_last_export_info(self, dir_path, filename):
        data = self.load_full_config()
        data['last_export'] = {'dir': dir_path, 'filename': filename}
        self.save_full_config(data)