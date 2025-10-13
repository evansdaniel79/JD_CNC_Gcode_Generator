"""Simple smoke tests for JD_CNC_Gcode_Generator core modules.
Run this from the extension directory:

    python3 tests/run_smoke_tests.py

It will import the modules and run a tiny GCodeLogic.generate call to ensure basic functionality.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from gcode_logic import GCodeLogic
from svg_parser import SVGParser
from config_manager import ConfigManager

print('Import OK')

# Minimal config for generation
config = {
    'bed_width': '300',
    'bed_height': '200',
    'safety_margin': '5',
    'travel_speed': '3000',
    'z_plunge_speed': '20',
    'z_raise_speed': '20',
    'cutting_speed': '1500',
    'scoring_speed': '800',
    # G-code templates expected by GCodeLogic
    'start_gcode': 'G21 ; start',
    'end_gcode': 'M2 ; end',
    # Servo defaults (used by servo-mode generation)
    'servo_score': '60',
    'servo_cut': '45',
    'servo_travel': '120',
    'servo_delay': '200',
}

# Synthetic tiny path: one cut path with one subpath of two points
cut_paths = [[[ (10.0, 10.0), (20.0, 10.0) ]]]
score_paths = []

logic = GCodeLogic()
print('Generating G-code...')
gcode, stats = logic.generate(config, cut_paths, score_paths)
print('G-code length:', len(gcode))
print('Stats:', stats)

print('Smoke test complete')
