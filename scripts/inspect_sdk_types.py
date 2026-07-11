"""Dump the REAL croo.types.DeliverableType / DeliverOrderRequest so we can
align the offline shim and the live call site exactly. Read-only, no network.
"""

from __future__ import annotations

import inspect

import croo.types as t


def dump(name: str, obj) -> None:
    print(f"\n========== {name} ==========")
    print("repr        :", repr(obj))
    print("type        :", type(obj))
    print("is class    :", inspect.isclass(obj))
    mro = getattr(obj, "__mro__", None)
    if mro:
        print("mro         :", [c.__name__ for c in mro])
    # Enum?
    try:
        import enum

        if inspect.isclass(obj) and issubclass(obj, enum.Enum):
            print("ENUM members:", {m.name: m.value for m in obj})
    except Exception as e:
        print("enum check  :", e)
    # Constructor signature
    try:
        print("init sig    :", inspect.signature(obj))
    except (TypeError, ValueError) as e:
        print("init sig    : <unavailable>", e)
    # Pydantic model fields?
    mf = getattr(obj, "model_fields", None)
    if mf:
        print("pydantic model_fields:")
        for fname, finfo in mf.items():
            print(f"    {fname}: required={finfo.is_required()} default={finfo.default!r} ann={finfo.annotation}")
    # Annotations
    ann = getattr(obj, "__annotations__", None)
    if ann:
        print("annotations :", dict(ann))
    # Public attributes
    pub = [a for a in dir(obj) if not a.startswith("_")]
    print("public attrs:", pub)


print("croo-sdk module file:", t.__file__)
print("croo.types public names:", [n for n in dir(t) if not n.startswith("_")])

dump("DeliverableType", getattr(t, "DeliverableType", None))
dump("DeliverOrderRequest", getattr(t, "DeliverOrderRequest", None))
