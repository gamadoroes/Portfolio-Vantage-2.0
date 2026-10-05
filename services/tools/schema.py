# services/tools/schema.py
"""Turn a pydantic input model into the plain JSON schema Claude is given."""

_DROP = ("$defs", "title")


def _inline(node, defs):
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _inline(defs[node["$ref"].split("/")[-1]], defs)
        rest = {k: _inline(v, defs) for k, v in node.items() if k != "$ref" and k not in _DROP}
        return {**target, **rest}
    out = {}
    for key, value in node.items():
        if key == "properties":
            # Property NAMES are kept as-is (a field may be called "title"); only their schemas are cleaned.
            out[key] = {name: _inline(schema, defs) for name, schema in value.items()}
        elif key in _DROP:
            continue
        else:
            out[key] = _inline(value, defs)
    return out


def claude_schema(model):
    schema = model.model_json_schema()
    return _inline(schema, schema.get("$defs", {}))
