
from VLM.vlmcall import vlm_call


def vlm_extract_table(image, dialog_title, fields):
    """
    Ask the VLM to read a results table out of a dialog screenshot and
    return structured JSON. This ONLY extracts what's visible -- matching
    logic lives in find_exact_match() below, not here.
    """
    field_lines = "\n".join(f'      "{f}": "..."' for f in fields)
    prompt = f"""You are looking at a screenshot of a Windows desktop application dialog titled "{dialog_title}". It contains a results table/list.

Return ONLY valid JSON (no markdown fences, no commentary) matching this exact shape:

{{
  "table_bbox": [x0, y0, x1, y1],
  "rows": [
    {{
{field_lines}
      "row_center_y_frac": 0.0
    }}
  ]
}}

Rules:
- table_bbox is the bounding box of the results table ONLY (not the search field or the OK/Cancel buttons), as fractions of the full image width/height (0.0 to 1.0), in [left, top, right, bottom] order.
- rows lists every visible row top to bottom, exactly as displayed. Use an empty string for a blank cell -- never guess or invent a value.
- row_center_y_frac is that row's vertical center as a fraction of the table_bbox's OWN height (0.0 = top of table, 1.0 = bottom of table).
- If there is no table or it has zero rows, return {{"table_bbox": null, "rows": []}}.
"""
    return vlm_call(image, prompt)

