"""ComfyUI MiniMax H3 Context Loop 0.5.

Disk-backed recursive MiniMax H3 scene loops with frame-exact picture/audio
continuation, review gates, checkpoint resume, and final assembly.

This project continues the looping work that grew from NikoDemon80's original
ComfyUI-H3-Motion-Context. It intentionally uses distinct public node ids and
vendors upstream's shared runtime-patch ABI so both packs can be installed
together without wrapping ComfyUI twice. The
original Motion Context, Save Latent, and Load Latent ids remain exclusively
owned by Niko's upstream pack; this pack exports its stricter Loop Trim, a
distinctly named Seam Probe adaptation, and the specialized H3 Chain nodes.

Registers the loop nodes without changing ordinary ComfyUI or general Qwen
behavior. On older ComfyUI builds, startup adds the released H3-only tokenizer
tokens through a module-local alias, and Chain Context activates two internal
fallback patches inline on first execution:

  patch_layout   lifts the first/last-only keyframe anchor restriction,
                 moves pinned audio onto the clip's own timeline, and
                 keeps anchor coordinates aligned when refs shift the
                 layout cursor
  patch_payload  stops the refs branch clobbering keyframe cond latents,
                 so pinned video and pinned audio can be used together

Both wrappers are marker-gated. Niko's upstream copy and this vendored copy
recognize the same patch-ownership markers; whichever activates second stands
down. H3 workflows that use neither pack remain stock. If either self-test
fails the nodes still load but refuse the affected path, so an upstream
ComfyUI change produces a clear message rather than a silently wrong render.

When ComfyUI's native MiniMax H3 Add Guide API from merged PR #15439 is
available, core owns arbitrary-position video/audio guides, Ref2VA target
alignment, and keyframe/ref payload merging. This pack switches automatically
to native guide records and installs no H3 layout or payload wrapper. Version
0.5 emits a one-time update warning before using the legacy fallback.
"""

from .tokenizer_compat import (
    install_minimax_tokenizer_compat as _install_minimax_tokenizer_compat,
)

_MINIMAX_TOKENIZER_COMPAT_STATUS = _install_minimax_tokenizer_compat()

from .nodes import (
    NODE_CLASS_MAPPINGS as _CONTEXT_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _CONTEXT_NODE_DISPLAY_NAME_MAPPINGS,
)
from .chain_nodes import (
    CHAIN_NODE_CLASS_MAPPINGS,
    CHAIN_NODE_DISPLAY_NAME_MAPPINGS,
)
from .upscale_nodes import (
    UPSCALE_NODE_CLASS_MAPPINGS,
    UPSCALE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .pixel_continuity import (
    NODE_CLASS_MAPPINGS as _PIXEL_CONTINUITY_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _PIXEL_CONTINUITY_NAMES,
)
from .pixel_boundary import (
    NODE_CLASS_MAPPINGS as _PIXEL_BOUNDARY_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _PIXEL_BOUNDARY_NAMES,
)
from .selflift_nodes import (
    NODE_CLASS_MAPPINGS as _SELFLIFT_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _SELFLIFT_NAMES,
)
from .probe_node import (
    NODE_CLASS_MAPPINGS as _PROBE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _PROBE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .masking_nodes import (
    NODE_CLASS_MAPPINGS as _MASKING_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _MASKING_NODE_DISPLAY_NAME_MAPPINGS,
)
from .master_audio_context import (
    NODE_CLASS_MAPPINGS as _MASTER_AUDIO_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _MASTER_AUDIO_NODE_DISPLAY_NAME_MAPPINGS,
)
from .masked_bridge import (
    NODE_CLASS_MAPPINGS as _MASKED_BRIDGE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _MASKED_BRIDGE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .source_av_target import (
    NODE_CLASS_MAPPINGS as _SOURCE_AV_TARGET_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _SOURCE_AV_TARGET_NODE_DISPLAY_NAME_MAPPINGS,
)
from .reference_video_fade import (
    NODE_CLASS_MAPPINGS as _REFERENCE_VIDEO_FADE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _REFERENCE_VIDEO_FADE_DISPLAY_NAMES,
)
from .audio_refine import (
    NODE_CLASS_MAPPINGS as _AUDIO_REFINE_NODES,
    NODE_DISPLAY_NAME_MAPPINGS as _AUDIO_REFINE_NAMES,
)

NODE_CLASS_MAPPINGS = dict(_CONTEXT_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(CHAIN_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(UPSCALE_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_PIXEL_CONTINUITY_NODES)
NODE_CLASS_MAPPINGS.update(_PIXEL_BOUNDARY_NODES)
NODE_CLASS_MAPPINGS.update(_SELFLIFT_NODES)
NODE_CLASS_MAPPINGS.update(_PROBE_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_MASKING_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_MASTER_AUDIO_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_MASKED_BRIDGE_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_SOURCE_AV_TARGET_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_REFERENCE_VIDEO_FADE_NODE_CLASS_MAPPINGS)
NODE_CLASS_MAPPINGS.update(_AUDIO_REFINE_NODES)

NODE_DISPLAY_NAME_MAPPINGS = dict(_CONTEXT_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(CHAIN_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(UPSCALE_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(_PIXEL_CONTINUITY_NAMES)
NODE_DISPLAY_NAME_MAPPINGS.update(_PIXEL_BOUNDARY_NAMES)
NODE_DISPLAY_NAME_MAPPINGS.update(_SELFLIFT_NAMES)
NODE_DISPLAY_NAME_MAPPINGS.update(_PROBE_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(_MASKING_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(_MASTER_AUDIO_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(_MASKED_BRIDGE_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(_SOURCE_AV_TARGET_NODE_DISPLAY_NAME_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(
    _REFERENCE_VIDEO_FADE_DISPLAY_NAMES)
NODE_DISPLAY_NAME_MAPPINGS.update(_AUDIO_REFINE_NAMES)

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
