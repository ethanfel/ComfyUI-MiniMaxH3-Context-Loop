"""Execution-local branch scope. Never mutate process-global active pointers.

Immutable revisions and project assets remain in the original run. Mutable
working files live in a branch directory. The legacy branch uses existing paths.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import json
import os
import re


_SCOPE = ContextVar("h3_working_branch", default=None)


def branch_id(value="main"):
    value = str(value or "main")
    if value != "main" and not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid H3 working branch id.")
    return value


def current_branch(run):
    scope = _SCOPE.get()
    return scope[1] if scope and scope[0] == str(run) else "main"


@contextmanager
def branch_scope(run, selected="main"):
    token = _SCOPE.set((str(run), branch_id(selected)))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def working_directory(project_directory, run, selected=None):
    selected = branch_id(current_branch(run) if selected is None else selected)
    root = os.path.realpath(project_directory)
    if selected == "main":
        return root
    path = os.path.realpath(os.path.join(root, "branches", selected))
    if os.path.commonpath((root, path)) != root:
        raise ValueError("H3 branch directory escapes its project.")
    if not os.path.isfile(os.path.join(path, "branch.json")):
        raise ValueError("Selected H3 branch is unavailable; select it again in Plan Studio.")
    return path


def _selection(value):
    if not isinstance(value, dict):
        return None
    for key in ("plan", "state", "source_manifest"):
        nested = _selection(value.get(key))
        if nested:
            return nested
    if value.get("run_name") and "_branch_id" in value:
        return str(value["run_name"]), branch_id(value["_branch_id"])
    return None


def _stamp(value, selected):
    # Only serializable execution carriers, never tensors/conditioning/models.
    if isinstance(value, dict):
        if selected[1] != "main" and value.get("run_name") and str(value["run_name"]) == selected[0]:
            value = dict(value, _branch_id=selected[1])
        if "result" in value:
            value = dict(value, result=_stamp(value["result"], selected))
        if "ui" in value:
            value = dict(value, ui={key: [_stamp(item, selected) for item in values]
                                   if isinstance(values, list) else values
                                   for key, values in value["ui"].items()})
        return value
    if isinstance(value, tuple):
        return tuple(_stamp(item, selected) for item in value)
    return value


def scoped_node(function):
    """Carry the explicit queued branch across independent ComfyUI node calls."""
    if getattr(function, "_h3_branch_scoped", False):
        return function
    signature = inspect.signature(function)

    def selected_inputs(args, kwargs):
        inputs = signature.bind_partial(*args, **kwargs).arguments
        selected = next((found for item in inputs.values()
                         if (found := _selection(item))), None)
        # Upstream Plan nodes execute before Studio. Carry its branch in the
        # authored JSON as well, so locks/editorial/resume cannot read Original.
        serialized_plan = inputs.get("plan_json_input") or inputs.get("plan_json")
        if isinstance(serialized_plan, str):
            try:
                authored = json.loads(serialized_plan)
            except ValueError:
                authored = None  # Let the Plan parser report malformed JSON.
            if isinstance(authored, dict) and "_branch_id" in authored:
                project = inputs.get("project_assets") or {}
                run = project.get("project") or inputs.get("run_name", "h3_chain")
                selected = str(run), branch_id(authored["_branch_id"])
        serialized = inputs.get("selection_json")
        if isinstance(serialized, str) and serialized.strip():
            saved = json.loads(serialized)
            if isinstance(saved, dict) and saved.get("run_name"):
                selected = saved["run_name"], branch_id(saved.get("_branch_id", "main"))
        if "working_branch_id" in signature.parameters:
            plan = inputs.get("plan") or inputs.get("project_assets") or {}
            run = plan.get("run_name") or plan.get("project") or inputs.get("run_name", "h3_chain")
            requested = (plan.get("_branch_id", "main") if inputs.get("plan") is not None
                         else inputs.get("working_branch_id", "main"))
            selected = str(run), branch_id(requested)
        return selected

    @wraps(function)
    def wrapped(*args, **kwargs):
        selected = selected_inputs(args, kwargs)
        if not selected:
            return function(*args, **kwargs)
        with branch_scope(*selected):
            return _stamp(function(*args, **kwargs), selected)

    @wraps(function)
    async def async_wrapped(*args, **kwargs):
        selected = selected_inputs(args, kwargs)
        if not selected:
            return await function(*args, **kwargs)
        with branch_scope(*selected):
            return _stamp(await function(*args, **kwargs), selected)

    result = async_wrapped if inspect.iscoroutinefunction(function) else wrapped
    result._h3_branch_scoped = True
    return result


def scope_nodes(mapping):
    for cls in set(mapping.values()):
        name = cls.FUNCTION
        method = getattr(cls, name)
        setattr(cls, name, scoped_node(method))


def scoped_request(function):
    @wraps(function)
    async def wrapped(request):
        values = dict(getattr(request, "query", {}) or {})
        if getattr(request, "method", "GET") == "POST":
            try:
                body = await request.json()
            except (ValueError, TypeError):
                from aiohttp import web
                return web.json_response({"error": "H3 request requires valid JSON."}, status=400)
            if isinstance(body, dict):
                values = {**body, **values}
        run = values.get("run_name")
        if not run or "branch_id" not in values:
            return await function(request)
        from aiohttp import web
        try:
            with branch_scope(run, values["branch_id"]):
                return await function(request)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return wrapped


def scoped_review(function, inventory):
    """Live review tokens, not browser selection, own their execution branch."""
    @wraps(function)
    async def wrapped(request):
        try:
            body = await request.json()
        except (ValueError, TypeError):
            from aiohttp import web
            return web.json_response({"error": "H3 review requires valid JSON."}, status=400)
        if not isinstance(body, dict):
            from aiohttp import web
            return web.json_response({"error": "H3 review requires a JSON object."}, status=400)
        plan = inventory.get(str(body.get("token") or ""), {}).get("plan", {})
        if plan.get("run_name"):
            with branch_scope(plan["run_name"], plan.get("_branch_id", "main")):
                return await function(request)
        return await function(request)
    return wrapped
