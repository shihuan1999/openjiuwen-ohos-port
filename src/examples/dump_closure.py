import sys, runpy
try:
    runpy.run_path("examples/ref_workflow.py", run_name="__main__")
finally:
    import importlib.metadata as md
    installed = {d.metadata["Name"].lower(): d.version for d in md.distributions()}
    used = sorted({m.split(".")[0] for m in sys.modules if not m.startswith("_")})
    import importlib.util
    ext_mods = []
    for m in sorted(sys.modules):
        spec = getattr(sys.modules[m], "__spec__", None)
        if spec and spec.origin and spec.origin.endswith((".so", ".pyd")):
            ext_mods.append(m)
    print("=== TOP-LEVEL MODULES IMPORTED ===")
    print(" ".join(used))
    print("=== EXTENSION MODULES (.so/.pyd) ===")
    print(" ".join(ext_mods))
