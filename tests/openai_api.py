import importlib.util, os, sys
root = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location('openai_api', os.path.join(root, 'openai_api.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for k, v in module.__dict__.items():
    globals()[k] = v
