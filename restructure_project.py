import os
import re
import shutil
from pathlib import Path

# Mapping of file to its new folder
MAPPING = {
    "config.py": "core",
    "state.py": "core",
    "utils.py": "core",
    "core_imports.py": "core",
    "physics.py": "engine",
    "scoring.py": "engine",
    "behavior_models.py": "engine",
    "route_prediction.py": "prediction",
    "intent_inference.py": "prediction",
    "tracking_filters.py": "prediction",
    "probabilistic_sampling.py": "prediction",
    "data.py": "data_processing",
    "fetchdata.py": "data_processing",
    "server.py": "api",
    "ui.py": "api",
    "test_astar.py": "tests",
    "test_integration.py": "tests",
    "test_predict.py": "tests",
    "test_predict_v2.py": "tests",
    "patch_ui.py": "scripts",
    "refactor.py": "scripts",
    "refactor_route.py": "scripts",
    "update_predict.py": "scripts"
}

FOLDERS = set(MAPPING.values())

# Create folders and __init__.py
for folder in FOLDERS:
    os.makedirs(folder, exist_ok=True)
    init_file = Path(folder) / "__init__.py"
    if not init_file.exists():
        init_file.touch()

# Read all python files that we need to process (including app.py)
all_py_files = ["app.py"] + list(MAPPING.keys())
file_contents = {}

for py_file in all_py_files:
    if os.path.exists(py_file):
        with open(py_file, "r", encoding="utf-8") as f:
            file_contents[py_file] = f.read()

# Build regex replacements for each module
for mod_file, target_folder in MAPPING.items():
    mod_name = mod_file[:-3]  # remove .py
    
    # regex for: import mod_name
    # replace with: from folder import mod_name
    import_pattern = re.compile(rf"^(import\s+){mod_name}(\b)", re.MULTILINE)
    
    # regex for: import mod_name as alias
    # replace with: from folder import mod_name as alias (wait, the above handles it if we just do from F import M as A ? No, "import M as A" -> "from F import M as A")
    # Actually, simpler:
    # "import M" -> "from F import M"
    # "import M, something" -> this is hard. But let's look at the codebase. They mostly do one import per line, except "import h3, numpy as np..." which are external.
    # The internal imports are mostly:
    # import state
    # import intent_inference
    # import route_prediction
    # import tracking_filters
    # import probabilistic_sampling
    
    for pf in list(file_contents.keys()):
        content = file_contents[pf]
        
        # 1. replace 'from M import' -> 'from F.M import'
        content = re.sub(rf"^(from\s+){mod_name}(\s+import)", rf"\1{target_folder}.{mod_name}\2", content, flags=re.MULTILINE)
        
        # 2. replace 'import M\n' or 'import M as'
        # To be safe, if we see exactly 'import M', replace it.
        # If it's part of a comma list, it's tricky, but let's just do exact line matches.
        content = re.sub(rf"^import\s+{mod_name}\s*$", rf"from {target_folder} import {mod_name}", content, flags=re.MULTILINE)
        
        # What about 'import M\n' inside a function? (e.g. import random \n import route_prediction)
        # Yes, re.MULTILINE handles ^ as start of line, but there might be indentation.
        content = re.sub(rf"^(\s*)import\s+{mod_name}\s*$", rf"\1from {target_folder} import {mod_name}", content, flags=re.MULTILINE)
        
        # 3. replace 'import core.state as state' if it happened? No, we are transforming to that.
        
        file_contents[pf] = content

# Write files back to their new locations
for pf, content in file_contents.items():
    if pf == "app.py":
        new_path = "app.py"
    else:
        new_path = os.path.join(MAPPING[pf], pf)
        
    with open(new_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    # Remove the old file if it moved
    if pf != "app.py" and os.path.exists(pf):
        os.remove(pf)

print("Restructure complete!")
