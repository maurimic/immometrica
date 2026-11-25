from fastapi import APIRouter, HTTPException, Body
from typing import Dict, Any, List
import json
from app.database import get_connection

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

PIPELINE_STAGES = [
    {"id": "neu", "name": "Neu eingetroffen", "color": "#1e73b3"},
    {"id": "merkliste", "name": "Merkliste / Prüfung", "color": "#004179"},
    {"id": "kontaktiert", "name": "Makler kontaktiert", "color": "#d55e00"},
    {"id": "besichtigung", "name": "Besichtigungstermin", "color": "#a15c07"},
    {"id": "angebot", "name": "Kaufangebot abgegeben", "color": "#cc79a7"},
    {"id": "notar", "name": "Beim Notar / Gekauft", "color": "#1a7f47"},
    {"id": "archiv", "name": "Archiviert / Abgelehnt", "color": "#61626a"}
]

@router.get("/stages")
def get_stages():
    return PIPELINE_STAGES

@router.get("/board")
def get_board():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM properties ORDER BY purchase_price DESC")
        rows = cursor.fetchall()
    finally:
        conn.close()

    columns = {stage["id"]: [] for stage in PIPELINE_STAGES}

    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        for json_col in ("ai_analysis", "image_analysis", "gallery_images"):
            raw_val = d.get(json_col)
            if isinstance(raw_val, str) and raw_val.strip():
                try:
                    d[json_col] = json.loads(raw_val)
                except Exception:
                    if json_col == "gallery_images":
                        d[json_col] = []
        stage = d.get("pipeline_stage") or "neu"
        if stage in columns:
            columns[stage].append(d)
        else:
            columns["neu"].append(d)

    board_response = []
    for stage in PIPELINE_STAGES:
        board_response.append({
            "stage": stage,
            "count": len(columns[stage["id"]]),
            "properties": columns[stage["id"]]
        })

    return board_response

@router.post("/move")
def move_property_stage(payload: Dict[str, Any] = Body(...)):
    property_id = payload.get("property_id")
    target_stage = payload.get("target_stage")

    valid_stage_ids = [s["id"] for s in PIPELINE_STAGES]
    if target_stage not in valid_stage_ids:
        raise HTTPException(status_code=400, detail="Invalid target stage")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE properties SET pipeline_stage = ? WHERE id = ?", (target_stage, property_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Property not found")
        conn.commit()
    finally:
        conn.close()

    return {"success": True, "property_id": property_id, "new_stage": target_stage}
