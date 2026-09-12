"""Content witnesses for copied-runtime inputs, without serializing tensors."""
import hashlib
import json


def execution_digest(value):
    """Hash supported input trees bit-exactly, using bounded CPU tensor chunks.

    Device/strides are not content; dtype, shape and every value byte are. Do
    not fall back to repr/object identity for an unknown runtime object. This
    witnesses supplied inputs, not their provenance from a model execution.
    """
    import torch
    from comfy.nested_tensor import NestedTensor

    digest, visiting = hashlib.sha256(), set()

    def header(value):
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)

    def tensor_bytes(tensor):
        if tensor.numel() * tensor.element_size() > 4 * 1024 * 1024:
            # Split by dimensions rather than flattening a huge noncontiguous
            # GPU view into a second full-size allocation.
            axis = next(i for i, n in enumerate(tensor.shape) if n > 1)
            middle = tensor.shape[axis] // 2
            yield from tensor_bytes(tensor.narrow(axis, 0, middle))
            yield from tensor_bytes(tensor.narrow(axis, middle, tensor.shape[axis]-middle))
        else:
            chunk = tensor.detach().to(device='cpu').contiguous().reshape(-1)
            yield memoryview(chunk.view(torch.uint8).numpy())

    def visit(item):
        if item is None or type(item) in (bool, int, float, str):
            header([type(item).__name__, item])
            return
        if torch.is_tensor(item):
            if (item.layout != torch.strided or item.is_quantized or item.is_nested
                    or item.is_conj() or item.is_neg()):
                raise ValueError('Unsupported tensor layout in scene-save input witness.')
            header(['tensor', str(item.dtype), list(item.shape)])
            for chunk in tensor_bytes(item):
                digest.update(chunk)
            return
        if id(item) in visiting:
            raise ValueError('Cyclic scene-save input witness.')
        visiting.add(id(item))
        try:
            if isinstance(item, NestedTensor):
                header(['nested_tensor', len(item.tensors)])
                for part in item.tensors:
                    visit(part)
            elif isinstance(item, (tuple, list)):
                header([type(item).__name__, len(item)])
                for part in item:
                    visit(part)
            elif isinstance(item, dict):
                if any(type(key) is not str for key in item):
                    raise ValueError('Scene-save input dictionaries require string keys.')
                header(['dict', sorted(item)])
                for key in sorted(item):
                    visit(item[key])
            else:
                raise ValueError('Unsupported scene-save input witness: '+type(item).__name__)
        finally:
            visiting.remove(id(item))

    visit(value)
    return digest.hexdigest()
