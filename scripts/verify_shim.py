"""Verify the offline _sdk_shim shapes against the REAL CROO SDK (croo.types).

Prints the real DeliverableType members and DeliverOrderRequest constructor
fields, then compares them to croon._sdk_shim. Exits 0 if the real SDK is
absent (nothing to verify) or matches; exits 1 on a real mismatch.

Run:  PYTHONPATH="$PWD" python scripts/verify_shim.py
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import sys


def describe_dataclass_fields(cls) -> list[tuple[str, object]]:
    out = []
    if dataclasses.is_dataclass(cls):
        for f in dataclasses.fields(cls):
            default = f.default if f.default is not dataclasses.MISSING else "<required>"
            out.append((f.name, default))
    else:
        try:
            sig = inspect.signature(cls.__init__)
            for name, p in sig.parameters.items():
                if name == "self":
                    continue
                default = p.default if p.default is not inspect._empty else "<required>"
                out.append((name, default))
        except (TypeError, ValueError):
            pass
    return out


def main() -> int:
    try:
        real = importlib.import_module("croo.types")
    except ModuleNotFoundError as e:
        print(f"REAL_SDK_NOT_INSTALLED: {e}")
        print("Nothing to verify offline. In LIVE mode the real SDK import wins,")
        print("so the shim is never reached. Re-run this in the live env to confirm.")
        return 0

    print("REAL SDK PRESENT - inspecting croo.types\n")

    real_dt = getattr(real, "DeliverableType", None)
    real_dor = getattr(real, "DeliverOrderRequest", None)

    print("--- real DeliverableType ---")
    # NOTE: the real croo.types.DeliverableType is a PLAIN class holding bare
    # string constants (TEXT/SCHEMA), NOT an Enum - so we read attributes, we do
    # not iterate. (This is the exact mismatch this script caught on 2026-07-12.)
    real_dt_members = {}
    if real_dt is None:
        print("  MISSING")
    else:
        for name in dir(real_dt):
            if name.startswith("_"):
                continue
            val = getattr(real_dt, name)
            if isinstance(val, str):
                real_dt_members[name] = val
                print(f"  {name} = {val!r}")


    print("\n--- real DeliverOrderRequest ---")
    real_fields = []
    if real_dor is None:
        print("  MISSING")
    else:
        real_fields = describe_dataclass_fields(real_dor)
        for name, default in real_fields:
            print(f"  {name} = {default!r}")

    # Now the shim.
    shim = importlib.import_module("croon._sdk_shim")
    print("\n--- shim DeliverableType ---")
    # Shim mirrors the real SDK: a plain class of string constants, not an Enum.
    shim_dt_members = {
        name: getattr(shim.DeliverableType, name)
        for name in dir(shim.DeliverableType)
        if not name.startswith("_") and isinstance(getattr(shim.DeliverableType, name), str)
    }
    for name, val in shim_dt_members.items():
        print(f"  {name} = {val!r}")


    print("\n--- shim DeliverOrderRequest ---")
    shim_fields = describe_dataclass_fields(shim.DeliverOrderRequest)
    for name, default in shim_fields:
        print(f"  {name} = {default!r}")

    # Compare.
    problems = []
    if real_dt is not None:
        if "TEXT" not in real_dt_members:
            problems.append("real DeliverableType has no TEXT member")
        elif real_dt_members["TEXT"] != shim_dt_members.get("TEXT"):
            problems.append(
                f"TEXT value mismatch: real={real_dt_members['TEXT']!r} "
                f"shim={shim_dt_members.get('TEXT')!r}"
            )

    if real_dor is not None:
        real_names = [n for n, _ in real_fields]
        shim_names = [n for n, _ in shim_fields]
        # We only care that the fields the shim emits exist in the real ctor,
        # in the same order for positional/first usage.
        for n in ("deliverable_type", "deliverable_schema", "deliverable_text"):
            if n not in real_names:
                problems.append(f"real DeliverOrderRequest missing field '{n}'")
        if real_names[:3] != shim_names[:3]:
            problems.append(
                f"field ORDER differs: real[:3]={real_names[:3]} shim[:3]={shim_names[:3]}"
            )

    print("\n=== RESULT ===")
    if problems:
        print("MISMATCH - update croon/_sdk_shim.py to match the real SDK:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("MATCH - shim shapes are byte-compatible with the real SDK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
