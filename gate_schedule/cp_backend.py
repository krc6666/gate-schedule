"""Select the runtime adapter; domain models stay in solver/duty_roster."""
import sys

if sys.platform == 'emscripten':
    from . import browser_cp as cp_model
else:
    from ortools.sat.python import cp_model
