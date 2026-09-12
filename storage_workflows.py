"""Read-only external-path audit and explicit in-memory workflow-copy relinking.

Historical recovery documents and embedded H3 contracts are never rewritten.
Unknown/embedded path syntax is a review blocker, not permission to replace
substrings. Callers must retain the original and save a separate reviewed copy.
"""

from copy import deepcopy
from pathlib import Path

if __package__:
    from .storage_resolver import resolve_output, logical_output, StorageError
else:
    from storage_resolver import resolve_output, logical_output, StorageError


def audit_workflow(workflow, output_root, project_root, *, legacy_project_root=None):
    output, project = Path(output_root).absolute(), Path(project_root).absolute()
    if project.parent != output/'h3_chains':
        raise ValueError("Workflow audit requires one project directly under output/h3_chains.")
    relative = project.relative_to(output).as_posix()
    roots = {str(project).replace('\\','/').rstrip('/'): str(output),
             relative: str(output)}
    if legacy_project_root:
        roots[str(legacy_project_root).replace('\\','/').rstrip('/')] = str(output)
    found = []

    def visit(value, locator=(), node_type=None, executable=False):
        if isinstance(value, dict):
            kind = value.get('class_type') or (value.get('type') if 'widgets_values' in value or 'inputs' in value else None)
            if isinstance(kind, str):
                node_type = kind
            for key, child in value.items():
                visit(child, locator+(key,), node_type,
                      executable or key in ('inputs','widgets_values'))
        elif isinstance(value, list):
            for index,child in enumerate(value):
                visit(child,locator+(index,),node_type,executable)
        elif isinstance(value, str):
            portable = value.replace('\\','/')
            prefix = next((root for root in sorted(roots,key=len,reverse=True)
                           if portable == root or portable.startswith(root+'/')), None)
            embedded = any(root+'/' in portable for root in roots)
            if prefix is None and not embedded:
                return
            row = {'locator':list(locator), 'node_type':node_type, 'value':value}
            cache_key='h3_plan_studio_checkpoint_cache_v1'
            cache_index=next((index for index in range(1,len(locator))
                             if locator[index]==cache_key and locator[index-1]=='properties'),None)
            if node_type=='MiniMaxH3ChainPlanStudio' and cache_index is not None:
                # Browser thumbnails/preview URLs, not authoring or checkpoint
                # authority. A reviewed workflow clone can drop this one cache
                # and let Plan Studio fetch fresh resolved URLs from the server.
                row.update(status='refreshable_preview_cache',cache_locator=list(locator[:cache_index+1]))
            elif node_type and node_type.startswith('MiniMaxH3') and executable:
                row['status']='managed_identity_preserved'
            elif prefix is None or '\n' in value or '\r' in value:
                row['status']='embedded_path_requires_review'
            elif not executable or not node_type:
                row['status']='unclassified_path_requires_review'
            else:
                suffix=portable[len(prefix):].lstrip('/')
                address=relative+('/'+suffix if suffix else '')
                try:
                    target=resolve_output(output,address)
                    if not target.exists():
                        row['status']='missing_path_requires_review'
                    elif target==project/suffix and prefix==str(project).replace('\\','/'):
                        return  # already a live physical address
                    else:
                        row.update(status='exact_external_path',target=str(target),
                                   logical=logical_output(output,target))
                except (OSError,ValueError) as exc:
                    row.update(status='invalid_path_requires_review',error=str(exc))
            found.append(row)
    visit(workflow)
    return found


def relink_workflow_copy(workflow, audit, *, approved_node_types):
    """Return a clone; only explicitly approved exact node values can change."""
    copied=deepcopy(workflow)
    approved=set(approved_node_types)
    blocked=[]
    cleared=set()
    for row in audit:
        if row['status']=='managed_identity_preserved':
            continue
        if row['status']=='refreshable_preview_cache':
            locator=tuple(row['cache_locator'])
            original=workflow
            for key in row['locator']:
                original=original[key]
            if original!=row['value']:
                raise StorageError("Workflow changed since preview cache audit.")
            if locator not in cleared:
                current=copied
                for key in locator[:-1]:
                    current=current[key]
                current.pop(locator[-1],None)
                cleared.add(locator)
            continue
        if row['status']!='exact_external_path' or row['node_type'] not in approved:
            blocked.append({'locator':row['locator'],'status':row['status'],'node_type':row['node_type']})
            continue
        current=copied
        for key in row['locator'][:-1]:
            current=current[key]
        key=row['locator'][-1]
        if current[key]!=row['value']:
            raise StorageError("Workflow changed since path audit; refusing stale relinking.")
        current[key]=row['target']
    if blocked:
        raise StorageError("Workflow copy still has %d unapproved or ambiguous path references; "
                           "review these before migration/relinking." % len(blocked))
    return copied
