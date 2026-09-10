# Browser duration rounding

The Plan editor and Plan Studio round requested seconds up to the same 24 fps,
`17k + 5` frame grid used by the Python compiler. JavaScript remainder preserves
the sign of its left operand, so the browser must normalize a potentially
negative remainder before adding its padding.

| Requested duration | Required frames | Previous browser result | Compiler / corrected browser |
|---|---:|---:|---:|
| 1 second | 24 | 22 | 39 |
| 6 seconds | 144 | 141 | 158 |
| 12 seconds | 288 | 277 | 294 |

This also affects Seconds → Exact frames conversion and rejects requests just
beyond the maximum 3592-frame duration instead of silently rounding them down.
Existing authored exact frame counts are unchanged. Existing seconds requests
already compiled correctly in Python; their browser estimates now agree.

Run `python tests/_plan_duration_parity_test.py` to compare 7,829 duration
requests with the actual Python helper, including fractional-frame requests,
decimal tolerance and upper-bound failures. The test also compares six native
compiled Plans with browser raw/delivered timing and checks exact-frame
conversion. ComfyUI imports are stubbed, projects are temporary, and no GPU
execution or project-file writes are required.
